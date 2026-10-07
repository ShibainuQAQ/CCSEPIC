"""手动抓放台 —— 人手动控制 Z 上下 / 夹爪开合 / X-Y 点动。

为什么单做一个：调试阶段最需要的是"**人盯着机构、手指随时能停**"的实时控制 ✗，
而不是一条条发命令 ✓。这个工具把常用动作绑到单键上，并**实时显示**当前 x/y/z/夹爪 ✓。

用法（项目根目录，端口会自动认，不用写 --port ✓）：
  D:\\python\\python.exe hw_bridge\\tools\\manual_pick.py

按键（回车执行 ✓）：
  ── Z 轴（脉宽 µs）
    w / s        Z 抬 / 降，按当前步长（默认 100µs）
    W / S        大步（×5）
  ── 夹爪（脉宽 µs；20°=722µs 最小开、150°=2167µs 最大开）
    a / d        夹爪 张开 / 夹紧，按当前步长
    A / D        大步（×5）
  ── X / Y（毫米）
    i / k        Y 后退 / 前进       j / l   X 左 / 右
    I / K / J / L  大步（×5）
  ── 记录/回到位置
    mz          把当前 Z 记为"抬起位"      mz2  记为"放下位"
    mg          把当前夹爪记为"张开位"      mg2  记为"夹住位"
    gz / gz2    去 Z 抬起位 / 放下位        gg / gg2  去夹爪张开位 / 夹住位
  ── 其它
    p           打印当前状态（x y 步/mm、z、夹爪、软限位）
    + / -       步长 ×2 / ÷2（Z、夹爪、X/Y 各自记）
    z           当前 X/Y 位置记为 0
    h           帮助        x 或 q   退出
"""
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

US_MIN, US_MAX = 600, 2400          # 固件允许的脉宽窗口（硬窗口，再往里还要按舵机收）
# ⚠️ 2026-10-04：**每个轴的安全窗口不一样** ✗，原来统一用 600~2400 是错的 ✓
#    · Z 轴：实测 10°~170° ⇒ 611~2389µs
#      ⭐ 用户当天要求把下限从 630 放到 **611** ✓ —— 因为 630 会**卡住"再往下一点"的需求** ✗
#      （夹不同高度的货需要不同的 Z 最低点 ✓；蓝色三角锥矮，630 夹不起来 ✓）
#      ⚠️⚠️ 但越接近 611 越贴近**舵机内部止挡**：**听到持续嗡嗡就往回收 20~30µs** ✗
#    · 夹爪：实测 20°~150° ⇒ 722~2167µs ⇒ 用 750/2140 留余量 ✓
#    超出窗口 = **顶在舵机内部止挡上持续堵转 = 烧舵机** ✗（本项目已经烧过一颗 ✓）。
LIMITS = {"z": (611, 2370), "grip": (750, 2140)}
Z_START, G_START = 1500, 1500


def find_port():
    """板载 CH340K 直认；否则逐个串口 8E1 发 PING，谁回 OK pong 谁是 STM32。

    ⚠️ 特别注意"**端口被别的程序占着**"这种情况：原来一律报"没找到 STM32" ✗，
    会让人以为是板子/接线的问题 ✓。其实最常见的原因是**上一次的工具还开着** ✗
    （比如手动台没退出又开了一次 ✓），所以这里单独报出来 ✓。
    """
    import serial
    from serial.tools import list_ports as lp
    cands = []
    for p in lp.comports():
        if "1A86:7522" in (p.hwid or "").upper():
            return p.device
        cands.append(p.device)
    busy = []
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
        except Exception as exc:
            if "Permission" in repr(exc) or "拒绝" in repr(exc):
                busy.append(dev)
            continue
    if busy:
        print("⚠️ 这些串口**被别的程序占着**打不开：%s" % ", ".join(busy))
        print("   → 多半是上一次的工具（手动台/点动台）还开着 ✗ 先退出它再试 ✓")
    return None


def main():
    port = find_port()
    if not port:
        print("[FAIL] 没找到 STM32（既没有 CH340K，也没有哪个串口回 OK pong）")
        print("       检查：板子供电、USB-TTL 的 TXD→PA10 / RXD→PA9 / GND→GND、BOOT0=0")
        return 1
    print("[i] STM32 = %s" % port)
    lk = Link(port)
    lk.cmd("EN 1", wait=0.8)
    lk.cmd("SPD 4000")      # 2026-10-04 用户要求：手动台也跑 4000Hz（=50mm/s）
    lk.cmd("ACC 400")       # 高速必须配足加速段，否则起步丢步 ✗
    # ⚠️ 必须先"声明位置"才能动 ✗：固件会对运动命令回 `ERR 11 not homed`
    #    （实测：刚上电时 X/Y 一 move 就被拒 ✓）—— `ZERO all` 把**当前位置**定义为 0 ✓。
    lk.cmd("ZERO all", wait=0.6)

    # 从固件读 SMM（步/mm），X/Y 一律换算成**步**再发：
    # 固件的 `MOVE <mm>` 只收整数毫米 ✗，步长一小数（0.6mm）就被拒 ✓。
    smm = 80
    for l in lk.cmd("POS", wait=0.6)[0]:
        if "smm=" in l:
            try:
                smm = int(l.split("smm=", 1)[1].split()[0])
            except ValueError:
                pass

    z, g = Z_START, G_START
    step_z, step_g, step_xy = 100, 100, 5.0
    saved = {}

    def clamp(v, axis="z"):
        lo, hi = LIMITS[axis]
        return max(lo, min(hi, v))

    def setz(v):
        nonlocal z
        want = int(v)
        z = clamp(want)
        if z != want:
            print("    ⚠️ Z 收到安全窗口 %d~%dµs 内：%d → %d" % (LIMITS["z"][0], LIMITS["z"][1], want, z))
        lk.cmd("ZSET %d" % z, wait=0.5)

    def setg(v):
        nonlocal g
        want = int(v)
        g = clamp(want, "grip")
        if g != want:
            print("    ⚠️ 夹爪收到安全窗口 %d~%dµs 内：%d → %d"
                  % (LIMITS["grip"][0], LIMITS["grip"][1], want, g))
        lk.cmd("GSET %d" % g, wait=0.5)

    def show():
        p = pos_xy()
        print("    Z=%dµs  夹爪=%dµs  |  x=%s y=%s 步"
              % (z, g, p[0] if p else "?", p[1] if p else "?"))

    def pos_xy():
        lines, _ = lk.cmd("POS", wait=0.6)
        for l in lines:
            if "x=" in l and "y=" in l:
                try:
                    sx = int(l.split("x=", 1)[1].split("mm", 1)[0])
                    sy = int(l.split("y=", 1)[1].split("mm", 1)[0])
                    return (sx, sy)
                except ValueError:
                    return None
        return None

    def goto_xy(target):
        cur = pos_xy()
        if cur is None:
            print("    读不到当前位置 ✗")
            return
        dx, dy = target[0] - cur[0], target[1] - cur[1]
        if dx:
            lk.cmd("STEP x %d" % dx, wait=0.4)
        if dy:
            lk.cmd("STEP y %d" % dy, wait=0.4)
        lk.wait_idle()

    # ⭐ 2026-10-04：**启动时绝不主动动舵机** ✗
    #    原来这里会 ZSET/GSET 到 1500 —— 于是每次开工具，Z 和夹爪都会"啪"地被拽到中位 ✗，
    #    调试时非常碍事（而且可能撞到东西 ✗）。现在改成**从固件读取当前值**做初值 ✓：
    #    `TIM3?` 的 ccr3 = Z、ccr4 = 夹爪（脉宽 µs ✓），读到什么就是什么 ✓，一个字节都不发 ✓。
    for l in lk.cmd("TIM3?", wait=0.9)[0]:
        if "ccr3=" in l:
            try:
                z = int(l.split("ccr3=", 1)[1].split()[0])
                g = int(l.split("ccr4=", 1)[1].split()[0])
                print("[i] 读到当前：Z=%dµs 夹爪=%dµs（**没有动它们** ✓）" % (z, g))
            except (ValueError, IndexError):
                pass

    print("[i] 就绪（启动没动舵机 ✓）：按 h 看帮助，x 退出。")
    show()

    while True:
        try:
            cmd = input("  > ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if not cmd:
            continue
        c = cmd
        low = c.lower()

        if low in ("x", "q", "quit", "exit"):
            break
        if low == "h":
            print(__doc__.split("按键", 1)[1][:1200])
            continue
        if low == "p":
            show()
            continue
        if low == "+":
            step_z *= 2
            step_g *= 2
            step_xy *= 2
            print("    步长：Z/夹爪 %dµs，X/Y %.1fmm" % (step_z, step_xy))
            continue
        if low == "-":
            step_z = max(10, step_z // 2)
            step_g = max(10, step_g // 2)
            step_xy = max(0.5, step_xy / 2)
            print("    步长：Z/夹爪 %dµs，X/Y %.1fmm" % (step_z, step_xy))
            continue
        if low == "z":
            lk.cmd("ZERO all", wait=0.4)
            print("    X/Y 已记为 0")
            continue

        # 记录 / 前往
        if low in ("mz", "mz2", "mg", "mg2"):
            saved[low] = (z if low.startswith("mz") else g)
            print("    记住 %s = %dµs" % (low, saved[low]))
            continue
        if low in ("mx", "mx2"):
            p = pos_xy()
            if p is None:
                print("    读不到位置 ✗")
                continue
            saved[low] = p
            print("    记住 %s = x=%d y=%d 步（= %.1f, %.1f mm）"
                  % (low, p[0], p[1], p[0] / smm, p[1] / smm))
            continue
        if low in ("gx", "gx2"):
            src = {"gx": "mx", "gx2": "mx2"}[low]
            if src not in saved:
                print("    还没记录 %s（先按 %s）" % (src, src))
                continue
            goto_xy(saved[src])
            show()
            continue
        if low in ("gz", "gz2", "gg", "gg2"):
            src = {"gz": "mz", "gz2": "mz2", "gg": "mg", "gg2": "mg2"}[low]
            if src not in saved:
                print("    还没记录 %s（先按 %s）" % (src, src))
                continue
            (setz if low.startswith("gz") else setg)(saved[src])
            show()
            continue

        # Z / 夹爪
        if c in ("w", "W", "s", "S"):
            d = step_z * (5 if c.isupper() else 1)
            setz(z + (d if c in ("w", "W") else -d))
            show()
            continue
        if c in ("a", "A", "d", "D"):
            d = step_g * (5 if c.isupper() else 1)
            setg(g + (d if c in ("a", "A") else -d))
            show()
            continue

        # X / Y
        if c in ("i", "I", "k", "K", "j", "J", "l", "L"):
            d = step_xy * (5 if c.isupper() else 1)
            axis = "y" if low in ("i", "k") else "x"
            sign = 1 if low in ("l", "k") else -1
            steps = int(round(sign * d * smm))
            if steps == 0:
                print("    换算后是 0 步（步长太小，用 + 放大一点）")
                continue
            lk.cmd("STEP %s %d" % (axis, steps), wait=0.4)  # 按步走：不受"整数毫米"限制 ✓
            lk.wait_idle()
            show()
            continue

        print("    不认识的输入：%s（按 h 看帮助）" % cmd)

    print("\n=== 结束时状态（把下面整段发我 ✓）===")
    print("  Z=%dµs  夹爪=%dµs" % (z, g))
    for k in ("mz", "mz2", "mg", "mg2"):
        if k in saved:
            print("  %s = %dµs" % (k, saved[k]))
    for k in ("mx", "mx2"):
        if k in saved:
            print("  %s = x=%d y=%d 步（%.1f, %.1f mm）"
                  % (k, saved[k][0], saved[k][1], saved[k][0] / smm, saved[k][1] / smm))

    # ⭐ 直接拼好"全流程"命令，复制即可用 ✓
    if all(k in saved for k in ("mx", "mx2", "mz", "mz2", "mg", "mg2")):
        dx = (saved["mx2"][0] - saved["mx"][0]) / float(smm)
        dy = (saved["mx2"][1] - saved["mx"][1]) / float(smm)
        print("\n=== 可直接复制的全流程命令（先空跑 ✓）===")
        print("D:\\python\\python.exe hw_bridge\\tools\\pick_place.py ^")
        print("    --item 0 0 --place %.1f %.1f ^" % (dx, dy))
        print("    --zup %d --zdn %d --gopen %d --gclose %d"
              % (saved["mz"], saved["mz2"], saved["mg"], saved["mg2"]))
        print("\n（确认轨迹没问题后，在命令末尾加 --yes 就会真的夹货 ✓）")
    else:
        print("\n  ⚠️ 还没记全：需要 mz/mz2（Z 抬起/放下）+ mg/mg2（夹爪开/合）+ mx/mx2（货物/放置位置）")

    lk.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
