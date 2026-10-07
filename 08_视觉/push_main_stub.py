"""把 K210 的 /sd/main.py 覆盖成无害占位脚本（不刷屏、不占 UART）。

为什么要这样：改名会被板子/固件恢复，直接覆盖才是稳的（原因见 omv/main_stub.py 头部）。
"""
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from board import Board  # noqa: E402

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

PORT = sys.argv[1] if len(sys.argv) > 1 else "COM5"

b = Board(PORT, 115200).open()
if not b.wait_prompt():
    print("[FAIL] K210 没就绪")
    raise SystemExit(1)
b.push(str(HERE / "omv" / "main_stub.py"), "/sd/main.py", verbose=False)
print("[i] 已把占位脚本写入 /sd/main.py")
out = b.cmd("print(open('/sd/main.py').read().splitlines()[-1])", wait=2.0, quiet=0.5)
print("    板上回显:", out.strip()[:100])
b.close()
print("[i] 完成 —— 下次上电 K210 会跑完占位脚本就回到 REPL，不再刷屏 ✓")
