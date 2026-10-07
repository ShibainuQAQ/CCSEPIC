"""CanMV/K210/K230 板子的 PC 侧 REPL 驱动（线路 A 自备）。

为什么需要它：**`mpremote` 进不去 CanMV 的 raw REPL**（实测 `could not enter raw repl`），
所以自己驱动最原始的 MicroPython 交互：
  ① 等启动跑完（打开串口会让板子复位）
  ② 反复 Ctrl-C，直到看见 `>>>` 提示符
  ③ Ctrl-E 进粘贴模式 → 整段发代码 → Ctrl-D 执行 → 收输出

用法（项目根目录）：
  D:\\python\\python.exe 12_视觉\\repl.py --cmd "print(123)"                  # 单条
  D:\\python\\python.exe 12_视觉\\repl.py --cmd "a=1" --cmd "print(a)"        # 一个会话连跑多条
  D:\\python\\python.exe 12_视觉\\repl.py --file 12_视觉\\omv\\probe_caps.py --wait 8
  D:\\python\\python.exe 12_视觉\\repl.py --out dump.bin --raw-bytes 200000   # 收原始字节存盘（收图用）
"""
import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "hw_bridge" / "vendor"))
import serial  # noqa: E402

# ⚠️ 本机控制台是 GBK，直接 print 带 emoji/生僻字的字符串会 UnicodeEncodeError
# （本项目踩过多次）→ 强制 stdout 走 UTF-8。
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass


def drain(ser, secs, quiet=0.5):
    """收数据：最多等 secs 秒；有数据后静默 quiet 秒就提前结束。"""
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


def wait_prompt(ser, tries=8, show=False):
    """反复 Ctrl-C 直到出现 >>> 提示符。"""
    buf = bytearray()
    for i in range(tries):
        ser.write(b"\x03")
        time.sleep(0.4)
        chunk = drain(ser, 0.8, 0.25)
        buf += chunk
        if b">>>" in chunk:
            if show:
                print("(第 %d 次 Ctrl-C 拿到提示符)" % (i + 1))
            return bytes(buf), True
    return bytes(buf), False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", default="COM5")
    ap.add_argument("--baud", type=int, default=115200)
    ap.add_argument("--cmd", action="append", default=[], help="可重复；同一会话里按顺序执行")
    ap.add_argument("--file", help="把整个文件粘贴过去执行")
    ap.add_argument("--raw-bytes", type=int, default=0, help="只收这么多原始字节（收图用）")
    ap.add_argument("--out", help="把收到的原始字节存成文件")
    ap.add_argument("--boot-wait", type=float, default=5.0, help="开端口后先等启动多久")
    ap.add_argument("--wait", type=float, default=5.0, help="每条命令执行后最多等多久")
    ap.add_argument("--quiet", type=float, default=0.5)
    ap.add_argument("--hex", action="store_true")
    a = ap.parse_args()

    ser = serial.Serial(a.port, a.baud, timeout=0.2, write_timeout=5,
                        dsrdtr=False, rtscts=False)
    try:
        ser.dtr = False
        ser.rts = False
    except Exception:
        pass

    try:
        boot = drain(ser, a.boot_wait, 0.6)
        print("--- 启动输出（%d 字节）---" % len(boot))
        print(boot.decode("utf-8", "replace")[-600:])

        pre, ok = wait_prompt(ser, show=True)
        print("--- 提示符：%s ---" % ("拿到了 [OK]" if ok else "没拿到 [FAIL]"))
        if not ok:
            print(pre.decode("utf-8", "replace")[-800:])

        collected = bytearray()

        if a.raw_bytes:
            got = drain(ser, 60, 2.0)
            collected += got
            # 继续收够为止
            t0 = time.time()
            while len(collected) < a.raw_bytes and time.time() - t0 < 120:
                got = drain(ser, 5, 1.0)
                if not got:
                    break
                collected += got
        else:
            for code in a.cmd:
                ser.write(b"\x05")
                time.sleep(0.3)
                ser.write((code + "\n").encode("utf-8"))
                ser.write(b"\x04")
                out = drain(ser, a.wait, a.quiet)
                print("=== %s ===" % code)
                print(out.decode("utf-8", "replace"))
                collected += out

            if a.file:
                code = Path(a.file).read_text(encoding="utf-8")
                ser.write(b"\x05")
                time.sleep(0.4)
                for line in code.splitlines(True):
                    ser.write(line.encode("utf-8"))
                    time.sleep(0.008)
                ser.write(b"\x04")
                out = drain(ser, a.wait, a.quiet)
                print("=== %s ===" % a.file)
                print(out.decode("utf-8", "replace"))
                collected += out

        if a.hex:
            print("--- HEX ---")
            print(" ".join("%02X" % b for b in collected[:2000]))
        if a.out:
            Path(a.out).write_bytes(bytes(collected))
            print("已存到 %s（%d 字节）" % (a.out, len(collected)))
        return 0
    finally:
        ser.close()


if __name__ == "__main__":
    sys.exit(main())
