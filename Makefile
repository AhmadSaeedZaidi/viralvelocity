.PHONY: help install lint test test-unit test-int clean

help:
	@echo "Pleiades Monorepo Development Commands:"
	@echo "  make install     - Install all dependencies"
	@echo "  make lint        - Run linters on all modules"
	@echo "  make test        - Run ALL tests (unit + integration)"
	@echo "  make test-unit   - Run unit tests only (Atlas + Maia)"
	@echo "  make test-int    - Run integration tests (Alkyone)"
	@echo "  make clean       - Clean all artifacts"

install:
	$(MAKE) -C atlas install
	$(MAKE) -C maia install
	$(MAKE) -C alkyone install

lint:
	$(MAKE) -C atlas lint
	$(MAKE) -C maia lint
	$(MAKE) -C alkyone lint

test: test-unit test-int

test-unit:
	$(MAKE) -C atlas test-unit
	$(MAKE) -C maia test-unit

test-int:
	$(MAKE) -C alkyone test-int

clean:
	$(MAKE) -C atlas clean
	$(MAKE) -C maia clean
	$(MAKE) -C alkyone clean
