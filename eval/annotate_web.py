import argparse
from datetime import datetime, timezone
from pathlib import Path

from flask import Flask, jsonify, render_template, request

from eval.annotate import _CATEGORIES, _CATEGORY_LABELS, load_jsonl, load_latest_annotations, save_annotation


def create_app(
    *,
    traces_path: Path,
    questions_path: Path,
    dataset_path: Path,
    annotator: str,
) -> Flask:
    app = Flask(__name__, template_folder="templates")
    app.config["TRACES_PATH"] = Path(traces_path)
    app.config["QUESTIONS_PATH"] = Path(questions_path)
    app.config["DATASET_PATH"] = Path(dataset_path)
    app.config["ANNOTATOR"] = annotator

    @app.get("/api/traces")
    def get_traces():
        traces = load_jsonl(app.config["TRACES_PATH"])
        questions_by_id = {q["id"]: q for q in load_jsonl(app.config["QUESTIONS_PATH"])}
        latest = load_latest_annotations(app.config["DATASET_PATH"])
        result = []
        for t in traces:
            q = questions_by_id.get(t["question_id"], {})
            result.append(
                {
                    "trace": t,
                    "expected_answer": q.get("expected_answer", ""),
                    "latest_annotation": latest.get(t["id"]),
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

    @app.get("/")
    def index():
        return render_template("annotate.html", categories=_CATEGORY_LABELS)

    return app


def _main() -> None:
    parser = argparse.ArgumentParser(description="HTML 标注工具")
    parser.add_argument("--annotator", required=True, help="标注者名字（写入 dataset.jsonl 的 annotated_by 字段）")
    parser.add_argument("--port", type=int, default=5000)
    args = parser.parse_args()

    app = create_app(
        traces_path=Path("data/traces.jsonl"),
        questions_path=Path("data/questions.jsonl"),
        dataset_path=Path("data/dataset.jsonl"),
        annotator=args.annotator,
    )
    print(f"标注工具运行在 http://127.0.0.1:{args.port}  （标注者：{args.annotator}）")
    app.run(host="127.0.0.1", port=args.port, debug=False)


if __name__ == "__main__":
    _main()
