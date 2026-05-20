# Unified Web Server Design

**Date:** 2026-05-20  
**Status:** Approved

## Problem

Two separate Flask servers (`eval/annotate_web.py` on port 5000, `eval/judge_web.py` on port 5001) serve the annotation and judge UIs. Running two processes is unnecessary — they share the same data files and have no conflicting logic. A single server can route both pages.

## Goal

Merge both apps into `eval/web.py`. One process, one port, two pages. Delete the old files.

---

## Architecture

### New File: `eval/web.py`

Single `create_app()` factory combining all routes from both existing apps.

```python
def create_app(
    *,
    traces_path: Path,
    questions_path: Path,
    dataset_path: Path,
    judge_results_path: Path,
    annotator: str,
) -> Flask
```

### Routes

| Method | Path | Handler | Description |
|--------|------|---------|-------------|
| `GET` | `/` | `index_annotate` | Render `annotate.html` |
| `GET` | `/judge` | `index_judge` | Render `judge.html` |
| `GET` | `/api/traces` | `get_traces` | Unified trace list (see below) |
| `POST` | `/api/annotate` | `post_annotate` | Save human annotation (unchanged logic) |
| `POST` | `/api/judge` | `post_judge` | Run LLM judge, save result (unchanged logic) |

### Entry Point

```bash
uv run python -m eval.web --port 5000 --annotator <name>
# Annotate UI: http://127.0.0.1:5000/
# Judge UI:    http://127.0.0.1:5000/judge
```

---

## Unified `/api/traces` Response

Both existing `get_traces()` functions return nearly identical data. The unified version returns the superset:

```json
[
  {
    "trace": { "...all trace fields..." },
    "expected_answer": "...",
    "human_annotation": { "label": "fail", "critique": "...", "..." },
    "judge_result": { "label": "fail", "dimensions": [...], "..." }
  }
]
```

**Key name change:** `annotate_web` used `latest_annotation`; unified API uses `human_annotation` (consistent with judge's existing key). `annotate.html` JS updated accordingly (a few references).

`human_annotation` and `judge_result` are `null` when not yet present.

---

## Template Changes

Minimal. Only navigation links added.

- `annotate.html`: add small nav link "Judge UI →" pointing to `/judge`
- `judge.html`: add small nav link "← 标注 UI" pointing to `/`
- Both templates' `fetch('/api/traces')` call is unchanged (same path)
- `annotate.html`: rename `latest_annotation` → `human_annotation` in JS

---

## Test Changes

Merge `tests/test_annotate_web.py` + `tests/test_judge_web.py` → `tests/test_web.py`.

- All existing test cases preserved (no coverage reduction)
- Mock patch paths: `eval.annotate_web.*` / `eval.judge_web.*` → `eval.web.*`
- No new test cases required (logic unchanged, just relocated)

---

## Files Deleted

- `eval/annotate_web.py`
- `eval/judge_web.py`
- `tests/test_annotate_web.py`
- `tests/test_judge_web.py`

## Files Created

- `eval/web.py`
- `tests/test_web.py`

## Files Modified

- `eval/templates/annotate.html` — nav link + key rename
- `eval/templates/judge.html` — nav link only
- `CLAUDE.md` — update commands section
- `README.md` — update Stage 2 + Stage 4 commands

---

## What Does NOT Change

- Route paths for `/api/annotate` and `/api/judge` — identical
- All business logic (annotation validation, judge execution, append-only writes, last-wins reads)
- Data files and their formats
- `load_latest_judge_results`, `save_judge_result` functions (move into `web.py`)
