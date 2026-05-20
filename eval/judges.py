"""Stage 4 LLM judges for the eval flywheel."""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field

import anthropic
import openai
from dotenv import load_dotenv

load_dotenv()


# ── Schema ────────────────────────────────────────────────────────────────────


@dataclass
class DimensionResult:
    dimension: str       # e.g. "answer_relevance" | "faithfulness" | "context_relevance"
    label: str           # "pass" | "fail"
    critique: str        # free-form analysis, no length restriction
    evidence: list[str]  # direct quotes from the trace that informed the verdict
    model: str           # judge model used


@dataclass
class EvalResult:
    trace_id: str
    dimensions: list[DimensionResult]
    label: str = field(init=False)  # "pass" if all dimensions pass, else "fail"

    def __post_init__(self) -> None:
        self.label = "pass" if all(d.label == "pass" for d in self.dimensions) else "fail"


# ── LLM routing ───────────────────────────────────────────────────────────────


def _call_llm(system: str, messages: list[dict], *, model: str, max_tokens: int = 1500) -> str:
    """Route to Anthropic or OpenAI-compatible backend based on model name."""
    if model.startswith("claude"):
        client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
        response = client.messages.create(
            model=model,
            max_tokens=max_tokens,
            system=system,
            messages=messages,
        )
        return response.content[0].text.strip()
    else:
        client = openai.OpenAI(
            api_key=os.environ["OPENAI_API_KEY"],
            base_url=os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1"),
        )
        oai_messages = [{"role": "system", "content": system}] + messages
        response = client.chat.completions.create(
            model=model,
            max_tokens=max_tokens,
            messages=oai_messages,
        )
        return (response.choices[0].message.content or "").strip()


# ── Parsing ───────────────────────────────────────────────────────────────────


def _parse_judge_response(raw: str, *, dimension: str, model: str) -> DimensionResult:
    """Parse the JSON string returned by the judge LLM into a DimensionResult."""
    data = json.loads(raw)
    verdict = data.get("verdict", "").strip().lower()
    label = "pass" if verdict == "pass" else "fail"
    return DimensionResult(
        dimension=dimension,
        label=label,
        critique=data.get("critique", ""),
        evidence=data.get("evidence", []),
        model=model,
    )


# ── A|Q Answer Relevance judge ────────────────────────────────────────────────

_SYSTEM_ANSWER_RELEVANCE = """\
你是一个评估客服机器人回复质量的评判员。
评判维度（answer_relevance）：回复是否直接回答了用户的具体问题？

标准：
1. 回复必须识别出用户问的是什么（而非仅做通用政策总结）
2. 回复中必须对用户的具体问题给出明确的直接答案
3. 对政策条文的归纳总结可以保留，但不能替代对用户问题的直接回答

严格输出以下 JSON，不要输出任何其他内容：
{"verdict": "Pass" 或 "Fail", "critique": "对判断过程的详细说明", "evidence": ["从回复中摘录的关键片段"]}"""

# Few-shot sourced from data/dataset.jsonl: q_001 = fail, q_003 = pass
_FEW_SHOT_ANSWER_RELEVANCE: list[tuple[str, str, str, str, list[str]]] = [
    (
        "省级企业技术中心项目专项资金支持的是哪些企业？",
        (
            "- **政策收益**：对省级企业技术中心购置用于实施产业创新能力建设项目所需的新增设备给予奖励，"
            "奖励范围为符合要求的试验设备、检验检测设备、中试孵化设备、成果转化设备、产业应用设备。\n"
            "- **申请条件**：\n"
            "1. 企业需为广州市行政区域内注册的独立法人企业；\n"
            "2. 该企业为获批认定的省级企业技术中心；\n"
            "3. 项目具有自主知识产权的创新成果，单个项目申请相关发明专利不少于3件；\n"
            "- **操作流程**：按申报指南要求准备材料，向区工信主管部门提交审查推荐。"
        ),
        "Fail",
        (
            "用户问的是'支持哪些企业'，期望的直接回答形式是'本项目支持XXX类型的企业'这样的明确陈述。"
            "回复只做了政策三要素（政策收益/申请条件/操作流程）的归纳罗列，即便申请条件中隐含了企业类型，"
            "也需要用户自行从条件中推断答案，而非由回复直接给出——这不算直接回答。"
        ),
        ["回复结构为政策收益/申请条件/操作流程三要素，无直接陈述支持哪类企业", "申请条件中的企业要求需用户自行提取，不构成直接回答"],
    ),
    (
        "省级企业技术中心项目专项资金支持方式及标准是？",
        (
            "- **政策收益**：对省级企业技术中心实施产业创新能力建设项目购置符合要求的新增设备给予财政资金奖励。\n"
            "- **申请条件**：企业为广州市行政区域内注册的独立法人企业；获批认定的省级企业技术中心；\n"
            "...\n\n"
            "支持方式及标准：采取事后奖励方式，奖励资金不超过项目新增符合要求的设备（含配套软件，不含税）"
            "购置额的**30%**，单个项目奖励最高不超过**500万元**。"
        ),
        "Pass",
        "在总结凝练政策要素之后，正面回答用户真实提问，给出了30%和500万元的具体标准",
        ["支持方式及标准：采取事后奖励方式", "不超过购置额的30%，单个项目最高500万元"],
    ),
]


def judge_answer_relevance(
    trace: dict,
    *,
    model: str = "mimo-v2.5-pro",
) -> EvalResult:
    """A|Q judge: does the response directly answer the user's question?"""
    messages: list[dict] = []
    for question, answer, verdict, critique, evidence in _FEW_SHOT_ANSWER_RELEVANCE:
        messages.append({"role": "user", "content": f"问题：{question}\n回复：{answer}"})
        messages.append({
            "role": "assistant",
            "content": json.dumps(
                {"verdict": verdict, "critique": critique, "evidence": evidence},
                ensure_ascii=False,
            ),
        })

    messages.append({
        "role": "user",
        "content": f"问题：{trace['question']}\n回复：{trace['actual_answer']}",
    })

    raw = _call_llm(_SYSTEM_ANSWER_RELEVANCE, messages, model=model)
    dim = _parse_judge_response(raw, dimension="answer_relevance", model=model)
    return EvalResult(trace_id=trace["id"], dimensions=[dim])


# ── A|C Faithfulness judge ────────────────────────────────────────────────────

_SYSTEM_FAITHFULNESS = """\
你是一个评估客服机器人回复质量的评判员。
评判维度（faithfulness）：回复中的具体政策声明是否有可追溯的文档依据？

标准：
1. 如果检索到的上下文（doc_context + faq_context）包含了回复中的核心声明，视为有依据
2. 如果上下文为空，但回复给出了具体政策细节，视为无文档依据——即使回复中说"根据XXX政策"也不算有效引用
3. 如果回复只使用模糊归因（如"根据广州市工信局相关政策"），而上下文中找不到对应内容，视为引用不可追溯
4. 如果回复中的具体声明（数字、条件、范围等）在上下文中有对应原文，视为通过

严格输出以下 JSON，不要输出任何其他内容：
{"verdict": "Pass" 或 "Fail", "critique": "对判断过程的详细说明", "evidence": ["从上下文或回复中摘录的关键片段"]}"""

# Few-shot sourced from data: q_009 = fail (empty context, vague attribution), q_005 = pass (verbatim match in faq)
_FEW_SHOT_FAITHFULNESS: list[tuple[str, str, str, str, str, list[str]]] = [
    (
        # doc_context, faq_context, answer, verdict, critique, evidence
        "",
        "",
        (
            "根据广州市工信局相关政策，省级企业技术中心项目可申请奖励的范围如下：\n\n"
            "**支持设备范围**\n主要支持省级企业技术中心购置用于实施产业创新能力建设项目所需的新增设备（含配套软件）。\n\n"
            "**具体包括**\n- 试验设备：用于产品试验、技术验证的专业设备\n"
            "- 检验检测设备：质量检测、性能测试相关设备\n"
            "- 中试孵化设备：用于技术成果中试放大的设备\n\n"
            "**不支持范围**\n- 生产设备\n- 办公设备"
        ),
        "Fail",
        (
            "检索上下文为空（doc_context 和 faq_context 均为空），但回复给出了详细的设备分类列表。"
            "回复虽以【根据广州市工信局相关政策】开头，但这只是模糊归因，并未指明具体政策文件名称或文号，"
            "也无任何检索文档可供核查。所有具体声明（试验设备、检验检测设备等分类）均无文档依据。"
        ),
        [
            "检索上下文为空，回复中的具体设备分类无从核查",
            "模糊归因：【根据广州市工信局相关政策】未指明任何具体政策文件",
        ],
    ),
    (
        # q_005: faq_context has verbatim "不低于500万元"
        "",
        (
            "question: 省级企业技术中心项目单个项目新购置研发仪器设备总额的最低要求？\n"
            "answer: 单个项目新购置研发仪器设备(含配套软件，应为实施项目的新增设备，"
            "不包括生产设备、办公设备等)总额(不含税)不低于500万元。"
        ),
        (
            "单个项目新购置研发仪器设备(含配套软件，应为实施项目的新增设备，"
            "不包括生产设备、办公设备等)总额(不含税)不低于**500万元**，并非600万元。"
        ),
        "Pass",
        (
            "回复中的核心声明（不低于500万元及设备范围说明）与 faq_context 中的原文高度吻合，"
            "具体数字和限定条件均可在检索到的文档中找到直接对应依据。"
        ),
        [
            "faq_context 原文：总额(不含税)不低于500万元",
            "回复内容与上下文原文高度一致，设备范围说明逐字对应",
        ],
    ),
]


def _build_context_block(trace: dict) -> str:
    doc = trace.get("doc_context", "").strip()
    faq = trace.get("faq_context", "").strip()
    if not doc and not faq:
        return "(检索上下文为空)"
    parts = []
    if doc:
        parts.append(f"doc_context:\n{doc}")
    if faq:
        parts.append(f"faq_context:\n{faq}")
    return "\n\n".join(parts)


def judge_faithfulness(
    trace: dict,
    *,
    model: str = "mimo-v2.5-pro",
) -> EvalResult:
    """A|C judge: are the answer's specific claims grounded in the retrieved context?"""
    messages: list[dict] = []
    for doc_ctx, faq_ctx, answer, verdict, critique, evidence in _FEW_SHOT_FAITHFULNESS:
        ctx_block = "(检索上下文为空)" if not doc_ctx and not faq_ctx else (
            "\n\n".join(filter(None, [
                f"doc_context:\n{doc_ctx}" if doc_ctx else "",
                f"faq_context:\n{faq_ctx}" if faq_ctx else "",
            ]))
        )
        messages.append({"role": "user", "content": f"检索上下文：\n{ctx_block}\n\n回复：{answer}"})
        messages.append({
            "role": "assistant",
            "content": json.dumps(
                {"verdict": verdict, "critique": critique, "evidence": evidence},
                ensure_ascii=False,
            ),
        })

    messages.append({
        "role": "user",
        "content": f"检索上下文：\n{_build_context_block(trace)}\n\n回复：{trace['actual_answer']}",
    })

    raw = _call_llm(_SYSTEM_FAITHFULNESS, messages, model=model, max_tokens=2000)
    dim = _parse_judge_response(raw, dimension="faithfulness", model=model)
    return EvalResult(trace_id=trace["id"], dimensions=[dim])


# ── Combined runner ───────────────────────────────────────────────────────────


def run_all_judges(trace: dict, *, model: str = "mimo-v2.5-pro") -> EvalResult:
    """Run all judge dimensions and combine into one EvalResult."""
    ar = judge_answer_relevance(trace, model=model)
    faith = judge_faithfulness(trace, model=model)
    return EvalResult(trace_id=trace["id"], dimensions=ar.dimensions + faith.dimensions)
