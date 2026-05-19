# HTML 标注工具设计

> 把现有终端 CLI（`eval/annotate.py`）的标注体验迁移到本地浏览器表单，覆盖 Stage 2 的 9 条 traces 标注与后续回看修改。

## 目标 & 非目标

**目标**
- 替换 CLI 的交互体验：键盘+鼠标皆可，看得到全局进度，能随意跳转、回看修改。
- 复用现有 `data/*.jsonl` 数据流与 schema，不引入数据库、不引入构建链。
- 与现有 CLI 共存，共享底层加载/保存函数。

**非目标**
- 不做多用户协同、多人冲突解决。
- 不做远程部署、不做鉴权（本地 127.0.0.1 only）。
- 不引入前端构建工具（React/Vue/Webpack）。HTML+CSS+原生 JS。
- 不做错误聚类/统计看板（属于 Stage 3）。

## 关键决策（已与用户对齐）

| 项 | 决策 |
|---|---|
| 范围 | 替换 CLI 体验 + 增强（进度、回看、快捷键、context 折叠、自动跳下一条） |
| 后端 | Flask + 单 HTML 模板（`templates/annotate.html`） |
| CLI 命运 | 保留并存，共享底层函数 |
| 布局 | 顶部状态色标签栏 + 单条详情页 |
| 持久化 | append-only；可改；按 id 取最新版本回显 |

## 架构

```
浏览器（单 HTML 文件，HTML+CSS+原生 JS）
        ↓ fetch /api/...
Flask app（eval/annotate_web.py）
        ↓ 读 traces.jsonl/questions.jsonl，append dataset.jsonl
data/*.jsonl
```

单进程、单用户、本地。`/api/traces` 启动时一次性返回全部 9 条（含最新标注），前端缓存，POST 成功后本地 merge，不再重新拉取。

## 文件结构

```
eval/
├── annotate.py            # 现有 CLI，保留；新增 load_latest_annotations()
├── annotate_web.py        # 新：Flask app + __main__ 入口
└── templates/
    └── annotate.html      # 新：单文件 HTML（HTML+CSS+JS 全在里面）
tests/
└── test_annotate_web.py   # 新：app.test_client() 路由测试
pyproject.toml             # 新增依赖 flask>=3.0
```

## 复用的现有函数（`eval/annotate.py`）

无需改动：
- `load_jsonl(path)` — 跳过 malformed 行
- `save_annotation(sample, dataset_path)` — append 到 jsonl
- `_CATEGORY_LABELS` / `_CATEGORIES` — 失败类型清单

新增（放在 `eval/annotate.py`，供 web 端复用）：
- `load_latest_annotations(dataset_path: Path) -> dict[str, AnnotatedSample]`：append-only 下按 `id` 取最后一条版本（后写覆盖前写）。

## HTTP API

| 方法 | 路径 | 入参 | 返回 |
|---|---|---|---|
| GET | `/` | — | 主 HTML 页面（渲染 `annotate.html`） |
| GET | `/api/traces` | — | `[{trace, question, latest_annotation \| null}, ...]` |
| POST | `/api/annotate` | `{trace_id, label, critique, failure_category}` | `200 {ok: true, annotation}` / `400 {error}` / `404 {error}` |

**设计约束**
- `annotator` 名字从启动参数 `--annotator` 读取，不放在 POST body 里。
- `annotated_at` 由服务端打 UTC ISO 戳，不信任前端时间。
- `/api/traces` 的响应里，每个对象同时挂上对应 `question` 的 `expected_answer`，前端不再二次关联。

### `/api/traces` 响应结构

```json
[
  {
    "trace": { /* Trace 全部字段 */ },
    "expected_answer": "……",
    "latest_annotation": null | { /* AnnotatedSample 全部字段 */ }
  }
]
```

### `/api/annotate` 校验规则

- `trace_id` 必须在 `traces.jsonl` 中，否则 `404`。
- `label ∈ {"pass", "fail", "skip"}`，否则 `400`。
- 当 `label == "fail"`：
  - `critique` strip 后非空，否则 `400 "fail 必须填写 critique"`。
  - `failure_category ∈ _CATEGORIES`，否则 `400 "fail 必须选择 failure_category"`。
- `skip` 与 `pass` 允许 `critique` 与 `failure_category` 为空。

## UI 布局

```
┌──────────────────────────────────────────────────────────┐
│ 标注者：alice    进度：3/9 已完成                          │
│ ┌──┬──┬──┬──┬──┬──┬──┬──┬──┐                              │
│ │q1│q2│q3│q4│q5│q6│q7│q8│q9│   状态色见下方                │
│ └──┴──┴──┴──┴──┴──┴──┴──┴──┘                              │
├──────────────────────────────────────────────────────────┤
│ 问题：……（蓝框）                                          │
│ ▸ 检索 query（folded）                                     │
│ ▸ 对话历史（folded，如有）                                 │
│ ▸ 文档 Context (doc_str)（folded）                         │
│ ▸ FAQ Context (faq_str)（folded）                          │
│ 引用：[1] 名称 → url   [2] ……                              │
│ Bot 实际回复：……（绿框）                                   │
│ 参考答案：……（淡色）                                       │
├──────────────────────────────────────────────────────────┤
│ [ Pass ]  [ Fail ]  [ Skip ]                                │
│   （选 Fail 时下面展开）                                    │
│     critique 文本框                                         │
│     失败类型：○幻觉 ○检索偏题 ○拒答失败 ○引用错误            │
│                ○回答不完整 ○答非所问 ○其他                  │
│ [保存 ↵]  [上一条 ←]  [下一条 →]                            │
└──────────────────────────────────────────────────────────┘
```

**状态色**：灰=未标 / 绿=pass / 红=fail / 黄=skip。当前 trace 高亮边框。

**键盘快捷键**
- `1` = Pass，`2` = Fail，`3` = Skip（focus 不在 input/textarea 时生效）
- `←` / `→` 切换上一/下一条
- `Enter` 保存（不在 critique textarea 时）；critique 框内 `Enter` 是换行，`Ctrl+Enter` 保存
- 保存成功后自动跳到下一个"未标注" trace；全部标完则停在当前条并提示

**回看修改**：点已标注的标签会回到该 trace，表单回显最新标注，可改可重新保存（视为新版本 append）。

## 数据流

### 切换 trace 时（包含初次进入页面）

前端根据当前 trace 的 `latest_annotation` 回填表单：
- 若为 null（未标注）：label 未选中，critique 空，failure_category 未选。
- 若非 null：按其 `label`/`critique`/`failure_category` 回填，方便回看与修改。

### 保存一次标注

1. 用户选 label（点按钮或按 1/2/3）。
2. fail 时填 critique + 选 failure_category。
3. 用户保存（点按钮或 Enter/Ctrl+Enter）→ JS `POST /api/annotate`。
4. Flask 校验 → 失败返回 400 / 404。
5. Flask 拼 `AnnotatedSample`：merge trace 全字段 + `question.expected_answer` + 表单字段 + `annotated_by`（启动参数）+ `annotated_at`（服务端 UTC ISO）。
6. `save_annotation` append 到 `dataset.jsonl`。
7. 返回 `{ok: true, annotation}`。
8. JS 更新本地缓存：当前 trace 状态色变化、进度计数仅在"之前未标注"时 +1，自动跳到下一个未标注 trace。

## 错误处理

- Flask 4xx 返回 `{"error": "<msg>"}`；JS 用 toast 显示红色提示；critique 缺失时输入框加红框并 focus。
- 写文件 IO 异常 → 500 + 错误信息，不吞错。
- `dataset.jsonl` 末行损坏：复用 `load_jsonl` 既有的跳过逻辑，不重复实现。
- 单用户场景，**不加文件锁**；POSIX 下 < 4KB append 原子，9 条数据足够安全。

## 启动 & 配置

```bash
uv run python -m eval.annotate_web --annotator alice [--port 5000]
```

- 启动时 print `http://localhost:5000`，用户手动打开浏览器。
- 不自动开浏览器（环境差异大）。
- `host=127.0.0.1`，`debug=False`，仅本地访问。
- `--annotator` 必填；缺省报错退出。
- 数据文件路径与 CLI 保持一致写死：`data/traces.jsonl` / `data/questions.jsonl` / `data/dataset.jsonl`，不暴露成 CLI flag（YAGNI）。

## 依赖变化

- `pyproject.toml` 添加 `flask>=3.0`。
- `uv sync` 更新 `uv.lock`。
- 无前端构建工具，无 Node.js。

## 测试策略

`tests/test_annotate_web.py`，用 `app.test_client()` + `tmp_path` 隔离数据。

| 测试 | 覆盖 |
|---|---|
| `test_load_latest_annotations_picks_last_version` | 同 id 多条 append 时返回最新（append-only 关键语义） |
| `test_load_latest_annotations_ignores_unannotated` | 没标注的 trace 不出现在 dict |
| `test_get_traces_returns_all_with_status` | `GET /api/traces` 返回所有 trace，含 `latest_annotation` 字段 |
| `test_get_traces_merges_question_expected_answer` | trace 关联的 question 的 `expected_answer` 挂在响应里 |
| `test_post_pass_appends_dataset` | POST pass → 200，`dataset.jsonl` 多一行 |
| `test_post_fail_without_critique_returns_400` | fail + 空 critique → 400 |
| `test_post_fail_without_category_returns_400` | fail + 空 failure_category → 400 |
| `test_post_skip_allows_empty_critique` | skip 不强制 critique |
| `test_post_unknown_trace_id_returns_404` | trace_id 不存在 → 404 |
| `test_post_overwrites_via_append` | 同 id 二次提交 → dataset 多一行，`/api/traces` 的 latest 是新版本 |
| `test_post_invalid_label_returns_400` | label 不在白名单 → 400 |
| `test_annotated_at_is_server_timestamp` | 前端传入的时间字段被忽略，服务端打戳 |

**HTML 本身不写自动化测试**：Selenium 维护成本高于收益，9 条数据手测一次足够。

## Out of Scope（明确不做）

- 错误聚类、Pass Rate 统计看板、按 failure_category 筛选 — Stage 3。
- LLM judges、自动评分 — Stage 4。
- Langfuse / 在线监控接入 — Stage 6。
- 多用户协同、鉴权、远程部署。
- HTML E2E 自动化测试。

## 受 superpowers 启发的部分

- **零前端构建链**：HTML+CSS+原生 JS 全在一个 `.html` 里，借鉴 superpowers visual-companion 的"单文件 HTML"哲学。
- **可放弃的部分**：visual-companion 的"newest file wins + 事件流"模型适合 Claude 单向推屏，不适合需要持久化与回看修改的标注场景，因此不采用。
