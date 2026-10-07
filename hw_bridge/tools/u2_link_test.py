"""无线链路 → STM32 的端到端测试（PC 侧，同时占两个口）。

为什么需要它：单次 `port_probe` 每发一条命令就重开一次串口，**而开串口会让 STM32 复位**，
`U2?` 里的收发计数会被清零 —— 那样永远看不到"刚才到底收到没有"。
所以这里**同时打开两个口并一直按住**：

    COM8 = STM32 调试口（**8E1**）—— 发 U2? 读计数、看它的回复
    COM7 = C3 无线桥（**8N1**） —— 往装置侧发数据

流程：
    ① 开 COM8（复位 STM32）→ 读基线 U2?
    ② 开 COM7（复位 C3）→ 等它重新连上 WiFi/TCP（约 12~15s）
    ③ 从 COM7 发一段文本
    ④ 回 COM8 再读 U2? → 看 u2_rx / u2_tx 涨了多少

判据：
    u2_rx 涨了           ⇒ **数据确实经无线链路进了 STM32 的 USART2**
    u2_tx 也涨了         ⇒ STM32 回了话（回复走 USART2 回 C6）
    COM7 收到 OK pong    ⇒ 整圈闭环

用法（项目根目录，先确认 C6 与 STM32 已接线、C6 在跑 c6_bridge）：
    D:\\python\\python.exe hw_bridge\\tools\\u2_link_test.py
    D:\\python\\python.exe hw_bridge\\tools\\u2_link_test.py --text "HELLO-VIA-WIFI"
"""
import argparse
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "vendor"))
import serial  # noqa: E402

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass


def drain(ser, secs, quiet=0.5):
    end = time.time() + secs
    buf = bytearray()
    last = time.time()
    while time.time() < end:
        n = ser.in_waiting
        if n:
            buf += ser.read(n)
            last = time.time()
        else:
            if buf and (time.time() - last) > quiet:
                break
            time.sleep(0.02)
    return bytes(buf)


def send_cmd(ser, cmd, wait=2.5, quiet=0.4):
    ser.reset_input_buffer()
    ser.write(cmd.encode() + b"\r\n")
    return drain(ser, wait, quiet).decode("utf-8", "replace")


def parse_u2(text):
    m = re.search(r"u2_rx=(\d+)\s+u2_tx=(\d+)", text)
    return (int(m.group(1)), int(m.group(2))) if m else (None, None)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--stm32", default="COM8", help="STM32 调试口（8E1）")
    ap.add_argument("--bridge", default="COM7", help="C3 无线桥口（8N1）")
    ap.add_argument("--text", default="PING")
    ap.add_argument("--reconnect", type=float, default=15.0, help="等 C3 重新连上无线桥的秒数")
    a = ap.parse_args()

    stm = serial.Serial(a.stm32, 115200, parity=serial.PARITY_EVEN, timeout=0.2,
                        write_timeout=3, dsrdtr=False, rtscts=False)
    br = serial.Serial(a.bridge, 115200, parity=serial.PARITY_NONE, timeout=0.2,
                       write_timeout=3, dsrdtr=False, rtscts=False)
    for s in (stm, br):
        try:
            s.dtr = False
            s.rts = False
        except Exception:
            pass

    try:
        print("=== ① STM32 基线（开 COM8 会复位它）===")
        boot = drain(stm, 2.0, 0.6).decode("utf-8", "replace")
        for line in boot.splitlines():
            if line.strip():
                print("  " + line.strip())
        base = send_cmd(stm, "U2?")
        print("  " + base.strip().replace("\r\n", " | "))
        rx0, tx0 = parse_u2(base)
        print("  基线: u2_rx=%s u2_tx=%s" % (rx0, tx0))

        print("=== ② 等 C3 无线桥重新连上（%.0fs）===" % a.reconnect)
        hello = drain(br, a.reconnect, 1.0).decode("utf-8", "replace")
        for line in hello.splitlines():
            if line.strip():
                print("  " + line.strip())

        print("=== ③ 从无线桥发: %r ===" % a.text)
        br.reset_input_buffer()
        br.write(a.text.encode() + b"\r\n")
        got = drain(br, 6.0, 1.5).decode("utf-8", "replace")
        print("  COM7 收到: %r" % got)

        print("=== ④ 再读 STM32 计数 ===")
        after = send_cmd(stm, "U2?")
        print("  " + after.strip().replace("\r\n", " | "))
        rx1, tx1 = parse_u2(after)

        print()
        print("=== 结论 ===")
        if None in (rx0, rx1):
            print("  ❌ 没能从 STM32 读到 u2_rx（U2? 没回或格式变了）")
        else:
            print("  u2_rx: %d → %d（+%d）" % (rx0, rx1, rx1 - rx0))
            print("  u2_tx: %d → %d（+%d）" % (tx0, tx1, tx1 - tx0))
            if rx1 > rx0:
                print("  ✅ 数据**确实经无线链路进了 STM32 的 USART2**")
            else:
                print("  ❌ STM32 一个字节都没收到 → 查 **C6 GPIO4(TX) → PA3(RX)** 这根线")
        if "pong" in got.lower():
            print("  ✅ 整圈闭环（PC → C3 → 无线 → C6 → STM32 → 原路回来）")
        return 0
    finally:
        stm.close()
        br.close()


if __name__ == "__main__":
    sys.exit(main())
