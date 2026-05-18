# Project Rules

- Use `uv` for all Python environment, dependency, test, and script commands.
- Do not run project Python commands through `pip`, bare `python`, or bare `pytest`; use `uv sync`, `uv run python ...`, and `uv run pytest ...`.
- Keep `pyproject.toml` as the dependency source of truth and commit `uv.lock` whenever `uv sync` updates it.
- Generated eval data under `data/*.jsonl` is runtime output and should not be committed.
