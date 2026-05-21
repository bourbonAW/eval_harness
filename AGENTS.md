# AGENTS.md

## Superpowers System

<EXTREMELY_IMPORTANT>
You have superpowers. Superpowers teach you new skills and capabilities. RIGHT NOW run: `~/.codex/superpowers/.codex/superpowers-codex bootstrap` and follow the instructions it returns.
</EXTREMELY_IMPORTANT>

If the bootstrap executable is unavailable in the current environment, report that briefly and continue by using the relevant locally available skill files.

## Project Identity

This repository is the local LLM eval flywheel for the `intelligent_customer` RAG customer-service bot.

The working loop is:

1. Maintain test questions in `data/questions.jsonl`.
2. Collect workflow traces into `data/traces.jsonl`.
3. Human-label traces into `data/dataset.jsonl`.
4. Run LLM judges into `data/judge_results.jsonl`.
5. Use Judge UI metrics to improve few-shot judges and grow the dataset.

Current product surface:

- `/collect`: question CRUD plus trace collection.
- `/`: human annotation UI.
- `/judge`: LLM judge UI plus validation metrics.

## Project Rules

- Use `uv` for all Python environment, dependency, test, and script commands.
- Do not run project Python commands through `pip`, bare `python`, or bare `pytest`; use `uv sync`, `uv run python ...`, and `uv run pytest ...`.
- Keep `pyproject.toml` as the dependency source of truth and commit `uv.lock` whenever `uv sync` updates it.
- Use TDD for new features and bugfixes: write the failing test first, then implement.
- Prefer `rg` / `rg --files` for repository search.
- Do not revert unrelated local edits. The worktree may contain user changes.

## Data Ownership

- `data/questions.jsonl` is human-curated and must be committed when intentionally changed.
- `data/dataset.jsonl` is human-curated gold data and must be committed when intentionally changed.
- `data/questions.xlsx` is a curated source file and should be kept with question-set changes.
- `data/traces.jsonl` is generated at runtime and must not be committed.
- `data/judge_results.jsonl` is generated at runtime and must not be committed.
- `data/judge_results_bak.jsonl` is generated/backup data and must not be committed.

## Common Commands

```bash
# Install/update dependencies
uv sync

# Start the unified web UI
uv run python -m eval.web --port 5000 --annotator <name>

# Collect traces from the question set
uv run python -m eval.collectors.workflow_collector data/questions.jsonl data/traces.jsonl

# Import questions from Excel
uv run python -m eval.importers.excel_importer data/questions.xlsx data/questions.jsonl

# Unit tests
uv run pytest tests/ -m "not integration" -q

# Integration tests with real external APIs
uv run pytest tests/ -m integration -v
```

## Configuration

Copy `.env.example` to `.env`.

Trace collection requires:

- `WORKFLOW_API_BASE_URL`
- `WORKFLOW_API_KEY`
- `WORKFLOW_SESSION_ID`
- `WORKFLOW_CHANNEL_ID`

LLM judge execution requires:

- `OPENAI_API_KEY` and optional `OPENAI_BASE_URL` for OpenAI-compatible models.
- `ANTHROPIC_API_KEY` for Claude models.

## Design Constraints

- Labels are binary for evaluation: `pass` / `fail`; `skip` is allowed for annotation flow control.
- `dataset.jsonl` and `judge_results.jsonl` are append-only; readers use last-wins by `trace_id`.
- The collector overwrites `traces.jsonl` per run, but keeps the old file if every question fails.
- Question CRUD in `/collect` only edits `question` and `expected_answer`; other question metadata is preserved or defaulted.
- Judge few-shot examples are the main tuning surface. Prefer improving examples over broad prompt rewrites.

## Known Workflow Issues

These are upstream bot/workflow issues, not eval-tool blockers:

- `q_002` / `q_006`: risk-control false refusals.
- `q_004` / `q_007`: retrieval miss, `ref_num=0`, empty `doc_context`.

Keep these visible when interpreting judge results, but do not fix them unless the task is explicitly Stage 3 Fix & Grow.
