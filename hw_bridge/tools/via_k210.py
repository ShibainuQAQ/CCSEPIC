"""不用 USB-TTL，走 K210 当串口桥给 STM32 发命令。

为什么要它：蓝板没有板载 CH340，USB-TTL 又一直连不通 ✗。但其实手上已经有一条现成的链路：
    PC ──USB(COM5)──> K210 ──UART2(PB10/PB11)──> STM32
K210 的 USB 在 PC 上就是一个普通串口 ✓，它的 UART2 已经接在 STM32 的视觉口 ✓。
所以：**把命令打包成板上脚本，让 K210 转发给 STM32，再把 STM32 的回执打回 USB** ✓。

⚠️ 命令必须带 `@` 前缀（固件对视觉口只认 `@` 开头的行，其余静默忽略 ✓）。
⚠️ 这条链**看不到 STM32 的开机日志/自发事件**（那些只从 USART1 出 ✗）；命令+回执完全够用 ✓。

用法（项目根目录）：
  D:\\python\\python.exe hw_bridge\\tools\\via_k210.py --cmd "@PING;@VER;@POS"
  D:\\python\\python.exe hw_bridge\\tools\\via_k210.py --file my_cmds.txt   # 一行一条
  D:\\python\\python.exe hw_bridge\\tools\\via_k210.py --port COM5 --cmd "@STEP x 800" --wait 900
"""
import argparse
import re
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
sys.path.insert(0, str(ROOT / "12_视觉"))

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

from board import Board  # noqa: E402

TEMPLATE = '''# -*- coding: utf-8 -*-
# 自动生成：PC -> K210(UART2) -> STM32 转发脚本
import time

from fpioa_manager import fm
from machine import UART

# ⚠️⚠️ 关键：这块板（CanMV_Yahboom K210）的 **TXD/RXD 排针 = IO8(TX) / IO6(RX)**，
#    必须**显式注册**给 UART2 —— 只写 UART(UART.UART2) 会落到芯片默认引脚上 ✗，
#    于是数据根本不在排针上，STM32 一个字节都收不到 ✓（这就是 2026-10-04 排查很久的坑）。
#    这一段是照抄板子自己的 /flash/main.py ✓。
fm.register(6, fm.fpioa.UART2_RX, force=True)
fm.register(8, fm.fpioa.UART2_TX, force=True)

u = UART(UART.UART2, 115200, 8, 0, 0, timeout=1000, read_buf_len=4096)
u.write(b"\\r\\n")
time.sleep_ms(250)

CMDS = {cmds}

for m in CMDS:
    u.write((m + "\\r\\n").encode())
    got = b""
    t0 = time.ticks_ms()
    while time.ticks_diff(time.ticks_ms(), t0) < {wait}:
        try:
            n = u.any()
            if n:
                got += u.read(n)
        except Exception as exc:
            got += ("<err %r>" % (exc,)).encode()
            break
        time.sleep_ms(15)
    print("SENT " + m + " -> " + repr(got))
print("VIADONE")
'''


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", default=None, help="K210 的串口；不给就自动找 CH340(1A86:7523)")
    ap.add_argument("--cmd", help="分号分隔的命令，例如 \"@PING;@VER\"")
    ap.add_argument("--file", help="每行一条命令的文件")
    ap.add_argument("--wait", type=int, default=600, help="每条命令等回执的毫秒数")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    cmds = []
    if a.file:
        for ln in Path(a.file).read_text(encoding="utf-8").splitlines():
            ln = ln.strip()
            if ln and not ln.startswith("#"):
                cmds.append(ln)
    if a.cmd:
        cmds += [c.strip() for c in a.cmd.split(";") if c.strip()]
    if not cmds:
        print("没有命令：用 --cmd \"@PING;@VER\" 或 --file <文件>")
        return 2

    code = TEMPLATE.format(cmds=repr(cmds), wait=a.wait)
    if a.dry_run:
        print(code)
        return 0

    port = a.port
    if not port:
        sys.path.insert(0, str(ROOT / "hw_bridge" / "vendor"))
        import serial.tools.list_ports as lp
        for p in lp.comports():
            if "1A86:7523" in (p.hwid or "").upper():
                port = p.device
                break
    if not port:
        print("没找到 K210（CH340 1A86:7523）")
        return 1
    print("[i] K210 = %s，共 %d 条命令" % (port, len(cmds)), flush=True)

    tmp = ROOT / "12_视觉" / "omv" / "_via_generated.py"
    tmp.write_text(code, encoding="utf-8")

    b = Board(port, 115200).open()
    try:
        if not b.wait_prompt():
            print("[FAIL] K210 没就绪")
            return 1
        b.push(str(tmp), "/sd/_via.py", verbose=False)
        out = b.run("/sd/_via.py", wait=2.0 + 0.0015 * a.wait * len(cmds), quiet=1.5, verbose=False)
    finally:
        b.close()

    text = out.decode("utf-8", "replace") if isinstance(out, (bytes, bytearray)) else str(out)
    for ln in text.splitlines():
        if ln.startswith("SENT ") or ln.startswith("VIADONE"):
            print("  " + ln.rstrip())
    if "VIADONE" not in text:
        print("  ⚠️ 没跑完（板上脚本没结束）—— 原始输出尾部：")
        for ln in text.splitlines()[-6:]:
            print("    " + ln.rstrip())
    return 0


if __name__ == "__main__":
    sys.exit(main())
