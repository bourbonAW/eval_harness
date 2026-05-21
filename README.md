# intelligent-customer Eval Flywheel

LLM eval 工具集，用于评估 `intelligent_customer` 客服机器人的回复质量。项目围绕 llm-eval + rag-eval 的 6-stage flywheel 搭建，当前重点是把问题集、trace 采集、人工标注、LLM judge 和指标验证串成一个可重复迭代的本地工作流。

## 当前状态

第一圈 flywheel 的 Stage 1-5 已跑通，Stage 6 生产接入暂缓。

| Stage | 状态 | 产出 |
|-------|------|------|
| 1 Strategy | 完成 | 评估维度：`answer_relevance` + `faithfulness` |
| 2 Dataset | 完成，待扩充 | `questions.jsonl`、`traces.jsonl`、`dataset.jsonl` |
| 3 Fix & Grow | 暂缓 | 已知 workflow 问题先记录，不阻塞 eval 工具 |
| 4 Evaluator | 完成 | 2 个 LLM judge 维度 |
| 5 Validate | 完成 | Judge UI 实时显示 TP/FP/FN/TN + Precision/Recall/F1 |
| 6 Production | 待办 | 待接入生产系统 |

当前问题集是 9 条小样本，下一步仍是扩充到 30+ 条，尤其补充 pass 类样本，让 F1 更有统计意义。

## 快速开始

### 1. 配置环境

```bash
cp .env.example .env
```

采集 traces 需要配置 workflow 平台：

```dotenv
WORKFLOW_API_BASE_URL=https://your-host
WORKFLOW_API_KEY=Bearer ...
WORKFLOW_SESSION_ID=your-session-id
WORKFLOW_CHANNEL_ID=1
```

运行 LLM judge 需要配置至少一种模型后端：

```dotenv
# OpenAI-compatible backend
OPENAI_API_KEY=sk-...
OPENAI_BASE_URL=https://api.openai.com/v1

# Claude models only
ANTHROPIC_API_KEY=sk-ant-...

# Model selection
JUDGE_MODEL=mimo-v2.5-pro
RUBRIC_SUGGEST_MODEL=mimo-v2.5-pro  # optional; defaults to JUDGE_MODEL
```

### 2. 安装依赖

```bash
uv sync
```

### 3. 启动 Web UI

```bash
uv run python -m eval.web --port 5000 --annotator <yourname>
```

启动后会打印三个入口：

| 页面 | URL | 用途 |
|------|-----|------|
| Collect | `http://127.0.0.1:5000/collect` | 管理问题集并采集 traces |
| Annotate | `http://127.0.0.1:5000/` | 人工标注 pass/fail/skip |
| Judge | `http://127.0.0.1:5000/judge` | 运行 LLM judge 并查看验证指标 |

未传 `--annotator` 时，新标注的 `annotated_by` 会写成 `unknown`。

## 常用工作流

### 1. 管理问题集

打开 `/collect`，切到「问题集」tab，可以查看、新增、编辑、删除 `data/questions.jsonl` 中的问题。

当前 UI 只编辑两个字段：

- `question`
- `expected_answer`

新增问题会自动生成下一个 `q_###` ID，其余字段使用默认值；编辑已有问题时只修改 `question` 和 `expected_answer`，其他元数据保持不变。

也可以从 Excel 重新导入问题集：

```bash
uv run python -m eval.importers.excel_importer data/questions.xlsx data/questions.jsonl
```

### 2. 采集 traces

在 `/collect` 的「采集」tab 点击采集按钮，后端会调用 workflow collector，并把结果写入 `data/traces.jsonl`。

采集策略：

- 每次采集重写 `data/traces.jsonl`，不是追加。
- 单题失败不会中断整批采集；成功部分会写入，失败 ID 会在 UI 中显示。
- 如果全部题目采集失败，旧的 `data/traces.jsonl` 不会被替换。

也可以用 CLI 采集：

```bash
uv run python -m eval.collectors.workflow_collector \
  data/questions.jsonl data/traces.jsonl
```

### 3. 人工标注

打开 `/` 进入 Annotate UI。

操作约定：

- `←` / `→` 切换 trace
- `Pass` / `Fail` / `Skip` 标注当前 trace
- `Fail` 必须填写 `failure_category` 和 `critique`
- `Enter` 保存并跳到下一条

标注结果追加到 `data/dataset.jsonl`。该文件是 append-only，同一 trace 重复标注时读取端采用 last-wins。

### 4. 运行 LLM Judge

打开 `/judge` 进入 Judge UI。

操作约定：

- 对当前 trace 运行 judge
- 批量运行所有未 judge 的 trace
- 用模型下拉框切换 judge 模型

Judge 维度：

| 维度 | 关系 | 含义 |
|------|------|------|
| `answer_relevance` | A\|Q | 回复是否直接回答了用户问题 |
| `faithfulness` | A\|C | 回复声明是否有可追溯的检索文档依据 |

结果追加到 `data/judge_results.jsonl`。该文件是运行产物，不提交。

### 5. 验证指标

Judge UI 会实时显示：

```text
运行 9/9 | TP:7  TN:2  FP:0  FN:0 | Precision:100%  Recall:100%  F1:100%
```

- `TP`：judge=fail，human=fail
- `FP`：judge=fail，human=pass
- `FN`：judge=pass，human=fail，最危险
- `TN`：judge=pass，human=pass

F1 低于 70% 时，优先回到 `eval/judges.py` 调整对应 judge 的 few-shot 例子，然后重新 Run All。

## 数据文件

| 文件 | 管理方式 | 说明 |
|------|---------|------|
| `data/questions.jsonl` | 人工维护，提交 | 问题集，Collect UI 会读写 |
| `data/dataset.jsonl` | 人工维护，提交 | 人工标注 gold standard，append-only |
| `data/questions.xlsx` | 人工维护，提交 | Excel 问题源，可重新导入 JSONL |
| `data/traces.jsonl` | 生成产物，不提交 | collector 输出，可重新生成 |
| `data/judge_results.jsonl` | 生成产物，不提交 | judge 输出，可重新生成 |

## 开发

```bash
# 单元测试
uv run pytest tests/ -m "not integration" -q

# 集成测试，会调用真实 API
uv run pytest tests/ -m integration -v
```

开发约定：

- Python 环境、脚本、测试统一使用 `uv`。
- 新功能和 bugfix 先写测试，再实现。
- 新增 judge 维度时，在 `eval/judges.py` 参照 `judge_faithfulness` 添加函数，并在 `run_all_judges` 中注册。
- `data/questions.jsonl` 和 `data/dataset.jsonl` 是业务资产；修改它们前确认这是本次任务目标。
