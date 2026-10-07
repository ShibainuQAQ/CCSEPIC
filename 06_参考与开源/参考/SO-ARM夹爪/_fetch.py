#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""SO-ARM100/101 Parallel Gripper 参考件的镜像下载器（队内工具，不进比赛提交件）。

为什么要这个脚本：
  * 本机 `git` / `curl.exe` 走 Windows schannel，报 SEC_E_NO_CREDENTIALS（沙箱环境没有
    可用凭据）→ 拉不动 GitHub；而 Python 用自带 OpenSSL，能正常访问镜像。
  * 记录"哪个文件从哪个地址来的"，便于日后追溯（许可与来源要留痕）。

用法（在项目根目录执行）：
  python "参考\\SO-ARM夹爪\\_fetch.py" list            # 列全仓库文件树（路径 + 字节数）
  python "参考\\SO-ARM夹爪\\_fetch.py" list models/    # 只看某个前缀
  python "参考\\SO-ARM夹爪\\_fetch.py" curated         # 下载队内需要的精选集
  python "参考\\SO-ARM夹爪\\_fetch.py" get <path>...   # 下载指定文件

镜像：ghproxy.net（github.com 直连不可用；raw.gitmirror.com 解析失败；
     jsDelivr 因仓库 >50MB 整体被拒）。
"""

from __future__ import annotations

import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

REPO = "roboninecom/SO-ARM100-101-Parallel-Gripper"
BRANCH = "main"
MIRROR = "https://ghproxy.net/"
RAW_BASE = MIRROR + "https://raw.githubusercontent.com/%s/%s/" % (REPO, BRANCH)
API_DIRECT = "https://api.github.com/repos/%s/git/trees/%s?recursive=1" % (REPO, BRANCH)
API_PROXIED = MIRROR + API_DIRECT

HERE = Path(__file__).resolve().parent
UA = {"User-Agent": "hw-reference-fetcher/1.0"}

# 队内精选集：只要 md 文档 + STL 模型 + 许可文件，不要图片/PDF/仿真包（那些几 MB 一个）
CURATED_EXACT = [
    "README.md",
    "LICENSING.md",
    "RELICENSING.md",
    "REUSE.toml",
    "NOTICE",
    "CONTRIBUTING.md",
    "HARDWARE-LICENSE.txt",
    "SOFTWARE-LICENSE.txt",
    "DOCS-LICENSE.txt",
    "models/README.md",
    # ⚠️ 这个 STEP 是最值钱的：可导入 Fusion 360 量尺寸/改尺寸（models/README 里没提它）
    "models/RB9.01.062.000 Gripper.STEP",
    "models/Follower gripper (165x165 bed size).STL",
    "docs/bom.md",
    "docs/specifications.md",
    "docs/assembly-guide.md",
    "docs/quick-start.md",
    "docs/Parallel gripper by Robo9.pdf",
    "docs/Assembly Guide Follower Gripper.pdf",
]
CURATED_PREFIXES = [
    "models/parts/",
]


def fetch_bytes(url: str, timeout: int = 60) -> bytes:
    request = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read()


def tree() -> list[dict]:
    last_error: Exception | None = None
    for label, url in (("direct", API_DIRECT), ("mirror", API_PROXIED)):
        try:
            payload = json.loads(fetch_bytes(url, timeout=45).decode("utf-8", "replace"))
            if "tree" not in payload:
                raise RuntimeError("响应里没有 tree 字段：%s" % str(payload)[:200])
            print("# 文件树来源：%s（%s）" % (label, url), file=sys.stderr)
            return [item for item in payload["tree"] if item.get("type") == "blob"]
        except Exception as error:  # 换下一个源
            last_error = error
            print("# %s 取文件树失败：%s" % (label, error), file=sys.stderr)
    raise RuntimeError("两个源都拿不到文件树：%s" % last_error)


def save(relative: str, data: bytes) -> Path:
    target = HERE / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)
    return target


def cmd_list(argv: list[str]) -> int:
    prefix = argv[0] if argv else ""
    items = [item for item in tree() if item["path"].startswith(prefix)]
    total = 0
    for item in items:
        size = int(item.get("size", 0))
        total += size
        print("%9d  %s" % (size, item["path"]))
    print("# 共 %d 个文件，合计 %.2f MB" % (len(items), total / 1048576.0))
    return 0


def cmd_get(argv: list[str]) -> int:
    wanted = list(argv)
    if not wanted:
        print("get 需要至少一个仓库内路径", file=sys.stderr)
        return 2
    known = {item["path"]: int(item.get("size", 0)) for item in tree()}
    failed = []
    for relative in wanted:
        if relative not in known:
            print("!! 仓库里没有这个路径：%s" % relative, file=sys.stderr)
            failed.append(relative)
            continue
        url = RAW_BASE + urllib.parse.quote(relative)
        try:
            data = fetch_bytes(url)
        except Exception as error:
            print("!! 下载失败 %s：%s" % (relative, error), file=sys.stderr)
            failed.append(relative)
            continue
        path = save(relative, data)
        print("%9d  %s  <- %s" % (len(data), path.relative_to(HERE), url))
        time.sleep(0.15)  # 对镜像客气一点
    print("# 成功 %d / 失败 %d" % (len(wanted) - len(failed), len(failed)))
    return 1 if failed else 0


def cmd_curated(argv: list[str]) -> int:
    items = tree()
    paths = {item["path"]: int(item.get("size", 0)) for item in items}
    chosen = [p for p in CURATED_EXACT if p in paths]
    for path in sorted(paths):
        if any(path.startswith(prefix) for prefix in CURATED_PREFIXES):
            chosen.append(path)
    missing = [p for p in CURATED_EXACT if p not in paths]
    if missing:
        print("# 注意：仓库里没找到 %s" % ", ".join(missing), file=sys.stderr)
    print("# 精选集共 %d 个文件，合计 %.2f MB" % (len(chosen), sum(paths[p] for p in chosen) / 1048576.0))
    return cmd_get(chosen)


def main(argv: list[str]) -> int:
    if not argv:
        print(__doc__)
        return 0
    action, rest = argv[0], argv[1:]
    if action == "list":
        return cmd_list(rest)
    if action == "get":
        return cmd_get(rest)
    if action == "curated":
        return cmd_curated(rest)
    print("未知子命令：%s" % action, file=sys.stderr)
    return 2


if __name__ == "__main__":
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    try:
        sys.exit(main(sys.argv[1:]))
    except KeyboardInterrupt:
        sys.exit(130)
