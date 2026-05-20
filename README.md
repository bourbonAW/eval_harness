# intelligent-customer Eval Flywheel

LLM eval 工具集，用于评估 intelligent_customer 客服机器人的回复质量。基于 [llm-eval](https://www.decodingai.com/p/llm-evaluation-framework) + [rag-eval](https://www.decodingai.com/p/rag-evaluation-metrics) 6-stage flywheel 框架。

## 当前状态

第一圈 flywheel 已完成：

| Stage | 状态 | 产出 |
|-------|------|------|
| 1 Strategy | ✅ | 评估维度：answer_relevance + faithfulness |
| 2 Dataset | ✅ | 9 条 questions，9 条人工标注 |
| 3 Fix & Grow | ⏸ 暂缓 | 已知 bug：风控误拦截、检索未命中 |
| 4 Evaluator | ✅ | 2 个 LLM judge 维度 |
| 5 Validate | ✅ | F1 实时显示在 Judge UI |
| 6 Production | 🔜 | 待接入生产系统 |

**下一步**：在 `data/questions.jsonl` 加更多问题（尤其是 pass 类），扩充 dataset 到 30+ 条，让 F1 指标有统计意义。

---

## 快速开始

### 1. 配置环境

```bash
cp .env.example .env
# 填入以下字段：
# OPENAI_API_KEY / OPENAI_BASE_URL  — LLM judge 调用
# WORKFLOW_API_BASE_URL / WORKFLOW_API_KEY 等 — trace 采集
```

### 2. 安装依赖

```bash
uv sync
```

### 3. 重新生成 traces（首次或问题集更新后）

```bash
uv run python -m eval.collectors.workflow_collector \
    data/questions.jsonl data/traces.jsonl
```

---

## Stage 2 — 采集 & 标注

**目标**：对每条 trace 打上 Pass/Fail 标签 + 失败原因。

```bash
# 启动标注 & Judge UI（替换 yourname）
uv run python -m eval.web --port 5000 --annotator yourname
# 标注 UI: http://127.0.0.1:5000/
# Judge UI: http://127.0.0.1:5000/judge
```

操作：
- `←` / `→` 切换 trace
- 选择 Pass / Fail，Fail 必须填 failure_category 和 critique
- `Enter` 保存，自动跳下一条

标注结果追加到 `data/dataset.jsonl`（append-only，同一 trace 重复标注以最后一条为准）。

**添加新问题**：直接编辑 `data/questions.jsonl`，参考现有格式，然后重跑 collector。

---

## Stage 4 — 运行 LLM Judge

**目标**：用 LLM 自动评判每条 trace，对比人工标注。

```bash
# 启动 Judge UI
uv run python -m eval.web --port 5000
# Judge UI: http://127.0.0.1:5000/judge
```

操作：
- `Enter` 对当前 trace 运行 judge
- `R` 批量运行所有未 judge 的 trace
- 模型下拉框切换 judge 模型

Judge 维度：
- **answer_relevance (A|Q)**：回复是否直接回答了用户的问题
- **faithfulness (A|C)**：回复声明是否有可追溯的检索文档依据

结果追加到 `data/judge_results.jsonl`。

---

## Stage 5 — 验证指标

在 Judge UI 的 strip 下方实时显示：

```
运行 9/9 | TP:7  TN:2  FP:0  FN:0 | Precision:100%  Recall:100%  F1:100%
```

- **TP**：judge=fail，human=fail（正确命中）
- **FP**：judge=fail，human=pass（误报）
- **FN**：judge=pass，human=fail（漏报，最危险）
- **F1 ≥ 70%**：judge 可信，绿色；50-70% 橙色；< 50% 红色

F1 低时：回到 `eval/judges.py`，更新对应 judge 的 few-shot 例子，重新 Run All。

---

## 数据文件说明

| 文件 | 性质 | 说明 |
|------|------|------|
| `data/questions.jsonl` | **人工维护，已提交** | 测试问题集，加问题直接编辑此文件 |
| `data/dataset.jsonl` | **人工维护，已提交** | 人工标注结果，标注 UI 追加写入 |
| `data/traces.jsonl` | 生成产物，gitignore | 运行 collector 重新生成 |
| `data/judge_results.jsonl` | 生成产物，gitignore | 运行 judge 重新生成 |

---

## 开发

```bash
# 跑单元测试
uv run pytest tests/ -m "not integration" -q

# 跑集成测试（需要真实 API key）
uv run pytest tests/ -m integration -v
```

新增 judge 维度：在 `eval/judges.py` 参照 `judge_faithfulness` 添加新函数，在 `run_all_judges` 里注册，集成测试先写。
