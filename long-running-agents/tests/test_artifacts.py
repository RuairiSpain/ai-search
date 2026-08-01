"""Offline test for the harvester's pure blob-key construction. The
prefix layout is load-bearing (docs/07 §2 item 2 — blob lifecycle
policies match on it, deletion is a single prefix delete), so pin its
shape explicitly rather than only exercising it incidentally elsewhere.
"""
from __future__ import annotations

from gateway.artifacts import ArtifactHarvester


def test_blob_key_matches_documented_prefix_layout():
    harvester = ArtifactHarvester(blob_service=None, container_name="artifacts", artifacts=None)  # type: ignore[arg-type]

    key = harvester.blob_key(
        app="ticket-triage",
        principal_hash="abc123",
        context_id="ctx_xyz",
        task_id="task_1",
        artifact_id="file_1",
        name="chart.png",
    )

    assert key == "artifacts/ticket-triage/abc123/ctx_xyz/task_1/file_1-chart.png"
