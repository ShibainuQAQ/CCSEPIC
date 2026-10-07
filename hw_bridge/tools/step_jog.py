"""交互式点动台 —— 自己用终端调 X/Y（也支持 Z/夹爪舵机）。

为什么单写一个：找机械极限要"走一点、看一眼、再走一点"，用一条条 `port_probe` 发命令的话
**每次打开串口都会复位 STM32 → 位置计数归零**，根本没法累积 ✗。
这个工具**只开一次口**，全程保持会话；并且自动记录"走过的最小/最大位置"，走完直接读数。

用法（项目根目录）：
  D:\\python\\python.exe hw_bridge\\tools\\step_jog.py --port COM9
  （端口用 `port_probe.py --list` 查：CH340K(1A86:7522) 那个是 STM32）

进去之后（回车即执行，不用记大小写）：
  +  /  -        当前轴 走一步（步长见下）
  2+ / 2-        一次走两步（前面加数字=走几倍）
  m 120          当前轴 走 120mm（可负）
  st 3200        当前轴 走 3200 步（可负；32 位真实计数，找极限最准）
  x / y          切换当前轴
  s 20           设置"一步"的毫米数（默认 10）
  hz 800         设置速度（Hz，默认 800；找极限时建议 600~1000）
  acc 200        加速步数（0=关加减速）
  p              读位置（步 + 毫米）
  0              把当前轴位置记为 0
  0a             两轴都记为 0
  en 1 / en 0    使能 / 失能
  lim off        关软限位     ｜  lim 0 300   设软限位
  e              读 M3? 诊断（acrl/aodr/ticks/rate/half…）
  q              退出（**退出前不会自动 STOP**，动不了就重进发 STOP）
  注意：一进工具就会先 EN 1 + 关软限位 + 两轴对零，并打印 `min/max` 记录起点。
"""
import argparse
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from step_test import Link  # noqa: E402  复用"一次开会话、读到安静为止"的连接层

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass


def parse_pos(lines):
    """从 POS 回执里抠出 (x, y)。"""
    for l in lines:
        if "x=" in l and "y=" in l:
            sx = l.split("x=", 1)[1].split("mm", 1)[0]
            sy = l.split("y=", 1)[1].split("mm", 1)[0]
            try:
                return int(sx), int(sy)
            except ValueError:
                return None
    return None


def parse_smm(lines, default=80):
    """从 POS/M3? 回执里读 SMM（步/mm），用来把"毫米"换算成"步"。

    ⚠️ 为什么要换算：固件的 `MOVE <mm>` 只收**整数毫米**（`parse_i32`），
    所以 `MOVE x 10.0` 会被拒（`ERR 10 mm must be an integer`）。
    本工具改成一律用 `STEP <步数>` 发 —— 步长可以是小数，且**不受 MOVE 的整数限制**。"""
    for l in lines:
        if "smm=" in l:
            try:
                return int(l.split("smm=", 1)[1].split()[0])
            except ValueError:
                return default
    return default


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", default="COM9")
    ap.add_argument("--step", type=float, default=10.0, help="一步多少毫米")
    ap.add_argument("--hz", type=int, default=4000)
    ap.add_argument("--no-setup", action="store_true", help="不自动 EN/ZERO（只连着看）")
    a = ap.parse_args()

    lk = Link(a.port)
    axis = "x"
    step_mm = a.step
    hz = a.hz
    mn = {"x": 0, "y": 0}
    mx = {"x": 0, "y": 0}
    smm = 80

    def pos():
        lines, _ = lk.cmd("POS", wait=0.6)
        return parse_pos(lines)

    def read_smm():
        lines, _ = lk.cmd("POS", wait=0.5)
        return parse_smm(lines, 80)

    def mv_mm(mm):
        """按毫米移动 —— 一律换算成**步**发给固件（避开 MOVE 的整数毫米限制）。"""
        steps = int(round(mm * smm))
        if steps == 0:
            print("    （换算后是 0 步：把步长或 SMM 调大一点）")
            return
        lk.cmd("STEP %s %d" % (axis, steps), wait=0.4)
        lk.wait_idle()
        track(pos())

    def track(p):
        if not p:
            return
        for i, ax in enumerate(("x", "y")):
            v = p[i]
            if v < mn[ax]:
                mn[ax] = v
            if v > mx[ax]:
                mx[ax] = v

    try:
        if not a.no_setup:
            lk.cmd("EN 1", wait=0.8)
            lk.cmd("ACC 200")
            lk.cmd("SPD %d" % hz)
            lk.cmd("LIM off")
            lk.cmd("ZERO all", wait=0.4)
            print("[i] 已 EN 1 / 关软限位 / 两轴对零。当前位置就是 0 ✓")
        st = pos()
        track(st)
        smm = read_smm()
        print("[i] 起始位置：x=%s y=%s（步）｜ SMM=%d 步/mm" % ((st or ("?", "?"))[0], (st or ("?", "?"))[1], smm))
        print("    步长 %.1fmm（= %.0f 步）｜ 速度 %dHz ｜ 当前轴 %s"
              % (step_mm, step_mm * smm, hz, axis.upper()))
        print("    输 + / - 走一步，m <mm> 走指定距离，st <步数> 按步走，x/y 切轴，? 帮助，q 退出")

        while True:
            try:
                line = input("  [%s %.1fmm] > " % (axis.upper(), step_mm)).strip()
            except (EOFError, KeyboardInterrupt):
                break
            if not line:
                continue
            low = line.lower().replace(" ", "")

            if low in ("q", "quit", "exit"):
                break
            if low == "?":
                print(__doc__.split("进去之后")[1][:900])
                continue
            if low in ("p", "pos"):
                track(pos())
                print("    %s: x=%d..%d  y=%d..%d（步，min..max）" % (axis.upper(), mn["x"], mx["x"], mn["y"], mx["y"]))
                continue
            if low in ("e", "m3?"):
                lk.cmd("M3?", wait=0.9)
                continue

            # 倍数前缀：2+ / 3-
            if low[-1] in "+-" and low[:-1].isdigit():
                n = int(low[:-1])
                d = 1 if low[-1] == "+" else -1
                mv_mm(d * n * step_mm)
                continue
            if low in ("+", "-"):
                d = 1 if low == "+" else -1
                mv_mm(d * step_mm)
                continue

            if low in ("x", "y"):
                axis = low
                print("    当前轴 -> %s" % axis.upper())
                continue
            if low in ("0", "zero"):
                lk.cmd("ZERO %s" % axis, wait=0.4)
                mn[axis] = mx[axis] = 0
                print("    %s 已记为 0（min/max 记录也重置）" % axis.upper())
                continue
            if low == "0a":
                lk.cmd("ZERO all", wait=0.4)
                mn = {"x": 0, "y": 0}
                mx = {"x": 0, "y": 0}
                print("    两轴已记为 0")
                continue

            parts = line.split()
            key = parts[0].lower()
            try:
                if key == "m" and len(parts) == 2:
                    mv_mm(float(parts[1]))
                elif key == "st" and len(parts) == 2:
                    lk.cmd("STEP %s %s" % (axis, parts[1]), wait=0.4)
                    lk.wait_idle()
                    track(pos())
                elif key == "s" and len(parts) == 2:
                    step_mm = float(parts[1])
                    print("    步长 = %.1fmm" % step_mm)
                elif key == "hz" and len(parts) == 2:
                    hz = int(parts[1])
                    lk.cmd("SPD %d" % hz)
                elif key == "acc" and len(parts) == 2:
                    lk.cmd("ACC %s" % parts[1])
                elif key == "en" and len(parts) == 2:
                    lk.cmd("EN %s" % parts[1], wait=0.6)
                elif key == "lim" and len(parts) == 1:
                    print("    用法：lim off  ｜  lim <min_mm> <max_mm>")
                elif key == "lim" and len(parts) == 2 and parts[1].lower() == "off":
                    lk.cmd("LIM off", wait=0.4)
                elif key == "lim" and len(parts) == 3:
                    lk.cmd("LIM %s %s %s" % (axis, parts[1], parts[2]), wait=0.4)
                else:
                    print("    不认识的输入：%s（? 看帮助）" % line)
            except ValueError:
                print("    参数不是数字：%s" % line)

        print("\n=== 本次走过范围（步 / 毫米，按 SMM=%s 换算）===" % "80")
        for ax in ("x", "y"):
            print("  %s: %d .. %d 步   (%.1f .. %.1f mm)"
                  % (ax.upper(), mn[ax], mx[ax], mn[ax] / 80.0, mx[ax] / 80.0))
        print("提示：这两个数就是软限位该填的值（记得扣掉 5~10mm 余量）")
        return 0
    finally:
        lk.close()


if __name__ == "__main__":
    sys.exit(main())
