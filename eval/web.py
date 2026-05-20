from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

from flask import Flask, jsonify, render_template, request

from eval.annotate import _CATEGORIES, _CATEGORY_LABELS, load_jsonl, load_latest_annotations, save_annotation
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


def create_app(
    *,
    traces_path: Path,
    questions_path: Path,
    dataset_path: Path,
    judge_results_path: Path,
    annotator: str = "unknown",
) -> Flask:
    app = Flask(__name__, template_folder="templates")
    app.config["TRACES_PATH"] = Path(traces_path)
    app.config["QUESTIONS_PATH"] = Path(questions_path)
    app.config["DATASET_PATH"] = Path(dataset_path)
    app.config["JUDGE_RESULTS_PATH"] = Path(judge_results_path)
    app.config["ANNOTATOR"] = annotator

    @app.get("/")
    def index_annotate():
        return render_template("annotate.html", categories=_CATEGORY_LABELS)

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
    print(f"Annotate UI: http://127.0.0.1:{args.port}/")
    print(f"Judge UI:    http://127.0.0.1:{args.port}/judge")
    app.run(host="127.0.0.1", port=args.port, debug=False)


if __name__ == "__main__":
    _main()
