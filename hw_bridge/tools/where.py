"""只读当前位置（**绝不发运动命令** ✓）。

用途：跑任何会动机器的脚本**之前**，先确认夹爪确实停在预期的参考点上 ✓
（2026-10-04 的教训：脚本以"当前点"为原点，若人没回到参考点就直接跑，
 所有坐标都会整体偏移 ⇒ 危险 ✗✗）。

输出：一段 JSON（给 PowerShell 判断用 ✓），并打印一行人能看的摘要 ✓
用法：
  D:\\python\\python.exe hw_bridge\\tools\\where.py
"""
import json
import sys
import time

sys.path.insert(0, r"hw_bridge\vendor")
import serial  # noqa: E402
from serial.tools import list_ports as lp  # noqa: E402

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass


def find_stm32():
    cands = []
    for p in lp.comports():
        if "1A86:7522" in (p.hwid or "").upper():
            return p.device
        cands.append(p.device)
    for dev in cands:
        try:
            s = serial.Serial(dev, 115200, parity=serial.PARITY_EVEN, timeout=0.3,
                              dsrdtr=False, rtscts=False)
            try:
                s.dtr = False
                s.rts = False
            except Exception:
                pass
            time.sleep(1.2)
            s.reset_input_buffer()
            s.write(b"STATUS\r\n")
            time.sleep(0.8)
            d = s.read(300)
            s.close()
            if b"state=" in d:
                return dev
        except Exception:
            continue
    return None


def main():
    dev = find_stm32()
    if not dev:
        print("NO_STM32")
        return 1
    s = serial.Serial(dev, 115200, parity=serial.PARITY_EVEN, timeout=0.4,
                      dsrdtr=False, rtscts=False)
    try:
        s.dtr = False
        s.rts = False
    except Exception:
        pass
    time.sleep(1.0)
    s.reset_input_buffer()
    s.write(b"STATUS\r\n")
    time.sleep(0.9)
    txt = s.read(400).decode("utf-8", "replace").strip()
    s.close()

    out = {"port": dev, "raw": txt}
    import re
    m = re.search(r"pos=(-?\d+),(-?\d+),(\d+),(\d+)", txt)
    if m:
        out.update({"x": int(m.group(1)), "y": int(m.group(2)),
                    "z": int(m.group(3)), "grip": int(m.group(4))})
        print("POS x=%d y=%d step | Z=%dus grip=%dus | port=%s"
              % (out["x"], out["y"], out["z"], out["grip"], dev))
    else:
        print("PARSE_FAIL " + txt)
    print("JSON " + json.dumps(out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
