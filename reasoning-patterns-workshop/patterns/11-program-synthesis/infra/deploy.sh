#!/usr/bin/env bash
source "$(cd "$(dirname "$0")/../../.." && pwd)/scripts/lib.sh"
init_pattern_infra "$0"
install_shared_reqs -r requirements.txt

echo "==> Sanity: acceptance tests fail against the LEGACY module (they must)"
python3 - << 'PY'
import shutil, subprocess, sys, tempfile
from pathlib import Path
ws = Path(tempfile.mkdtemp())
shutil.copy("fixture/tests/test_migrated.py", ws / "test_migrated.py")
shutil.copy("fixture/legacy_config/parser.py", ws / "config.py")  # legacy AS the module
r = subprocess.run([sys.executable, "-m", "pytest", "-q", "test_migrated.py"],
                   cwd=ws, capture_output=True, text=True, timeout=60)
assert r.returncode != 0, "legacy already passes?! tests are too weak"
print("  legacy fails the suite as expected — the migration has real work to do")
PY
echo "✅ pattern 11 ready. Try: make run"
