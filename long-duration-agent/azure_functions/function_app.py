"""Reference hosting for the translation Workflow on Azure Functions' Durable Task engine.

This is the production upgrade path from ``hosted_agent/app.py`` (a single FastAPI process
holding one SSE connection open per in-flight operation) to Azure's own durable execution
engine: automatic cross-process/cross-machine durability, retries, and no long-lived HTTP
connection required for a run that pauses for hours waiting on a HITL response.

The *same* ``build_workflow(...)`` from ``durable/pipeline.py`` is reused unchanged - only the
hosting layer differs. Concretely, that means:

- Durability changes hands. FileCheckpointStorage/TableCheckpointStorage
  (durable/engine.py) are NOT used here - pass ``checkpoint_storage=None`` when building the
  workflow. The Durable Task engine's own orchestration-replay model provides durability
  instead; ``agent_framework_durabletask`` translates each Executor/edge into an
  orchestrator/activity function pair that Functions' own storage account persists and
  replays. Running both durability mechanisms at once would be redundant, not additive.
- The client contract changes from SSE to Durable Functions' standard async-HTTP pattern:
  the starter function below returns 202 with a status-check URL immediately, instead of
  holding a connection open and streaming events. A client polls that URL for status/output,
  or - per ``WorkflowHitlContext`` (agent_framework_azurefunctions) - calls a respond URL to
  answer a pending HITL request, playing the same role as our own
  ``POST /invocations/{operation_id}/respond``.

Verified in this repo: ``AgentFunctionApp``'s constructor signature and
``WorkflowHitlContext``'s shape, by importing `agent-framework-azurefunctions` /
`agent-framework-durabletask` and inspecting them directly (both are real, installable
packages - `pip install --pre agent-framework-azurefunctions agent-framework-durabletask`).
NOT verified end-to-end: this repo's sandbox has no Azure Functions Core Tools and no Durable
Task storage backend to actually run a live orchestration against, so treat this file as a
reviewed starting point, not a tested one. Before relying on it: `func start` locally against
Azurite (Functions' local storage emulator, `AzureWebJobsStorage=UseDevelopmentStorage=true`),
run a translation through it end to end including a steering/HITL round-trip, and confirm the
respond/status URLs `WorkflowHitlContext` builds behave the way `docs/architecture.md`
describes.
"""

from __future__ import annotations

from agent_framework_azurefunctions import AgentFunctionApp

from long_duration_agent.durable.pipeline import build_workflow

# checkpoint_storage=None: the Durable Task engine's own orchestration-replay model is what
# provides durability under this hosting model, not agent_framework's CheckpointStorage
# protocol - see the module docstring above.
workflow = build_workflow(workflow_name="lda-translate", checkpoint_storage=None)

# Registers the HTTP starter, the orchestrator function, and one activity function per
# Executor in the workflow graph (validate, translate, save_markdown, artifact_created,
# steering_gate, upload, cleanup_local, link, stop) - all derived from the same Workflow
# object durable/pipeline.py already defines.
app = AgentFunctionApp(workflow=workflow)
