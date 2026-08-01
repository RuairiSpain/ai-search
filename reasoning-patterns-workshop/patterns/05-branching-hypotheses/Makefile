include ../common.mk

run-interactive:    ## steer prune boundaries (VARIANT=steerable)
	VARIANT=steerable python3 src/workflow.py --interactive
viz:                ## render the REAL MAF-built graph (not the ARCHITECTURE.md drawing)
	python3 -c "import sys;sys.path.insert(0,'src');from reasoning_common.config import load_variant;from reasoning_common.costs import CostLedger;from maf_workflow import viz;print(viz(load_variant(__import__('pathlib').Path('.'))))"
.PHONY: run-interactive viz
