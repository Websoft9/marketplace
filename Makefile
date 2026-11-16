.DEFAULT_GOAL := help
.PHONY: help cli test test-main test-ci cloud-configure opencode opencode-clear branch-update

PYTHON ?= python3
ARGS ?=
cloud ?=
# Legacy alias; prefer the lowercase cloud variable.
CLOUD ?=
CLI_VENV ?= .venv
CLI_BIN := $(CLI_VENV)/bin
TEST_PYTHON := $(if $(wildcard $(CLI_BIN)/python),$(CLI_BIN)/python,$(PYTHON))
ENV_FILE := $(if $(cloud),.secrets/$(cloud).env,$(if $(CLOUD),.secrets/$(CLOUD).env,))

help:
	@printf '\nUsage: make <target> [cloud=aws] [ARGS="..."]\n\n'
	@printf '  %-18s %s\n' 'help' 'show available local development targets'
	@printf '  %-18s %s\n' 'cli' 'enter the mk CLI shell (ARGS: one-shot command; cloud=aws loads .secrets/aws.env)'
	@printf '  %-18s %s\n' 'test' 'run the main and CI test suites (test-main / test-ci for one suite)'
	@printf '  %-18s %s\n' 'cloud-configure' 'write .secrets/<cloud>.env with blank secrets (cloud=aws, cloud=all)'
	@printf '  %-18s %s\n' 'opencode' 'start OpenCode with proxy variables disabled'
	@printf '  %-18s %s\n' 'opencode-clear' 'clear OpenCode V2 repo sessions via API (ARGS: --all/--directory PATH/--dry-run/--yes)'
	@printf '  %-18s %s\n' 'branch-update' 'rebase the current development branch onto origin/main (ARGS: --dry-run)'
	@printf '\nInside the CLI shell run: mk --help\n\n'

cli:
	@if [ -n "$(ENV_FILE)" ] && [ ! -f "$(ENV_FILE)" ]; then \
		printf 'error: %s not found; run make cloud-configure cloud=%s\n' \
			"$(ENV_FILE)" "$(if $(cloud),$(cloud),$(CLOUD))" >&2; \
		exit 1; \
	fi
	@if [ ! -x "$(CLI_BIN)/python" ] || ! "$(CLI_BIN)/python" -c 'import setuptools, click, jsonschema' >/dev/null 2>&1; then \
		printf 'Preparing %s with system Python packages...\n' "$(CLI_VENV)"; \
		rm -rf "$(CLI_VENV)"; \
		$(PYTHON) -m venv --system-site-packages "$(CLI_VENV)"; \
	fi

	@if ! "$(CLI_BIN)/python" -c 'import setuptools, click, jsonschema' >/dev/null 2>&1; then \
		printf 'error: setuptools, click and jsonschema are required in %s; install them first\n' "$(PYTHON)" >&2; \
		exit 1; \
	fi
	@$(CLI_BIN)/python -m pip install --quiet --disable-pip-version-check --no-index --no-build-isolation --no-deps -e .
	@if [ -n "$(ARGS)" ]; then \
		exec env $(if $(ENV_FILE),MK_ENV_FILE="$(ENV_FILE)",) "$(CLI_BIN)/mk" $(ARGS); \
	else \
		printf 'mk CLI ready. Run "mk --help"; type "exit" to leave.\n'; \
		exec env $(if $(ENV_FILE),MK_ENV_FILE="$(ENV_FILE)",) PATH="$(CURDIR)/$(CLI_BIN):$$PATH" PS1="(mk) $$PS1" bash --norc --noprofile -i; \
	fi

test: test-main test-ci

test-main:
	@$(TEST_PYTHON) -m unittest discover -s tests -q

test-ci:
	@$(TEST_PYTHON) -m unittest discover -s ci/tests -q

cloud-configure:
	@$(PYTHON) ci/cloud_configure.py $(if $(cloud),--cloud "$(cloud)",$(if $(CLOUD),--cloud "$(CLOUD)",)) $(ARGS)

opencode:
	@command -v opencode >/dev/null 2>&1 || { echo "error: opencode is not installed" >&2; exit 1; }
	@unset HTTP_PROXY HTTPS_PROXY http_proxy https_proxy ALL_PROXY all_proxy; \
		NO_PROXY="*" no_proxy="*" opencode

opencode-clear:
	@$(PYTHON) ci/scripts/opencode_clear.py $(ARGS)

branch-update:
	@bash ci/scripts/branch_update.sh $(ARGS)
