# OpenMagpie Makefile
# Per-concern targets live in make/*.mk

# Path to the private deployments repo when checked out as a sibling (the
# openmagpie-suite layout). Override if it lives elsewhere.
DEPLOY_DIR ?= ../deployments

include make/dev.mk

# Infra commands from the private deployments repo — available only when it's a
# sibling checkout (silently skipped otherwise). CWD-independent ones, e.g.
# `make db-forward` (prod Postgres port-forward for DataGrip/psql).
-include $(DEPLOY_DIR)/make/db.mk

.PHONY: help
help:
	@./scripts/make-help.sh $(MAKEFILE_LIST)
