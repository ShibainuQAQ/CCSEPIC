#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Qwen（阿里 MaaS token-plan）生图工具 —— 队内生成概念参考图用。

key 从 `~/.dsh/.credentials.yaml` 里**按 env 名自动找**（支持国内/国际两个 plan），并根据
是 `..._CN_...` 还是 `..._INDIVIDUAL_...` 自动选对应 endpoint —— 与 DSH 的路由一致。
**key 不写进任何文件、不打印（只显示长度和结尾 4 位）。**

⚠️ 这个 key 是 JWT 格式、**会过期**；过期就 401 InvalidApiKey，到时去 DSH 重新部署一个再来。

用法（在项目根目录执行）：
  python gen_image.py --models                                  # 列出该 plan 可用的模型（先跑这个验证 key）
  python gen_image.py --probe                                   # chat 验证 + 逐个候选图像模型试
  python gen_image.py "一只趴在电路板上的橘猫"                    # 用默认模型出 1 张到 概念图\
  python gen_image.py "..." --model qwen-image --size 1024*1024 --n 2

⚠️ 合规：生成的图只作队内参考，**不进匿名提交件**（工创赛说明书图必须从我们自己的 CAD 出）。
"""

from __future__ import annotations

import json
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

CRED = Path.home() / ".dsh" / ".credentials.yaml"
# 2026-09-17：项目文件已分类整理，本脚本在 05_工具与校核\ 下 → 出图写到项目根的 07_概念图\
OUT_DIR = Path(__file__).resolve().parent.parent / "07_概念图"
# 实测：这个 key 是**标准 DashScope（百炼）国内站** key，在 dashscope.aliyuncs.com 上有效
# （2026-09-17 实测 253 个模型）；token-plan 子域名（cn-beijing / ap-southeast-1）对它一律 401。
DASHSCOPE = "https://dashscope.aliyuncs.com/compatible-mode/v1"
KEY_NAMES = [  # 按优先级在 credentials.yaml 里找 key 值
    "QWEN_TOKEN_PLAN_CN_API_KEY",
    "QWEN_TOKEN_PLAN_INDIVIDUAL_API_KEY",
    "QWEN_TOKEN_PLAN_API_KEY",
    "DASHSCOPE_API_KEY",
]
DEFAULT_MODEL = "qwen-image"   # 2026-09-17 原生 API 实测可用（qwen-image-max 等会 404）
DEFAULT_SIZE = "1024*1024"
CHAT_PROBE_MODEL = "qwen-turbo"
IMAGE_CANDIDATES = [  # 2026-09-17 在 dashscope 国内站 /models 里确认存在的图像模型
    "qwen-image-max", "qwen-image-3.0-pro", "qwen-image-3.0",
    "qwen-image-2.0-pro", "qwen-image-2.0", "wan2.7-image-pro", "wan2.7-image",
    "z-image-turbo", "qwen-image-plus",
]


def load_key() -> tuple[str, str, str]:
    """从 credentials.yaml 找 Qwen/DashScope key，返回 (key, endpoint, 标签)。"""
    text = CRED.read_text(encoding="utf-8", errors="replace")
    for name in KEY_NAMES:
        match = re.search(re.escape(name) + r":\s*(\S+)", text)
        if match:
            return match.group(1), DASHSCOPE, "dashscope 国内标准"
    raise RuntimeError("在 %s 里没找到任何 Qwen/DashScope key（%s）" % (CRED, "、".join(KEY_NAMES)))


def call(path: str, payload: dict | None, key: str, base: str, timeout: int = 120):
    url = base + path
    data = None
    headers = {"Authorization": "Bearer " + key}
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=data, headers=headers,
                                     method="POST" if payload is not None else "GET")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, json.loads(response.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as error:
        body = error.read().decode("utf-8", "replace")
        raise RuntimeError("HTTP %s：%s" % (error.code, body[:500]))


# ---- 阿里 DashScope 原生文生图 API（异步任务制） ----
# 注意：compatible-mode 没有 /images/generations（2026-09-17 实测全部 404），
# 生图必须走这个原生端点：提交任务 → 轮询 → 拿图。
NATIVE_T2I = "https://dashscope.aliyuncs.com/api/v1/services/aigc/text2image/image-synthesis"
NATIVE_TASK = "https://dashscope.aliyuncs.com/api/v1/tasks/"
NATIVE_IMAGE_CANDIDATES = [
    "qwen-image", "qwen-image-plus", "wanx2.5-t2i-preview",
    "wanx2.1-t2i-turbo", "wanx2.1-t2i-plus", "wanx2.0-t2i-turbo", "wanx-v1",
]


def native_submit(prompt: str, model: str, size: str, count: int, key: str) -> str:
    body = json.dumps({
        "model": model,
        "input": {"prompt": prompt},
        "parameters": {"size": size, "n": count},
    }).encode("utf-8")
    request = urllib.request.Request(NATIVE_T2I, data=body, method="POST", headers={
        "Authorization": "Bearer " + key,
        "Content-Type": "application/json",
        "X-DashScope-Async": "enable",   # wanx/qwen-image 生图要求异步
    })
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            data = json.loads(response.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as error:
        raise RuntimeError("提交失败 HTTP %s：%s" % (error.code, error.read().decode("utf-8", "replace")[:400]))
    task_id = (data.get("output") or {}).get("task_id")
    if not task_id:
        raise RuntimeError("响应里没有 task_id：%s" % json.dumps(data, ensure_ascii=False)[:400])
    return task_id


def native_poll(task_id: str, key: str, timeout: int = 300) -> list[str]:
    deadline = time.time() + timeout
    while time.time() < deadline:
        request = urllib.request.Request(NATIVE_TASK + task_id,
                                         headers={"Authorization": "Bearer " + key})
        with urllib.request.urlopen(request, timeout=30) as response:
            data = json.loads(response.read().decode("utf-8", "replace"))
        output = data.get("output") or {}
        status = output.get("task_status")
        if status == "SUCCEEDED":
            return [r.get("url") for r in (output.get("results") or []) if r.get("url")]
        if status in ("FAILED", "CANCELED"):
            raise RuntimeError("任务 %s：%s" % (status, json.dumps(data, ensure_ascii=False)[:400]))
        time.sleep(2)
    raise RuntimeError("任务轮询超时（task_id=%s）" % task_id)


def cmd_models(key: str, base: str, label: str) -> int:
    print("key 长度 %d、结尾 …%s  |  [%s] %s" % (len(key), key[-4:], label, base))
    try:
        _status, data = call("/models", None, key, base)
    except Exception as error:
        print("❌ %s" % str(error)[:200])
        return 1
    ids = [item.get("id") for item in data.get("data", [])]
    print("✅ key 有效，共 %d 个模型。" % len(ids))
    image_like = [m for m in ids if m and re.search(r"image|wanx|t2i|flux|seedream|dall", m, re.I)]
    print("图像模型（%d 个）：%s" % (len(image_like), ", ".join(image_like) if image_like else "（没有）"))
    return 0


def cmd_probe(key: str, base: str, label: str) -> int:
    print("【1/2】chat/completions 验证（[%s]，model=%s）" % (label, CHAT_PROBE_MODEL))
    try:
        _s, data = call("/chat/completions", {
            "model": CHAT_PROBE_MODEL,
            "messages": [{"role": "user", "content": "只回复两个字：在线"}],
            "max_tokens": 8,
        }, key, base, timeout=60)
        text = (data.get("choices") or [{}])[0].get("message", {}).get("content", "")
        print("  ✅ key 有效：%s" % (text or "（空）"))
    except Exception as error:
        print("  ❌ %s" % str(error)[:300])
        return 1
    print("【2/2】原生文生图 API 逐个候选图像模型试（能用就停）")
    for model in NATIVE_IMAGE_CANDIDATES:
        try:
            task_id = native_submit("a single red cube on a white table", model, DEFAULT_SIZE, 1, key)
            urls = native_poll(task_id, key, timeout=240)
            print("  ✅ model=%s 可用（返回 %d 张）—— 生图就用它" % (model, len(urls)))
            return 0
        except Exception as error:
            print("  ❌ model=%s：%s" % (model, str(error)[:200]))
    print("  → 候选图像模型全失败。")
    return 1


def cmd_generate(prompt: str, model: str, size: str, count: int, out: str | None,
                 key: str, base: str, label: str) -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    print("请求：[%s] 原生文生图 model=%s size=%s n=%d" % (label, model, size, count))
    task_id = native_submit(prompt, model, size, count, key)
    print("任务已提交 task_id=%s，轮询中…" % task_id)
    urls = native_poll(task_id, key, timeout=300)
    if not urls:
        print("任务成功但没返回图片 URL")
        return 1
    saved = []
    for index, url in enumerate(urls):
        if out and count == 1:
            target = Path(out)
        else:
            stem = Path(out).stem if out else ("img-%s" % time.strftime("%Y%m%d-%H%M%S"))
            suffix = "" if count == 1 else "-%d" % (index + 1)
            target = OUT_DIR / ("%s%s.png" % (stem, suffix))
        with urllib.request.urlopen(url, timeout=120) as response:
            target.write_bytes(response.read())
        saved.append(target)
        print("已存：%s（%d 字节）" % (target, target.stat().st_size))
    return 0 if saved else 1


def main(argv: list[str]) -> int:
    key, base, label = load_key()
    args = list(argv)
    if args and args[0] == "--models":
        return cmd_models(key, base, label)
    if args and args[0] == "--probe":
        return cmd_probe(key, base, label)
    if not args:
        print(__doc__)
        return 0
    prompt = args.pop(0)
    model, size, count, out = DEFAULT_MODEL, DEFAULT_SIZE, 1, None
    i = 0
    while i < len(args):
        flag = args[i]
        value = args[i + 1] if i + 1 < len(args) else None
        if flag == "--model" and value:
            model = value
        elif flag == "--size" and value:
            size = value
        elif flag == "--n" and value:
            count = int(value)
        elif flag == "--out" and value:
            out = value
        else:
            i += 1
            continue
        i += 2
    return cmd_generate(prompt, model, size, count, out, key, base, label)


if __name__ == "__main__":
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    try:
        sys.exit(main(sys.argv[1:]))
    except Exception as error:
        print("出错：%s" % error)
        sys.exit(1)
