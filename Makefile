.DEFAULT_GOAL := help
.PHONY: help install cli test test-main test-ci cloud-configure opencode opencode-clear branch-update

PYTHON ?= python3
ARGS ?=
cloud ?=
# Legacy alias; prefer the lowercase cloud variable.
CLOUD ?=
CLI_VENV ?= .venv
CLI_BIN := $(CLI_VENV)/bin
TEST_PYTHON := $(if $(wildcard $(CLI_BIN)/python),$(CLI_BIN)/python,$(PYTHON))
ENV_FILE := $(if $(cloud),.secrets/$(cloud).env,$(if $(CLOUD),.secrets/$(CLOUD).env,))

# Respect NO_COLOR and dumb terminals; headings stay readable without color.
ifeq ($(NO_COLOR),)
ifeq ($(TERM),dumb)
HEADING :=
DIM :=
RESET :=
else
HEADING := \033[1;36m
DIM := \033[2m
RESET := \033[0m
endif
else
HEADING :=
DIM :=
RESET :=
endif

help:
	@printf '\nUsage: make <target> [cloud=aws] [ARGS="..."]\n'
	@printf '  Run "make help" or "mk --help" for details.\n\n'
	@printf '$(HEADING)%s$(RESET)\n' 'DEVELOPMENT'
	@printf '$(DIM)%s$(RESET)\n' '──────────────────────────────'
	@printf '  %-18s %s\n' 'help' 'show available local development targets'
	@printf '  %-18s %s\n' 'install' 'create .venv and install the CLI with all optional integration extras'
	@printf '  %-18s %s\n' 'cli' 'enter the mk CLI shell (ARGS: one-shot command; cloud=aws loads .secrets/aws.env)'
	@printf '\n$(HEADING)%s$(RESET)\n' 'TESTING'
	@printf '$(DIM)%s$(RESET)\n' '──────────────────────────────'
	@printf '  %-18s %s\n' 'test' 'run the main and CI test suites'
	@printf '  %-18s %s\n' 'test-main' 'run the main suite (tests/)'
	@printf '  %-18s %s\n' 'test-ci' 'run the CI suite (ci/tests/)'
	@printf '\n$(HEADING)%s$(RESET)\n' 'ENVIRONMENT'
	@printf '$(DIM)%s$(RESET)\n' '──────────────────────────────'
	@printf '  %-18s %s\n' 'cloud-configure' 'write .secrets/<cloud>.env with blank secrets (cloud=aws, cloud=all)'
	@printf '\n$(HEADING)%s$(RESET)\n' 'OPENCODE'
	@printf '$(DIM)%s$(RESET)\n' '──────────────────────────────'
	@printf '  %-18s %s\n' 'opencode' 'start OpenCode with proxy variables disabled'
	@printf '  %-18s %s\n' 'opencode-clear' 'clear OpenCode V2 repo sessions via API (ARGS: --all/--directory PATH/--dry-run/--yes)'
	@printf '\n$(HEADING)%s$(RESET)\n' 'REPOSITORY'
	@printf '$(DIM)%s$(RESET)\n' '──────────────────────────────'
	@printf '  %-18s %s\n' 'branch-update' 'rebase the current development branch onto origin/main (ARGS: --dry-run)'
	@printf '\nInside the CLI shell run: mk --help\n\n'

install:
	@if [ ! -x "$(CLI_BIN)/python" ]; then \
		printf 'Preparing isolated Python environment in %s...\n' "$(CLI_VENV)"; \
		$(PYTHON) -m venv "$(CLI_VENV)" || { printf 'error: unable to create %s; install python3-venv first\n' "$(CLI_VENV)" >&2; exit 1; }; \
	fi
	@"$(CLI_BIN)/python" -m pip install --disable-pip-version-check 'setuptools>=68' -e '.[aws,aliyun,screenshots]'

cli:
	@if [ -n "$(ENV_FILE)" ] && [ ! -f "$(ENV_FILE)" ]; then \
		printf 'error: %s not found; run make cloud-configure cloud=%s\n' \
			"$(ENV_FILE)" "$(if $(cloud),$(cloud),$(CLOUD))" >&2; \
		exit 1; \
	fi
	@if [ ! -x "$(CLI_BIN)/python" ]; then \
		printf 'Preparing %s with system Python packages...\n' "$(CLI_VENV)"; \
		$(PYTHON) -m venv --system-site-packages "$(CLI_VENV)"; \
	fi

	@if ! "$(CLI_BIN)/python" -c 'import setuptools, click, jsonschema' >/dev/null 2>&1; then \
		printf 'error: CLI dependencies are missing in %s; run make install CLI_VENV="%s"\n' "$(CLI_VENV)" "$(CLI_VENV)" >&2; \
		exit 1; \
	fi
	@$(CLI_BIN)/python -m pip install --quiet --disable-pip-version-check --no-index --no-build-isolation --no-deps -e .
	@if [ -n "$(ARGS)" ]; then \
		exec env $(if $(ENV_FILE),MK_ENV_FILE="$(ENV_FILE)",) "$(CLI_BIN)/mk" $(ARGS); \
	else \
		printf '\nWebsoft9 Marketplace CLI\n\n'; \
		env $(if $(ENV_FILE),MK_ENV_FILE="$(ENV_FILE)",) "$(CLI_BIN)/mk" help || exit $$?; \
		printf '\nShell: run mk <command> --help for details; type exit to leave.\n\n'; \
		if [ -t 0 ] && [ -t 1 ] && [ -z "$$NO_COLOR" ] && [ "$$TERM" != dumb ]; then \
			prompt='\[\033[1;36m\]mk\[\033[0m\] \[\033[2m\]\w [#\!]\[\033[0m\]\n\[\033[1;32m\]\$$\[\033[0m\] '; \
		else \
			prompt='mk \w [#\!]\n\$$ '; \
		fi; \
		exec env $(if $(ENV_FILE),MK_ENV_FILE="$(ENV_FILE)",) PATH="$(CURDIR)/$(CLI_BIN):$$PATH" PS1="$$prompt" PROMPT_COMMAND='history -a' bash --norc --noprofile -i; \
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
