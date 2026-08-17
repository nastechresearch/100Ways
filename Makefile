# 100Ways developer Makefile
#
# Common dev tasks.  All targets are local; nothing pushes or publishes.

PYTHON ?= python3
VENV   ?= .venv

.PHONY: help install install-dev test lint format coverage update forkcheck weekly clean

help:
	@echo "100Ways targets:"
	@echo "  install     install the package into the active Python"
	@echo "  install-dev install with dev extras (pytest, ruff, build)"
	@echo "  test        run the full test suite (no network)"
	@echo "  lint        ruff check + format check"
	@echo "  format      ruff format (apply)"
	@echo "  coverage    pytest with coverage; fails below the 70% floor"
	@echo "  update      run the 19-stage update pipeline against a local fixture"
	@echo "  forkcheck   diff a candidate against a real nastech-agent fork"
	@echo "  weekly      build a plan-only weekly report"
	@echo "  clean       remove build / cache artifacts"

install:
	$(PYTHON) -m pip install -e .

install-dev:
	$(PYTHON) -m pip install -e ".[dev]"

test:
	$(PYTHON) -m pytest tests/ -q

lint:
	$(PYTHON) -m ruff check hundredways tests
	$(PYTHON) -m ruff format --check hundredways tests

format:
	$(PYTHON) -m ruff format hundredways tests
	$(PYTHON) -m ruff check --fix hundredways tests

coverage:
	$(PYTHON) -m pip install --quiet pytest-cov
	$(PYTHON) -m pytest tests/ --cov=hundredways --cov-report=term-missing --cov-fail-under=70

update:
	$(PYTHON) -m hundredways.cli --repo $(REPO) update \
	    --hermes-url $(HERMES) \
	    --updates-dir updates \
	    --emit-outputs update-outputs.json

forkcheck:
	$(PYTHON) -m hundredways.cli --repo $(REPO) forkcheck \
	    --candidate $(CANDIDATE) \
	    --upstream $(HERMES)

weekly:
	$(PYTHON) -m hundredways.cli --repo $(REPO) weekly-full-sync \
	    --state-dir $(STATE_DIR) \
	    --upstream $(HERMES)

clean:
	rm -rf build/ dist/ .pytest_cache/ .ruff_cache/ *.egg-info
	find . -type d -name __pycache__ -exec rm -rf {} +
