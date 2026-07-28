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
        # Keyed by app name, not upstream id: `output_schema` (D4,
        # docs/02-decisions.md) is per-app, baked into the adapter at
        # construction (FoundryResponsesAdapter._text_format), so two apps
        # sharing one upstream can no longer safely share one adapter
        # instance. Trades a little duplicate-client overhead (two
        # AIProjectClients instead of one, for the rare app pair that
        # shares an upstream) for not inventing a separate 1:1-enforcement
        # validation layer -- every app in this repo's own example config
        # already has its own upstream.
        for app_cfg in self._config.apps:
            upstream = self._config.upstream_for_app(app_cfg.name)
            self._adapters[app_cfg.name] = self._build_adapter(upstream, app_cfg)

    def _build_adapter(self, upstream, app_cfg) -> UpstreamAdapter:
        if upstream.tier == "t2":
            project = AIProjectClient(endpoint=upstream.project_endpoint, credential=self._credential)
            return FoundryHostedAdapter(
                project_client=project,
                agent_name=upstream.agent_name,
                identity_mode=upstream.identity,
                project_endpoint=upstream.project_endpoint,
                credential=self._credential,
                output_schema=app_cfg.output_schema.model_dump() if app_cfg.output_schema else None,
            )
        if upstream.tier == "t3":
            return DurableAdapter(
                instances=upstream.instances,
                health_path=upstream.health,
                event_source=self._task_store,
            )
        raise ValueError(f"unknown tier {upstream.tier!r}")

    def adapter_for_app(self, app_name: str) -> UpstreamAdapter:
        return self._adapters[app_name]

    async def health_check_all(self) -> dict[str, bool]:
        results: dict[str, bool] = {}
        for app_name, adapter in self._adapters.items():
            try:
                results[app_name] = await adapter.health()
            except Exception:
                log.exception("health check failed for app %s", app_name)
                results[app_name] = False
        return results
