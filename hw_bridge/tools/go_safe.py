"""按已知坐标把夹爪送回「安全位置」，**分小段走 + 每段核对位置**。

为什么不能一步走完（2026-10-04 连续两次意外的教训 ✗）：
  · 若计数器与真实位置不一致（被人为归零过 / 丢步 / 顶过挡块），
    一条"STEP x +8000"会**一路顶到机械极限** ✗✗，只能靠人急停 ✓。
  ⇒ 所以：**每次最多走一小段（默认 20mm），走完立刻读 POS 核对** ✓
      · 位置按预期变化 ⇒ 继续 ✓
      · 位置**没变**（顶住/丢步/被挡）⇒ **立刻停止** ✗ 不再继续 ✓
  · 全程只动 X/Y ✓；Z 一律保持最高（安全姿态 ✓）
  · 最后 `ZERO all` ⇒ 把**安全位置定义为原点** ✓（后续脚本坐标统一 ✓）

用法：
  # 当前在"相对安全位置 (-100, +65)mm"处，要回到 (0,0)
  D:\\python\\python.exe hw_bridge\\tools\\go_safe.py --at -100 65 --speed 2000
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

SMM = 80


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


def pos(lk):
    import re
    for l in lk.cmd("POS", wait=0.6)[0]:
        m = re.search(r"x=(-?\d+).*?y=(-?\d+).*?busy=(\d+)", l)
        if m:
            return int(m.group(1)), int(m.group(2))
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--at", nargs=2, type=float, required=True,
                    help="当前夹爪**相对安全位置**的坐标(mm)，例如 --at -100 65")
    ap.add_argument("--chunk", type=float, default=20.0, help="每小段最大位移(mm)")
    ap.add_argument("--speed", type=int, default=4000)
    a = ap.parse_args()

    port = find_stm32()
    if not port:
        print("[FAIL] 没找到 STM32")
        return 1
    print("[i] STM32 = %s" % port)
    lk = Link(port)
    try:
        lk.cmd("EN 1", wait=0.8)
        lk.cmd("SPD %d" % a.speed)
        lk.cmd("ACC 400")
        lk.cmd("ZSET 2370", wait=0.6)          # 安全姿态：Z 最高 ✓
        time.sleep(1.5)

        cur = pos(lk)
        if cur is None:
            print("[FAIL] 读不到位置")
            return 1
        print("[i] 当前计数 (x, y) = (%d, %d) 步" % cur)

        # 目标位移（相对现在）：把 --at 这点送回 (0,0) ⇒ Δ = (0-at_x, 0-at_y) ✓
        tx = -a.at[0]
        ty = -a.at[1]
        print("[i] 目标位移 ΔX=%+.1fmm  ΔY=%+.1fmm（分小段，每段 ≤%.0fmm ✓）"
              % (tx, ty, a.chunk))

        for axis, total in (("x", tx), ("y", ty)):
            remain = total
            while abs(remain) > 0.01:
                step_mm = max(-a.chunk, min(a.chunk, remain))
                remain -= step_mm
                steps = int(round(step_mm * SMM))
                before = pos(lk)
                print("    走 %s %+.1fmm（%d 步）…" % (axis, step_mm, steps))
                lk.cmd("STEP %s %d" % (axis, steps), wait=0.4)
                t = max(8.0, abs(steps) / float(a.speed) * 3.0 + 4.0)
                lk.wait_idle(timeout=t)
                after = pos(lk)
                if before and after:
                    moved = abs(after[0] - before[0]) + abs(after[1] - before[1])
                    print("      → 现在 (%d, %d)；本段实际动了 %d 步"
                          % (after[0], after[1], moved))
                    if moved < abs(steps) * 0.5:
                        print("✗ 本段只动了 %d / %d 步 —— **疑似顶住或丢步，立即停止** ✗"
                              % (moved, abs(steps)))
                        return 1
        # 到位 ⇒ 把这点定义为原点 ✓
        lk.cmd("ZERO all", wait=0.6)
        c = pos(lk)
        print("\n✅ 已回到安全位置并归零：POS = %s" % (c,))
        print("   Z 保持最高 2370µs ✓   现在脚本坐标就有了统一的原点 ✓")
        return 0
    finally:
        lk.close()


if __name__ == "__main__":
    sys.exit(main())
