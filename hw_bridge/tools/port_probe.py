"""COM 口探针（线路 A 自有工具）—— 开一个串口，可选发一段数据，然后把收到的字节按 hex + ASCII 打出来。

为什么需要它：本机 hw_bridge 依赖 DSH 的 mcp 插件注册，换 DSH 版本/配置后工具可能不在；
这个小脚本只用自带 pyserial（hw_bridge\\vendor），一次调用即可完成"发 + 收"，不依赖任何插件。

用法（在 hw_bridge 目录下跑）：
  D:\\python\\python.exe tools\\port_probe.py COM7 --listen 4
  D:\\python\\python.exe tools\\port_probe.py COM7 --baud 115200 --send "AT" --crlf --listen 2
  D:\\python\\python.exe tools\\port_probe.py COM6 --baud 115200 --parity E --send "PING" --crlf --listen 2
参数：
  --baud N     波特率，默认 115200
  --parity X   N/E/O（默认 N）—— STM32 那条是 8E1，必须传 E
  --send S     打开后发送的 ASCII 文本（可选）
  --hex H      打开后发送的十六进制字节串，如 41540D0A（与 --send 二选一）
  --crlf       在 --send 文本后补 \\r\\n
  --listen S   收多久（秒），默认 3
  --dtr 0/1    打开时 DTR 电平，默认 0（不驱动，避免某些板子被按在复位态）
  --rts 0/1    打开时 RTS 电平，默认 0
  --no-open-check  只列端口不开（等价于 --list）
  --list       只列端口
注意：ESP32-C3/C6 的原生 USB-Serial-JTAG 会被 DTR/RTS 的翻转序列触发复位/进下载模式，
      所以默认把两者都置 0；如果一开就看到一堆 ROM 启动日志，说明还是被复位了（本身也是有用信息）。
"""
import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "vendor"))
import serial  # noqa: E402
import serial.tools.list_ports as list_ports  # noqa: E402


def show_ports():
    ps = list(list_ports.comports())
    print("ports=%d" % len(ps))
    for p in ps:
        print("  %-6s | %s | %s" % (p.device, p.description, p.hwid))
    return ps


def preview(data: bytes) -> str:
    out = []
    for b in data:
        if 32 <= b < 127:
            out.append(chr(b))
        elif b == 13:
            out.append("\\r")
        elif b == 10:
            out.append("\\n")
        elif b == 9:
            out.append("\\t")
        else:
            out.append("\\x%02X" % b)
    return "".join(out)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("port", nargs="?")
    ap.add_argument("--baud", type=int, default=115200)
    ap.add_argument("--parity", default="N")
    ap.add_argument("--send")
    ap.add_argument("--hex")
    ap.add_argument("--crlf", action="store_true")
    ap.add_argument("--listen", type=float, default=3.0)
    ap.add_argument("--send-after", type=float, default=0.0,
                    help="打开端口后先等这么多秒再发（对端开机要连 WiFi 时用）")
    ap.add_argument("--dtr", type=int, default=0)
    ap.add_argument("--rts", type=int, default=0)
    ap.add_argument("--list", action="store_true")
    a = ap.parse_args()

    show_ports()
    if a.list or not a.port:
        return

    par = {"N": serial.PARITY_NONE, "E": serial.PARITY_EVEN, "O": serial.PARITY_ODD}[a.parity.upper()]
    print("\nopen %s baud=%d parity=%s dtr=%d rts=%d" % (a.port, a.baud, a.parity, a.dtr, a.rts))
    ser = serial.Serial(port=a.port, baudrate=a.baud, bytesize=8, parity=par, stopbits=1,
                        timeout=0.2, write_timeout=1, dsrdtr=False, rtscts=False)
    try:
        # pyserial 3.5 的构造函数不接受 dtr/rts 关键字（只有属性 setter）→ 开完立刻置电平
        try:
            ser.dtr = bool(a.dtr)
            ser.rts = bool(a.rts)
        except Exception as exc:  # pragma: no cover
            print("warn: cannot set dtr/rts: %r" % (exc,))
        time.sleep(0.2)
        payload = b""
        if a.hex:
            payload = bytes.fromhex(a.hex.replace(" ", ""))
        elif a.send is not None:
            payload = a.send.encode("utf-8", "replace")
            if a.crlf:
                payload += b"\r\n"
        if payload and a.send_after > 0:
            print("waiting %.1fs before send ..." % a.send_after)
            t_wait = time.time()
            pre = bytearray()
            while time.time() - t_wait < a.send_after:
                chunk = ser.read(4096)
                if chunk:
                    pre += chunk
            if pre:
                print("RX during wait: %d B %s" % (len(pre), preview(bytes(pre[:400]))))
        if payload:
            n = ser.write(payload)
            ser.flush()
            print("TX %d B: %s" % (n, preview(payload)))
        t0 = time.time()
        buf = bytearray()
        while time.time() - t0 < a.listen:
            chunk = ser.read(4096)
            if chunk:
                buf += chunk
        print("RX %d B (%.1fs):" % (len(buf), a.listen))
        if buf:
            print("  HEX: %s" % " ".join("%02X" % b for b in buf[:400]))
            print("  TXT: %s" % preview(bytes(buf[:2000])))
        else:
            print("  (nothing)")
    finally:
        ser.close()
        print("closed")


if __name__ == "__main__":
    main()
