"""PC 侧：让 CanMV(K210) 板拍一张照并把照片取回电脑。

流程：等启动 → Ctrl-C 拿提示符 → **把 omv/shot_to_pc.py 分片推进板子** → `exec` 跑它
      → 从输出里抠 `B64 …` 行拼起来 → base64 解码存成 .jpg。

用法（项目根目录）：
  D:\\python\\python.exe 12_视觉\\shot.py
  D:\\python\\python.exe 12_视觉\\shot.py --out 12_视觉\\shots\\tray.jpg --remote /sd/_shot.py
"""
import argparse
import base64
import re
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from board import Board  # noqa: E402

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", default="COM5")
    ap.add_argument("--baud", type=int, default=115200)
    ap.add_argument("--script", default=str(HERE / "omv" / "shot_to_pc.py"))
    ap.add_argument("--remote", default="/sd/_shot.py")
    ap.add_argument("--out", default=None)
    ap.add_argument("--keep-log", default=None)
    ap.add_argument("--timeout", type=float, default=150.0)
    a = ap.parse_args()

    out = Path(a.out) if a.out else (HERE / "shots" / time.strftime("shot-%Y%m%d-%H%M%S.jpg"))
    out.parent.mkdir(parents=True, exist_ok=True)

    b = Board(a.port, a.baud).open()
    try:
        if not b.wait_prompt():
            print("[FAIL] 拿不到提示符")
            return 1
        b.push(a.script, a.remote)
        text = b.run(a.remote, wait=a.timeout, quiet=6.0, stop=b"JPG_END")
        if a.keep_log:
            Path(a.keep_log).write_text(text, encoding="utf-8")

        # 现在是"分片推送 + exec"，回显很小 → 打印板子的输出，但**过滤 base64 刷屏**
        for line in text.splitlines():
            s = line.strip()
            if s.startswith("exec(") or s.startswith("B64 ") or s in (">>>", "..."):
                continue
            if s:
                print("  " + s)

        parts = re.findall(r"^B64\s+(\S+)\s*$", text, flags=re.M)
        if not parts:
            print("[FAIL] 没收到 B64；输出尾部：")
            print(text[-1500:])
            return 2
        blob = base64.b64decode("".join(parts))
        out.write_bytes(blob)
        print("[ok] %d 段 base64 → %d 字节 → %s" % (len(parts), len(blob), out))
        print("     头 4 字节 %s（FFD8 才是 JPEG）" % blob[:4].hex().upper())
        return 0
    finally:
        b.close()


if __name__ == "__main__":
    sys.exit(main())
