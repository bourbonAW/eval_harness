import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Set

from rich.console import Console
from rich.markup import escape
from rich.panel import Panel
from rich.prompt import Prompt

from eval.schema import AnnotatedSample, FailureCategory, Question, Trace

console = Console()

_CATEGORY_LABELS: dict[FailureCategory, str] = {
    "hallucination":  "幻觉        — 答案包含 context 不支持的内容",
    "context_miss":   "检索偏题    — retrieved context 没覆盖问题",
    "refusal_fail":   "拒答失败    — 应该说不知道但没有",
    "citation_error": "引用错误    — 来源链接缺失或与内容不符",
    "incomplete":     "回答不完整  — 遗漏关键信息",
    "off_topic":      "答非所问    — 回答跑题",
    "other":          "其他",
}
_CATEGORIES = list(_CATEGORY_LABELS.keys())


def load_jsonl(path: Path) -> List[dict]:
    if not path.exists():
        return []

    items: List[dict] = []
    with open(path, encoding="utf-8") as f:
        for lineno, line in enumerate(f, 1):
            if not line.strip():
                continue
            try:
                items.append(json.loads(line))
            except json.JSONDecodeError as e:
                # Append-only files can have a partial last line if a writer
                # was killed mid-write. Skip and keep going.
                console.print(f"[yellow][warn] skipping malformed line {lineno} in {path}: {e}[/yellow]")
    return items


def load_annotated_ids(dataset_path: Path) -> Set[str]:
    return {s["id"] for s in load_jsonl(dataset_path)}


def needs_annotation(trace_id: str, dataset_path: Path) -> bool:
    return trace_id not in load_annotated_ids(dataset_path)


def save_annotation(sample: AnnotatedSample, dataset_path: Path) -> None:
    dataset_path.parent.mkdir(parents=True, exist_ok=True)
    with open(dataset_path, "a", encoding="utf-8") as f:
        f.write(json.dumps(sample, ensure_ascii=False) + "\n")


def _display(trace: Trace, expected_answer: str) -> None:
    # escape() prevents Rich from interpreting bot/chunk content as markup tags.
    console.print(Panel(escape(trace["question"]), title="[bold blue]问题", border_style="blue"))

    if trace.get("complete_question") and trace["complete_question"] != trace["question"]:
        console.print(f"[dim]  检索 query: {escape(trace['complete_question'])}[/dim]\n")

    if trace["conversation_history"]:
        console.print("[dim]--- 对话历史 ---[/dim]")
        for turn in trace["conversation_history"]:
            label = "用户" if turn["role"] == "user" else "Bot"
            console.print(f"  [dim][{label}] {escape(turn['content'])}[/dim]")
        console.print()

    if trace.get("doc_context"):
        console.print(
            Panel(
                escape(trace["doc_context"]),
                title="[bold yellow]文档 Context (doc_str)",
                border_style="yellow",
            )
        )

    if trace.get("faq_context"):
        console.print(
            Panel(
                escape(trace["faq_context"]),
                title="[bold cyan]FAQ Context (faq_str)",
                border_style="cyan",
            )
        )

    if trace.get("references"):
        refs = "  ".join(
            f"[[{r['doc_id']}] {escape(r['name'])}]({escape(r['url'])})"
            for r in trace["references"]
        )
        console.print(f"[dim]引用：{refs}[/dim]\n")

    console.print(Panel(escape(trace["actual_answer"]), title="[bold green]Bot 实际回复", border_style="green"))

    if expected_answer:
        console.print(Panel(escape(expected_answer), title="[dim]参考答案（仅供参考，不参与评分）", border_style="dim"))


def _ask_failure_category() -> FailureCategory:
    console.print("\n[bold]失败类型：[/bold]")
    for key, label in _CATEGORY_LABELS.items():
        console.print(f"  [cyan]{key:<16}[/cyan]{label}")
    return Prompt.ask("选择类型", choices=_CATEGORIES)


def annotate_interactive(
    traces_path: Path,
    questions_path: Path,
    dataset_path: Path,
    annotator_name: str,
) -> None:
    traces = load_jsonl(traces_path)
    questions_by_id: dict[str, Question] = {q["id"]: q for q in load_jsonl(questions_path)}
    annotated_ids = load_annotated_ids(dataset_path)
    pending = [t for t in traces if t["id"] not in annotated_ids]

    console.print(f"\n[bold]待标注：{len(pending)} 条 / 已完成：{len(traces) - len(pending)} 条[/bold]\n")
    if not pending:
        console.print("[green]全部标注完毕！[/green]")
        return

    for i, trace in enumerate(pending, 1):
        console.rule(f"[bold]{i}/{len(pending)}  id={trace['id']}")
        q = questions_by_id.get(trace["question_id"], {})
        _display(trace, q.get("expected_answer", ""))

        while True:
            label = Prompt.ask("标注结果", choices=["pass", "fail", "skip"], default="skip")

            if label == "skip":
                console.print("[dim]已跳过[/dim]")
                break

            critique = ""
            failure_category = None

            if label == "fail":
                critique = Prompt.ask("失败原因（一句话）").strip()
                if not critique:
                    console.print("[red]fail 必须填写 critique，请重新输入[/red]")
                    continue
                failure_category = _ask_failure_category()

            sample: AnnotatedSample = {
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
                "annotated_by": annotator_name,
                "annotated_at": datetime.now(timezone.utc).isoformat(),
            }
            save_annotation(sample, dataset_path)
            console.print(f"[green]已保存 [{label}][/green]")
            break

    total_done = len(load_jsonl(dataset_path))
    console.print(f"\n[bold green]本次完成。dataset.jsonl 共 {total_done} 条已标注记录。[/bold green]")


if __name__ == "__main__":
    annotate_interactive(
        traces_path=Path("data/traces.jsonl"),
        questions_path=Path("data/questions.jsonl"),
        dataset_path=Path("data/dataset.jsonl"),
        annotator_name=sys.argv[1] if len(sys.argv) > 1 else "unknown",
    )
