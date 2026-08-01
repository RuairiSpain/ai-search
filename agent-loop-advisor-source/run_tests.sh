#!/usr/bin/env bash
# Full check: unit tests, then the golden-set measurement.
set -e
cd "$(dirname "$0")"
echo "== unit tests =="
python3 -m unittest discover -s tests -v 2>&1 | tail -5
echo
echo "== catalogue integrity =="
PYTHONPATH=src python3 -m patcomp.cli --catalogue catalogue catalogue | tail -2
echo
echo "== golden set =="
PYTHONPATH=src python3 -m patcomp.cli --catalogue catalogue goldenset | tail -18
