.PHONY: help install lint test clean lint-all test-all

help:
	@echo "Pleiades Monorepo Development Commands:"
	@echo "  make install     - Install all dependencies"
	@echo "  make lint        - Run linters on all modules"
	@echo "  make test        - Run unit tests on all modules"
	@echo "  make clean       - Clean all artifacts"

install:
	$(MAKE) -C atlas install
	$(MAKE) -C maia install
	$(MAKE) -C alkyone install

lint:
	$(MAKE) -C atlas lint
	$(MAKE) -C maia lint
	$(MAKE) -C alkyone lint

test:
	$(MAKE) -C atlas test
	$(MAKE) -C maia test
	$(MAKE) -C alkyone test

clean:
	$(MAKE) -C atlas clean
	$(MAKE) -C maia clean
	$(MAKE) -C alkyone clean
