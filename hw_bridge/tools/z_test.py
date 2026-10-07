"""只动 Z 的诊断脚本：抬起 → 放下 →（可选）恢复。

⭐ 为什么单独写它（2026-10-04 用户要求"只让 Z 到底试一下" ✓）：
  Z 轴是**舵机**驱动、**没有位置反馈** ✗ ⇒ 固件永远不知道"到了没有" ✓
  只能靠**人眼**看它到底要多久 ✓ ⇒ 这个脚本把每一步的**时刻**打出来 ✓，
  你看着机构，对照时刻就知道"从发出到停稳"用了多久 ✓。

⚠️ 安全：**只发 ZSET** ✗ —— 绝不碰 X/Y、绝不碰夹爪 ✓
   Z 的窗口 = 611~2370µs ✓（611 是舵机实测最低点 ✓，顶住别硬顶 ✗）

用法：
  D:\\python\\python.exe hw_bridge\\tools\\z_test.py
  ... --settle 4        # 每个动作后等几秒（给你看的时间 ✓）
  ... --hold 5          # 到底后保持几秒（听有没有堵转嗡嗡声 ✓）
  ... --restore         # 结束把 Z 抬回最高（回到安全姿态 ✓；不加就停在底部 ✓）
"""
import argparse
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from step_test import Link  # noqa: E402

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

US_MIN, US_MAX = 611, 2370


def find_stm32():
    import serial
    from serial.tools import list_ports as lp
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
            s.write(b"PING\r\n")
            time.sleep(0.7)
            d = s.read(300)
            s.close()
            if b"pong" in d:
                return dev
        except Exception:
            continue
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--up", type=int, default=2370)
    ap.add_argument("--dn", type=int, default=611)
    ap.add_argument("--settle", type=float, default=4.0, help="每个动作后等待秒数（留给你观察 ✓）")
    ap.add_argument("--hold", type=float, default=5.0, help="到底后保持秒数（听堵转声 ✓）")
    ap.add_argument("--restore", action="store_true", help="结束把 Z 抬回最高 ✓")
    a = ap.parse_args()

    up = max(US_MIN, min(US_MAX, a.up))
    dn = max(US_MIN, min(US_MAX, a.dn))

    port = find_stm32()
    if not port:
        print("[FAIL] 没找到 STM32")
        return 1
    print("[i] STM32 = %s" % port)
    print("[i] ⚠️ 本次**只动 Z**：绝不碰 X/Y、绝不碰夹爪 ✓")
    print("[i] Z 窗口 %d~%dµs（611 = 舵机实测最低点 ✓）" % (US_MIN, US_MAX))

    lk = Link(port)
    t0 = time.time()

    def stamp(msg):
        print("   [%6.2fs] %s" % (time.time() - t0, msg))

    try:
        stamp("发送 ZSET %d（抬到最高）" % up)
        r = lk.cmd("ZSET %d" % up, wait=0.6)[0]
        print("        %s" % (r[-1].strip() if r else ""))
        print("        ⏱ 舵机规格：0.17s/60° ⇒ 走满 158° 约 **0.45 秒** ✓")
        print("           → 你看到它停稳用了几秒？_____ 秒")
        time.sleep(a.settle)

        stamp("发送 ZSET %d（放到底 ⭐ 重点看这一步）" % dn)
        r = lk.cmd("ZSET %d" % dn, wait=0.6)[0]
        print("        %s" % (r[-1].strip() if r else ""))
        print("        ⏱ 从这一刻起，看机构什么时候**停稳** → 记下秒数给我 ✓")
        time.sleep(a.hold)

        stamp("保持 %.0f 秒后（若听到持续嗡嗡 = 顶住/堵转 ✗ 要回一点）" % a.hold)

        if a.restore:
            stamp("发送 ZSET %d（恢复安全姿态）" % up)
            r = lk.cmd("ZSET %d" % up, wait=0.6)[0]
            print("        %s" % (r[-1].strip() if r else ""))
            time.sleep(a.settle)
            stamp("已抬回最高 ✓")
        else:
            stamp("按你的要求**停在底部** ✓（要抬起来加 --restore 再跑一次 ✓）")

        print("\n=== 完成 ✓ ===")
        return 0
    finally:
        lk.close()


if __name__ == "__main__":
    sys.exit(main())
