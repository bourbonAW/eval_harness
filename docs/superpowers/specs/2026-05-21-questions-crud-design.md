# Spec: /collect 页面问题集管理（CRUD）

**日期**: 2026-05-21  
**状态**: 待实现

---

## 背景

`/collect` 页面目前只能触发 trace 采集，无法查看或修改 `data/questions.jsonl`。用户需要在终端手动编辑问题集，再回到浏览器触发采集，打断了操作流。

目标：在 `/collect` 页面内支持问题集的查看、新增、编辑、删除，让用户不离开浏览器完成整个 flywheel 前置准备。

---

## 设计决策摘要

| 决策点 | 选择 | 理由 |
|--------|------|------|
| 布局 | 双 Tab（采集 / 问题集） | 问题集 tab 独占主内容区，不挤压采集面板 |
| 编辑模式 | 行内展开（Accordion） | 不离开列表，上下文清晰，适合快速小改 |
| 表单字段 | 精简：`question` + `expected_answer` | 其余字段保持默认值，降低操作负担 |
| 删除确认 | 行内二次确认 | 低成本防误删；`questions.jsonl` 是 git 管理可恢复 |

---

## 前端设计（collect.html）

### Tab 切换

在页头 `.header` 区域增加 Tab 切换器，切换「采集」和「问题集」两个视图。两个 Tab 共用同一页面实例，通过 JS 切换 `display`。

```
[Eval · Collect]  [采集]  [问题集]
```

### 问题集 Tab 布局

```
┌─────────────────────────────────────────────┐
│  [+ 添加问题]          问题集 · N 条          │
├─────────────────────────────────────────────┤
│  q_001  省级企业技术中心…    [编辑] [删]       │
│  ▼ 行内表单（展开中）                          │
│    问题:     [___________________________]   │
│    期望回答: [___________________________]   │
│    [保存]  [取消]                             │
├─────────────────────────────────────────────┤
│  q_002  知识产权要求？       [编辑] [删]       │
│  q_003  申报条件要求？  [确认删除？ [✓] [✗]]  │
└─────────────────────────────────────────────┘
```

### 交互规则

- **编辑展开**：点「编辑」后，该行下方展开 2 字段表单（`question` / `expected_answer`），预填当前值。同一时刻只允许一条处于展开状态；点另一行「编辑」会先收起当前行。
- **新增问题**：点「+ 添加问题」在列表顶部插入一条空白行内表单（`question` 和 `expected_answer` 均为空），保存后新问题追加到列表末尾。
- **删除确认**：点「删」后该行文字变为「确认删除？ [✓] [✗]」；点 ✓ 执行删除，点 ✗ 恢复原状。
- **切换 Tab**：切换到「采集」Tab 时，若有未保存的行内表单，直接丢弃（不提示）。
- **页面加载**：默认显示「采集」Tab；JS 初始化时调用 `loadQuestions()` 预加载数据（后台），切换到「问题集」Tab 时立即可用。

### 侧边栏

不变。侧边栏的「问题数」统计卡在问题集增删后需调用 `refreshInfo()` 更新。

---

## 后端设计（eval/web.py）

### 新增路由（4 条）

**`GET /api/questions`**  
返回 `questions.jsonl` 全量列表（数组）。文件不存在时返回 `[]`。

**`POST /api/questions`**  
新增一条问题。请求体：
```json
{ "question": "...", "expected_answer": "..." }
```
- 校验 `question` 和 `expected_answer` 不为空
- 自动生成 `id`：读取现有最大 `q_NNN` 编号，+1，格式为 `q_XXX`（3 位补零）。若无符合格式的 ID，从 `q_001` 开始。
- 其余字段写入默认值（见下方）
- 追加写入 `questions.jsonl`（全量覆盖）
- 返回新建的完整 question 对象，状态码 201

**`PUT /api/questions/<id>`**  
更新指定 ID 的 `question` 和 `expected_answer`。请求体：
```json
{ "question": "...", "expected_answer": "..." }
```
- 若 `id` 不存在，返回 404
- 校验两字段不为空
- 仅修改 `question` 和 `expected_answer`，其余字段原样保留
- 全量覆盖写入 `questions.jsonl`
- 返回更新后的完整 question 对象

**`DELETE /api/questions/<id>`**  
删除指定 ID。
- 若 `id` 不存在，返回 404
- 全量覆盖写入（去掉该条）
- 返回 `{"ok": true}`

### 新增问题的默认字段值

```python
DEFAULT_QUESTION_FIELDS = {
    "source_policy_url": "",
    "source_doc_url": "",
    "source_doc_name": "",
    "is_multi_intent": False,
    "knowledge_type": "文档",
    "is_prohibited": False,
    "conversation_history": [],
    "notes": "",
}
```

### 并发保护

新增 `_questions_lock = threading.Lock()`，所有写操作（POST / PUT / DELETE）持锁。  
与 `_collect_lock` 独立，互不干扰。但 collector 运行期间不阻止问题集编辑（两者操作不同文件）。

### 文件读写工具函数

```python
def _load_questions(path: Path) -> list[dict]:
    return load_jsonl(path)  # 复用已有函数

def _save_questions(questions: list[dict], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        for q in questions:
            f.write(json.dumps(q, ensure_ascii=False) + "\n")
```

---

## 测试（tests/test_web.py）

新增 8 个测试用例：

| 测试 | 验证 |
|------|------|
| `test_get_questions_returns_list` | GET /api/questions 返回数组，含 question_id 字段 |
| `test_get_questions_empty_when_no_file` | 文件不存在时返回 [] |
| `test_post_question_creates_with_auto_id` | 新增后 ID 为 q_003（接续已有 q_001/q_002） |
| `test_post_question_validates_required_fields` | question/expected_answer 为空时返回 400 |
| `test_put_question_updates_fields` | PUT 后 question/expected_answer 更新，其余字段不变 |
| `test_put_question_unknown_id_returns_404` | 不存在的 ID 返回 404 |
| `test_delete_question_removes_entry` | DELETE 后列表减少一条 |
| `test_delete_question_unknown_id_returns_404` | 不存在的 ID 返回 404 |

---

## 不在范围内

- 不支持从 UI 修改除 `question` / `expected_answer` 以外的字段
- 不支持拖拽排序
- 不支持批量删除
- 不支持 undo（git 可恢复）
- 不支持从 UI 修改 questions.jsonl 文件路径

---

## 验证

```bash
# 单元测试
uv run pytest tests/test_web.py -v -k "question"

# 手动测试
uv run python -m eval.web --port 5000 --annotator test
# 访问 http://127.0.0.1:5000/collect
# 1. 点「问题集」Tab → 显示 9 条问题
# 2. 点「编辑」→ 行内展开表单，修改后保存
# 3. 点「+ 添加问题」→ 顶部出现空白表单，填写后保存
# 4. 点「删」→ 出现行内确认，✓ 确认删除
# 5. 切换回「采集」Tab → 侧边栏问题数已更新
```
