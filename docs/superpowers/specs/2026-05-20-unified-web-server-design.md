# Unified Web Server Design

**Date:** 2026-05-20  
**Status:** Approved (updated after Codex review)

## Problem

Two separate Flask servers (`eval/annotate_web.py` on port 5000, `eval/judge_web.py` on port 5001) serve the annotation and judge UIs. Running two processes is unnecessary — they share the same data files and have no conflicting logic. A single server can route both pages.

## Goal

Merge both apps into `eval/web.py`. One process, one port, two pages. Delete the old files.

**Breaking change:** Deleting `eval/annotate_web.py` and `eval/judge_web.py` is intentional. This is an internal eval tool, not a library. No compatibility shims.

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

**CLI flags and defaults** (all paths default to the standard `data/` layout):

| Flag | Default | Required |
|------|---------|----------|
| `--port` | `5000` | no |
| `--annotator` | `"unknown"` | no — judge UI doesn't need it |
| `--traces` | `data/traces.jsonl` | no |
| `--questions` | `data/questions.jsonl` | no |
| `--dataset` | `data/dataset.jsonl` | no |
| `--judge-results` | `data/judge_results.jsonl` | no |

`--annotator` is optional. If omitted, annotations are saved with `annotator="unknown"`. Judge UI ignores the value entirely.

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

**Key name change:** `annotate_web` used `latest_annotation`; unified API uses `human_annotation` (consistent with judge's existing key). `annotate.html` JS updated accordingly. This is a deliberate breaking change to the frontend contract.

`human_annotation` and `judge_result` are `null` when not yet present.

### Join semantics

- Join key: `trace["id"]` (= `trace_id`) links traces → annotations → judge results
- Ordering: traces returned in the same order as `traces.jsonl`
- Duplicates: last-wins per `trace_id` (existing `load_latest_annotations` / `load_latest_judge_results` behavior, unchanged)
- Missing annotation or judge result: field is `null`, not omitted
- Missing or empty files: `traces.jsonl` absence returns empty list `[]`; `dataset.jsonl` / `judge_results.jsonl` absence treated as no annotations / no results

### Concurrency

This is a single-threaded local Flask dev server. Concurrent requests are not a design concern. Append-only JSONL writes are atomic at the OS level for single-process use; no additional locking is added.

---

## Template Changes

Minimal. Only navigation links added.

- `annotate.html`: add small nav link "Judge UI →" pointing to `/judge`
- `judge.html`: add small nav link "← 标注 UI" pointing to `/`
- Both templates' `fetch('/api/traces')` call is unchanged (same path)
- `annotate.html`: rename `latest_annotation` → `human_annotation` in JS (all occurrences)

Templates are designed for local root hosting only (`http://127.0.0.1:<port>/`). No reverse proxy or URL prefix support is required.

---

## Test Changes

Merge `tests/test_annotate_web.py` + `tests/test_judge_web.py` → `tests/test_web.py`.

- All existing test cases preserved (no coverage reduction)
- Mock patch paths: `eval.annotate_web.*` / `eval.judge_web.*` → `eval.web.*`

**New test cases required:**

| Test | Reason |
|------|--------|
| `test_get_traces_returns_human_annotation_key` | Regression for `latest_annotation → human_annotation` rename |
| `test_get_traces_human_annotation_null_when_missing` | Verify `null` (not missing key) when no annotation exists |
| `test_get_traces_judge_result_null_when_missing` | Verify `null` (not missing key) when no judge result exists |
| `test_get_root_serves_annotate_page` | `/` → annotate.html |
| `test_get_judge_serves_judge_page` | `/judge` → judge.html |

---

## Helper Functions

Functions moved into `eval/web.py` (previously split between the two files):

| Function | Origin | Notes |
|----------|--------|-------|
| `load_latest_judge_results` | `judge_web.py` | Moved as-is |
| `save_judge_result` | `judge_web.py` | Moved as-is |
| Annotation helpers (`load_jsonl`, `load_latest_annotations`, `save_annotation`) | `annotate.py` (imported) | Continue to be imported from `eval.annotate`, not duplicated |

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

- `eval/templates/annotate.html` — nav link + `human_annotation` key rename
- `eval/templates/judge.html` — nav link only
- `CLAUDE.md` — update commands section
- `README.md` — update Stage 2 + Stage 4 commands

---

## What Does NOT Change

- Route paths for `/api/annotate` and `/api/judge` — identical
- All business logic (annotation validation, judge execution, append-only writes, last-wins reads)
- Data files and their formats
- `eval/annotate.py` — unchanged, still the source for annotation helpers
