import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import List

from rich.console import Console
from rich.panel import Panel
from rich.prompt import Prompt

from eval.schema import AnnotatedSample, Question, Trace

console = Console()


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
                console.print(f"[yellow][warn] skipping malformed line {lineno} in {path}: {e}[/yellow]")
    return items


def needs_annotation(trace_id: str, dataset_path: Path) -> bool:
    annotated_ids = {s["id"] for s in load_jsonl(dataset_path)}
    return trace_id not in annotated_ids


def save_annotation(sample: AnnotatedSample, dataset_path: Path) -> None:
    dataset_path.parent.mkdir(parents=True, exist_ok=True)
    with open(dataset_path, "a", encoding="utf-8") as f:
        f.write(json.dumps(sample, ensure_ascii=False) + "\n")


def _display(trace: Trace, expected_answer: str) -> None:
    console.print(Panel(trace["question"], title="[bold blue]问题", border_style="blue"))
    if trace["conversation_history"]:
        console.print("[dim]--- 对话历史 ---[/dim]")
        for turn in trace["conversation_history"]:
            label = "用户" if turn["role"] == "user" else "Bot"
            console.print(f"  [dim][{label}] {turn['content']}[/dim]")
        console.print()
    if trace["retrieved_chunks"]:
        console.print(
            Panel(
                "\n---\n".join(trace["retrieved_chunks"]),
                title="[bold yellow]检索到的 Context",
                border_style="yellow",
            )
        )
    console.print(Panel(trace["actual_answer"], title="[bold green]Bot 实际回复", border_style="green"))
    if expected_answer:
        console.print(Panel(expected_answer, title="[dim]参考答案（仅供参考，不参与评分）", border_style="dim"))


def annotate_interactive(
    traces_path: Path,
    questions_path: Path,
    dataset_path: Path,
    annotator_name: str,
) -> None:
    traces = load_jsonl(traces_path)
    questions_by_id: dict[str, Question] = {q["id"]: q for q in load_jsonl(questions_path)}
    pending = [t for t in traces if needs_annotation(t["id"], dataset_path)]

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
            if label == "fail":
                critique = Prompt.ask("失败原因（一句话）").strip()
                if not critique:
                    console.print("[red]fail 必须填写 critique，请重新输入[/red]")
                    continue

            sample: AnnotatedSample = {
                **trace,
                "expected_answer": q.get("expected_answer", ""),
                "label": label,
                "critique": critique,
                "failure_category": None,
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
