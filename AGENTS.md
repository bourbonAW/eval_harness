# Project Rules

- Use `uv` for all Python environment, dependency, test, and script commands.
- Do not run project Python commands through `pip`, bare `python`, or bare `pytest`; use `uv sync`, `uv run python ...`, and `uv run pytest ...`.
- Keep `pyproject.toml` as the dependency source of truth and commit `uv.lock` whenever `uv sync` updates it.
- `data/questions.jsonl` and `data/dataset.jsonl` are human-curated assets and **must be committed**.
- `data/traces.jsonl` and `data/judge_results.jsonl` are generated at runtime and must **not** be committed.
