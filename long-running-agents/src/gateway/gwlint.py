"""gwlint — the D6 CI linter (docs/02-decisions.md D6, rule catalogue in
that section).

**Scope note, read before adding a rule or wondering why one is missing:**
the full L0xx catalogue as documented spans four files this repo doesn't
contain at all — `azure.yaml`, `agents/*.yaml`, `workflows/*.yaml` belong
to the separate Foundry-agent-deployment repo, not this gateway — plus a
few rules that need a live Foundry project connection to resolve
(model/tool/region availability, live RBAC grants) or reference a
dependency pin that lives in a *different* deployable's pyproject.toml
(L031: `azure-ai-agentserver-core` is the T2 agent CONTAINER's dependency,
confirmed absent from this gateway's own pyproject.toml — the gateway
calls the agent through `azure-ai-projects`, it never runs inside the
container). This implementation covers exactly what's checkable from this
repo alone: the gateway's own `apps.yaml`/`upstreams.yaml` and its own
source tree. Every rule this can't check is reported as SKIPPED at every
run, not silently dropped — see `_SKIPPED_RULES`. Parses `apps.yaml` as
plain YAML rather than through `gateway.config.load_config()`
deliberately: this is a structural linter, not a deploy-time loader, and
shouldn't fail on a missing `${VAR}` for a secret it doesn't need to
resolve to check `identity`/`justification`/`tier`/`preview`/
`pushNotifications`.
"""
from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

_SECRET_KEY_RE = re.compile(r"(key|secret|password|token|credential)", re.IGNORECASE)
_VAR_PLACEHOLDER_RE = re.compile(r"^\$\{[A-Za-z_][A-Za-z0-9_]*\}$")
_ASSISTANTS_API_RE = re.compile(
    r"\.beta\.threads\b|\.beta\.assistants\b|openai\.types\.beta\.assistant"
)

# rule -> why gwlint can't check it from this repo alone. Printed on every
# run so "not applicable" and "not yet implemented" never look the same as
# "silently forgotten."
_SKIPPED_RULES: dict[str, str] = {
    "L001": "model deployment exists in the target project -- needs a live Foundry connection",
    "L002": "model available in the project's region -- needs a live Foundry connection",
    "L003": "instructionsFile resolves -- references agents/*.yaml, not part of this repo",
    "L004": "skills/toolboxes/connections resolve -- references agents/*.yaml, not part of this repo",
    "L005": "foundry_agent matches a declared agent -- references agents/*.yaml, not part of this repo",
    "L010": "default_mode: long requires a background-capable model -- needs a live Foundry connection",
    "L011": "tool available in region/model -- needs a live Foundry connection",
    "L012": "code interpreter apps declare container_policy -- references agents/*.yaml, not part of this repo",
    "L014": "gateway identity holds UserIdentityImpersonation -- needs a live Azure RBAC check (infra/scripts/grant-agent-access.sh)",
    "L021": "x-ms-user-identity charset -- enforced at request time by Principal.user_identity_header(), not config-time",
    "L024": "identity: service + UserEntraToken conflict -- references Foundry-agent connection config, not part of this repo",
    "L031": "container protocol < 2.0.0 -- azure-ai-agentserver-core is a dependency of the T2 "
    "agent's own container image, not this gateway's pyproject.toml (confirmed absent from "
    "it -- the gateway only ever calls the agent through azure-ai-projects, it doesn't run "
    "inside the container)",
}


@dataclass(frozen=True)
class Finding:
    rule: str
    severity: str  # "fail" | "warn"
    message: str


def check_l013_input_required_output_schema(data: dict[str, Any]) -> list[Finding]:
    """D4 (docs/02-decisions.md): input_required: true declares intent,
    output_schema is the mechanism (converted to a Responses API
    text.format param -- src/gateway/upstream/foundry_responses.py
    `_to_text_format`). Checkable from apps.yaml alone now that both are
    real AppConfig fields, unlike when L013 was skipped as "an adapter
    Capability, not YAML-configurable here"."""
    findings = []
    for app in data.get("apps", []) or []:
        if not app.get("input_required"):
            continue
        schema = app.get("output_schema") or {}
        props = schema.get("properties") or {}
        status = props.get("status") or {}
        message = props.get("message") or {}
        problems = []
        if not schema:
            problems.append("output_schema is missing")
        else:
            enum = status.get("enum")
            if not isinstance(enum, list) or sorted(enum) != ["answered", "needs_input"]:
                problems.append("properties.status.enum must be exactly [answered, needs_input]")
            if not status.get("required"):
                problems.append("properties.status.required must be true")
            if message.get("type") not in (None, "string"):
                problems.append("properties.message.type must be string")
            if not message.get("required"):
                problems.append("properties.message.required must be true")
        if problems:
            findings.append(
                Finding(
                    "L013",
                    "fail",
                    f"app {app.get('name')!r}: input_required: true requires a "
                    "conforming D4 outputSchema -- " + "; ".join(problems),
                )
            )
    return findings


def check_l020_service_identity_justification(data: dict[str, Any]) -> list[Finding]:
    findings = []
    for up in data.get("upstreams", []) or []:
        if up.get("identity") == "service" and not str(up.get("justification") or "").strip():
            findings.append(
                Finding(
                    "L020",
                    "fail",
                    f"upstream {up.get('id')!r}: identity: service requires a non-empty justification",
                )
            )
    return findings


def check_l022_no_inline_secrets(data: Any) -> list[Finding]:
    findings: list[Finding] = []

    def walk(node: Any, path: str) -> None:
        if isinstance(node, dict):
            for k, v in node.items():
                if (
                    isinstance(v, str)
                    and _SECRET_KEY_RE.search(str(k))
                    and v
                    and not _VAR_PLACEHOLDER_RE.match(v)
                ):
                    findings.append(
                        Finding(
                            "L022",
                            "fail",
                            f"{path}.{k}: looks like an inline secret, not a ${{VAR}} reference",
                        )
                    )
                walk(v, f"{path}.{k}")
        elif isinstance(node, list):
            for i, v in enumerate(node):
                walk(v, f"{path}[{i}]")

    walk(data, "$")
    return findings


def check_l023_push_allowlist(data: dict[str, Any]) -> list[Finding]:
    any_push = any(
        ((app.get("card") or {}).get("capabilities") or {}).get("pushNotifications")
        for app in data.get("apps", []) or []
    )
    if any_push and not data.get("push_notification_allowlist"):
        return [
            Finding(
                "L023",
                "fail",
                "at least one app declares pushNotifications: true but "
                "push_notification_allowlist is empty -- registration fails "
                "closed by design (GatewayPushConfigStore), so this is a "
                "misconfiguration, not a latent security hole, but it means "
                "push notifications are unusable until fixed",
            )
        ]
    return []


_PREVIEW_TIER_REASON = {
    "t2": "always sends the HostedAgents=V1Preview feature header (FoundryHostedAdapter._PREVIEW)",
    "t3": "runs entirely on prerelease packages -- agent-framework-a2a, "
    "-durabletask, -azurefunctions are all pinned --pre (docs/01 §3)",
}


def check_l030_preview_deny(data: dict[str, Any]) -> list[Finding]:
    findings = []
    for app in data.get("apps", []) or []:
        tier = app.get("tier")
        reason = _PREVIEW_TIER_REASON.get(tier)
        if reason and app.get("preview", "deny") == "deny":
            findings.append(
                Finding(
                    "L030",
                    "fail",
                    f"app {app.get('name')!r}: tier {tier} {reason}, which "
                    "conflicts with preview: deny -- either accept preview "
                    f"or move this app off {tier} (D10: 'preview: deny means "
                    "tier 1 only')",
                )
            )
    return findings


def check_l032_no_assistants_api(src_root: Path) -> list[Finding]:
    findings: list[Finding] = []
    if not src_root.exists():
        return findings
    for py_file in sorted(src_root.rglob("*.py")):
        text = py_file.read_text(encoding="utf-8")
        if _ASSISTANTS_API_RE.search(text):
            findings.append(
                Finding(
                    "L032",
                    "fail",
                    f"{py_file}: references the Assistants API (threads/assistants) -- "
                    "retires 26 Aug 2026 and doesn't support incoming A2A, hard-banned (D6)",
                )
            )
    return findings


def run(config_path: Path, src_root: Path) -> list[Finding]:
    raw_yaml_text = config_path.read_text(encoding="utf-8")
    data = yaml.safe_load(raw_yaml_text) or {}

    findings: list[Finding] = []
    findings += check_l013_input_required_output_schema(data)
    findings += check_l020_service_identity_justification(data)
    findings += check_l022_no_inline_secrets(data)
    findings += check_l023_push_allowlist(data)
    findings += check_l030_preview_deny(data)
    findings += check_l032_no_assistants_api(src_root)
    return findings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="gwlint", description="D6 CI linter -- docs/02-decisions.md D6")
    parser.add_argument("config", type=Path, nargs="?", default=Path("config/apps.yaml"))
    parser.add_argument("--src", type=Path, default=Path("src"))
    args = parser.parse_args(argv)

    if not args.config.exists():
        print(f"gwlint: {args.config} not found", file=sys.stderr)
        return 2

    findings = run(args.config, args.src)

    for rule in sorted(_SKIPPED_RULES):
        print(f"SKIP  {rule}: {_SKIPPED_RULES[rule]}")

    warned = [f for f in findings if f.severity == "warn"]
    failed = [f for f in findings if f.severity == "fail"]
    for f in warned:
        print(f"WARN  {f.rule}: {f.message}")
    for f in failed:
        print(f"FAIL  {f.rule}: {f.message}")

    print(
        f"\n{len(failed)} failed, {len(warned)} warned, "
        f"{len(_SKIPPED_RULES)} skipped (not checkable from this repo alone)"
    )
    # Severity per D6: L0xx safety rules fail the build. Every rule
    # implemented here (L013, L020, L022, L023, L030, L032) is a safety rule.
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
