VERSION ?= 0.0.15
PYTHON ?= python3

LICENSE_MARKER := Licensed under the Apache License, Version 2.0

.PHONY: test splice-dars build-examples test-examples dev dev-deps lint license-check clean clean-pyc clean-build clean-test build release

init:
	$(PYTHON) setup.py install

dev-init:
	$(PYTHON) -m pip install . --group dev

test:
	pytest

splice-dars:
	scripts/fetch-splice-dars.sh

EXAMPLE_DIRS := crowd_loans splice-api-token-burn-mint-v1 splice-util-batched-markers splice-wallet

build-examples: splice-dars
	set -e; for ex in $(EXAMPLE_DIRS); do \
		forge dar examples/$$ex -o examples/$$ex/$$ex-ci.dar; \
	done

SCRIPT_TEST_DIRS := splice-util-batched-markers-test splice-util-batched-markers-original-test

test-examples: build-examples
	cd examples/splice-util-batched-markers-original && dpm build
	set -e; for ex in $(SCRIPT_TEST_DIRS); do \
		(cd examples/$$ex && dpm test); \
	done

lint: mypy black flake8 isort

license-check:
	@missing=$$(git ls-files --cached --others --exclude-standard -- setup.py 'forge/*.py' 'tests/*.py' \
		| grep -v '^forge/version.py$$' \
		| while read -r file; do head -n 20 "$$file" | grep -q "$(LICENSE_MARKER)" || echo "$$file"; done); \
	if [ -n "$$missing" ]; then \
		echo "Missing Apache-2.0 license header:"; echo "$$missing"; exit 1; \
	fi; \
	echo "All Python files have the Apache-2.0 license header."

mypy:
	mypy -p forge

black:
	black forge/ tests/ setup.py

flake8: black
	flake8 forge/ tests/

isort: black
	isort forge/ tests/ setup.py

build: clean
	$(PYTHON) -m build
	ls -la dist/

dev:
	SETUPTOOLS_SCM_PRETEND_VERSION=$(VERSION) $(MAKE) build

dev-release:
	SETUPTOOLS_SCM_PRETEND_VERSION=$(VERSION) \
	$(PYTHON) -m pip install --no-deps --force-reinstall -e .

release: build
	twine check dist/*
	#twine upload --repository-url <your-private-index> dist/*

clean: clean-build clean-pyc clean-test

clean-build:
	@echo Cleaning python build files...
	@rm -fr build/
	@rm -fr dist/
	@rm -fr *.egg-info
	@rm -f forge/version.py forge/damlforge_git_commithash.txt
	@rm -f *.spec

clean-pyc:
	@echo Cleaning python files...
	@find . -name '*.pyc' -exec rm -f {} +
	@find . -name '*.pyo' -exec rm -f {} +
	@find . -name '*~' -exec rm -f {} +
	@find . -name '__pycache__' -exec rmdir {} +

clean-test:
	@echo Cleaning test files...
	@find . -name 'htmlcov' -exec rm -rf {} +
	@rm -fr coverage.xml
	@rm -fr .coverage
	@rm -fr .eggs/
	@rm -fr .hypothesis/
	@rm -fr .pytest_cache/
	@rm -rf .tox/
	@rm -fr .mypy_cache/
