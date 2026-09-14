# Actions Scheduler —— 你自己的「免费青龙」

一个 workflow + 一个 `scripts/` 目录，就是你的私人定时任务调度器。
**加任务 = 丢个脚本文件**，不用为每个任务单独写 yml，也不会被上游仓库同步覆盖。

---

## 目录结构

```
.
├── .github/workflows/scheduler.yml   # 调度中心（改时间、配 Secrets 注入）
├── scripts/
│   ├── run.py                        # 调度引擎（读 tasks.json 分发任务）
│   └── demo.py                       # 示例任务（照抄改逻辑）
├── tasks.json                        # 任务清单（加/停任务都在这）
├── requirements.txt                  # Python 依赖
└── package.json                      # Node 依赖（可选）
```

---

## 快速开始（3 步）

### 1. 建仓库并推上去

```bash
git init
git add .
git commit -m "init: actions scheduler"
git branch -M main
git remote add origin https://github.com/<你的用户名>/<仓库名>.git
git push -u origin main
```

> ⚠️ **必须用 Public 仓库**：私有仓库每月只有 2000 分钟免费额度，容易爆。

### 2. 启用 Actions

推上去后，打开仓库 **Actions** 标签页 → 点绿色按钮 **"I understand my workflows, go ahead and enable them"**。

然后手动跑一次验证：**Actions → Scheduler → Run workflow**。

### 3. 配置 Secrets（可选）

**Settings → Secrets and variables → Actions → New repository secret**

| Secret 名 | 用途 |
|---|---|
| `DEMO_TOKEN` | 示例用，换成你自己的 |
| `TG_BOT_TOKEN` | Telegram 机器人 Token |
| `TG_CHAT_ID` | Telegram 聊天 ID |
| `SERVERCHAN_KEY` | Server酱 SendKey |

> **没配的 Secret 会是空字符串**，脚本里用 `ctx.get()` 判空跳过即可，不会报错。

---

## 日常使用

### 加一个新任务

1. 在 `scripts/` 建 `my_task.py`：

```python
def run(ctx):
    # 读配置（优先 tasks.json 的 env，其次 Secrets，最后默认值）
    token = ctx.require("MY_TOKEN")        # 必填，缺失直接报错

    # 带重试的请求
    status, body = ctx.http_get("https://example.com/api")
    ctx.log(f"状态 {status}")

    # 追加到汇总通知
    ctx.notify("今天签到成功")

    return "任务完成"                        # 返回 = 成功；抛异常 = 失败
```

2. 在 `tasks.json` 的 `tasks` 数组里加一段：

```json
{
  "name": "my-task",
  "enabled": true,
  "type": "python",
  "module": "my_task",
  "entry": "run",
  "env": { "MY_TOKEN": "" },
  "notify": ["telegram"]
}
```

### 停用一个任务

`tasks.json` 里把 `"enabled"` 改成 `false`。

### 贴别人的脚本（不想改写成 Python）

```json
{
  "name": "legacy",
  "enabled": true,
  "type": "shell",
  "command": "python scripts/some_legacy_script.py",
  "notify": ["console"]
}
```

### 改执行时间

编辑 `.github/workflows/scheduler.yml` 的 cron：

| 北京时间 | cron |
|---|---|
| 每天 08:00 | `0 0 * * *` |
| 每天 09:00 | `0 1 * * *` |
| 每 6 小时 | `0 */6 * * *` |
| 每 30 分钟 | `*/30 * * * *` |

> ⚠️ **cron 只认 UTC**（北京时间 - 8）。**最小间隔 5 分钟**，`* * * * *` 无效。
> 高峰期可能延迟 15~30 分钟，别把时间卡在整点。

### 本地调试

```bash
pip install -r requirements.txt
python scripts/run.py              # 跑全部启用的任务
python scripts/run.py --task demo  # 只跑 demo
```

---

## Ctx API 速查

| 方法 | 说明 |
|---|---|
| `ctx.log(*args)` | 打日志 |
| `ctx.get(key, default="")` | 读配置（env → Secrets → 默认值） |
| `ctx.require(key)` | 读必填配置，缺失抛错 |
| `ctx.http_get/post(url, ...)` | 带重试的 HTTP，返回 `(status, body)` |
| `ctx.notify(text)` | 追加到汇总通知 |

---

## 内置的安全设计

| 机制 | 作用 |
|---|---|
| `timeout-minutes: 20` | 防挂机被判定滥用，也防脚本卡死烧额度 |
| 失败非 0 退出 | Actions 标红 + GitHub 自动发邮件提醒 |
| 保活提交 | 自动 commit 时间戳，防止 **60 天无活动被停用** |
| 重试 + 退避 | 网络抖动自动重试，4xx（除 429）不无脑重试 |
| Secrets 隔离 | 密钥不落代码、不进日志 |

---

## 避坑清单

- ❌ **别挖矿、别搭代理** → 秒封号，无商量
- ❌ **别开太密** → 5 分钟是下限，别搞秒级
- ❌ **别批量建仓库刷** → 一个仓库搞定
- ✅ **用公开仓库** → 私有额度有限
- ✅ **日志别打印敏感信息** → 公开仓库的日志人人可见

---

## 保活说明

GitHub 规定：**仓库 60 天无活动，定时任务自动停用**。
本项目的 workflow 每次运行都会提交 `.state/.last-run`，只要任务在跑就不会休眠。

如果你把所有任务都停用了（不会有提交），需要自己定期 push 一次，或保留一个轻量任务。
