# OpenMagpie Makefile
# Per-concern targets live in make/*.mk

# Private deployments repo as a sibling checkout (the openmagpie-suite layout).
# DEPLOY_DIR makes its build/deploy paths resolve from here; APP_REPO ("." = this
# repo) is where its web targets find the Next apps. Override if either differs.
DEPLOY_DIR ?= ../deployments
APP_REPO ?= .

include make/local.mk

# All infra/prod commands from the private deployments repo become available
# here when it's a sibling checkout — db-forward, render, release, deploy,
# web-deploy, logs-*, etc. The leading `-` silently skips them when the repo
# isn't present, so the public repo still works standalone.
-include $(DEPLOY_DIR)/make/*.mk

.PHONY: help
help:
	@./scripts/make-help.sh $(MAKEFILE_LIST)
