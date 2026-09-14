# -*- coding: utf-8 -*-
"""
示例任务 —— 教你写自己的任务

这是"函数式"写法的范例。你复制这个文件改名，改掉逻辑就行。
"""


def run(ctx):
    """
    Args:
        ctx: 任务上下文。可用：
            ctx.log(...)              打日志
            ctx.get("KEY")            读配置（tasks.json 的 env 或 Secrets）
            ctx.require("KEY")        读必填配置，缺失直接报错
            ctx.http_get/post(url)    带重试的 HTTP 请求，返回 (status, body)
            ctx.notify("文本")        追加到汇总通知里
    Returns:
        str: 成功信息（会出现在通知里）。抛异常 = 任务失败。
    """
    ctx.log("示例任务开始")

    # --- 演示 1：读配置 ---
    # DEMO_TOKEN 没配也不报错，只是空字符串
    token = ctx.get("DEMO_TOKEN")
    if token:
        ctx.log(f"读到 DEMO_TOKEN（长度 {len(token)}），实际用的时候别打印明文")
    else:
        ctx.log("未配置 DEMO_TOKEN，跳过需要鉴权的部分")

    # --- 演示 2：发一个请求（这里用公开 API，不需要密钥）---
    status, body = ctx.http_get(
        "https://httpbin.org/json",
        headers={"User-Agent": "actions-scheduler"},
    )
    ctx.log(f"请求状态: {status}")
    if status == 200:
        ctx.log(f"返回内容前 120 字: {body.strip()[:120]}")
    else:
        ctx.log(f"返回: {body[:120]}")

    # --- 演示 3：把结果塞进汇总通知 ---
    ctx.notify(f"HTTP 探测结果: {status}")

    return f"示例任务完成，HTTP {status}"
