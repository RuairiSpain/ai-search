"""Environment + variant configuration.

Variants are the workshop's A/B mechanism: `variants/baseline.yaml` vs
`variants/cheap-model.yaml` etc. The eval runner tags each run with the variant
name so the Experiments table shows comparable, reproducible rows.
"""
from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]


@lru_cache
def shared_env() -> dict[str, str]:
    """Load .shared-env written by infra/shared/deploy.sh (env vars win)."""
    env: dict[str, str] = {}
    f = REPO_ROOT / ".shared-env"
    if f.exists():
        for line in f.read_text().splitlines():
            if "=" in line and not line.startswith("#"):
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip()
    env.update({k: v for k, v in os.environ.items() if k in env or k.startswith(("FOUNDRY_", "MCP_", "SEARCH_", "APPINSIGHTS_"))})
    missing = [k for k in ("FOUNDRY_PROJECT_ENDPOINT",) if not env.get(k)]
    if missing:
        raise RuntimeError(
            f"Missing {missing}. Run infra/shared/deploy.sh first (see root README)."
        )
    return env


def load_variant(pattern_dir: Path, name: str | None = None) -> dict:
    """Load variants/<name>.yaml for a pattern. Precedence: arg > $VARIANT > baseline."""
    name = name or os.environ.get("VARIANT") or "baseline"
    path = pattern_dir / "variants" / f"{name}.yaml"
    if not path.exists():
        available = sorted(p.stem for p in (pattern_dir / "variants").glob("*.yaml"))
        raise FileNotFoundError(f"No variant '{name}'. Available: {available}")
    cfg = yaml.safe_load(path.read_text())
    cfg["_variant_name"] = name
    cfg["_pattern"] = pattern_dir.name
    return cfg


def load_budgets(pattern_dir: Path) -> dict:
    return yaml.safe_load((pattern_dir / "budgets.yaml").read_text())
