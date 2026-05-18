# 智能客服 Chatbot Eval Flywheel 建设计划

> Session working doc — 用于跨 session 接续工作
> 最后更新：2026-05-18

---

## 1. 背景与现状

### 项目背景
- **业务域**：政策咨询（**high-severity** 域，错答 = 误导用户 = 合规风险）
- **当前状态**：chatbot 已上线生产环境运行一段时间
- **核心痛点**：
  - 做功能升级时无法快速迭代
  - 无法正确评估 chatbot 效果
  - eval flywheel 完全缺失

### 技术栈
- **chatbot 框架**：Dify 搭建的 agentic workflow（低代码平台）
- **RAG 栈**：外部知识库 KB + BM25 + vector search（hybrid retrieval）
- **trace 存储**：Langfuse（已接入，生产 traces 持续写入）
- **重要约束**：外部 KB 不归我们控制，**无法获取 chunk 的 ground truth 相关性标签**

---

## 2. 已确立的核心策略

### 2.1 业务域定位：High-Severity（政策咨询）

| 维度 | 决策 |
|---|---|
| Precision vs Recall | **Precision 优于 Recall**（宁可拒答不要乱说） |
| 头号指标 | **`A\|C` Faithfulness**（防幻觉） |
| 次要必加 | **Answerability (`Q\|C`)** 进 Tier 2（答不了要诚实拒答） |
| 必加项 | **Citation Correctness**（用户要能追溯到政策原文） |

### 2.2 Tier 1 检索基线指标处理

- **跳过 Precision@K / Recall@K / MRR**（外部 KB 拿不到 ground truth）
- **补偿方案**：`C|Q` (Context Relevance) LLM judge 升级为**唯一**的检索健康信号源
- **长期保护**：给 `C|Q` 设漂移告警线（如周环比下降 >10% 触发预警），防止外部 KB 变更导致检索退化

### 2.3 技术架构分工（核心决策）

```
[Dify Agentic Workflow]   ← 生产运行时（黑盒，不动它）
        ↓ 自动上报
[Langfuse]                ← Trace 存储（已有）
        ↑ ↓
[独立 Python eval harness] ← 要新建的部分
   - 从 Langfuse 拉 traces
   - 跑 code checks + LLM judges
   - 把 scores 写回 Langfuse
```

**为什么 eval 系统必须建在 Dify 外面：**
1. Dify 低代码限制就是"无法快速迭代"的根因，里面跑不了 CI eval
2. Langfuse 内置 datasets / scores / annotation queues，等于免费送了大半个 flywheel
3. 三者解耦：Dify 负责"跑"，Langfuse 负责"看"，独立 harness 负责"评"

---

## 3. 6 阶段 Flywheel 路线图

```
[生产 traces (Langfuse 已有)]
      ↓
[Stage 1] 策略：决定在哪/何时/如何评估   ← 当前阶段
      ↓
[Stage 2] 数据集：采样 20-50 条真实 traces，binary Pass/Fail + critique
      ↓
[Stage 3] 修明显 bug + 失败 trace → 回归测试集
      ↓
[Stage 4] 评估器：先 code check（格式/citation），再 LLM judge
          → 核心三件套 + 1：A|C、C|Q、A|Q、Citation Correctness
      ↓
[Stage 5] 验证 judge：60/20/20 split，F1 > 0.70，至少 3 轮迭代
      ↓
[Stage 6] 上线监控 + 每周 30min 标注 session + 漂移告警
      ↑________________________________|
```

预估初始搭建：3-4 天；后续维护：~30 min/week

---

## 4. Stage 1: Eval Strategy（已设计完成）

### 4.1 三个 use case 优先级

| Use case | 解决什么痛点 | 优先级 |
|---|---|---|
| **Offline regression eval**（拿固定数据集跑） | 改 prompt/workflow 时知道有没有打破已有能力 | **P0**（直接打"无法迭代"的痛点） |
| **Online monitoring**（生产 traces 抽样打分） | 外部 KB 变更/漂移预警 | P1 |
| **CI gate**（每次改动自动跑） | 防止劣化合入 | P2 |

### 4.2 Guardrails vs Evaluators

- **Guardrails**（实时阻断）：**先不做**。塞进 Dify 成本高，且当前痛点是迭代速度而非线上事故
- **Evaluators**（异步抽样）：**全部精力放这里**

### 4.3 评估对象

- Dify workflow 的端到端输出（trace 级）
- 关键 span（retrieval 步骤、generation 步骤）

### 4.4 第一批 Judges

| Judge | 类型 | 作用 | 客服+政策场景的意义 |
|---|---|---|---|
| `A\|C` Faithfulness | LLM judge | 防幻觉 | **头号指标**，政策错答 = 合规风险 |
| `C\|Q` Context Relevance | LLM judge | 检索质量 | 外部 KB 健康的唯一信号源 |
| `A\|Q` Answer Relevance | LLM judge | 端到端 | 用户是否真被解决 |
| `Citation Correctness` | Code check | 引用准确性 | 用户能追溯到政策原文 |
| `Q\|C` Answerability | LLM judge（Stage 4 后期） | 拒答/转人工 | KB 答不了时能否优雅拒答 |

### 4.5 节奏

- 每次大改前跑 offline regression
- 每周抽样 online 监控
- CI gate 等 dataset 稳定后再加

---

## 5. 进入 Stage 2 前的开放问题（待用户回答）

1. **eval harness 项目结构**：Python 独立项目可以吗？还是塞进现有某个仓库？如果有现有仓库路径请提供。
2. **Langfuse 部署形态**：self-hosted 还是 cloud？决定 traces 拉取方式与权限模型。
3. **是否有"标准答案"沉淀**：政策 FAQ、客服 SOP、人工审核过的好回答样本？可加速 Stage 2 的标注工作。

---

## 6. 已确立的跨阶段原则（来自 llm-eval / rag-eval skill）

- **Binary labels for subjective quality**：人工标注和 LLM judge 都用 Pass/Fail + critique，不用 1-5 分
- **Few-shot examples > elaborate prompts**：judge 的 system prompt 中性即可，labeled examples 才是真信号
- **Data first, criteria second**：先看 20-50 条真实失败 trace，再定 eval 标准。绝不在真空中设计指标
- **Generic metrics 是手电筒不是成绩单**：helpfulness/BERTScore 这类只能用于排查，不能作为系统级判定
- **Tools provide infrastructure; teams own judgment**：Langfuse 给基础设施，业务质量门槛、失败分类、标注节奏要团队自己定义
- **Code evaluators 处理客观检查**：citation 格式、必含字段、tool call 校验等用 code 做，便宜快可复现
- **Errors compound in multi-turn**：agentic workflow 出错时永远找上游第一个失败点，不要看下游症状

---

## 7. 参考资源

- llm-eval skill：`~/.claude/skills/llm-eval/`
- rag-eval skill：`~/.claude/skills/rag-eval/`
- 关键参考文档：
  - `references/01-eval-strategy.md`（策略细节）
  - `references/02-dataset-building.md`（Stage 2 的下一步）
  - `references/06-rag-metrics.md`（RAG 指标实现细节）

---

## 8. 下一步动作（接续 session 时从这里开始）

1. 回答 §5 的 3 个开放问题
2. 启动 **Stage 2: Dataset Building**
   - 从 Langfuse 按时间/问题类型/是否报错分层抽 20-50 条真实 traces
   - 人工 binary 标注 Pass/Fail，每条写一句 critique 解释失败原因
   - 对 critique 做错误聚类，识别 top 3-5 失败模式
   - 失败 trace 进入回归数据集
