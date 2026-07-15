from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
solutions = [
    ROOT / "local_solutions/00_seed_local_solution.py",
    ROOT / "local_solutions/01_add_update_delete_local_solution.py",
    ROOT / "local_solutions/02_search_local_solution.py",
    ROOT / "solutions/08_parse_word_document_solution.py",
]
for solution in solutions:
    print(f"\n### Running {solution.relative_to(ROOT)}")
    subprocess.run([sys.executable, str(solution)], check=True, cwd=ROOT)
