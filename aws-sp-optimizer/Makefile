.PHONY: install test lint type check clean

install:
	python -m pip install -e ".[test,dev]"

test:
	pytest tests/ -v || test $$? -eq 5

test-cov:
	pytest tests/ --cov=scripts --cov-report=term-missing || test $$? -eq 5

lint:
	ruff check scripts/ tests/

lint-fix:
	ruff check --fix scripts/ tests/
	ruff format scripts/ tests/

type:
	mypy scripts/

check: lint type test

clean:
	find . -type d -name __pycache__ -exec rm -rf {} +
	find . -type d -name .pytest_cache -exec rm -rf {} +
	find . -type d -name .mypy_cache -exec rm -rf {} +
	find . -type f -name "*.pyc" -delete
