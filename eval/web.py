from __future__ import annotations

import argparse
import json
import re
import threading
import time
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

from flask import Flask, current_app, jsonify, render_template, request

from eval.annotate import _CATEGORIES, _CATEGORY_LABELS, load_jsonl, load_latest_annotations, save_annotation
from eval.collectors.workflow_collector import collect_all as _collect_all_default
from eval.judges import (
    DEFAULT_RUBRIC_PATH,
    get_default_judge_model,
    get_default_rubric_suggest_model,
    load_rubric,
    run_all_judges,
)


_VALID_DIMENSIONS = {"answer_relevance", "faithfulness"}


def load_latest_judge_results(path: Path) -> dict:
    """Last-wins deduplication by trace_id."""
    latest: dict = {}
    for row in load_jsonl(path):
        latest[row["trace_id"]] = row
    return latest


def save_judge_result(result: dict, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(result, ensure_ascii=False) + "\n")


_DEFAULT_QUESTION_FIELDS: dict = {
    "source_policy_url": "",
    "source_doc_url": "",
    "source_doc_name": "",
    "is_multi_intent": False,
    "knowledge_type": "文档",
    "is_prohibited": False,
    "conversation_history": [],
    "notes": "",
}


def _next_q_id(questions: list[dict]) -> str:
    nums = []
    for q in questions:
        m = re.match(r"q_(\d+)$", q.get("id", ""))
        if m:
            nums.append(int(m.group(1)))
    n = max(nums) + 1 if nums else 1
    return f"q_{n:03d}"


def _save_questions(questions: list[dict], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        for q in questions:
            f.write(json.dumps(q, ensure_ascii=False) + "\n")
    tmp.replace(path)


def _save_rubric(data: dict, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def _mtime_to_iso(mtime: float) -> str:
    return datetime.fromtimestamp(mtime, tz=timezone.utc).isoformat()


def _is_stale_judge_results(rubric_path: Path, judge_results: dict) -> bool:
    if not rubric_path.exists():
        return False
    rubric_iso = _mtime_to_iso(rubric_path.stat().st_mtime)
    return any((jr.get("judged_at") or "") < rubric_iso for jr in judge_results.values())


def _parse_llm_json_object(raw: str) -> dict:
    text = raw.strip()
    if not text:
        raise ValueError("LLM 返回空内容")

    candidates = [text]
    fenced = re.fullmatch(r"```(?:json)?\s*(.*?)\s*```", text, flags=re.IGNORECASE | re.DOTALL)
    if fenced:
        candidates.append(fenced.group(1).strip())

    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end > start:
        candidates.append(text[start : end + 1])

    last_error: json.JSONDecodeError | None = None
    for candidate in dict.fromkeys(candidates):
        try:
            parsed = json.loads(candidate)
        except json.JSONDecodeError as exc:
            last_error = exc
            continue
        if not isinstance(parsed, dict):
            raise ValueError("LLM 返回 JSON 顶层必须是对象")
        return parsed

    preview = text[:200].replace("\n", " ")
    raise ValueError(f"LLM 返回内容不是 JSON 对象: {preview}") from last_error


def create_app(
    *,
    traces_path: Path,
    questions_path: Path,
    dataset_path: Path,
    judge_results_path: Path,
    rubric_path: Path = DEFAULT_RUBRIC_PATH,
    annotator: str = "unknown",
    collector_fn=None,
) -> Flask:
    app = Flask(__name__, template_folder="templates")
    app.config["TRACES_PATH"] = Path(traces_path)
    app.config["QUESTIONS_PATH"] = Path(questions_path)
    app.config["DATASET_PATH"] = Path(dataset_path)
    app.config["JUDGE_RESULTS_PATH"] = Path(judge_results_path)
    app.config["RUBRIC_PATH"] = Path(rubric_path)
    app.config["JUDGE_MODEL"] = get_default_judge_model()
    app.config["RUBRIC_SUGGEST_MODEL"] = get_default_rubric_suggest_model()
    app.config["ANNOTATOR"] = annotator
    _collector = collector_fn if collector_fn is not None else _collect_all_default
    _collect_state: dict = {
        "status": "idle",
        "message": "",
        "elapsed_s": 0,
        "succeeded": 0,
        "failed": [],
    }
    _collect_lock = threading.Lock()
    _questions_lock = threading.Lock()
    _rubric_lock = threading.Lock()

    def _run_collector() -> None:
        output_path = app.config["TRACES_PATH"]
        questions_file = app.config["QUESTIONS_PATH"]
        tmp_path = output_path.with_suffix(".tmp")
        started_at = time.monotonic()

        try:
            summary = _collector(questions_file, tmp_path)
            elapsed_s = round(time.monotonic() - started_at)
            succeeded = int(summary["succeeded"])
            failed = list(summary["failed"])

            if succeeded == 0:
                tmp_path.unlink(missing_ok=True)
                status = "error"
                message = f"全部 {len(failed)} 题采集失败，旧文件未修改"
            else:
                tmp_path.replace(output_path)
                if failed:
                    status = "warning"
                    message = f"成功 {succeeded} / 失败 {len(failed)}，已写入 {output_path.name}"
                else:
                    status = "success"
                    message = f"成功采集 {succeeded} 条 traces"
        except Exception as exc:
            tmp_path.unlink(missing_ok=True)
            elapsed_s = round(time.monotonic() - started_at)
            succeeded = 0
            failed = []
            status = "error"
            message = str(exc)[:200]

        with _collect_lock:
            _collect_state.update(
                {
                    "status": status,
                    "message": message,
                    "elapsed_s": elapsed_s,
                    "succeeded": succeeded,
                    "failed": failed,
                }
            )

    @app.get("/")
    def index_annotate():
        return render_template("annotate.html", categories=_CATEGORY_LABELS)

    @app.get("/collect")
    def index_collect():
        return render_template("collect.html")

    @app.get("/judge")
    def index_judge():
        return render_template("judge.html", judge_model=app.config["JUDGE_MODEL"])

    @app.get("/api/traces")
    def get_traces():
        traces = load_jsonl(app.config["TRACES_PATH"])
        questions_by_id = {q["id"]: q for q in load_jsonl(app.config["QUESTIONS_PATH"])}
        human_annotations = load_latest_annotations(app.config["DATASET_PATH"])
        judge_results = load_latest_judge_results(app.config["JUDGE_RESULTS_PATH"])
        result = []
        for t in traces:
            q = questions_by_id.get(t["question_id"], {})
            result.append(
                {
                    "trace": t,
                    "expected_answer": q.get("expected_answer", ""),
                    "human_annotation": human_annotations.get(t["id"]),
                    "judge_result": judge_results.get(t["id"]),
                }
            )
        return jsonify(result)

    @app.post("/api/annotate")
    def post_annotate():
        body = request.get_json(silent=True) or {}
        trace_id = body.get("trace_id")
        label = body.get("label")
        critique = (body.get("critique") or "").strip()
        failure_category = body.get("failure_category")

        if label not in ("pass", "fail", "skip"):
            return jsonify({"error": "label 必须是 pass/fail/skip"}), 400

        traces = load_jsonl(app.config["TRACES_PATH"])
        trace = next((t for t in traces if t["id"] == trace_id), None)
        if trace is None:
            return jsonify({"error": f"trace_id {trace_id} 不存在"}), 404

        if label == "fail":
            if not critique:
                return jsonify({"error": "fail 必须填写 critique"}), 400
            if failure_category not in _CATEGORIES:
                return jsonify({"error": "fail 必须选择 failure_category"}), 400
        else:
            failure_category = None

        questions_by_id = {q["id"]: q for q in load_jsonl(app.config["QUESTIONS_PATH"])}
        q = questions_by_id.get(trace["question_id"], {})

        sample = {
            **trace,
            "complete_question": trace.get("complete_question", trace["question"]),
            "doc_context": trace.get("doc_context", ""),
            "faq_context": trace.get("faq_context", ""),
            "references": trace.get("references", []),
            "ref_num": trace.get("ref_num", 0),
            "expected_answer": q.get("expected_answer", ""),
            "label": label,
            "critique": critique,
            "failure_category": failure_category,
            "annotated_by": app.config["ANNOTATOR"],
            "annotated_at": datetime.now(timezone.utc).isoformat(),
        }
        save_annotation(sample, app.config["DATASET_PATH"])
        return jsonify({"ok": True, "annotation": sample})

    @app.post("/api/judge")
    def post_judge():
        body = request.get_json(silent=True) or {}
        trace_id = body.get("trace_id")
        model = str(body.get("model") or app.config["JUDGE_MODEL"]).strip()

        traces = load_jsonl(app.config["TRACES_PATH"])
        trace = next((t for t in traces if t["id"] == trace_id), None)
        if trace is None:
            return jsonify({"error": f"trace_id {trace_id} 不存在"}), 404

        eval_result = run_all_judges(trace, model=model, rubric_path=app.config["RUBRIC_PATH"])

        row = {
            "trace_id": eval_result.trace_id,
            "label": eval_result.label,
            "dimensions": [asdict(d) for d in eval_result.dimensions],
            "judged_at": datetime.now(timezone.utc).isoformat(),
        }
        save_judge_result(row, app.config["JUDGE_RESULTS_PATH"])
        return jsonify(row)

    @app.get("/api/rubric/<dimension>")
    def get_rubric(dimension: str):
        if dimension not in _VALID_DIMENSIONS:
            return jsonify({"error": f"unknown dimension: {dimension}"}), 400
        rubric = load_rubric(dimension, current_app.config["RUBRIC_PATH"])
        return jsonify({"dimension": dimension, **rubric})

    @app.put("/api/rubric/<dimension>")
    def put_rubric(dimension: str):
        if dimension not in _VALID_DIMENSIONS:
            return jsonify({"error": f"unknown dimension: {dimension}"}), 400

        body = request.get_json(silent=True) or {}
        system_prompt = (body.get("system_prompt") or "").strip()
        if not system_prompt:
            return jsonify({"error": "system_prompt 不能为空"}), 400

        few_shot = body.get("few_shot", [])
        if not isinstance(few_shot, list):
            return jsonify({"error": "few_shot 必须是数组"}), 400

        for ex in few_shot:
            if not isinstance(ex, dict):
                return jsonify({"error": "few_shot 每项必须是对象"}), 400
            if ex.get("verdict") not in ("Pass", "Fail"):
                return jsonify({"error": "verdict 必须是 'Pass' 或 'Fail'（title case）"}), 400
            if not (ex.get("answer") or "").strip():
                return jsonify({"error": "few_shot answer 不能为空"}), 400
            if dimension == "answer_relevance" and not (ex.get("question") or "").strip():
                return jsonify({"error": "answer_relevance few_shot question 不能为空"}), 400

        rubric_path = current_app.config["RUBRIC_PATH"]
        with _rubric_lock:
            try:
                current = json.loads(rubric_path.read_text(encoding="utf-8"))
            except Exception:
                current = {}
            current[dimension] = {"system_prompt": system_prompt, "few_shot": few_shot}
            try:
                _save_rubric(current, rubric_path)
            except OSError as exc:
                return jsonify({"error": f"保存失败: {exc}"}), 500
        return jsonify({"ok": True})

    @app.post("/api/rubric/<dimension>/suggest")
    def post_rubric_suggest(dimension: str):
        if dimension not in _VALID_DIMENSIONS:
            return jsonify({"error": f"unknown dimension: {dimension}"}), 400

        traces_raw = load_jsonl(current_app.config["TRACES_PATH"])
        traces_by_id = {t["id"]: t for t in traces_raw}
        dataset = load_jsonl(current_app.config["DATASET_PATH"])
        human_by_id = {r["id"]: r for r in dataset}
        judge_results = load_latest_judge_results(current_app.config["JUDGE_RESULTS_PATH"])

        disagreements = []
        for trace_id, jr in judge_results.items():
            human = human_by_id.get(trace_id)
            if not human:
                continue
            dim_result = next((d for d in jr.get("dimensions", []) if d.get("dimension") == dimension), None)
            if not dim_result:
                continue
            if dim_result.get("label") != human.get("label"):
                disagreements.append(
                    {
                        "trace_id": trace_id,
                        "trace": traces_by_id.get(trace_id, {}),
                        "human_label": human.get("label"),
                        "human_critique": human.get("critique", ""),
                        "all_judge_dimensions": jr.get("dimensions", []),
                        "target_dim_label": dim_result.get("label"),
                        "target_dim_critique": dim_result.get("critique", ""),
                    }
                )

        rubric_path = current_app.config["RUBRIC_PATH"]
        stale_warning = _is_stale_judge_results(rubric_path, judge_results) if disagreements else False
        if not disagreements:
            return jsonify({"fp_fn_count": 0, "stale_warning": stale_warning, "suggestions": []})

        from eval.judges import _call_llm

        rubric = load_rubric(dimension, rubric_path)
        model = current_app.config["RUBRIC_SUGGEST_MODEL"]
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
- 只输出 JSON 对象本身，不要使用 Markdown 代码块或额外解释文字
- type=system_prompt 时，proposed_full 是完整的新 system prompt（不是片段替换）
- type=few_shot 时，proposed_example 是建议新增的例子
- 只建议真正有价值的改动，无需改动时 suggestions 为空数组
- 维度：{dimension}
"""
        user_content = f"""当前 rubric:

system_prompt:
{rubric["system_prompt"]}

few_shot 例子:
{json.dumps(rubric["few_shot"], ensure_ascii=False, indent=2)}

误判案例（共 {len(disagreements)} 条）:
{json.dumps(disagreements, ensure_ascii=False, indent=2)}

请分析这些误判是否真正属于 {dimension} 维度的问题，并给出改进建议。"""

        try:
            raw = _call_llm(system, [{"role": "user", "content": user_content}], model=model, max_tokens=2000)
            result = _parse_llm_json_object(raw)
            suggestions = result.get("suggestions", [])
        except Exception as exc:
            return jsonify({"error": f"LLM 调用失败: {exc}"}), 500

        return jsonify(
            {
                "fp_fn_count": len(disagreements),
                "stale_warning": stale_warning,
                "suggestions": suggestions,
            }
        )

    @app.get("/api/collect/status")
    def get_collect_status():
        with _collect_lock:
            return jsonify(dict(_collect_state))

    @app.get("/api/collect/info")
    def get_collect_info():
        questions_file = app.config["QUESTIONS_PATH"]
        traces_file = app.config["TRACES_PATH"]
        return jsonify(
            {
                "question_count": len(load_jsonl(questions_file)),
                "trace_count": len(load_jsonl(traces_file)),
                "questions_path": str(questions_file),
                "traces_path": str(traces_file),
            }
        )

    @app.post("/api/collect")
    def post_collect():
        with _collect_lock:
            if _collect_state["status"] == "running":
                return jsonify({"error": "already running"}), 409
            _collect_state.update(
                {
                    "status": "running",
                    "message": "",
                    "elapsed_s": 0,
                    "succeeded": 0,
                    "failed": [],
                }
            )

        threading.Thread(target=_run_collector, daemon=True).start()
        return jsonify({"status": "started"})

    @app.get("/api/questions")
    def get_questions():
        return jsonify(load_jsonl(app.config["QUESTIONS_PATH"]))

    @app.post("/api/questions")
    def post_question():
        body = request.get_json(silent=True) or {}
        question = (body.get("question") or "").strip()
        expected_answer = (body.get("expected_answer") or "").strip()
        if not question:
            return jsonify({"error": "question 不能为空"}), 400
        if not expected_answer:
            return jsonify({"error": "expected_answer 不能为空"}), 400
        with _questions_lock:
            questions = load_jsonl(app.config["QUESTIONS_PATH"])
            new_q = {
                "id": _next_q_id(questions),
                "question": question,
                "expected_answer": expected_answer,
                **_DEFAULT_QUESTION_FIELDS,
                "conversation_history": [],
            }
            questions.append(new_q)
            _save_questions(questions, app.config["QUESTIONS_PATH"])
        return jsonify(new_q), 201

    @app.put("/api/questions/<qid>")
    def put_question(qid: str):
        body = request.get_json(silent=True) or {}
        question = (body.get("question") or "").strip()
        expected_answer = (body.get("expected_answer") or "").strip()
        if not question:
            return jsonify({"error": "question 不能为空"}), 400
        if not expected_answer:
            return jsonify({"error": "expected_answer 不能为空"}), 400
        with _questions_lock:
            questions = load_jsonl(app.config["QUESTIONS_PATH"])
            target = next((q for q in questions if q["id"] == qid), None)
            if target is None:
                return jsonify({"error": f"id {qid!r} 不存在"}), 404
            target["question"] = question
            target["expected_answer"] = expected_answer
            _save_questions(questions, app.config["QUESTIONS_PATH"])
        return jsonify(target)

    @app.delete("/api/questions/<qid>")
    def delete_question(qid: str):
        with _questions_lock:
            questions = load_jsonl(app.config["QUESTIONS_PATH"])
            filtered = [q for q in questions if q["id"] != qid]
            if len(filtered) == len(questions):
                return jsonify({"error": f"id {qid!r} 不存在"}), 404
            _save_questions(filtered, app.config["QUESTIONS_PATH"])
        return jsonify({"ok": True})

    return app


def _main() -> None:
    parser = argparse.ArgumentParser(description="Eval Web UI")
    parser.add_argument("--annotator", default="unknown", help="标注者名字（写入 dataset.jsonl）")
    parser.add_argument("--host", default="127.0.0.1", help="监听地址（默认 127.0.0.1，局域网访问用 0.0.0.0）")
    parser.add_argument("--port", type=int, default=5000)
    parser.add_argument("--traces", default="data/traces.jsonl")
    parser.add_argument("--questions", default="data/questions.jsonl")
    parser.add_argument("--dataset", default="data/dataset.jsonl")
    parser.add_argument("--judge-results", default="data/judge_results.jsonl", dest="judge_results")
    parser.add_argument("--rubric-path", default=str(DEFAULT_RUBRIC_PATH), dest="rubric_path")
    args = parser.parse_args()

    app = create_app(
        traces_path=Path(args.traces),
        questions_path=Path(args.questions),
        dataset_path=Path(args.dataset),
        judge_results_path=Path(args.judge_results),
        rubric_path=Path(args.rubric_path),
        annotator=args.annotator,
    )
    if args.annotator == "unknown":
        print('WARNING: 未指定 --annotator，新标注的 annotated_by 将记为 "unknown"')
    display_host = "127.0.0.1" if args.host == "0.0.0.0" else args.host
    print(f"Collect UI:  http://{display_host}:{args.port}/collect")
    print(f"Annotate UI: http://{display_host}:{args.port}/")
    print(f"Judge UI:    http://{display_host}:{args.port}/judge")
    app.run(host=args.host, port=args.port, debug=False)


if __name__ == "__main__":
    _main()
