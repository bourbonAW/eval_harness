# Spec: /collect 页面 — Trace 采集 UI

**日期**: 2026-05-21  
**状态**: 待实现

---

## 背景

`workflow_collector` 是 eval flywheel 的第一步，目前只能在终端运行：

```bash
uv run python -m eval.collectors.workflow_collector data/questions.jsonl data/traces.jsonl
```

采集耗时 1-3 分钟，用户需要切换到终端触发并等待。目标是把这个操作集成进 web UI，让用户不离开浏览器就能完成 flywheel 的全部步骤（Collect → 标注 → Judge）。

---

## 设计决策

### 页面独立性
新建 `/collect` 页面作为独立一级页面，不嵌入现有页面。飞轮顺序：`/collect` → `/` → `/judge`，对应 Collect / 标注 / Judge 三态 FAB。

### 后端架构：后台线程 + 轮询
`workflow_collector` 耗时 1-3 分钟，Flask 默认 `threaded=True`，采用：
- `POST /api/collect` — 在后台线程运行 collector，立即返回 `{"status": "started"}`
- `GET /api/collect/status` — 返回当前状态 `{status, message, elapsed_s}`
- 状态机：`idle → running → success | error`
- 同一时刻只允许一个采集任务运行（并发保护）

不使用 SSE，不需要逐行实时输出，只需感知"正在运行"。

---

## 后端实现（`eval/web.py`）

### 为什么直接调用 collect_all() 而非 subprocess
`workflow_collector.__main__` 在部分题目失败时仍返回 returncode 0，无法通过 subprocess 检测；且 `open(output_path, "w")` 会立即清空文件，中途崩溃则数据丢失。直接在线程里调用 `collect_all()` 可以：
- 拿到结构化返回值 `{"succeeded": N, "failed": [...]}`
- 写临时文件，成功/warning 后再原子 `replace()`
- 方便 mock 测试，无时序竞态

### 状态存储
在 `create_app()` 内用闭包维护状态 dict（非持久化，服务重启归 idle）：

```python
_collect_state = {"status": "idle", "message": "", "elapsed_s": 0, "succeeded": 0, "failed": []}
_collect_lock  = threading.Lock()
```

### 新增路由

**`POST /api/collect`**  
1. 持锁检查 status；若 == "running" 返回 409 `{"error": "already running"}`  
2. **在主请求线程内（持锁）** 同步设 status = "running"，再 `Thread.start()`  
   （避免两个并发 POST 都通过检查的竞态）  
3. 后台线程逻辑：
   - 写临时文件 `traces_path.with_suffix(".tmp")`
   - 调用 `collect_all(questions_path, tmp_path)`
   - 成功或部分失败（warning）：`tmp_path.replace(traces_path)`；纯失败（succeeded == 0）：删除 tmp
   - 落终态：`"success"` / `"warning"` / `"error"`

**`GET /api/collect/status`**  
返回 `_collect_state` 快照：`{status, message, elapsed_s, succeeded, failed}`

### 部分失败行为（Warning）
| 情形 | status | 行为 |
|------|--------|------|
| 全部成功 | `"success"` | 替换 traces.jsonl，绿色显示「成功采集 N 条」 |
| 部分成功 | `"warning"` | 替换 traces.jsonl，黄色显示「成功 M / 失败 K」 |
| 全部失败 | `"error"` | 不替换旧文件，红色显示失败 ID 列表 |

### 侧边栏数据
`GET /api/collect/info` — 返回 questions 数量和 traces 数量（读 JSONL 行数）。

---

## 前端实现（`eval/templates/collect.html`）

### 布局
两列网格，与 annotate.html / judge.html 保持一致：
- 左：主内容区（路径行 + 采集按钮 + 状态区）
- 右：侧边栏（问题数 / 已有 traces 数统计卡）

### 五个 UI 状态

| 状态 | 按钮样式 | 状态区颜色 | 显示内容 |
|------|---------|-----------|---------|
| `idle` | 蓝色「▶ 开始采集」 | 灰色斜体 | 「等待触发…」 |
| `running` | 浅蓝禁用 + spinner | 蓝色 | 「采集中，请稍候（约 1-3 分钟）」 |
| `success` | 绿色「✓ 采集完成」 | 绿色 | 「成功采集 N 条 traces · 耗时 Xs」 |
| `warning` | 橙色「⚠ 部分完成」 | 橙色 | 「成功 M / 失败 K · 已写入 traces.jsonl」+ 失败 ID 列表 |
| `error` | 蓝色恢复「▶ 重新采集」 | 红色 | 「采集失败：全部 N 题未采到，旧文件未修改」 |

### 轮询逻辑
```js
// 页面初始化：先读一次状态（处理刷新/已有任务运行的情形）
async function init() {
  const s = await fetch('/api/collect/status').then(r => r.json())
  renderState(s)
  if (s.status === 'running') startPolling()  // 已在跑，直接接上轮询
  await refreshInfo()                          // 刷新侧边栏统计
}

// 触发采集
async function startCollect() { ... POST /api/collect ... startPolling() }

// 轮询
function startPolling() {
  pollTimer = setInterval(async () => {
    const s = await fetch('/api/collect/status').then(r => r.json())
    renderState(s)
    if (s.status !== 'running') {
      clearInterval(pollTimer)
      await refreshInfo()  // 采集完成后刷新侧边栏 trace 数
    }
  }, 3000)
}
```

### FAB 三态
`collect.html` 中 FAB 显示「**Collect**（蓝色）| 标注 → | Judge →」。  
同步更新 `annotate.html` 和 `judge.html` 的 FAB 为三态：「← Collect | **当前页** | 另一页 →」

---

## CSS 约定
- 复用现有 CSS 变量（`--blue`, `--pass`, `--fail`, `--border`, `--surface`…）
- spinner 动画复制 judge.html 已有的 `@keyframes spin` 写法（保持一致）
- `.collect-btn` / `.status-area` 新增 class，不影响现有样式

---

## 不在范围内
- 不支持从 UI 修改 questions.jsonl 路径（硬编码读 `app.config["QUESTIONS_PATH"]`）
- 不支持同时多个采集任务
- 不显示逐 trace 进度（只显示总体状态）
- 不持久化采集历史（服务重启后状态归 idle）

---

## 验证

```bash
# 单元测试（mock subprocess，不调真实 API）
uv run pytest tests/test_web.py -v -k "collect"

# 手动测试
uv run python -m eval.web --port 5000 --annotator test
# 1. 访问 http://127.0.0.1:5000/collect
# 2. 点击「开始采集」→ 按钮变蓝禁用 + spinner
# 3. 等待完成 → 变绿 + 显示 trace 数
# 4. 测试 FAB 三态跳转：Collect ↔ 标注 ↔ Judge
# 5. 测试并发保护：快速点两次，第二次应提示「已在运行中」
```
