.PHONY: help install lint lint-fix test test-unit test-int clean pre-commit-install pre-commit

help:
	@echo "Pleiades Monorepo Development Commands:"
	@echo "  make install            - Install all dependencies"
	@echo "  make pre-commit-install - Install pre-commit hook scripts"
	@echo "  make pre-commit         - Run pre-commit on all files (commit stage)"
	@echo "  make lint               - Run linters (readonly) on all modules"
	@echo "  make lint-fix           - Auto-fix lint issues (ruff check --fix + format)"
	@echo "  make test               - Run ALL tests (unit + integration)"
	@echo "  make test-unit          - Run unit tests only (Atlas + Maia)"
	@echo "  make test-int           - Run integration tests (Alkyone)"
	@echo "  make clean              - Clean all artifacts"

install:
	$(MAKE) -C atlas install
	$(MAKE) -C maia install
	$(MAKE) -C alkyone install
	$(MAKE) -C mcp install

lint-local:
	$(MAKE) -C atlas lint-local
	$(MAKE) -C maia lint-local
	$(MAKE) -C alkyone lint-local
	$(MAKE) -C mcp lint-local

lint:
	$(MAKE) -C atlas lint
	$(MAKE) -C maia lint
	$(MAKE) -C alkyone lint
	$(MAKE) -C mcp lint

pre-commit-install:
	pre-commit install --hook-type pre-commit --hook-type pre-push

pre-commit:
	pre-commit run --all-files

lint-fix:
	$(MAKE) -C atlas lint-local
	$(MAKE) -C maia lint-local
	$(MAKE) -C alkyone lint-local
	$(MAKE) -C mcp lint-local

test: test-unit test-int

test-unit:
	$(MAKE) -C atlas test-unit
	$(MAKE) -C maia test-unit
	$(MAKE) -C mcp test

test-int:
	$(MAKE) -C alkyone test-int

clean:
	$(MAKE) -C atlas clean
	$(MAKE) -C maia clean
	$(MAKE) -C alkyone clean
	$(MAKE) -C mcp clean
