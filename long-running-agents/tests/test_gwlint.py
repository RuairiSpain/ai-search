"""Offline tests for gwlint (docs/02-decisions.md D6). Each implemented
rule gets a positive (clean config, no finding) and negative (violation
detected) case, plus a dogfooding check that the real example config
passes -- if it doesn't, either the example config or the rule is wrong,
and both are worth knowing immediately.
"""
from __future__ import annotations

from pathlib import Path

from gateway import gwlint

REPO_ROOT = Path(__file__).resolve().parents[1]

_BASE_CONFIG = """
auth:
  tenant_id: ${GATEWAY_TENANT_ID}
  audience: api://a2a-gateway
apps:
  - name: ticket-triage
    tier: t2
    upstream: triage-hosted
    preview: allow
upstreams:
  - id: triage-hosted
    tier: t2
    project_endpoint: ${FOUNDRY_PROJECT_ENDPOINT}
    agent_name: triage-agent
    identity: per_user
"""


def _write(tmp_path: Path, text: str) -> Path:
    p = tmp_path / "apps.yaml"
    p.write_text(text)
    return p


def test_real_example_config_has_no_failures():
    config_path = REPO_ROOT / "config" / "apps.example.yaml"
    findings = gwlint.run(config_path, REPO_ROOT / "src")
    failed = [f for f in findings if f.severity == "fail"]
    assert failed == [], failed


def test_clean_config_has_no_findings(tmp_path):
    findings = gwlint.run(_write(tmp_path, _BASE_CONFIG), tmp_path / "empty-src")
    assert findings == []


def test_l020_service_identity_without_justification_fails(tmp_path):
    config = _BASE_CONFIG.replace("identity: per_user", "identity: service")
    findings = gwlint.run(_write(tmp_path, config), tmp_path / "empty-src")
    assert any(f.rule == "L020" and f.severity == "fail" for f in findings)


def test_l020_service_identity_with_justification_passes(tmp_path):
    config = _BASE_CONFIG.replace(
        "identity: per_user",
        "identity: service\n    justification: batch job, no end user",
    )
    findings = gwlint.run(_write(tmp_path, config), tmp_path / "empty-src")
    assert not any(f.rule == "L020" for f in findings)


def test_l022_inline_secret_detected(tmp_path):
    config = _BASE_CONFIG + "  # leaked below\napi_key: sk-live-abc123thisisnotavar\n"
    findings = gwlint.run(_write(tmp_path, config), tmp_path / "empty-src")
    assert any(f.rule == "L022" and f.severity == "fail" for f in findings)


def test_l022_var_placeholder_not_flagged(tmp_path):
    config = _BASE_CONFIG + "api_key: ${SOME_API_KEY}\n"
    findings = gwlint.run(_write(tmp_path, config), tmp_path / "empty-src")
    assert not any(f.rule == "L022" for f in findings)


def test_l023_push_enabled_without_allowlist_fails(tmp_path):
    config = _BASE_CONFIG.replace(
        "preview: allow",
        "preview: allow\n    card:\n      capabilities: { pushNotifications: true }",
    )
    findings = gwlint.run(_write(tmp_path, config), tmp_path / "empty-src")
    assert any(f.rule == "L023" and f.severity == "fail" for f in findings)


def test_l023_push_enabled_with_allowlist_passes(tmp_path):
    config = (
        "push_notification_allowlist:\n  - push.example.com\n"
        + _BASE_CONFIG.replace(
            "preview: allow",
            "preview: allow\n    card:\n      capabilities: { pushNotifications: true }",
        )
    )
    findings = gwlint.run(_write(tmp_path, config), tmp_path / "empty-src")
    assert not any(f.rule == "L023" for f in findings)


def test_l030_t2_preview_deny_fails(tmp_path):
    config = _BASE_CONFIG.replace("preview: allow", "preview: deny")
    findings = gwlint.run(_write(tmp_path, config), tmp_path / "empty-src")
    assert any(f.rule == "L030" and f.severity == "fail" for f in findings)


def test_l030_t2_default_preview_also_fails(tmp_path):
    # preview defaults to "deny" when omitted entirely -- same violation.
    config = _BASE_CONFIG.replace("    preview: allow\n", "")
    findings = gwlint.run(_write(tmp_path, config), tmp_path / "empty-src")
    assert any(f.rule == "L030" and f.severity == "fail" for f in findings)


def test_l030_t2_preview_allow_passes(tmp_path):
    findings = gwlint.run(_write(tmp_path, _BASE_CONFIG), tmp_path / "empty-src")
    assert not any(f.rule == "L030" for f in findings)


def test_l032_assistants_api_reference_detected(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    (src / "bad.py").write_text("resp = client.beta.threads.create()\n")
    findings = gwlint.run(_write(tmp_path, _BASE_CONFIG), src)
    assert any(f.rule == "L032" and f.severity == "fail" for f in findings)


def test_l032_clean_src_tree_passes(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    (src / "fine.py").write_text("resp = client.responses.create()\n")
    findings = gwlint.run(_write(tmp_path, _BASE_CONFIG), src)
    assert not any(f.rule == "L032" for f in findings)


def test_main_returns_nonzero_on_failure(tmp_path, capsys):
    config = _BASE_CONFIG.replace("identity: per_user", "identity: service")
    config_path = _write(tmp_path, config)
    exit_code = gwlint.main([str(config_path), "--src", str(tmp_path / "empty-src")])
    assert exit_code == 1
    out = capsys.readouterr().out
    assert "FAIL  L020" in out


def test_main_returns_zero_on_clean_config(tmp_path):
    config_path = _write(tmp_path, _BASE_CONFIG)
    exit_code = gwlint.main([str(config_path), "--src", str(tmp_path / "empty-src")])
    assert exit_code == 0


def test_main_returns_2_for_missing_config(tmp_path):
    exit_code = gwlint.main([str(tmp_path / "does-not-exist.yaml")])
    assert exit_code == 2
