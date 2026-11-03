.PHONY: install test lint chat app quality evals examples

install:
	uv sync --all-extras

test:
	uv run pytest

lint:
	uv run ruff check src tests evals examples
	uv run ruff format --check src tests evals examples

chat:
	uv run emporio-chat

app:
	uv run streamlit run src/emporio/interfaces/app.py

quality:
	uv run python -m emporio.core.quality

evals:
	uv run python -m evals.run

examples:
	uv run python -m examples.generate
