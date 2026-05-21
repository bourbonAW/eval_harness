from __future__ import annotations

import argparse
import json
import re
import threading
import time
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

from flask import Flask, jsonify, render_template, request

from eval.annotate import _CATEGORIES, _CATEGORY_LABELS, load_jsonl, load_latest_annotations, save_annotation
from eval.collectors.workflow_collector import collect_all as _collect_all_default
from eval.judges import run_all_judges


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


def create_app(
    *,
    traces_path: Path,
    questions_path: Path,
    dataset_path: Path,
    judge_results_path: Path,
    annotator: str = "unknown",
    collector_fn=None,
) -> Flask:
    app = Flask(__name__, template_folder="templates")
    app.config["TRACES_PATH"] = Path(traces_path)
    app.config["QUESTIONS_PATH"] = Path(questions_path)
    app.config["DATASET_PATH"] = Path(dataset_path)
    app.config["JUDGE_RESULTS_PATH"] = Path(judge_results_path)
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
        return render_template("judge.html")

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
        model = body.get("model", "mimo-v2.5-pro")

        traces = load_jsonl(app.config["TRACES_PATH"])
        trace = next((t for t in traces if t["id"] == trace_id), None)
        if trace is None:
            return jsonify({"error": f"trace_id {trace_id} 不存在"}), 404

        eval_result = run_all_judges(trace, model=model)

        row = {
            "trace_id": eval_result.trace_id,
            "label": eval_result.label,
            "dimensions": [asdict(d) for d in eval_result.dimensions],
            "judged_at": datetime.now(timezone.utc).isoformat(),
        }
        save_judge_result(row, app.config["JUDGE_RESULTS_PATH"])
        return jsonify(row)

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
    parser.add_argument("--port", type=int, default=5000)
    parser.add_argument("--traces", default="data/traces.jsonl")
    parser.add_argument("--questions", default="data/questions.jsonl")
    parser.add_argument("--dataset", default="data/dataset.jsonl")
    parser.add_argument("--judge-results", default="data/judge_results.jsonl", dest="judge_results")
    args = parser.parse_args()

    app = create_app(
        traces_path=Path(args.traces),
        questions_path=Path(args.questions),
        dataset_path=Path(args.dataset),
        judge_results_path=Path(args.judge_results),
        annotator=args.annotator,
    )
    if args.annotator == "unknown":
        print('WARNING: 未指定 --annotator，新标注的 annotated_by 将记为 "unknown"')
    print(f"Collect UI:  http://127.0.0.1:{args.port}/collect")
    print(f"Annotate UI: http://127.0.0.1:{args.port}/")
    print(f"Judge UI:    http://127.0.0.1:{args.port}/judge")
    app.run(host="127.0.0.1", port=args.port, debug=False)


if __name__ == "__main__":
    _main()
