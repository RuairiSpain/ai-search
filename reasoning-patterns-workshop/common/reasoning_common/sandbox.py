"""Constrained execution for MODEL-GENERATED code (patterns 07 and 11).

Threat model
------------
Both patterns run code an LLM wrote. Pattern 11 runs generated *implementation*
against fixed tests; pattern 07 runs a test file the model *authored*. Before
this module they ran as `subprocess.run([sys.executable, "-m", "pytest", ...])`
in a tempdir: full network, full filesystem as the workshop user, the operator's
Azure credentials in the environment, and no resource ceiling. A "skill" whose
acceptance test read ~/.azure/ and POSTed it somewhere would have worked.

What this gives you (defence in depth, cheap, no Docker required)
----------------------------------------------------------------
1. **Stripped environment** — anything matching AZURE_*/OPENAI_*/*KEY*/*SECRET*/
   *TOKEN*/*PASSWORD* is removed, HOME and TMPDIR are redirected into the
   throwaway workspace, so `~/.azure/`, `~/.ssh/` and friends resolve to an
   empty directory even via tilde expansion.
2. **Network block** — an injected `sitecustomize.py` neuters
   `socket.socket.connect`/`connect_ex`, `socket.create_connection` and
   `ssl.SSLContext.wrap_socket` at interpreter start, so any Python-level
   egress raises immediately. Deliberately patches the connecting METHODS,
   not the socket/SSLSocket classes — reassigning the class breaks anything
   that subclasses it at import time (ssl.SSLSocket does, and pytest's own
   plugin autoloading triggers that import on every run; found by testing
   this against the real fixtures, not a toy script).
3. **Process-spawn block** — the same hook patches `subprocess.Popen`'s
   constructor (which `run`/`call`/`check_call`/`check_output` all go
   through) plus `os.system`, `os.exec*`, `os.spawn*` and `os.fork`, which
   stops the obvious escape of shelling out to curl.
4. **Resource ceilings** — RLIMIT_CPU, RLIMIT_AS, RLIMIT_FSIZE and RLIMIT_NPROC
   via `preexec_fn`, so a fork bomb or a 10GB allocation dies instead of taking
   the laptop with it.
5. **Wall-clock timeout** and a working directory that is deleted afterwards.

What this is NOT
----------------
This is not a security boundary. It is Python-level hardening inside the same
OS user: native extensions, `ctypes`, or a bug in the blocks can bypass it.
For anything beyond a workshop on synthetic data, run generated code in a
container with a read-only rootfs, no egress and a dedicated identity
(ACI, Container Apps job, or a GitHub Actions runner) — see SECURITY.md.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

SECRET_MARKERS = ("KEY", "SECRET", "TOKEN", "PASSWORD", "CONNECTION_STRING", "CREDENTIAL")
SECRET_PREFIXES = ("AZURE_", "OPENAI_", "ANTHROPIC_", "AWS_", "GOOGLE_", "FOUNDRY_",
                   "MCP_", "APPINSIGHTS_", "SEARCH_", "STORAGE_")

_SITECUSTOMIZE = '''\
"""Injected by reasoning_common.sandbox — blocks egress and process spawning.

Patches METHODS/CONSTRUCTORS, not classes. Reassigning socket.socket or
subprocess.Popen to a plain function breaks any subclass built on top of them
at import time — found empirically while wiring this in: pytest's own plugin
autoloading imports anyio, which imports ssl, and ssl.SSLSocket subclasses
socket.socket at module scope. The naive "replace the class" version broke
EVERY pytest run in an unrelated-looking way (TypeError deep in ssl.py, no
mention of the sandbox anywhere in the traceback). Patching connect()/
connect_ex() and Popen.__init__ instead blocks the same operations without
touching class identity, so subclassing at import time keeps working.
"""
import os
import socket


class SandboxViolation(RuntimeError):
    pass


def _blocked(*_a, **_k):
    raise SandboxViolation(
        "blocked by the workshop sandbox: generated code may not open network "
        "connections or spawn processes (see common/reasoning_common/sandbox.py)")


# Network: block the CONNECTING methods, not the socket class itself.
socket.socket.connect = _blocked
socket.socket.connect_ex = _blocked
socket.create_connection = _blocked
socket.socketpair = _blocked
if hasattr(socket, "create_server"):
    socket.create_server = _blocked

try:
    import ssl
    # ssl.SSLSocket overrides connect()/connect_ex() itself; patch those too,
    # and do it AFTER the socket.socket patches above so subclassing during
    # `import ssl` already succeeded before we touch anything ssl-specific.
    ssl.SSLSocket.connect = _blocked
    ssl.SSLSocket.connect_ex = _blocked
    ssl.SSLContext.wrap_socket = _blocked
except Exception:
    pass

# Process spawn: patch Popen's CONSTRUCTOR, not the class. subprocess.run/
# call/check_call/check_output all construct a Popen internally (stdlib,
# 3.5+), so this one patch blocks the entire high-level API from one place
# instead of enumerating every entry point (the original version missed
# check_call entirely).
try:
    import subprocess
    subprocess.Popen.__init__ = _blocked
except Exception:
    pass

# os-level primitives: os.system doesn't go through subprocess at all, and
# exec*/spawn*/fork are additional escape hatches subprocess doesn't cover.
for _name in ("system", "fork", "forkpty", "execv", "execve", "execvp", "execvpe",
              "spawnv", "spawnve", "spawnvp", "spawnvpe", "posix_spawn"):
    if hasattr(os, _name):
        setattr(os, _name, _blocked)
'''


def _clean_env(workspace: Path) -> dict[str, str]:
    env = {}
    for k, v in os.environ.items():
        upper = k.upper()
        if upper.startswith(SECRET_PREFIXES) or any(m in upper for m in SECRET_MARKERS):
            continue
        env[k] = v
    env["HOME"] = str(workspace)          # ~/.azure, ~/.ssh resolve into the tempdir
    env["TMPDIR"] = str(workspace)
    env["PYTHONPATH"] = str(workspace)     # so sitecustomize.py is picked up
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env.pop("VIRTUAL_ENV", None)
    return env


def _limits(cpu_seconds: int, address_space_mb: int, max_file_mb: int, max_procs: int):
    def _apply():  # runs in the child between fork and exec
        import resource
        resource.setrlimit(resource.RLIMIT_CPU, (cpu_seconds, cpu_seconds))
        if address_space_mb:
            b = address_space_mb * 1024 * 1024
            resource.setrlimit(resource.RLIMIT_AS, (b, b))
        b = max_file_mb * 1024 * 1024
        resource.setrlimit(resource.RLIMIT_FSIZE, (b, b))
        try:
            resource.setrlimit(resource.RLIMIT_NPROC, (max_procs, max_procs))
        except (ValueError, OSError):
            pass  # not settable on every platform; the spawn block still applies
    return _apply


def install(workspace: Path) -> None:
    """Write the sitecustomize hook into a workspace before running code there."""
    (workspace / "sitecustomize.py").write_text(_SITECUSTOMIZE, encoding="utf-8")


def run_python(
    args: list[str],
    workspace: Path,
    *,
    timeout_s: int = 60,
    cpu_seconds: int = 30,
    address_space_mb: int = 1024,
    max_file_mb: int = 32,
    max_procs: int = 64,
) -> tuple[int, str]:
    """Run `python <args>` inside `workspace` under the constraints above.

    Returns (returncode, combined_output). A timeout returns (-1, message) so
    callers can treat it as an ordinary failure rather than an exception.
    """
    install(workspace)
    try:
        proc = subprocess.run(
            [sys.executable, *args],
            cwd=workspace,
            env=_clean_env(workspace),
            capture_output=True,
            text=True,
            timeout=timeout_s,
            preexec_fn=_limits(cpu_seconds, address_space_mb, max_file_mb, max_procs),
        )
        return proc.returncode, (proc.stdout + proc.stderr)
    except subprocess.TimeoutExpired:
        return -1, (f"TIMEOUT: exceeded {timeout_s}s wall clock "
                    "(infinite loop, or code waiting on something it cannot reach)")
