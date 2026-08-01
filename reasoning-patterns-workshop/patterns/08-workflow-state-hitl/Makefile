include ../common.mk

deploy-durable:     ## provision + publish the Durable Functions host
	./infra/deploy-durable.sh
run-interactive:    ## real human at the exception gate (VARIANT=steerable)
	VARIANT=steerable python3 src/workflow.py --interactive
.PHONY: deploy-durable run-interactive
