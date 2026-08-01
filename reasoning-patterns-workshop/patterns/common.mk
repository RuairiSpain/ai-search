# patterns/common.mk — included by every pattern's Makefile.
#
# `deploy run eval eval-smoke cost destroy` were byte-identical across all 11
# pattern Makefiles (see project review, item 16). Defining them once here
# means a change to, say, the cost-report invocation is one edit instead of
# eleven. A pattern Makefile includes this file and then adds ONLY the
# targets that make it different (run-interactive, viz, optimize, ...).
#
# Included, not copied: `make -C patterns/03-multi-agent-routing help` still
# works exactly as if every target were written out locally.

VARIANT ?= baseline
export VARIANT
export PYTHONPATH := ../../common

.PHONY: deploy run eval eval-smoke cost destroy help

deploy:             ## Provision + register agents (shared infra must exist)
	./infra/deploy.sh
run:                ## Run the pattern once on data/sample_input.json
	python3 src/workflow.py
eval:               ## Full evaluation for $(VARIANT); results land in Experiments
	python3 -m reasoning_common.eval_runner --pattern-dir . --variant $(VARIANT)
eval-smoke:         ## First 2 rows only — fast sanity check
	python3 -m reasoning_common.eval_runner --pattern-dir . --variant $(VARIANT) --limit 2
cost:               ## Cost table across all runs so far
	python3 -c "import sys,pathlib;sys.path.insert(0,'../../common');from reasoning_common.costs import report;print(report(pathlib.Path('runs')))"
destroy:            ## Remove this pattern's resources
	./infra/destroy.sh
help:               ## List available targets in this pattern
	@grep -hE '^[a-zA-Z_-]+:.*##' $(MAKEFILE_LIST) | sort -t: -u -k1,1 | awk 'BEGIN{FS=":.*##"}{printf "  %-16s %s\n", $$1, $$2}'
