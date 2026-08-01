#!/usr/bin/env python3
"""Audit every requirements.txt in the repo against the latest PyPI release.

Usage:
    python3 scripts/check_package_versions.py [--json]

For each pinned package: pinned vs latest version, and — when the package
publishes one in its metadata — a direct link to its changelog/release notes,
falling back to the PyPI release history.

Run this BEFORE every workshop delivery: the volatile-surface warning in the
root README is not decoration, and this script is how you act on it.
No third-party deps (stdlib only) so it runs before `pip install`.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
CHANGELOG_KEYS = re.compile(r"changelog|changes|release ?notes|history|news", re.I)


def parse_requirements(path: Path) -> list[tuple[str, str, bool]]:
    """Returns (package, pinned_version, is_deliberate_hold).

    A trailing comment containing `HOLD:` marks a pin that must NOT be bumped
    (dependency conflict, untested major, etc). The audit reports those
    separately so nobody "helpfully" upgrades the build into a broken state.
    """
    pins = []
    for raw in path.read_text().splitlines():
        body, _, comment = raw.partition("#")
        line = body.strip()
        if not line:
            continue
        m = re.match(r"^([A-Za-z0-9_.\-]+)\s*==\s*([A-Za-z0-9_.\-]+)$", line)
        if m:
            pins.append((m.group(1), m.group(2), "HOLD:" in comment))
        else:
            print(f"  ! {path.relative_to(REPO_ROOT)}: unpinned or complex spec kept as-is: '{line}'")
    return pins


def fetch_pypi(pkg: str) -> dict | None:
    url = f"https://pypi.org/pypi/{pkg}/json"
    try:
        with urllib.request.urlopen(url, timeout=15) as r:
            return json.load(r)
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as e:
        print(f"  ! could not reach PyPI for {pkg}: {e}")
        return None


def _ver_key(v: str):
    """Sortable version key; pre-releases (b/rc/a/dev) rank below finals."""
    parts = re.split(r"[.\-]", v)
    key = []
    for p in parts:
        m = re.match(r"^(\d+)([a-zA-Z].*)?$", p)
        if m:
            key.append((int(m.group(1)), m.group(2) or "~"))  # '~' sorts after letters
        else:
            key.append((-1, p))
    return key


def latest_version(data: dict, include_prerelease: bool) -> str:
    releases = [v for v, files in data.get("releases", {}).items()
                if files and not any(f.get("yanked") for f in files)]
    if not include_prerelease:
        finals = [v for v in releases if not re.search(r"(a|b|rc|dev)\d*$", v)]
        releases = finals or releases
    return max(releases, key=_ver_key) if releases else data["info"]["version"]


def changelog_link(data: dict) -> str:
    info = data.get("info", {})
    urls = info.get("project_urls") or {}
    for key, url in urls.items():
        if CHANGELOG_KEYS.search(key or ""):
            return url
    # Common convention: GitHub repos -> releases page
    for key, url in urls.items():
        if url and "github.com" in url:
            return url.rstrip("/") + "/releases"
    return f"https://pypi.org/project/{info.get('name', '')}/#history"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    args = ap.parse_args()

    req_files = sorted(REPO_ROOT.rglob("requirements.txt"))
    report, outdated, held = [], 0, 0
    for rf in req_files:
        if not args.json:
            print(f"\n== {rf.relative_to(REPO_ROOT)} ==")
        for pkg, pinned, is_hold in parse_requirements(rf):
            data = fetch_pypi(pkg)
            if not data:
                continue
            pre = bool(re.search(r"(a|b|rc|dev)\d*$", pinned))  # match pin's channel
            latest = latest_version(data, include_prerelease=pre)
            exists = pinned in data.get("releases", {})
            row = {"file": str(rf.relative_to(REPO_ROOT)), "package": pkg,
                   "pinned": pinned, "latest": latest,
                   "pin_exists_on_pypi": exists, "deliberate_hold": is_hold,
                   "changelog": changelog_link(data)}
            report.append(row)
            if not args.json:
                if not exists:
                    print(f"  ✗ {pkg}=={pinned} DOES NOT EXIST on PyPI (latest: {latest})")
                    print(f"      changelog: {row['changelog']}")
                    outdated += 1
                elif _ver_key(latest) > _ver_key(pinned) and is_hold:
                    print(f"  ⏸ {pkg}: pinned {pinned}, latest {latest} — DELIBERATE HOLD, do not bump")
                    held += 1
                elif _ver_key(latest) > _ver_key(pinned):
                    print(f"  ↑ {pkg}: pinned {pinned}, latest {latest}")
                    print(f"      changelog: {row['changelog']}")
                    outdated += 1
                else:
                    print(f"  ✓ {pkg}=={pinned} (latest)")
    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print(f"\n{outdated} package(s) need attention." if outdated
              else "\nAll pins current (excluding deliberate holds).")
        if held:
            print(f"{held} pin(s) are on a documented HOLD — bumping them breaks the build.")
        print("Before bumping: read the changelog, bump, then run "
              "`python3 scripts/verify_offline.py` and a pattern's `make eval-smoke`. "
              "foundry_client.py and maf_workflow.py absorb breaking SDK changes.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
