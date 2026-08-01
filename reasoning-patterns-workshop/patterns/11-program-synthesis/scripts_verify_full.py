"""The weak-tests punchline: take the newest generated config.py from runs/
and execute the FULL acceptance suite over it. Whatever fails here is exactly
what a weak suite would have shipped to production."""
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

runs = sorted(Path("runs").glob("*-config.py"))
if not runs:
    sys.exit("No generated config.py in runs/ — run `make run VARIANT=weak-tests` first.")
latest = runs[-1]
ws = Path(tempfile.mkdtemp())
shutil.copy("fixture/tests/test_migrated.py", ws / "test_migrated.py")
shutil.copy(latest, ws / "config.py")
print(f"Full suite over {latest.name}:")
r = subprocess.run([sys.executable, "-m", "pytest", "-q", "--tb=line", "test_migrated.py"],
                   cwd=ws, capture_output=True, text=True, timeout=60)
print(r.stdout + r.stderr)
sys.exit(0)
