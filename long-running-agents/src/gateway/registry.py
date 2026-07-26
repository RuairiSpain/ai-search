"""Builds the per-app EntraValidator and UpstreamAdapter instances from
GatewayConfig at startup. One registry, constructed once — there is no
traffic splitting (docs/05 §6.1), so config changes require a redeploy,
not a hot swap.
"""
from __future__ import annotations

import logging
import os

from azure.ai.projects.aio import AIProjectClient
from azure.identity.aio import DefaultAzureCredential
from azure.storage.blob.aio import BlobServiceClient

from gateway.artifacts import ArtifactHarvester
from gateway.auth.principal import EntraValidator
from gateway.config import GatewayConfig
from gateway.store.artifact_store import ArtifactStore
from gateway.store.task_store import TaskStore
from gateway.upstream.base import UpstreamAdapter
from gateway.upstream.durable import DurableAdapter
from gateway.upstream.foundry_hosted import FoundryHostedAdapter
from gateway.upstream.foundry_responses import FoundryResponsesAdapter

log = logging.getLogger(__name__)


class Registry:
    def __init__(self, config: GatewayConfig, task_store: TaskStore, artifact_store: ArtifactStore):
        self._config = config
        self._task_store = task_store
        self.validator = EntraValidator(
            tenant_id=config.auth.tenant_id,
            audience=config.auth.audience,
            subject_claim=config.auth.subject_claim,
        )
        self._adapters: dict[str, UpstreamAdapter] = {}
        self._credential = DefaultAzureCredential()

        blob_service = BlobServiceClient(
            account_url=os.environ["ARTIFACTS_STORAGE_ACCOUNT_URL"], credential=self._credential
        )
        self.harvester = ArtifactHarvester(
            blob_service=blob_service,
            container_name=os.environ.get("ARTIFACTS_CONTAINER", "artifacts"),
            artifacts=artifact_store,
        )

    def build(self) -> None:
        for upstream in self._config.upstreams:
            self._adapters[upstream.id] = self._build_adapter(upstream)

    def _build_adapter(self, upstream) -> UpstreamAdapter:
        if upstream.tier == "t1":
            project = AIProjectClient(endpoint=upstream.project_endpoint, credential=self._credential)
            client = project.get_openai_client(agent_name=upstream.agent_name)
            return FoundryResponsesAdapter(
                openai_client=client,
                agent_name=upstream.agent_name,
                project_endpoint=upstream.project_endpoint,
                credential=self._credential,
            )
        if upstream.tier == "t2":
            project = AIProjectClient(endpoint=upstream.project_endpoint, credential=self._credential)
            return FoundryHostedAdapter(
                project_client=project,
                agent_name=upstream.agent_name,
                identity_mode=upstream.identity,
            )
        if upstream.tier == "t3":
            return DurableAdapter(
                instances=upstream.instances,
                health_path=upstream.health,
                event_source=self._task_store,
            )
        raise ValueError(f"unknown tier {upstream.tier!r}")

    def adapter_for_app(self, app_name: str) -> UpstreamAdapter:
        upstream_id = self._config.app(app_name).upstream
        return self._adapters[upstream_id]

    async def health_check_all(self) -> dict[str, bool]:
        results: dict[str, bool] = {}
        for upstream_id, adapter in self._adapters.items():
            try:
                results[upstream_id] = await adapter.health()
            except Exception:
                log.exception("health check failed for upstream %s", upstream_id)
                results[upstream_id] = False
        return results
