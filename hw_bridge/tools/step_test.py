"""X/Y 步进联调工具（线路 A 自备）。

为什么必须专门写一个：
  · **打开串口会让 STM32 复位**（板载 CH340K 的 DTR/RTS 接了复位）。所以"一条命令开一次口"
    的玩法在这里行不通 —— `EN 1` 刚使能，下一次开串口就复位、电机又松了，位置也归零。
  · 这个工具**只开一次口**，然后在同一个会话里把整串命令发完、边发边读回执。

用法（项目根目录）：
  # 只做诊断，不动电机（最安全，先跑这个）
  D:\\python\\python.exe hw_bridge\\tools\\step_test.py --port COM6 --no-move

  # 默认安全序列：诊断 → EN 1 → 各轴 ±5mm 来回
  D:\\python\\python.exe hw_bridge\\tools\\step_test.py --port COM6

  # 自定义：走 10mm、速度 1500Hz
  D:\\python\\python.exe hw_bridge\\tools\\step_test.py --port COM6 --mm 10 --hz 1500

  # 完全自定义命令（分号分隔）
  D:\\python\\python.exe hw_bridge\\tools\\step_test.py --port COM6 --cmd "EN 1;MOVE x 10;POS"

⚠️ 它**不会**替你判"机构能不能走这么远"：**第一次一定用小 mm**（默认 5mm），
   听到"咔咔"顿响（丢步）或顶住的声音，立刻 Ctrl-C（工具退出时不会自动 STOP，请再发一次 STOP）。
"""
import argparse
import re
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
for cand in (HERE.parent / "vendor", Path(r"D:\python\Lib\site-packages")):
    if cand.exists():
        sys.path.insert(0, str(cand))
try:
    import serial
    import serial.tools.list_ports as list_ports
except ImportError:
    print("找不到 pyserial：用 D:\\python\\python.exe 运行，或确认 hw_bridge\\vendor 存在")
    raise SystemExit(1)

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

BOOT_QUIET = ("LOG boot", "LOG diag", "EVT ", "HB ")


class Link:
    def __init__(self, port, baud=115200, boot_wait=2.5):
        self.ser = serial.Serial(port, baud, parity=serial.PARITY_EVEN, timeout=0.2,
                                 write_timeout=3, dsrdtr=False, rtscts=False)
        try:
            self.ser.dtr = False
            self.ser.rts = False
        except Exception:
            pass
        time.sleep(boot_wait)          # 开串口 = 复位，等它启动完
        # ⚠️ CH340 的内部 FIFO 会**跨"打开端口"保留旧数据**：不清干净的话，上一轮的
        #    `M3?`/`POS` 回执会粘到这一轮的回执上，看起来像固件串包或电磁干扰（实际都不是）。
        self.ser.reset_input_buffer()
        self.drain(2.0, quiet=0.5)
        print("[i] 已连 %s（115200 8E1）。⚠️ 开串口已让板子复位一次：位置=0、EN=失能" % port)

    def drain(self, secs, quiet=0.4):
        """读到"安静 quiet 秒"为止（上限 secs 秒）。

        ⚠️ 原来只固定读 secs 秒 → 长回执（如 M3?）没读完，尾巴会粘到**下一条**命令的回执上，
        看起来像固件串包，其实是我读得不够。改成"静默即停"就干净了。"""
        end = time.time() + secs
        last = time.time()
        buf = bytearray()
        while time.time() < end:
            n = self.ser.in_waiting
            if n:
                buf += self.ser.read(n)
                last = time.time()
            elif time.time() - last > quiet:
                break
            else:
                time.sleep(0.02)
        return bytes(buf).decode("utf-8", "replace")

    def cmd(self, text, wait=0.9):
        """发一条命令并收集回执；返回 (回执行列表, 原始文本)。"""
        self.drain(0.15)          # 先把上一条的残渣读干净，避免回执粘包（长回执尤其明显）
        self.ser.write(text.encode() + b"\r\n")
        raw = self.drain(wait)
        lines = [l.strip() for l in raw.splitlines() if l.strip()]
        lines = [l for l in lines if not l.startswith(BOOT_QUIET)]
        print("  >>> %-22s %s" % (text, " | ".join(lines) if lines else "(无应答)"))
        return lines, raw

    def wait_idle(self, timeout=8.0):
        """轮询 POS 直到 busy=0。返回最后一次 POS 的 (x, y) 步数。

        ⚠️⚠️ 2026-10-04 血泪教训：**超时不能拍脑袋定** ✗
        原来默认 8 秒 —— 但 `STEP x -8000`（8000 步）在 600Hz 下要 **13 秒** ✓，
        于是 8 秒超时**静默返回** ✗，调用方以为"走完了"，结果：
          · Z 在 X 还在动的时候就降下去了 ✗✗（危险动作）
          · 下一条运动命令吃 `ERR 15 axis busy` ✗
        现在：调用方必须给**够长的超时**（pick_place 里按距离算 ✓），并且
        **超时后会明确报出来**（返回 None），而不是假装成功 ✓。
        """
        t0 = time.time()
        x = y = None
        while time.time() - t0 < timeout:
            lines, _ = self.cmd("POS", wait=0.35)
            m = None
            for l in lines:
                m = re.search(r"x=(-?\d+).*?y=(-?\d+).*?busy=(\d+)", l)
                if m:
                    break
            if not m:
                continue
            x, y, busy = int(m.group(1)), int(m.group(2)), int(m.group(3))
            if busy == 0:
                return x, y
            time.sleep(0.1)
        print("    ⚠️ wait_idle 超时（%.1fs）—— 电机可能还在动 ✗" % timeout)
        return None

    def close(self):
        self.ser.close()


def run_suite(lk, a):
    """回归套件：全部自动判 PASS/FAIL。**不发超过 20mm 的运动**，安全。"""
    results = []

    def check(name, ok, detail=""):
        results.append((ok, name, detail))
        print("  [%s] %s %s" % ("PASS" if ok else "FAIL", name, detail))

    print("\n=== 步进回归套件 ===")
    lk.cmd("VER")
    lk.cmd("EN 1", wait=0.8)
    lk.cmd("ACC 200")
    lk.cmd("LIM off", wait=0.4)
    lk.cmd("ZERO all", wait=0.4)

    # ---- 1. 重复精度：5 个来回，每次都必须精确回 0 ----
    print("\n[1] 重复精度（5 × ±10mm，每次都要精确回 0）")
    lk.cmd("SPD 1600")
    worst = 0
    for i in range(5):
        lk.cmd("MOVE x 10", wait=0.4)
        _, _ = lk.wait_idle()
        lk.cmd("MOVE x -10", wait=0.4)
        px, _ = lk.wait_idle()
        worst = max(worst, abs(px or 0))
        if (px or 0) != 0:
            print("      第 %d 轮没回 0：x=%s" % (i + 1, px))
    check("5 个来回后精确回 0", worst == 0, "最大偏差 %d 步" % worst)

    # ---- 2. 限速扫描：不同 SPD 下都要能走完（走不完会 busy 卡住→超时） ----
    print("\n[2] 限速扫描（每个速度走 10mm 来回，看能不能顺利完成）")
    for hz in (800, 1600, 2400, 3200):
        lk.cmd("SPD %d" % hz)
        t0 = time.time()
        lk.cmd("MOVE x 10", wait=0.3)
        p1 = lk.wait_idle(timeout=6.0)
        t1 = time.time() - t0
        lk.cmd("MOVE x -10", wait=0.3)
        p2 = lk.wait_idle(timeout=6.0)
        done = (p1 and p1[0] == 800 and p2 and p2[0] == 0)
        check("%dHz 10mm 来回" % hz, done, "用时 %.2fs，终点 x=%s → %s" % (t1, p1 and p1[0], p2 and p2[0]))

    # ---- 3. 异常路径（这些是"安全门"必须正确拒绝的情况） ----
    print("\n[3] 异常路径（该拒的要拒）")
    lk.cmd("SPD 1600")
    lk.cmd("MOVE x 20", wait=0.2)              # 不等它走完，立刻再发 → 应 ERR 15 busy
    lines, _ = lk.cmd("MOVE x 20", wait=0.5)
    check("运动中再发 MOVE → ERR 15", any("ERR 15" in l for l in lines), " | ".join(lines))
    lk.wait_idle()
    lk.cmd("STOP")

    lines, _ = lk.cmd("MOVE x 0", wait=0.4)    # 0 步数 → ERR 10
    check("MOVE 0mm → ERR 10", any("ERR 10" in l for l in lines), " | ".join(lines))

    lk.cmd("EN 0", wait=0.5)
    lines, _ = lk.cmd("MOVE x 5", wait=0.5)    # 未使能 → ERR 16
    check("未使能 MOVE → ERR 16", any("ERR 16" in l for l in lines), " | ".join(lines))
    lk.cmd("EN 1", wait=0.5)

    lk.cmd("LIM x -5 5", wait=0.4)
    lines, _ = lk.cmd("MOVE x 10", wait=0.5)   # 超软限位 → ERR 17
    check("超软限位 → ERR 17", any("ERR 17" in l for l in lines), " | ".join(lines))
    lk.cmd("LIM off", wait=0.4)

    lines, _ = lk.cmd("HOMED 0", wait=0.4)
    lines, _ = lk.cmd("MOVE x 5", wait=0.5)    # 未定位 → ERR 11
    check("未对零 MOVE → ERR 11", any("ERR 11" in l for l in lines), " | ".join(lines))
    lk.cmd("ZERO all", wait=0.4)

    # ---- 4. 无加减速对照（ACC 0）----
    print("\n[4] ACC 0（关加减速）也要能走完")
    lk.cmd("ACC 0")
    lk.cmd("SPD 800")
    lk.cmd("MOVE x 10", wait=0.4)
    p = lk.wait_idle(timeout=6.0)
    check("ACC0 800Hz 10mm", bool(p and p[0] == 800), "终点 x=%s" % (p and p[0],))
    lk.cmd("ACC 200")

    # ---- 收尾：恢复安全默认 ----
    lk.cmd("SPD 1600")
    lk.cmd("ZERO all", wait=0.4)
    lk.cmd("LIM x -400 400", wait=0.3)
    lk.cmd("LIM y -400 400", wait=0.3)
    lk.cmd("POS")

    ok_n = sum(1 for ok, _, _ in results if ok)
    print("\n=== 套件结果：%d/%d 通过 ===" % (ok_n, len(results)))
    for ok, name, detail in results:
        if not ok:
            print("  ✗ %s %s" % (name, detail))
    return 0 if ok_n == len(results) else 1


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", default="COM6")
    ap.add_argument("--baud", type=int, default=115200)
    ap.add_argument("--mm", type=int, default=5, help="每轴试走多少毫米（第一次别调大）")
    ap.add_argument("--hz", type=int, default=1200, help="目标步频")
    ap.add_argument("--acc", type=int, default=200, help="加速步数（0=关加减速）")
    ap.add_argument("--no-move", action="store_true", help="只诊断，不发任何运动命令")
    ap.add_argument("--cmd", default=None, help="自定义命令串（分号分隔），给了就只跑它")
    ap.add_argument("--suite", action="store_true",
                    help="跑回归套件：重复精度 / 限速扫描 / 异常路径（自动判 PASS/FAIL）")
    ap.add_argument("--list", action="store_true")
    a = ap.parse_args()

    if a.list:
        for p in list_ports.comports():
            print("  %-6s | %s | %s" % (p.device, p.description, p.hwid))
        return 0

    lk = Link(a.port, a.baud)
    try:
        if a.suite:
            return run_suite(lk, a)
        if a.cmd:
            for one in a.cmd.split(";"):
                one = one.strip()
                if one:
                    lk.cmd(one, wait=0.6)
            return 0

        # ---- 1. 诊断（不动电机）----
        print("\n[1] 诊断")
        lk.cmd("VER")
        diag, _ = lk.cmd("M3?")
        d = " ".join(diag)
        ok_acrl = "acrl=" in d
        if ok_acrl:
            m = re.search(r"acrl=(\d+)", d)
            acrl = int(m.group(1)) if m else 0
            print("    acrl = 0x%08X   （应为 0x33334B33；PA5=3 才说明 Y 轴 PUL 配成输出了）" % acrl)
            m = re.search(r"aodr=(\d+)", d)
            if m:
                odr = int(m.group(1))
                print("    aodr 低8位 = 0b%s （应为 10010000：PA4/PA7=EN 高=失能，PA0/PA1/PA5/PA6=PUL/DIR 低）"
                      % format(odr & 0xFF, "08b"))
        lk.cmd("POS")

        if a.no_move:
            print("\n[2] --no-move：到此为止，没有发任何运动命令 ✓")
            return 0

        # ---- 2. 使能 + 参数 ----
        print("\n[2] 使能 + 对零（EN 后电机应立刻自锁：手拧轴拧不动）")
        lk.cmd("EN 1", wait=0.8)
        lk.cmd("SPD %d" % a.hz)
        lk.cmd("ACC %d" % a.acc)
        lk.cmd("ZERO all", wait=0.4)      # 手动对零：同时置"已定位"，运动安全门才放行

        # ---- 3. 每轴小步来回 ----
        for axis in ("x", "y"):
            print("\n[3] %s 轴：+%dmm / -%dmm" % (axis.upper(), a.mm, a.mm))
            lk.cmd("MOVE %s %d" % (axis, a.mm), wait=0.5)
            p1 = lk.wait_idle()
            print("      到位后 POS: x=%s y=%s" % p1)
            lk.cmd("MOVE %s %d" % (axis, -a.mm), wait=0.5)   # ⚠️ 别写成 -%d 再传负数 → 会变成 --5
            p2 = lk.wait_idle()
            print("      回来之后 POS: x=%s y=%s" % p2)

        print("\n[4] 收尾")
        lk.cmd("M3?")
        lk.cmd("POS")
        print("\n判读：")
        print("  · 两条 POS 的差应 ≈ 0（闭环不丢步；差得远 = 丢步或方向线接反）")
        print("  · %dmm 的理论步数 = %d × SMM(默认80)" % (a.mm, a.mm))
        return 0
    finally:
        lk.close()


if __name__ == "__main__":
    sys.exit(main())
