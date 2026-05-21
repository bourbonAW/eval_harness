# Judge Rubric Editor — Design Spec

**Date:** 2026-05-21  
**Status:** Approved  
**Scope:** /judge 页面新增 Rubric 编辑模态框，支持手动编辑 + LLM 一键分析建议

---

## 背景与目标

当前 LLM judge 的 system prompt 和 few-shot 例子硬编码在 `eval/judges.py` 中。团队成员若想调优 rubric（例如 F1 下跌后想加 few-shot 例子、修改评判标准），必须直接改 Python 源码，门槛高且容易出错。

本设计在 `/judge` 页面引入一个弹出式 **Rubric 编辑器**，让整个团队都能在 Web UI 中完成 eval flywheel Stage 5 的"改进 judge"环节，无需接触代码。

---

## 功能范围

### 在范围内

- 查看并手动编辑任一 judge 维度的 system prompt（textarea 自由编辑）
- 查看、新增、删除 few-shot 例子（含 verdict + question/answer/critique）
- 一键 AI 分析：Claude 读取当前维度的 FP/FN 失误案例 → 返回结构化改进建议
- 逐条采纳 / 忽略 AI 建议，或一键全部采纳
- 保存后立即生效（下次 Run judge 即使用新 rubric）
- 两个现有维度均支持：`answer_relevance`、`faithfulness`

### 不在范围内

- 新增 judge 维度（仅管理现有两个维度的 rubric）
- Rubric 版本历史 / rollback
- AI 分析结果 streaming 显示
- Few-shot 例子的详细字段编辑（仅新增/删除，不支持内联编辑 critique）

---

## 数据层设计

### 新增文件：`data/judge_rubric.json`

```json
{
  "answer_relevance": {
    "system_prompt": "你是一个评估客服机器人回复质量的评判员。\n...",
    "few_shot": [
      {
        "question": "省级企业技术中心项目专项资金支持的是哪些企业？",
        "answer": "- **政策收益**：...",
        "verdict": "Fail",
        "critique": "用户问的是'支持哪些企业'...",
        "evidence": ["回复结构为政策收益/申请条件/操作流程三要素，无直接陈述支持哪类企业"]
      }
    ]
  },
  "faithfulness": {
    "system_prompt": "你是一个评估客服机器人回复质量的评判员。\n...",
    "few_shot": [
      {
        "doc_context": "",
        "faq_context": "",
        "answer": "根据广州市工信局相关政策...",
        "verdict": "Fail",
        "critique": "检索上下文为空...",
        "evidence": ["检索上下文为空，回复中的具体设备分类无从核查"]
      }
    ]
  }
}
```

**约定：**
- 文件由 git 管理，团队共享 rubric 改进历史
- `judges.py` 在每次 judge 调用时读取此文件；文件不存在则 fallback 到 `judges.py` 中的硬编码默认值
- 写入使用原子操作（写 `.tmp` 再 rename），与 questions.jsonl 保持一致

---

## API 设计

### `GET /api/rubric/<dimension>`

返回指定维度的当前 rubric（优先读文件，fallback 硬编码）。

**Response 200:**
```json
{
  "dimension": "answer_relevance",
  "system_prompt": "...",
  "few_shot": [...]
}
```

**错误：** `dimension` 不是 `answer_relevance` 或 `faithfulness` → 400

---

### `PUT /api/rubric/<dimension>`

保存修改后的 rubric。

**Request body:**
```json
{
  "system_prompt": "...",
  "few_shot": [...]
}
```

**Validation：**
- `system_prompt` 不能为空字符串
- `few_shot` 中每条 `verdict` 必须是 `"Pass"` 或 `"Fail"`
- `few_shot` 中每条 `answer` 不能为空；answer_relevance 维度中每条 `question` 也不能为空

**Response 200:** `{"ok": true}`  
**Response 400:** `{"error": "..."}`

---

### `POST /api/rubric/<dimension>/suggest`

调用 Claude API，分析当前维度的 FP/FN 失误案例，返回结构化改进建议。

**流程：**
1. 读取 `data/traces.jsonl` + `data/dataset.jsonl`（人工标注）+ `data/judge_results.jsonl`
2. 找出该维度下 judge label ≠ human label 的 traces（FP/FN）
3. 读取当前 rubric（system prompt + few_shot）
4. 构造 prompt，调用 Claude（`claude-sonnet-4-6`，与现有 judge 调用走相同的 `_call_llm` 路由）
5. 解析并返回建议

**Response 200:**
```json
{
  "fp_fn_count": 2,
  "suggestions": [
    {
      "type": "system_prompt",
      "description": "建议修改标准 #2：...",
      "original": "回复中必须对用户的具体问题给出明确的直接答案",
      "proposed": "回复中必须在开头或结尾对用户问题给出明确的直接答案，不能仅靠条件列表暗示"
    },
    {
      "type": "few_shot",
      "description": "基于 q_007 FN 案例建议新增 Fail 例子",
      "proposed_example": {
        "question": "...",
        "answer": "...",
        "verdict": "Fail",
        "critique": "...",
        "evidence": ["..."]
      }
    }
  ]
}
```

**Response 200（无 FP/FN）:** `{"fp_fn_count": 0, "suggestions": []}`  
**Response 500:** LLM 调用失败，返回 `{"error": "..."}`

---

## UI 设计（judge.html）

### 顶栏新增按钮

在现有顶栏（含 Run All 按钮区域）右侧加：

```
[ ▶ Run All ]  [ ✎ 编辑 Rubric ]
```

点击「编辑 Rubric」→ 打开模态框，背景半透明遮罩。

---

### 模态框结构

```
┌─────────────────────────────────────────────────┐
│ Rubric 编辑器    [answer_relevance ▾]      [✕] │
├─────────────────────────────────────────────────┤
│ [评判标准]  [Few-shot 例子]  [✦ AI 分析]        │
├─────────────────────────────────────────────────┤
│                                                 │
│   (Tab content — see below)                     │
│                                                 │
├─────────────────────────────────────────────────┤
│                          [取消]  [保存 Rubric]  │
└─────────────────────────────────────────────────┘
```

**维度 dropdown**：切换时重新 `GET /api/rubric/<dim>` 加载内容，未保存时提示确认。

---

### Tab 0 — 评判标准

- `<textarea>` 展示并允许编辑 `system_prompt` 全文
- 底部显示字符数
- 内容变化后「保存」按钮高亮

---

### Tab 1 — Few-shot 例子

- 列表展示每条 few-shot：verdict badge + question 摘要 + critique 摘要 + [✕ 删除]（无内联编辑，修改例子须先删后加）
- 底部「+ 从标注 traces 中添加例子」：弹出已标注 traces 列表，勾选后追加到 few-shot
  - 加入时，`question` / `answer` / `verdict`（来自 human_annotation.label）自动填充；`critique` 和 `evidence` 留空（由用户后续手动完善，或 AI 建议时补充）
- faithfulness 维度的 few-shot 包含 `doc_context` / `faq_context` 字段，添加时从 trace 自动带入

---

### Tab 2 — ✦ AI 分析

- 状态 A（idle）：说明文字 + 「一键分析 FP/FN → 生成改进建议」按钮
- 状态 B（loading）：spinner + "正在分析 FP/FN 案例…"
- 状态 C（no errors）：「当前维度无 FP/FN 失误，judge 表现良好」
- 状态 D（有建议）：
  - 顶部：「发现 N 处 FP/FN，生成 M 条建议」+ 「✓ 全部采纳」
  - 每条建议卡片：类型 tag（SYSTEM PROMPT / FEW-SHOT） + 说明 + [✓ 采纳] [✕ 忽略]
  - 采纳后该卡片变灰，相应 Tab 内容更新（但尚未保存到文件）
- 分析结果在本次 modal 会话内有效；关闭 modal 丢弃未保存建议

---

## `judges.py` 改动

### `load_rubric(dimension: str) -> dict`

新增函数，按以下优先级返回 rubric：

1. 读取 `data/judge_rubric.json`，返回对应维度的 dict
2. 文件不存在或解析失败 → 返回硬编码默认值（现有常量）

### `judge_answer_relevance` / `judge_faithfulness`

- 调用时执行 `load_rubric(...)` 获取 system_prompt 和 few_shot
- 构造 messages 列表的逻辑不变，只是数据源从硬编码常量改为 `load_rubric()` 返回值

---

## 错误处理

| 场景 | 处理 |
|------|------|
| `judge_rubric.json` 缺失 | fallback 到硬编码，不报错 |
| `judge_rubric.json` 格式损坏 | fallback 到硬编码，后端 log warning |
| PUT /api/rubric 写入失败 | 返回 500，modal 显示"保存失败，请重试" |
| POST /suggest LLM 调用超时 | 返回 500，UI 显示"AI 分析失败，请重试" |
| FP/FN traces 不足（< 1条） | 返回 `{"fp_fn_count": 0, "suggestions": []}` |
| 切换维度时有未保存修改 | confirm dialog："当前修改未保存，确认切换？" |

---

## 测试要求

### 单元测试

- `test_load_rubric_file_missing` — 文件不存在时 fallback 正确
- `test_load_rubric_uses_file_when_present` — 文件存在时读取文件内容
- `test_get_rubric_returns_defaults` — GET /api/rubric/answer_relevance 返回 system_prompt 和 few_shot
- `test_put_rubric_saves_and_reloads` — PUT 后 GET 返回更新内容
- `test_put_rubric_validates_empty_prompt` — system_prompt 为空时返回 400
- `test_put_rubric_validates_verdict` — verdict 非 Pass/Fail 时返回 400
- `test_put_rubric_atomic_write` — 写入操作通过 .tmp rename 实现（mock os.replace）

### 手动冒烟测试

1. 启动 web server，访问 /judge
2. 点「编辑 Rubric」→ modal 打开，加载 answer_relevance 内容
3. 修改 system prompt → 保存 → 关闭 → Run judge → 确认新 prompt 生效（通过 log 或 judge_results 中 critique 内容验证）
4. 切换到 faithfulness 维度 → 内容正确加载
5. Few-shot Tab：删除一条例子 → 保存 → 重新打开 → 条目已消失
6. AI 分析 Tab（F1 < 100% 时）：点击分析按钮 → 获得建议 → 采纳一条 → 保存 → Run judge → F1 变化符合预期
7. 有未保存修改时切换维度 → confirm dialog 出现

---

## 文件改动清单

| 文件 | 变更类型 | 说明 |
|------|---------|------|
| `eval/judges.py` | 修改 | 新增 `load_rubric()` 函数；两个 judge 函数改用动态 rubric |
| `eval/web.py` | 修改 | 新增 3 个 API endpoints；新增 rubric 文件读写辅助函数 |
| `eval/templates/judge.html` | 修改 | 新增「编辑 Rubric」按钮；新增 modal HTML + JS |
| `data/judge_rubric.json` | 新增 | 初始内容为现有硬编码 rubric 的 JSON 版本 |
| `tests/test_rubric_api.py` | 新增 | 7 个单元测试 |
