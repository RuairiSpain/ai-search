"""The hosted agent's local scratch filesystem (stands in for $HOME/artifacts).

Files here are temporary working state for a single in-flight operation only.
Nothing durable lives here: once an artifact is uploaded to the blob store,
``cleanup_workspace_file`` deletes the local copy so the hosted-agent
container doesn't accumulate disk usage across invocations.
"""

from __future__ import annotations

from pathlib import Path

from .config import get_settings


def workspace_path(operation_id: str) -> Path:
    return get_settings().workspace_root / f"{operation_id}.md"


def write_workspace_file(operation_id: str, content: str) -> Path:
    path = workspace_path(operation_id)
    path.write_text(content, encoding="utf-8")
    return path


def delete_workspace_file(operation_id: str) -> None:
    workspace_path(operation_id).unlink(missing_ok=True)
