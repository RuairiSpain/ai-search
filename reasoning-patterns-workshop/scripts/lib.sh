#!/usr/bin/env bash
# scripts/lib.sh — sourced (never executed) by every pattern's infra/deploy.sh
# and infra/destroy.sh.
#
# Why this exists: the same 4-line preamble (cd into the pattern dir, compute
# REPO_ROOT, source .shared-env, pip-install-with-fallback) was copy-pasted
# across 11 deploy.sh files, and the same agent-teardown pattern was
# copy-pasted across several destroy.sh files. One of those copies (patterns
# 01 and 02) drifted onto a dead SDK surface (`project_client().agents.*`,
# removed in azure-ai-projects 2.x) and shipped for two review rounds because
# nothing executed the shell-embedded Python to catch it. Centralizing here
# means a fix in one place actually reaches every pattern, and the one
# execution path is what scripts/verify_offline.py's `t_lib_functions` test
# exercises directly.
#
# Usage, from a pattern's infra/deploy.sh (cwd is the pattern dir; make runs
# targets from there):
#   #!/usr/bin/env bash
#   source "$(cd "$(dirname "$0")/../../.." && pwd)/scripts/lib.sh"
#   init_pattern_infra "$0"
#   install_shared_reqs                 # or: install_shared_reqs pytest==9.1.1
#   ... pattern-specific deploy steps ...
set -euo pipefail

# init_pattern_infra <script-path>
#   cd into the pattern directory, export REPO_ROOT, source the shared env
#   written by infra/shared/deploy.sh. Must be called with "$0" from the
#   calling script so the relative path math is anchored correctly.
init_pattern_infra() {
  local script_path="$1"
  cd "$(dirname "$script_path")/.."
  REPO_ROOT="$(cd ../.. && pwd)"
  export REPO_ROOT
  # shellcheck disable=SC1091
  source "$REPO_ROOT/.shared-env"
}

# install_shared_reqs [extra pip-install args...]
#   Installs the shared package requirements, with a --user fallback for
#   externally-managed environments (PEP 668). Extra args (e.g. a
#   pattern-local requirements.txt, or a single pinned package) are appended
#   to BOTH attempts. Do not pass a version pin for a package already pinned
#   in common/reasoning_common/requirements.txt — that produces two
#   conflicting exact-version constraints in one pip invocation (this is
#   exactly the bug patterns 03 and 06 had: an inline
#   azure-storage-blob==12.24.0 fighting the shared file's 12.30.0 pin).
#
#   On some systems --user ALSO refuses under PEP 668 (confirmed by actually
#   running this against a Debian-family interpreter while writing it) — the
#   original per-pattern scripts would then dump the same pip error twice and
#   die under `set -e` with no actionable hint. Both attempts run quietly and
#   a single clear message points at the real fix (a venv) on total failure.
install_shared_reqs() {
  : "${REPO_ROOT:?install_shared_reqs: call init_pattern_infra first}"
  local reqs="$REPO_ROOT/common/reasoning_common/requirements.txt"
  if python3 -m pip install -q -r "$reqs" "$@" 2>/dev/null; then
    return 0
  fi
  if python3 -m pip install -q --user -r "$reqs" "$@" 2>/dev/null; then
    return 0
  fi
  echo "pip install failed under both the default and --user modes." >&2
  echo "This Python is almost certainly externally managed (PEP 668)." >&2
  echo "Fix: python3 -m venv .venv && source .venv/bin/activate, then re-run." >&2
  echo "See TROUBLESHOOTING.md -> 'pip refuses to install'." >&2
  return 1
}

# noop_destroy <pattern-label>
#   The teardown for patterns that provision nothing beyond shared infra.
noop_destroy() {
  echo "pattern $1 provisions nothing beyond shared infra — nothing to destroy."
}

# delete_pattern_agents <prefix> [<vector-store-prefix>]
#   Thin wrapper around the tested Python helpers in foundry_client.py, so
#   destroy.sh files call ONE line instead of embedding a heredoc each.
delete_pattern_agents() {
  : "${REPO_ROOT:?delete_pattern_agents: call init_pattern_infra first}"
  local agent_prefix="$1"
  local vs_prefix="${2:-}"
  python3 - "$agent_prefix" "$vs_prefix" << PY
import sys
from pathlib import Path
sys.path.insert(0, str(Path("$REPO_ROOT") / "common"))
from reasoning_common import foundry_client as fc
agent_prefix, vs_prefix = sys.argv[1], sys.argv[2]
for name in fc.delete_agents_by_prefix(agent_prefix):
    print("deleted agent", name)
if vs_prefix:
    for name in fc.delete_vector_stores_by_prefix(vs_prefix):
        print("deleted vector store", name)
PY
}
