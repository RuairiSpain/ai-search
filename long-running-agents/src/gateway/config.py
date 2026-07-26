"""Loads apps.yaml (the gateway's own config, not azure.yaml — see
docs/02-decisions.md "azure.yaml <-> gateway YAML" table) and resolves
`${VAR}` substitutions from the process environment.

T1 (prompt agents) is not a gateway tier: it's short/synchronous by
nature and gets Foundry's own native incoming A2A directly, with no
per-user isolation multiplexing to broker and no streaming/artifacts to
add. This gateway exists specifically for what Foundry's native A2A
endpoint doesn't do — streaming, artifacts, and safe per-user identity
multiplexing (docs/00-tier-model-and-concepts.md) — which is exactly T2
and T3.

`${{ ... }}` (double-brace, server-side Foundry substitution) is left
untouched here — that syntax is never resolved by the gateway, only by
Foundry at runtime. Mixing the two up puts a secret in the wrong plane.
"""
from __future__ import annotations

import os
import re
from functools import lru_cache
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field

_VAR_RE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")


class AuthConfig(BaseModel):
    tenant_id: str
    audience: str
    subject_claim: str = "oid"


class CardCapabilities(BaseModel):
    streaming: bool = False
    pushNotifications: bool = False


class CardConfig(BaseModel):
    description: str = ""
    capabilities: CardCapabilities = Field(default_factory=CardCapabilities)


class AppConfig(BaseModel):
    name: str
    tier: Literal["t2", "t3"]
    upstream: str
    default_mode: Literal["short", "long"] = "short"
    sync_budget_ms: int = 8000
    foundry_agent: str | None = None
    preview: Literal["allow", "deny"] = "deny"
    card: CardConfig = Field(default_factory=CardConfig)


class UpstreamConfig(BaseModel):
    id: str
    tier: Literal["t2", "t3"]
    # T2
    project_endpoint: str | None = None
    agent_name: str | None = None
    identity: Literal["per_user", "service"] = "per_user"
    justification: str | None = None  # required by linter rule L020 when identity=service
    # T3
    protocol: Literal["a2a", "rest+callback"] | None = None
    instances: list[str] = Field(default_factory=list)
    health: str = "/healthz"


class GatewayConfig(BaseModel):
    auth: AuthConfig
    apps: list[AppConfig]
    upstreams: list[UpstreamConfig]

    def app(self, name: str) -> AppConfig:
        for app in self.apps:
            if app.name == name:
                return app
        raise KeyError(f"no app named {name!r}")

    def upstream(self, upstream_id: str) -> UpstreamConfig:
        for up in self.upstreams:
            if up.id == upstream_id:
                return up
        raise KeyError(f"no upstream {upstream_id!r}")

    def upstream_for_app(self, app_name: str) -> UpstreamConfig:
        return self.upstream(self.app(app_name).upstream)


def _substitute_env(raw: str) -> str:
    def repl(match: re.Match[str]) -> str:
        name = match.group(1)
        try:
            return os.environ[name]
        except KeyError as exc:
            raise RuntimeError(
                f"config references ${{{name}}} but it is not set in the environment"
            ) from exc

    return _VAR_RE.sub(repl, raw)


def load_config(path: str | Path) -> GatewayConfig:
    raw = Path(path).read_text(encoding="utf-8")
    resolved = _substitute_env(raw)
    data = yaml.safe_load(resolved)
    return GatewayConfig.model_validate(data)


@lru_cache(maxsize=1)
def get_config() -> GatewayConfig:
    """Process-wide singleton. Cleared implicitly on process restart —
    there is no traffic splitting (docs/05 §6.1), so a config change is a
    redeploy, not a hot reload."""
    path = os.environ.get("GATEWAY_CONFIG_PATH", "config/apps.yaml")
    return load_config(path)
