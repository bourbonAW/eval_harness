# intelligent-customer Eval Flywheel — 项目上下文

## 项目身份

这是 intelligent_customer 客服机器人的 LLM eval 工具集。评估框架基于 llm-eval + rag-eval 6-stage flywheel，用于持续监控和改进机器人的回复质量。

## Flywheel 当前位置

**第一圈已完成。下一个推进点：扩充 dataset。**

- Stage 1-5：完成
- Stage 6（生产接入）：暂缓
- 当前瓶颈：9 条 traces 样本量太小，F1 指标统计意义不足。目标 30+ 条

## 关键设计决策（不要改变）

- **Binary labels only**：Pass / Fail，不使用 0-1 score
- **Few-shot > prompt engineering**：judge 的 few-shot 例子是最重要的信号，标注数据就是秘密武器
- **Append-only JSONL + last-wins**：`dataset.jsonl` / `judge_results.jsonl` 只追加，读取时取 trace_id 最后一条
- **TDD**：新功能先写测试（RED），再实现（GREEN）

## 常用命令

```bash
# 采集 traces（更新问题集后重跑）
uv run python -m eval.collectors.workflow_collector data/questions.jsonl data/traces.jsonl

# 标注 & Judge UI（单一服务）
uv run python -m eval.web --port 5000 --annotator <name>
# 标注 UI: http://127.0.0.1:5000/
# Judge UI: http://127.0.0.1:5000/judge

# 单元测试
uv run pytest tests/ -m "not integration" -q

# 集成测试（调真实 API）
uv run pytest tests/ -m integration -v
```

## 数据约定

| 文件 | 管理方式 | 说明 |
|------|---------|------|
| `data/questions.jsonl` | git 管理 | 人工维护的测试问题集 |
| `data/dataset.jsonl` | git 管理 | 人工标注的 gold standard |
| `data/traces.jsonl` | gitignore | collector 生成，可重新生成 |
| `data/judge_results.jsonl` | gitignore | judge 生成，可重新生成 |

## Judge 维度

| 维度 | 关系 | Few-shot 来源 |
|------|------|--------------|
| `answer_relevance` | A\|Q | fail: q_001，pass: q_003 |
| `faithfulness` | A\|C | fail: q_009，pass: q_005 |

**扩充 judge**：在 `eval/judges.py` 参照 `judge_faithfulness` 添加新函数，在 `run_all_judges` 注册，集成测试先写。

## Stage 5 验证指标解读

Judge UI strip 下方实时显示 TP/FP/FN/TN + Precision/Recall/F1：
- FN（漏报）是最危险的错误：judge 说 pass 但人工说 fail
- F1 ≥ 70% judge 可信；低于此阈值回 `eval/judges.py` 改 few-shot

## 当用户说"继续推进 flywheel"时

优先建议以下顺序：
1. 在 `data/questions.jsonl` 加新问题（优先 pass 类，当前 pass:fail = 2:7，严重失衡）
2. 重跑 collector 生成新 traces
3. 启动标注 UI 标注新 traces
4. Run All judges，观察 F1 变化
5. F1 稳定在 70%+ 后，再考虑 Stage 3 Fix 或 Stage 6 生产接入

## 已知 workflow bug（Stage 3 待修复）

- q_002 / q_006：风控误拦截，bot 直接拒绝未走检索
- q_004 / q_007：检索未命中，`ref_num=0`，`doc_context` 为空
- 以上 bug 暂缓修复，不影响 eval 工具本身运行
