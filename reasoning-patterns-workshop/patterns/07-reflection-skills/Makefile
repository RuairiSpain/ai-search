include ../common.mk

reset-library:      ## remove acquired skills; keep the preloaded standard-ledger skill
	@find skill_library/active -mindepth 1 -maxdepth 1 ! -name standard-ledger -exec rm -rf {} +
	@rm -rf skill_library/quarantine/* skill_library/pending/* 2>/dev/null || true
	@echo "skill library reset to the preloaded baseline"
.PHONY: reset-library
