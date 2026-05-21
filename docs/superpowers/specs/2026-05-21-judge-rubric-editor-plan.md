# Judge Rubric Editor — Implementation Plan

**Spec:** `docs/superpowers/specs/2026-05-21-judge-rubric-editor-design.md`  
**Date:** 2026-05-21  
**TDD required:** RED → GREEN for every backend task

---

## Phase 0: Documentation Discovery (Already Complete)

### Allowed APIs & Patterns

| Pattern | Source | Lines |
|---------|--------|-------|
| `create_app(**kwargs) -> Flask` | `eval/web.py` | 64–73 |
| Config storage: `app.config["KEY"] = Path(...)` | `eval/web.py` | 69–76 |
| Atomic write: `tmp = path.with_suffix(".tmp"); open(tmp); tmp.replace(path)` | `eval/web.py` | 55–61 |
| `load_jsonl(path)` imported from `eval.annotate` | `eval/web.py` | 14 |
| `load_latest_judge_results(path) -> dict` | `eval/web.py` | 19–24 |
| `_call_llm(system, messages, *, model, max_tokens=1500) -> str` | `eval/judges.py` | 40–62 |
| answer_relevance few-shot tuple: `(question, answer, verdict, critique, evidence[])` | `eval/judges.py` | 103–130 |
| faithfulness few-shot tuple: `(doc_ctx, faq_ctx, answer, verdict, critique, evidence[])` | `eval/judges.py` | 185–220 |
| Test fixture: `data_dir(tmp_path)` + `_make_client(data_dir)` | `tests/test_web.py` | 118–144 |
| Endpoint test: `client.put("/api/...", json={...}); resp.get_json()` | `tests/test_web.py` | 690–699 |
| CSS color tokens: `--pass`, `--fail`, `--blue`, `--bg`, `--surface`, `--border` | `eval/templates/judge.html` | 11–27 |

### Anti-Patterns

- Do NOT use `request.json` — use `request.get_json(silent=True) or {}`
- Do NOT write rubric JSON directly to final path — always go through `.tmp` → `.replace()`
- Do NOT access `app.config` outside request context — use `current_app.config` inside route handlers
- Do NOT add `dataclasses` or new third-party deps — judges.py already has its dependencies

---

## Phase 1: judges.py — Dynamic Rubric Loading

**Goal:** `load_rubric(dimension, rubric_path)` that reads from file (fallback to hardcoded constants). `judge_answer_relevance` and `judge_faithfulness` use it at call time.

### Task 1.1 — Write `data/judge_rubric.json` (initial content from hardcoded values)

Create `data/judge_rubric.json`. Copy system prompt text verbatim from `_SYSTEM_ANSWER_RELEVANCE` (judges.py:87–101) and `_SYSTEM_FAITHFULNESS` (judges.py:169–183). Convert each `_FEW_SHOT_*` tuple to a dict:

**answer_relevance few-shot dict schema:**
```json
{
  "question": "<tuple[0]>",
  "answer": "<tuple[1]>",
  "verdict": "<tuple[2]>",
  "critique": "<tuple[3]>",
  "evidence": ["<tuple[4][0]>", ...]
}
```

**faithfulness few-shot dict schema:**
```json
{
  "doc_context": "<tuple[0]>",
  "faq_context": "<tuple[1]>",
  "answer": "<tuple[2]>",
  "verdict": "<tuple[3]>",
  "critique": "<tuple[4]>",
  "evidence": ["<tuple[5][0]>", ...]
}
```

Full file structure:
```json
{
  "answer_relevance": {
    "system_prompt": "...",
    "few_shot": [...]
  },
  "faithfulness": {
    "system_prompt": "...",
    "few_shot": [...]
  }
}
```

**Verification:** `python -c "import json; d=json.load(open('data/judge_rubric.json')); assert 'answer_relevance' in d and 'faithfulness' in d"`

---

### Task 1.2 — RED: Write tests for `load_rubric`

Create `tests/test_rubric.py`. Follow the pattern from `tests/test_web.py:720-730` (real file system, no mocks).

```python
# tests/test_rubric.py
import json
from pathlib import Path
from eval.judges import load_rubric, _SYSTEM_ANSWER_RELEVANCE, _FEW_SHOT_ANSWER_RELEVANCE

def test_load_rubric_file_missing_returns_hardcoded(tmp_path):
    rubric = load_rubric("answer_relevance", tmp_path / "missing.json")
    assert rubric["system_prompt"] == _SYSTEM_ANSWER_RELEVANCE
    assert len(rubric["few_shot"]) == len(_FEW_SHOT_ANSWER_RELEVANCE)

def test_load_rubric_file_present_overrides_hardcoded(tmp_path):
    custom = {
        "answer_relevance": {
            "system_prompt": "custom prompt",
            "few_shot": [{"question": "q", "answer": "a", "verdict": "Pass", "critique": "c", "evidence": []}]
        }
    }
    path = tmp_path / "rubric.json"
    path.write_text(json.dumps(custom), encoding="utf-8")
    rubric = load_rubric("answer_relevance", path)
    assert rubric["system_prompt"] == "custom prompt"
    assert rubric["few_shot"][0]["question"] == "q"

def test_load_rubric_corrupt_file_returns_hardcoded(tmp_path):
    path = tmp_path / "rubric.json"
    path.write_text("NOT JSON", encoding="utf-8")
    rubric = load_rubric("answer_relevance", path)
    assert rubric["system_prompt"] == _SYSTEM_ANSWER_RELEVANCE
```

Confirm tests **fail** before implementation: `uv run pytest tests/test_rubric.py -q`

---

### Task 1.3 — GREEN: Implement `load_rubric` in `judges.py`

Add after the existing `_parse_judge_response` function (after line 79):

```python
_HARDCODED_RUBRIC = {
    "answer_relevance": {
        "system_prompt": _SYSTEM_ANSWER_RELEVANCE,
        "few_shot": [
            {"question": q, "answer": a, "verdict": v, "critique": c, "evidence": e}
            for q, a, v, c, e in _FEW_SHOT_ANSWER_RELEVANCE
        ],
    },
    "faithfulness": {
        "system_prompt": _SYSTEM_FAITHFULNESS,
        "few_shot": [
            {"doc_context": d, "faq_context": f, "answer": a, "verdict": v, "critique": c, "evidence": e}
            for d, f, a, v, c, e in _FEW_SHOT_FAITHFULNESS
        ],
    },
}

def load_rubric(dimension: str, rubric_path: Path) -> dict:
    """Return rubric dict for dimension; fallback to hardcoded if file missing or corrupt."""
    try:
        data = json.loads(rubric_path.read_text(encoding="utf-8"))
        return data[dimension]
    except Exception:
        return _HARDCODED_RUBRIC[dimension]
```

**Note:** `_HARDCODED_RUBRIC` must be defined **after** the `_SYSTEM_*` and `_FEW_SHOT_*` constants.

---

### Task 1.4 — Update `judge_answer_relevance` and `judge_faithfulness`

Both functions need a `rubric_path` parameter. Follow the existing signature patterns:

**`judge_answer_relevance` (judges.py:133):** Add `rubric_path: Path = Path("data/judge_rubric.json")` parameter. Replace hardcoded `_SYSTEM_ANSWER_RELEVANCE` and `_FEW_SHOT_ANSWER_RELEVANCE` usage with:

```python
rubric = load_rubric("answer_relevance", rubric_path)
system = rubric["system_prompt"]
messages: list[dict] = []
for ex in rubric["few_shot"]:
    messages.append({"role": "user", "content": f"问题：{ex['question']}\n回复：{ex['answer']}"})
    messages.append({"role": "assistant", "content": json.dumps(
        {"verdict": ex["verdict"], "critique": ex["critique"], "evidence": ex["evidence"]},
        ensure_ascii=False,
    )})
messages.append({"role": "user", "content": f"问题：{trace['question']}\n回复：{trace['actual_answer']}"})
raw = _call_llm(system, messages, model=model)
```

**`judge_faithfulness` (judges.py:238):** Same pattern; few-shot user message format is:
```python
ctx_block = (f"doc_context:\n{ex['doc_context']}" if ex.get("doc_context") else "") + ...
# Use _build_context_block logic adapted for dict access
messages.append({"role": "user", "content": f"检索上下文：\n{ctx_block}\n\n回复：{ex['answer']}"})
```

**`run_all_judges` (judges.py:274):** Add `rubric_path` parameter and pass through to both functions.

**Verification:** `uv run pytest tests/test_rubric.py tests/test_judges.py -q`

---

## Phase 2: web.py — `rubric_path` + API Endpoints

**Goal:** Three rubric endpoints; `create_app()` accepts `rubric_path`; CLI gets `--rubric-path`.

### Task 2.1 — Add `rubric_path` to `create_app()` and CLI

**In `create_app()` (web.py:64):** Add `rubric_path: Path = Path("data/judge_rubric.json")` as keyword-only param. Store as `app.config["RUBRIC_PATH"] = Path(rubric_path)`.

**In `_main()` (web.py:326):** Add parser argument:
```python
parser.add_argument("--rubric-path", default="data/judge_rubric.json", dest="rubric_path")
```
Pass to `create_app(rubric_path=Path(args.rubric_path), ...)`.

**In existing `/api/judge` endpoint:** Pass `rubric_path=current_app.config["RUBRIC_PATH"]` when calling `run_all_judges(trace, model=model, rubric_path=...)`.

**Verification:** `uv run python -m eval.web --help` shows `--rubric-path`.

---

### Task 2.2 — RED: Write tests for rubric API endpoints

Append to `tests/test_rubric_api.py` (new file). Follow the pattern from `tests/test_web.py:126–144`.

```python
# tests/test_rubric_api.py
import json
from pathlib import Path
import pytest
from eval.web import create_app

VALID_DIMENSIONS = ["answer_relevance", "faithfulness"]

@pytest.fixture
def rubric_client(tmp_path):
    app = create_app(
        traces_path=tmp_path / "traces.jsonl",
        questions_path=tmp_path / "questions.jsonl",
        dataset_path=tmp_path / "dataset.jsonl",
        judge_results_path=tmp_path / "judge_results.jsonl",
        rubric_path=tmp_path / "rubric.json",
    )
    app.testing = True
    return app.test_client(), tmp_path

def test_get_rubric_returns_defaults(rubric_client):
    client, _ = rubric_client
    resp = client.get("/api/rubric/answer_relevance")
    assert resp.status_code == 200
    body = resp.get_json()
    assert "system_prompt" in body
    assert "few_shot" in body
    assert body["dimension"] == "answer_relevance"

def test_get_rubric_invalid_dimension_returns_400(rubric_client):
    client, _ = rubric_client
    resp = client.get("/api/rubric/invalid_dim")
    assert resp.status_code == 400

def test_put_rubric_saves_and_reloads(rubric_client):
    client, tmp_path = rubric_client
    payload = {
        "system_prompt": "new prompt",
        "few_shot": [{"question": "q", "answer": "a", "verdict": "Pass", "critique": "c", "evidence": []}]
    }
    put_resp = client.put("/api/rubric/answer_relevance", json=payload)
    assert put_resp.status_code == 200
    get_resp = client.get("/api/rubric/answer_relevance")
    body = get_resp.get_json()
    assert body["system_prompt"] == "new prompt"

def test_put_rubric_atomic_write_no_tmp_leftover(rubric_client):
    client, tmp_path = rubric_client
    payload = {"system_prompt": "p", "few_shot": []}
    client.put("/api/rubric/answer_relevance", json=payload)
    assert not (tmp_path / "rubric.tmp").exists()

def test_put_rubric_empty_prompt_returns_400(rubric_client):
    client, _ = rubric_client
    resp = client.put("/api/rubric/answer_relevance", json={"system_prompt": "", "few_shot": []})
    assert resp.status_code == 400
    assert "system_prompt" in resp.get_json()["error"]

def test_put_rubric_invalid_verdict_returns_400(rubric_client):
    client, _ = rubric_client
    resp = client.put("/api/rubric/answer_relevance", json={
        "system_prompt": "p",
        "few_shot": [{"question": "q", "answer": "a", "verdict": "fail", "critique": "c", "evidence": []}]
    })
    assert resp.status_code == 400

def test_put_rubric_answer_relevance_validates_question_field(rubric_client):
    client, _ = rubric_client
    resp = client.put("/api/rubric/answer_relevance", json={
        "system_prompt": "p",
        "few_shot": [{"question": "", "answer": "a", "verdict": "Pass", "critique": "c", "evidence": []}]
    })
    assert resp.status_code == 400
```

Confirm tests **fail**: `uv run pytest tests/test_rubric_api.py -q`

---

### Task 2.3 — GREEN: Implement the three rubric endpoints

Add to `eval/web.py`, following the style of the existing `POST /api/questions` endpoint (web.py:271–290).

#### Helper: `_save_rubric(data: dict, path: Path) -> None`

```python
def _save_rubric(data: dict, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)
```

#### `GET /api/rubric/<dimension>`

```python
_VALID_DIMENSIONS = {"answer_relevance", "faithfulness"}

@app.get("/api/rubric/<dimension>")
def get_rubric(dimension: str):
    if dimension not in _VALID_DIMENSIONS:
        return jsonify({"error": f"unknown dimension: {dimension}"}), 400
    from eval.judges import load_rubric
    rubric = load_rubric(dimension, current_app.config["RUBRIC_PATH"])
    return jsonify({"dimension": dimension, **rubric})
```

#### `PUT /api/rubric/<dimension>`

```python
@app.put("/api/rubric/<dimension>")
def put_rubric(dimension: str):
    if dimension not in _VALID_DIMENSIONS:
        return jsonify({"error": f"unknown dimension: {dimension}"}), 400
    body = request.get_json(silent=True) or {}
    system_prompt = (body.get("system_prompt") or "").strip()
    if not system_prompt:
        return jsonify({"error": "system_prompt 不能为空"}), 400
    few_shot = body.get("few_shot", [])
    for ex in few_shot:
        if ex.get("verdict") not in ("Pass", "Fail"):
            return jsonify({"error": "verdict 必须是 'Pass' 或 'Fail'（title case）"}), 400
        if not (ex.get("answer") or "").strip():
            return jsonify({"error": "few_shot answer 不能为空"}), 400
        if dimension == "answer_relevance" and not (ex.get("question") or "").strip():
            return jsonify({"error": "answer_relevance few_shot question 不能为空"}), 400
    # Read current file, update dimension, write back atomically
    rubric_path = current_app.config["RUBRIC_PATH"]
    try:
        current = json.loads(rubric_path.read_text(encoding="utf-8"))
    except Exception:
        current = {}
    current[dimension] = {"system_prompt": system_prompt, "few_shot": few_shot}
    _save_rubric(current, rubric_path)
    return jsonify({"ok": True})
```

#### `POST /api/rubric/<dimension>/suggest`

```python
@app.post("/api/rubric/<dimension>/suggest")
def post_rubric_suggest(dimension: str):
    if dimension not in _VALID_DIMENSIONS:
        return jsonify({"error": f"unknown dimension: {dimension}"}), 400

    # Load data
    traces_raw = load_jsonl(current_app.config["TRACES_PATH"])
    traces_by_id = {t["id"]: t for t in traces_raw}
    dataset = load_jsonl(current_app.config["DATASET_PATH"])
    human_by_id = {r["id"]: r for r in dataset}
    judge_results = load_latest_judge_results(current_app.config["JUDGE_RESULTS_PATH"])

    # Find disagreements: judge per-dimension label vs human overall label
    disagreements = []
    for trace_id, jr in judge_results.items():
        human = human_by_id.get(trace_id)
        if not human:
            continue
        dim_result = next((d for d in jr.get("dimensions", []) if d["dimension"] == dimension), None)
        if not dim_result:
            continue
        # Include in analysis if any disagreement (LLM will assess if dimension-relevant)
        if dim_result["label"] != human.get("label"):
            disagreements.append({
                "trace_id": trace_id,
                "trace": traces_by_id.get(trace_id, {}),
                "human_label": human.get("label"),
                "human_critique": human.get("critique", ""),
                "all_judge_dimensions": jr.get("dimensions", []),
                "target_dim_label": dim_result["label"],
                "target_dim_critique": dim_result.get("critique", ""),
            })

    # Check staleness: compare rubric file mtime vs oldest judge result
    rubric_path = current_app.config["RUBRIC_PATH"]
    stale_warning = False
    if rubric_path.exists() and disagreements:
        rubric_mtime = rubric_path.stat().st_mtime
        oldest_judged = min(
            (jr.get("judged_at", "") for jr in judge_results.values()),
            default=""
        )
        if oldest_judged and oldest_judged < _mtime_to_iso(rubric_mtime):
            stale_warning = True

    if not disagreements:
        return jsonify({"fp_fn_count": 0, "stale_warning": stale_warning, "suggestions": []})

    # Build LLM prompt
    from eval.judges import load_rubric, _call_llm
    rubric = load_rubric(dimension, rubric_path)
    model = "claude-sonnet-4-6"

    system = f"""你是一个 LLM judge 优化专家。
给定一个 judge 维度的 rubric（system prompt + few-shot 例子）和若干误判案例，
分析误判原因，提出具体的 rubric 改进建议。

输出严格 JSON：
{{
  "suggestions": [
    {{
      "type": "system_prompt",
      "description": "建议描述",
      "proposed_full": "完整的新 system prompt 文本"
    }},
    {{
      "type": "few_shot",
      "description": "建议描述",
      "proposed_example": {{
        "question": "...",
        "answer": "...",
        "verdict": "Pass 或 Fail",
        "critique": "...",
        "evidence": ["..."]
      }}
    }}
  ]
}}

说明：
- type=system_prompt 时，proposed_full 是完整的新 system prompt（不是片段替换）
- type=few_shot 时，proposed_example 是建议新增的例子
- 只建议真正有价值的改动，无需改动时 suggestions 为空数组
- 维度：{dimension}
"""

    user_content = f"""当前 rubric:

system_prompt:
{rubric['system_prompt']}

few_shot 例子数量: {len(rubric['few_shot'])}

误判案例（共 {len(disagreements)} 条）:
{json.dumps(disagreements, ensure_ascii=False, indent=2)}

请分析这些误判是否真正属于 {dimension} 维度的问题，并给出改进建议。"""

    try:
        raw = _call_llm(system, [{"role": "user", "content": user_content}], model=model, max_tokens=2000)
        result = json.loads(raw)
        suggestions = result.get("suggestions", [])
    except Exception as e:
        return jsonify({"error": f"LLM 调用失败: {e}"}), 500

    return jsonify({
        "fp_fn_count": len(disagreements),
        "stale_warning": stale_warning,
        "suggestions": suggestions,
    })


def _mtime_to_iso(mtime: float) -> str:
    from datetime import datetime, timezone
    return datetime.fromtimestamp(mtime, tz=timezone.utc).isoformat()
```

**Verification:** `uv run pytest tests/test_rubric_api.py -q` — all 7 tests green.

**Run full suite:** `uv run pytest tests/ -m "not integration" -q` — no regressions.

---

## Phase 3: judge.html — Modal HTML + CSS

**Goal:** Add "✎ 编辑 Rubric" button and the full modal overlay HTML + CSS. No JS behavior yet.

### Task 3.1 — Add CSS for modal system

Append to the `<style>` block in `eval/templates/judge.html`. Follow the existing CSS variable conventions (lines 11–27): use `--surface`, `--border`, `--bg`, `--text`, `--blue`, etc.

```css
/* ── Rubric editor modal ───────────────────────── */
.rubric-btn {
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 6px;
  padding: 5px 12px;
  font-size: 13px;
  color: var(--text-2);
  cursor: pointer;
  transition: all .12s;
}
.rubric-btn:hover { border-color: var(--blue); color: var(--blue); }

.rubric-overlay {
  display: none;
  position: fixed;
  inset: 0;
  background: rgba(0,0,0,0.45);
  z-index: 200;
  align-items: center;
  justify-content: center;
}
.rubric-overlay.open { display: flex; }

.rubric-modal {
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 10px;
  width: min(640px, 94vw);
  max-height: 80vh;
  display: flex;
  flex-direction: column;
  box-shadow: 0 8px 32px rgba(0,0,0,0.18);
}
.rubric-modal-header {
  padding: 12px 16px;
  border-bottom: 1px solid var(--border);
  display: flex;
  align-items: center;
  gap: 10px;
}
.rubric-modal-title { font-size: 14px; font-weight: 600; flex: 1; }
.rubric-dim-select {
  background: var(--surface-2);
  border: 1px solid var(--border);
  border-radius: 5px;
  padding: 3px 8px;
  font-size: 12px;
  color: var(--text-2);
}
.rubric-close { color: var(--text-3); font-size: 20px; cursor: pointer; line-height: 1; border: none; background: none; }

.rubric-tabs {
  display: flex;
  border-bottom: 1px solid var(--border);
  background: var(--surface-2);
}
.rubric-tab {
  padding: 8px 16px;
  font-size: 12px;
  color: var(--text-2);
  cursor: pointer;
  border-bottom: 2px solid transparent;
  transition: color .12s;
  background: none;
  border-top: none;
  border-left: none;
  border-right: none;
}
.rubric-tab.active { color: var(--blue); border-bottom-color: var(--blue); }
.rubric-tab.ai-tab { color: #7c3aed; }
.rubric-tab.ai-tab.active { border-bottom-color: #7c3aed; }

.rubric-tab-panel { display: none; padding: 14px 16px; overflow-y: auto; flex: 1; }
.rubric-tab-panel.active { display: block; }

.rubric-field-label {
  font-size: 10px;
  font-weight: 600;
  color: var(--text-3);
  letter-spacing: .06em;
  margin-bottom: 6px;
}
.rubric-textarea {
  width: 100%;
  min-height: 140px;
  background: var(--surface-2);
  border: 1px solid var(--border);
  border-radius: 6px;
  padding: 10px;
  font-size: 12px;
  color: var(--text);
  line-height: 1.6;
  resize: vertical;
  font-family: inherit;
  outline: none;
}
.rubric-textarea:focus { border-color: var(--blue); }
.rubric-char-count { font-size: 10px; color: var(--text-3); text-align: right; margin-top: 4px; }

.rubric-fs-list { display: flex; flex-direction: column; gap: 8px; margin-bottom: 10px; }
.rubric-fs-item {
  background: var(--surface-2);
  border: 1px solid var(--border);
  border-radius: 6px;
  padding: 8px 10px;
}
.rubric-fs-header { display: flex; align-items: center; gap: 8px; }
.rubric-fs-q { font-size: 11px; color: var(--text); flex: 1; }
.rubric-fs-critique { font-size: 10px; color: var(--text-2); margin-top: 4px; line-height: 1.4; }
.rubric-fs-del {
  font-size: 11px;
  padding: 2px 7px;
  border-radius: 4px;
  background: var(--surface);
  border: 1px solid var(--border);
  color: var(--text-3);
  cursor: pointer;
}
.rubric-fs-del:hover { background: var(--fail-light); border-color: var(--fail-border); color: var(--fail); }

.rubric-add-btn {
  width: 100%;
  background: var(--surface-2);
  border: 1px dashed var(--border);
  border-radius: 6px;
  padding: 8px;
  text-align: center;
  font-size: 11px;
  color: var(--text-3);
  cursor: pointer;
}
.rubric-add-btn:hover { border-color: var(--blue); color: var(--blue); }

.rubric-ai-desc { font-size: 12px; color: var(--text-2); line-height: 1.6; margin-bottom: 12px; }
.rubric-ai-run-btn {
  width: 100%;
  padding: 10px;
  background: linear-gradient(135deg, #7c3aed, var(--blue));
  border-radius: 6px;
  text-align: center;
  font-size: 13px;
  font-weight: 600;
  color: #fff;
  cursor: pointer;
  border: none;
  margin-bottom: 12px;
}
.rubric-stale-banner {
  background: #fef3c7;
  border: 1px solid #fbbf24;
  border-radius: 6px;
  padding: 8px 10px;
  font-size: 11px;
  color: #92400e;
  margin-bottom: 10px;
}
.rubric-suggestion {
  background: var(--surface-2);
  border: 1px solid #a78bfa44;
  border-radius: 6px;
  padding: 10px 12px;
  margin-bottom: 8px;
}
.rubric-suggestion-tag {
  font-size: 9px;
  font-weight: 700;
  padding: 2px 6px;
  border-radius: 3px;
  letter-spacing: .05em;
  margin-right: 6px;
}
.rubric-suggestion-tag.sys { background: #dbeafe; color: #1d4ed8; }
.rubric-suggestion-tag.fewshot { background: #ede9fe; color: #6d28d9; }
.rubric-suggestion-desc { font-size: 11px; color: var(--text); line-height: 1.5; margin-top: 5px; }
.rubric-suggestion-actions { display: flex; gap: 6px; margin-top: 8px; }
.rubric-adopt-btn { background: var(--pass-light); border: 1px solid var(--pass-border); color: var(--pass); font-size: 10px; padding: 2px 8px; border-radius: 4px; cursor: pointer; }
.rubric-reject-btn { background: var(--surface); border: 1px solid var(--border); color: var(--text-3); font-size: 10px; padding: 2px 8px; border-radius: 4px; cursor: pointer; }
.rubric-suggestion.adopted { opacity: 0.45; }

.rubric-modal-footer {
  padding: 10px 16px;
  border-top: 1px solid var(--border);
  display: flex;
  justify-content: flex-end;
  gap: 8px;
  background: var(--surface-2);
  border-radius: 0 0 10px 10px;
}
.rubric-cancel-btn {
  background: var(--surface);
  border: 1px solid var(--border);
  color: var(--text-2);
  font-size: 12px;
  padding: 5px 14px;
  border-radius: 5px;
  cursor: pointer;
}
.rubric-save-btn {
  background: var(--blue);
  color: #fff;
  font-size: 12px;
  font-weight: 600;
  padding: 5px 16px;
  border-radius: 5px;
  cursor: pointer;
  border: none;
}
```

---

### Task 3.2 — Add button + modal HTML

**In topbar** (after line 330, inside `.header` element): Add `<button class="rubric-btn" onclick="openRubric()">✎ 编辑 Rubric</button>`.

**Before the closing `</body>` tag:** Add the modal overlay HTML:

```html
<!-- Rubric editor modal -->
<div class="rubric-overlay" id="rubricOverlay" onclick="onOverlayClick(event)">
  <div class="rubric-modal">
    <div class="rubric-modal-header">
      <span class="rubric-modal-title">Rubric 编辑器</span>
      <select class="rubric-dim-select" id="rubricDimSelect" onchange="onDimChange()">
        <option value="answer_relevance">answer_relevance</option>
        <option value="faithfulness">faithfulness</option>
      </select>
      <button class="rubric-close" onclick="closeRubric()">✕</button>
    </div>
    <div class="rubric-tabs">
      <button class="rubric-tab active" onclick="switchRubricTab(0)">评判标准</button>
      <button class="rubric-tab" onclick="switchRubricTab(1)">Few-shot 例子</button>
      <button class="rubric-tab ai-tab" onclick="switchRubricTab(2)">✦ AI 分析</button>
    </div>
    <!-- Tab 0: system prompt -->
    <div class="rubric-tab-panel active" id="rubricTab0">
      <div class="rubric-field-label">SYSTEM PROMPT</div>
      <textarea class="rubric-textarea" id="rubricPromptTA" oninput="onPromptInput()"></textarea>
      <div class="rubric-char-count" id="rubricCharCount">0 字符</div>
    </div>
    <!-- Tab 1: few-shot -->
    <div class="rubric-tab-panel" id="rubricTab1">
      <div class="rubric-fs-list" id="rubricFsList"></div>
      <button class="rubric-add-btn" onclick="openAddFromTraces()">+ 从标注 traces 中添加例子</button>
    </div>
    <!-- Tab 2: AI analysis -->
    <div class="rubric-tab-panel" id="rubricTab2">
      <div class="rubric-ai-desc">分析当前维度的 FP/FN 失误案例，自动生成 system prompt 和 few-shot 改进建议。</div>
      <button class="rubric-ai-run-btn" id="rubricAiBtn" onclick="runSuggest()">✦ 一键分析 FP/FN → 生成改进建议</button>
      <div id="rubricStaleBanner" class="rubric-stale-banner" style="display:none">
        部分 judge 结果早于当前 rubric，建议先 Run All 再分析
      </div>
      <div id="rubricSuggestions"></div>
    </div>
    <div class="rubric-modal-footer">
      <button class="rubric-cancel-btn" onclick="closeRubric()">取消</button>
      <button class="rubric-save-btn" onclick="saveRubric()">保存 Rubric</button>
    </div>
  </div>
</div>
```

**Verification:** Open `/judge` in browser — button appears in topbar. Click it: overlay appears but no behavior yet (no JS). Close by pressing cancel/✕ (no JS yet — just visual check).

---

## Phase 4: judge.html — JS Behavior

**Goal:** Full interactive behavior for all modal actions.

### Task 4.1 — Core modal state and open/close

Add JS block before the closing `</script>` tag. All new variables and functions in one block.

```javascript
// ── Rubric editor state ──────────────────────────────────
let _rubricDim = 'answer_relevance';
let _rubricDirty = false;
let _rubricFewShot = [];       // current few-shot list (in-memory)
let _rubricPrompt = '';        // current system prompt (in-memory)
let _suggestions = [];         // AI suggestions from /suggest

function openRubric() {
  _rubricDim = document.getElementById('rubricDimSelect').value;
  document.getElementById('rubricOverlay').classList.add('open');
  loadRubric(_rubricDim);
}

function closeRubric() {
  if (_rubricDirty && !confirm('当前修改未保存，确认关闭？')) return;
  document.getElementById('rubricOverlay').classList.remove('open');
  _rubricDirty = false;
}

function onOverlayClick(e) {
  if (e.target === document.getElementById('rubricOverlay')) closeRubric();
}

function switchRubricTab(n) {
  document.querySelectorAll('.rubric-tab').forEach((t, i) => t.classList.toggle('active', i === n));
  document.querySelectorAll('.rubric-tab-panel').forEach((p, i) => p.classList.toggle('active', i === n));
}

function onDimChange() {
  if (_rubricDirty && !confirm('当前修改未保存，确认切换维度？')) {
    document.getElementById('rubricDimSelect').value = _rubricDim;
    return;
  }
  _rubricDim = document.getElementById('rubricDimSelect').value;
  _rubricDirty = false;
  loadRubric(_rubricDim);
}
```

---

### Task 4.2 — Load and render rubric

```javascript
async function loadRubric(dim) {
  const resp = await fetch(`/api/rubric/${dim}`);
  const data = await resp.json();
  _rubricPrompt = data.system_prompt || '';
  _rubricFewShot = data.few_shot || [];
  // Render tab 0
  const ta = document.getElementById('rubricPromptTA');
  ta.value = _rubricPrompt;
  document.getElementById('rubricCharCount').textContent = `${_rubricPrompt.length} 字符`;
  // Render tab 1
  renderFewShot();
  // Reset AI tab
  document.getElementById('rubricSuggestions').innerHTML = '';
  document.getElementById('rubricStaleBanner').style.display = 'none';
  _suggestions = [];
  _rubricDirty = false;
}

function onPromptInput() {
  _rubricPrompt = document.getElementById('rubricPromptTA').value;
  document.getElementById('rubricCharCount').textContent = `${_rubricPrompt.length} 字符`;
  _rubricDirty = true;
}

function renderFewShot() {
  const list = document.getElementById('rubricFsList');
  if (_rubricFewShot.length === 0) {
    list.innerHTML = '<div style="font-size:12px;color:var(--text-3);text-align:center;padding:12px">暂无 few-shot 例子</div>';
    return;
  }
  list.innerHTML = _rubricFewShot.map((ex, i) => {
    const vclass = ex.verdict === 'Pass' ? 'pass' : 'fail';
    const label = ex.question || ex.answer?.slice(0, 40) || '(无标题)';
    return `<div class="rubric-fs-item">
      <div class="rubric-fs-header">
        <span class="verdict-badge ${vclass}">${ex.verdict}</span>
        <span class="rubric-fs-q">${esc(label.slice(0, 60))}</span>
        <button class="rubric-fs-del" onclick="deleteFewShot(${i})">✕</button>
      </div>
      ${ex.critique ? `<div class="rubric-fs-critique">${esc(ex.critique.slice(0, 100))}</div>` : ''}
    </div>`;
  }).join('');
}

function deleteFewShot(i) {
  _rubricFewShot.splice(i, 1);
  _rubricDirty = true;
  renderFewShot();
}
```

---

### Task 4.3 — Add from annotated traces

```javascript
async function openAddFromTraces() {
  // Filter traces with human annotation label != skip
  const annotated = traces.filter(e => e.human_annotation && e.human_annotation.label !== 'skip');
  if (annotated.length === 0) {
    showToast('没有可用的标注 traces', 'error');
    return;
  }
  // Simple pick: show trace IDs with label; user picks via prompt (can be enhanced to a sub-modal)
  const opts = annotated.map((e, i) => `${i}: [${e.human_annotation.label}] ${e.trace.question?.slice(0,50) || e.trace.id}`).join('\n');
  const choice = prompt(`选择要添加的 trace（输入序号）:\n${opts}`);
  if (choice === null || choice.trim() === '') return;
  const idx = parseInt(choice, 10);
  if (isNaN(idx) || idx < 0 || idx >= annotated.length) {
    showToast('无效选择', 'error');
    return;
  }
  const entry = annotated[idx];
  const ha = entry.human_annotation;
  const t = entry.trace;
  const verdict = ha.label === 'pass' ? 'Pass' : 'Fail';
  let newEx = { verdict, critique: ha.critique || '', evidence: [] };
  if (_rubricDim === 'answer_relevance') {
    newEx.question = t.question || '';
    newEx.answer = t.actual_answer || '';
  } else {
    newEx.doc_context = t.doc_context || '';
    newEx.faq_context = t.faq_context || '';
    newEx.answer = t.actual_answer || '';
  }
  _rubricFewShot.push(newEx);
  _rubricDirty = true;
  renderFewShot();
  showToast('已添加 few-shot 例子', 'success');
}
```

---

### Task 4.4 — Save rubric

```javascript
async function saveRubric() {
  const payload = { system_prompt: _rubricPrompt, few_shot: _rubricFewShot };
  try {
    const resp = await fetch(`/api/rubric/${_rubricDim}`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    if (!resp.ok) {
      const err = await resp.json();
      showToast('保存失败: ' + (err.error || resp.status), 'error');
      return;
    }
    _rubricDirty = false;
    showToast('Rubric 已保存', 'success');
  } catch (e) {
    showToast('保存失败: ' + e.message, 'error');
  }
}
```

---

### Task 4.5 — AI analysis (suggest + adopt)

```javascript
async function runSuggest() {
  const btn = document.getElementById('rubricAiBtn');
  btn.disabled = true;
  btn.textContent = '分析中…';
  document.getElementById('rubricSuggestions').innerHTML =
    '<span style="font-size:12px;color:var(--text-3)">正在分析 FP/FN 案例…</span>';

  try {
    const resp = await fetch(`/api/rubric/${_rubricDim}/suggest`, { method: 'POST' });
    const data = await resp.json();
    if (!resp.ok) throw new Error(data.error || resp.status);

    document.getElementById('rubricStaleBanner').style.display =
      data.stale_warning ? '' : 'none';

    _suggestions = data.suggestions || [];
    if (data.fp_fn_count === 0) {
      document.getElementById('rubricSuggestions').innerHTML =
        '<div style="font-size:12px;color:var(--text-2)">当前维度无 FP/FN 失误，judge 表现良好 ✓</div>';
    } else {
      renderSuggestions();
    }
  } catch (e) {
    document.getElementById('rubricSuggestions').innerHTML =
      `<div style="font-size:12px;color:var(--fail)">AI 分析失败: ${esc(e.message)}</div>`;
  } finally {
    btn.disabled = false;
    btn.textContent = '✦ 一键分析 FP/FN → 生成改进建议';
  }
}

function renderSuggestions() {
  const el = document.getElementById('rubricSuggestions');
  if (_suggestions.length === 0) {
    el.innerHTML = '<div style="font-size:12px;color:var(--text-2)">无具体建议</div>';
    return;
  }
  el.innerHTML = `
    <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px">
      <span style="font-size:12px;color:var(--text-2)">生成 ${_suggestions.length} 条建议</span>
      <button class="rubric-adopt-btn" onclick="adoptAll()">✓ 全部采纳</button>
    </div>
    ${_suggestions.map((s, i) => {
      const tag = s.type === 'system_prompt' ? 'sys' : 'fewshot';
      const label = s.type === 'system_prompt' ? 'SYSTEM PROMPT' : 'FEW-SHOT';
      return `<div class="rubric-suggestion" id="sug${i}">
        <div><span class="rubric-suggestion-tag ${tag}">${label}</span></div>
        <div class="rubric-suggestion-desc">${esc(s.description)}</div>
        <div class="rubric-suggestion-actions">
          <button class="rubric-adopt-btn" onclick="adoptSuggestion(${i})">✓ 采纳</button>
          <button class="rubric-reject-btn" onclick="rejectSuggestion(${i})">✕ 忽略</button>
        </div>
      </div>`;
    }).join('')}`;
}

function adoptSuggestion(i) {
  const s = _suggestions[i];
  if (!s) return;
  if (s.type === 'system_prompt' && s.proposed_full) {
    _rubricPrompt = s.proposed_full;
    document.getElementById('rubricPromptTA').value = _rubricPrompt;
    document.getElementById('rubricCharCount').textContent = `${_rubricPrompt.length} 字符`;
  } else if (s.type === 'few_shot' && s.proposed_example) {
    _rubricFewShot.push(s.proposed_example);
    renderFewShot();
  }
  _rubricDirty = true;
  document.getElementById(`sug${i}`)?.classList.add('adopted');
  switchRubricTab(s.type === 'system_prompt' ? 0 : 1);
}

function rejectSuggestion(i) {
  document.getElementById(`sug${i}`)?.classList.add('adopted');
}

function adoptAll() {
  _suggestions.forEach((_, i) => adoptSuggestion(i));
}
```

**Verification:**
1. Open `/judge` — "✎ 编辑 Rubric" button visible
2. Click it — modal opens, system prompt loaded from API
3. Edit prompt text → dirty flag set → close shows confirm dialog
4. Switch to Few-shot tab → list renders
5. Click "+ 从标注 traces 中添加例子" → picks trace → added to list
6. Click 保存 → API call → success toast
7. Click ✦ AI 分析 → runs suggest → renders suggestions → adopt one → updates tab content

---

## Phase 5: Full Regression + Smoke Test

### Task 5.1 — Run all unit tests

```bash
uv run pytest tests/ -m "not integration" -q
```

Expected: all existing tests pass + new rubric tests green. Fix any regressions before proceeding.

### Task 5.2 — Manual smoke test checklist

Follow the checklist from the design spec:

1. [ ] Start server: `uv run python -m eval.web --port 5000 --annotator test`
2. [ ] Open `/judge` — button visible in topbar
3. [ ] Click "✎ 编辑 Rubric" — modal opens, answer_relevance content loaded
4. [ ] Modify system prompt → save → close → run judge on one trace → check judge_results for new critique pattern
5. [ ] Switch to faithfulness → content loads correctly
6. [ ] Few-shot tab: delete one entry → save → reopen → entry gone
7. [ ] "从标注 traces 添加例子" → picks annotated trace → critique auto-filled → save → reopen → new entry visible
8. [ ] AI 分析 tab (when F1 < 100%): click analyze → suggestions appear → adopt one → system prompt tab updated
9. [ ] Stale warning: save rubric → do NOT re-run judges → click analyze → stale banner visible
10. [ ] Unsaved changes: edit → switch dimension → confirm dialog appears

---

## Files Changed Summary

| File | Change |
|------|--------|
| `data/judge_rubric.json` | NEW — initial rubric from hardcoded values |
| `eval/judges.py` | ADD `_HARDCODED_RUBRIC` dict; ADD `load_rubric(dim, path)`; UPDATE `judge_answer_relevance`, `judge_faithfulness`, `run_all_judges` to take `rubric_path` |
| `eval/web.py` | ADD `rubric_path` to `create_app()` + `_main()` CLI; ADD `_save_rubric()`; ADD 3 rubric endpoints; UPDATE `/api/judge` to pass `rubric_path` |
| `eval/templates/judge.html` | ADD CSS block; ADD button to topbar; ADD modal HTML; ADD JS block |
| `tests/test_rubric.py` | NEW — 3 tests for `load_rubric()` |
| `tests/test_rubric_api.py` | NEW — 7 tests for rubric API endpoints |
