#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
============================================================
 任务调度入口 —— 一个入口跑所有任务
============================================================

【它干什么】
  读 tasks.json，按顺序执行启用的任务，收集结果，统一发通知，
  并把结果写到 .state/.last-result（供保活提交）。

【日常怎么用】
  1. 加任务：往 scripts/ 丢一个 .py 文件，在 tasks.json 里加一行
  2. 停任务：在 tasks.json 里把 "enabled" 改成 false
  3. 单跑一个：本地执行 python scripts/run.py --task demo
     Actions 上：点 Run workflow，在 task 输入框填任务名

【脚本约定】（两种写法任选）

  写法 A —— 函数式（推荐，零耦合）
      # scripts/my_task.py
      def run(ctx):
          ctx.log("干活中...")
          # ctx.get("KEY") 读配置/环境变量
          # ctx.http_get(url, headers=...) 带重试的请求
          # ctx.notify("这条会进汇总通知")
          return "成功信息"        # 返回字符串 = 成功；抛异常 = 失败

  写法 B —— 命令式（贴别人的脚本）
      直接在 tasks.json 里写 type: "shell"，command 填命令即可，
      比如 "python other.py" 或 "node x.js"。

【通知】
  在 tasks.json 的 notify 字段配置渠道；Secrets 里填对应密钥。
  Telegram / Server酱 / 控制台日志 三选多。
"""
import argparse
import importlib
import json
import os
import ssl
import sys
import time
import traceback
import urllib.error
import urllib.request
from datetime import datetime, timezone, timedelta

# ---------- 路径 ----------
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS_DIR = os.path.join(ROOT, "scripts")
TASKS_FILE = os.path.join(ROOT, "tasks.json")
STATE_DIR = os.path.join(ROOT, ".state")
RESULT_FILE = os.path.join(STATE_DIR, ".last-result")

# 让 python 能 import scripts 目录下的模块
sys.path.insert(0, SCRIPTS_DIR)

CST = timezone(timedelta(hours=8))  # 北京时间，日志好看点


# ============================================================
#  上下文对象：传给每个任务的 run(ctx)
# ============================================================
class Ctx:
    """任务上下文。任务脚本通过它拿配置、发请求、记日志、发通知。"""

    def __init__(self, name, config):
        self.name = name
        self.config = config
        self.messages = []      # 任务想推送的额外消息

    # ---- 配置读取 ----
    def get(self, key, default=""):
        """先查 tasks.json 的 env，再查环境变量(Secrets)，最后给默认值。"""
        if key in self.config.get("env", {}):
            return self.config["env"][key]
        return os.environ.get(key, default)

    def require(self, key):
        """必须存在的配置，没有就抛错（避免脚本静默跑错）。"""
        v = self.get(key)
        if not v:
            raise ValueError(f"缺少必要配置: {key}（请在 Secrets 或 tasks.json 中配置）")
        return v

    # ---- 日志 ----
    def log(self, *args):
        print(f"[{self.name}]", *args, flush=True)

    def notify(self, text):
        """把一条消息加入本任务的汇总通知。"""
        self.messages.append(text)

    # ---- HTTP（带重试 + 超时）----
    def http(self, url, method="GET", headers=None, data=None,
             retries=3, timeout=20):
        headers = headers or {}
        if isinstance(data, (dict, list)):
            data = json.dumps(data).encode()
            headers.setdefault("Content-Type", "application/json")
        elif isinstance(data, str):
            data = data.encode()

        last_err = None
        for i in range(1, retries + 1):
            try:
                req = urllib.request.Request(url, data=data, headers=headers,
                                             method=method)
                with urllib.request.urlopen(req, timeout=timeout) as resp:
                    body = resp.read().decode("utf-8", errors="replace")
                    return resp.status, body
            except urllib.error.HTTPError as e:
                last_err = f"HTTP {e.code}"
                # 4xx 一般不重试（除了 429 限流）
                if 400 <= e.code < 500 and e.code != 429:
                    try:
                        return e.code, e.read().decode("utf-8", errors="replace")
                    except Exception:
                        return e.code, ""
            except Exception as e:
                last_err = str(e)
            if i < retries:
                sleep = i * 2
                self.log(f"请求失败({last_err})，{sleep}s 后重试 {i}/{retries - 1}")
                time.sleep(sleep)
        raise RuntimeError(f"请求最终失败: {url} -> {last_err}")

    def http_get(self, url, **kw):
        return self.http(url, "GET", **kw)

    def http_post(self, url, **kw):
        return self.http(url, "POST", **kw)


# ============================================================
#  通知渠道
# ============================================================
def _post(url, payload, headers=None):
    """发通知用的极简 POST，失败不影响主流程。"""
    try:
        data = json.dumps(payload).encode()
        h = {"Content-Type": "application/json"}
        if headers:
            h.update(headers)
        req = urllib.request.Request(url, data=data, headers=h, method="POST")
        with urllib.request.urlopen(req, timeout=15) as r:
            return r.status == 200, r.read().decode("utf-8", "replace")
    except Exception as e:
        return False, str(e)


def notify_telegram(title, body):
    token = os.environ.get("TG_BOT_TOKEN", "")
    chat = os.environ.get("TG_CHAT_ID", "")
    if not token or not chat:
        return False, "未配置 TG_BOT_TOKEN / TG_CHAT_ID"
    text = f"*{title}*\n\n{body}"
    return _post(f"https://api.telegram.org/bot{token}/sendMessage", {
        "chat_id": chat, "text": text, "parse_mode": "Markdown",
        "disable_web_page_preview": True,
    })


def notify_serverchan(title, body):
    key = os.environ.get("SERVERCHAN_KEY", "")
    if not key:
        return False, "未配置 SERVERCHAN_KEY"
    return _post(f"https://sctapi.ftqq.com/{key}.send",
                 {"title": title, "desp": body})


def notify_console(title, body):
    print("\n" + "=" * 50)
    print(title)
    print("-" * 50)
    print(body)
    print("=" * 50 + "\n", flush=True)
    return True, "ok"


CHANNELS = {
    "telegram": notify_telegram,
    "serverchan": notify_serverchan,
    "console": notify_console,
}


def dispatch_notify(config, title, body):
    """按 tasks.json 的 notify 配置发通知；console 总是执行。"""
    names = config.get("notify") or ["console"]
    if "console" not in names:
        names = list(names) + ["console"]
    for name in names:
        fn = CHANNELS.get(name)
        if not fn:
            print(f"未知通知渠道: {name}", flush=True)
            continue
        ok, msg = fn(title, body)
        print(f"通知[{name}]: {'OK' if ok else '失败 - ' + msg}", flush=True)


# ============================================================
#  任务加载与执行
# ============================================================
def load_config():
    if not os.path.isfile(TASKS_FILE):
        print(f"找不到 {TASKS_FILE}", flush=True)
        sys.exit(1)
    with open(TASKS_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def run_one(task, only=None):
    """执行单个任务，返回 (ok: bool, detail: str)。"""
    name = task["name"]
    ctype = task.get("type", "python")

    if only and name != only:
        return None

    if not task.get("enabled", True):
        print(f"[{name}] 已禁用，跳过", flush=True)
        return None

    print(f"\n{'=' * 50}\n▶ 开始任务: {name} ({ctype})\n{'=' * 50}", flush=True)
    ctx = Ctx(name, task)

    try:
        if ctype == "python":
            module = task["module"]
            entry = task.get("entry", "run")
            mod = importlib.import_module(module)
            fn = getattr(mod, entry)
            result = fn(ctx)
            detail = str(result) if result is not None else "完成"

        elif ctype == "shell":
            import subprocess
            cmd = task["command"]
            ctx.log(f"$ {cmd}")
            p = subprocess.run(cmd, shell=True, cwd=ROOT,
                               capture_output=True, text=True, timeout=600)
            if p.stdout:
                print(p.stdout, flush=True)
            if p.returncode != 0:
                raise RuntimeError(f"命令退出码 {p.returncode}\n{p.stderr}")
            detail = "命令执行成功"

        else:
            raise ValueError(f"未知任务类型: {ctype}")

        # 任务自己攒的额外通知
        if ctx.messages:
            detail += "\n" + "\n".join(ctx.messages)

        print(f"✅ 任务成功: {name}", flush=True)
        return True, detail

    except Exception as e:
        err = f"{type(e).__name__}: {e}"
        print(f"❌ 任务失败: {name}\n{traceback.format_exc()}", flush=True)
        return False, err


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--task", default="", help="只跑指定任务名")
    args = ap.parse_args()

    config = load_config()
    tasks = config.get("tasks", [])
    only = args.task.strip() or None

    started = datetime.now(CST)
    results = []
    for t in tasks:
        r = run_one(t, only=only)
        if r is not None:
            results.append((t["name"], *r))

    # ---------- 汇总 ----------
    ok_list = [n for n, ok, _ in results if ok]
    fail_list = [(n, d) for n, ok, d in results if not ok]

    lines = [f"开始: {started:%Y-%m-%d %H:%M:%S} (CST)",
             f"任务数: {len(results)}  成功: {len(ok_list)}  失败: {len(fail_list)}", ""]
    if ok_list:
        lines.append("✅ 成功: " + ", ".join(ok_list))
    for n, d in fail_list:
        lines.append(f"❌ 失败: {n}\n   {d[:300]}")

    body = "\n".join(lines)
    title = ("✅ 全部成功" if not fail_list else f"❌ {len(fail_list)} 个失败") \
        + f" ({len(results)} 任务)"

    # 控制台 + 配置的渠道
    dispatch_notify(config, title, body)

    # 写状态文件（给保活提交用）
    os.makedirs(STATE_DIR, exist_ok=True)
    with open(RESULT_FILE, "w", encoding="utf-8") as f:
        f.write(f"{datetime.now(CST):%Y-%m-%d %H:%M:%S}\n{title}\n{body}\n")

    # 有失败则以非 0 退出 → Actions 会标红 + GitHub 会发邮件
    if fail_list:
        sys.exit(1)


if __name__ == "__main__":
    main()
