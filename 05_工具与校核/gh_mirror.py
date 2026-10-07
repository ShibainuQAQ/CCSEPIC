#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""通用 GitHub 镜像下载器（本机 github.com 直连不可用，必须走镜像 + Python）。

为什么不用 git / curl：
  * 本机 PowerShell 是 5.1，curl.exe 走 Windows schannel → SEC_E_NO_CREDENTIALS
  * Python 自带 OpenSSL 证书，能正常握手

用法（在项目根目录执行）：
  python "05_工具与校核\\gh_mirror.py" list   <owner/repo> [前缀]
  python "05_工具与校核\\gh_mirror.py" match  <owner/repo> <正则>
  python "05_工具与校核\\gh_mirror.py" get    <owner/repo> <仓库内路径...> --out <目录>
      （--out 默认 ./_download/<repo名>）

镜像顺序：ghproxy.net → gh-proxy.com → 直连（留作以后网络放开时的兜底）
"""

from __future__ import annotations

import json
import re
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

MIRRORS = ["https://ghproxy.net/", "https://gh-proxy.com/", ""]
UA = {"User-Agent": "hw-reference-fetcher/1.0"}


def fetch(url: str, timeout: int = 90) -> bytes:
    with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=timeout) as r:
        return r.read()


def tree(repo: str, branch: str = "") -> list[dict]:
    direct = "https://api.github.com/repos/%s/git/trees/%s?recursive=1" % (repo, branch or "main")
    errors = []
    for m in MIRRORS:
        try:
            payload = json.loads(fetch(m + direct, timeout=45).decode("utf-8", "replace"))
            if "tree" not in payload:
                raise RuntimeError(str(payload)[:200])
            return [i for i in payload["tree"] if i.get("type") == "blob"]
        except Exception as e:
            errors.append("%s -> %s" % (m or "direct", e))
    raise RuntimeError("取文件树失败：%s" % "; ".join(errors))


def raw(repo: str, path: str, branch: str = "main") -> bytes:
    target = "https://raw.githubusercontent.com/%s/%s/%s" % (repo, branch, urllib.parse.quote(path))
    errors = []
    for m in MIRRORS:
        try:
            return fetch(m + target)
        except Exception as e:
            errors.append("%s -> %s" % (m or "direct", e))
    raise RuntimeError("下载失败 %s：%s" % (path, "; ".join(errors)))


def save(out: Path, path: str, data: bytes) -> None:
    t = out / path
    t.parent.mkdir(parents=True, exist_ok=True)
    t.write_bytes(data)
    print("%10d  %s" % (len(data), t.as_posix()))


def main(argv: list[str]) -> int:
    if not argv:
        print(__doc__)
        return 0
    action = argv[0]
    if action == "list":
        repo = argv[1]
        prefix = argv[2] if len(argv) > 2 else ""
        items = [i for i in tree(repo) if i["path"].startswith(prefix)]
        total = 0
        for i in items:
            total += int(i.get("size", 0))
            print("%10d  %s" % (int(i.get("size", 0)), i["path"]))
        print("# %d 个文件，合计 %.2f MB" % (len(items), total / 1048576.0))
        return 0
    if action == "match":
        repo, pattern = argv[1], argv[2]
        rx = re.compile(pattern, re.I)
        items = [i for i in tree(repo) if rx.search(i["path"])]
        total = 0
        for i in items:
            total += int(i.get("size", 0))
            print("%10d  %s" % (int(i.get("size", 0)), i["path"]))
        print("# %d 个文件，合计 %.2f MB" % (len(items), total / 1048576.0))
        return 0
    if action == "get":
        repo = argv[1]
        rest = argv[2:]
        out = Path("_download") / repo.split("/")[-1]
        if "--out" in rest:
            k = rest.index("--out")
            out = Path(rest[k + 1])
            rest = rest[:k] + rest[k + 2:]
        known = {i["path"]: int(i.get("size", 0)) for i in tree(repo)}
        bad = 0
        for p in rest:
            if p not in known:
                print("!! 仓库无此路径：%s" % p, file=sys.stderr)
                bad += 1
                continue
            try:
                save(out, p, raw(repo, p))
            except Exception as e:
                print("!! %s：%s" % (p, e), file=sys.stderr)
                bad += 1
            time.sleep(0.2)
        print("# 失败 %d / 共 %d" % (bad, len(rest)))
        return 1 if bad else 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    sys.exit(main(sys.argv[1:]))
