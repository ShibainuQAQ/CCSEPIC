"""全流程：抓取 → 放置（一轮）。

设计原则（都是被今天踩的坑逼出来的 ✗）：
  ① **端口自动识别**：用 8E1 发 PING，谁回 OK pong 谁是 STM32 ✓
     （这块 USB-TTL 的 COM 号今天从 COM6→9→12→13→14 变了 5 次 ✗）
  ② **坐标用"相对偏移"**：装置没有回零开关 ✗，所以脚本先 `ZERO all`
     把**当前夹爪位置**定义为 (0,0) ⇒ 你只要给"放置点相对货物的偏移" ✓
     操作法：把夹爪点动到**货物正上方** → 记下 `p` 的位置 → 再点动到**放置点** → 差值就是 (dx, dy) ✓
  ③ **一律用 `STEP <步数>` 发运动**（固件 MOVE 只收整数毫米 ✗，小数步长会被拒）
  ④ **每条命令都查回执**，一见 `ERR` 立刻停 ✗
  ⑤ **默认只"空跑"轨迹**（不真的夹紧 ✗）；真要夹货必须显式加 `--yes` ✓

用法（项目根目录）：
  # 1) 先空跑一遍看轨迹（不夹货 ✓）
  D:\\python\\python.exe hw_bridge\\tools\\pick_place.py --place 120 -80 ^
      --zup 900 --zdn 2000 --gopen 2100 --gclose 800

  # 2) 确认无误后再真的夹（加 --yes ✓）
  ... 同上 ... --yes

参数：
  --item  X Y      货物坐标（默认 0 0 = 你把夹爪停在货物正上方再跑 ✓）
  --place X Y      放置点坐标（相对同一个 0 点 ✓）
  --zup   µs       Z 抬起位（越高越安全 ✓）
  --zdn   µs       Z 放下位（夹爪能碰到货 ✓）
  --gopen µs       夹爪张开位
  --gclose µs      夹爪夹紧位
  --speed Hz       步进速度（默认 600，慢＝安全 ✓）
  --pause s        每个动作后的停顿（默认 0.4s ✓ 方便你看）
  --yes            真的执行夹紧/张开（不加就是空跑 ✓）
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


def find_port():
    import serial
    from serial.tools import list_ports as lp
    cands, busy = [], []
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
        except Exception as exc:
            if "Permission" in repr(exc) or "拒绝" in repr(exc):
                busy.append(dev)
    if busy:
        print("⚠️ 串口被占用：%s（先退出其他工具 ✗）" % ", ".join(busy))
    return None


class Deck:
    def __init__(self, port, pause, speed=5000, zsettle=5.0, gsettle=0.35, zlift=5.0):
        self.lk = Link(port)
        self.pause = pause
        self.speed = speed
        self.zsettle = zsettle      # Z **放下**用（要等够 ✓ 5.0s ⇒ 加补偿后 6.0s ✓）
        self.gsettle = gsettle      # 夹爪用（行程短 ✓ 0.35s ✓）
        self.zlift = zlift          # Z **抬起**用
        # ⭐ 2026-10-04 用户要求"每一步的等待时间给我放出来" ✓ ⇒ 记录每步**实际用时** ✓
        self.timeline = []          # [(说明, 秒数), ...] 结束时打成表 ✓
        self.smm = 80
        for l in self.lk.cmd("POS", wait=0.6)[0]:
            if "smm=" in l:
                try:
                    self.smm = int(l.split("smm=", 1)[1].split()[0])
                except ValueError:
                    pass

    def send(self, code, wait=0.5):
        lines, _ = self.lk.cmd(code, wait=wait)
        txt = " | ".join(x.strip() for x in lines if x.strip())
        print("    %-22s %s" % (code, txt[:90]))
        if "ERR" in txt:
            raise RuntimeError("板子回了错误：%s" % txt)
        return txt

    def _log(self, label, t0):
        """记录一步的**实际耗时**（含等舵机的时间 ✓），并立刻打印一行 ✓。"""
        sec = time.time() - t0
        self.timeline.append((label, sec))
        print("      ⏱ %-26s 用时 %.1f 秒" % (label, sec))

    def z(self, us, lift=False):
        """Z 动作。`lift=True` 表示**抬起** ✓（抬起用 zlift ✓；放下用 zsettle ✓ 长）。

        ⚠️ 为什么要分开（2026-10-04 用户要求"再快一点" ✓）：
          · **放下**：必须等够 ✓（用户要求"放下后等满 6 秒再夹取" ✓）—— 舵机没有位置反馈 ✗，
            只能靠时间保证"真到底了" ✓
          · **抬起**：也要等够 ✓（用户后来要求抬起同样等 5.0s ✓）
        """
        t0 = time.time()
        self.send("ZSET %d" % us)
        wait = self.zlift if lift else self.zsettle
        time.sleep(wait)
        self._log("Z%s %dµs（等 %.1fs）" % ("抬起" if lift else "放下", us, wait), t0)

    def grip(self, us):
        t0 = time.time()
        self.send("GSET %d" % us)
        # ⭐ 模拟舵机**本身没有调速** ✗ ⇒ 只能"别让软件干等" ✓。
        #    夹爪行程短（950↔1850 = 900µs ✓，远小于 Z 的 1759µs ✓）⇒ 单独用 gsettle ✓
        time.sleep(self.gsettle)
        self._log("夹爪 %dµs（等 %.2fs）" % (us, self.gsettle), t0)

    def move(self, x_mm, y_mm):
        t0 = time.time()
        total = 0
        for axis, mm in (("x", x_mm), ("y", y_mm)):
            steps = int(round(mm * self.smm))
            if steps == 0:
                continue
            total += abs(steps)
            self.send("STEP %s %d" % (axis, steps), wait=0.4)
        # ⚠️ 超时必须**按距离算** ✗：8000 步 @600Hz ≈ 13s，用固定 8s 会静默提前返回，
        #    于是 Z 会在 X 还没停的时候就降下去 ✗✗（2026-10-04 实际踩到过）。
        timeout = max(10.0, total / float(self.speed) * 3.0 + 5.0)
        if self.lk.wait_idle(timeout=timeout) is None:
            raise RuntimeError("移动未在 %.0fs 内完成（电机可能还在动 ✗）" % timeout)
        self.send("POS", wait=0.6)
        self._log("移动 Δ(%.1f, %.1f)mm 共 %d 步" % (x_mm, y_mm, total), t0)

    def wait(self, extra=0.0):
        time.sleep(self.pause + extra)

    def report(self):
        """⭐ 把每一步的实际用时打成表 ✓（用户要求"每一步的等待时间放出来" ✓）。"""
        print("\n" + "=" * 62)
        print("  每步实际用时（含等舵机的时间 ✓）")
        print("=" * 62)
        for i, (lab, sec) in enumerate(self.timeline, 1):
            print("   %2d. %-34s %6.1f 秒" % (i, lab, sec))
        tot = sum(s for _, s in self.timeline)
        print("   " + "-" * 58)
        print("   合计（动作+等待）%28.1f 秒" % tot)
        print("=" * 62)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--item", nargs=2, type=float, default=[0.0, 0.0])
    ap.add_argument("--place", nargs=2, type=float, required=True)
    ap.add_argument("--zup", type=int, required=True)
    ap.add_argument("--zdn", type=int, required=True)
    ap.add_argument("--gopen", type=int, required=True)
    ap.add_argument("--gclose", type=int, required=True)
    ap.add_argument("--speed", type=int, default=5000)   # 固件上限 5000Hz = 62.5mm/s ✓
    ap.add_argument("--pause", type=float, default=0.25)
    ap.add_argument("--zsettle", type=float, default=6.0,
                    help="**Z 放下**后等待秒数。⭐ 用户 2026-10-04 要求改为 **6.0s** ✓"
                         "（另有 0.85s 的 pause 补偿 ⇒ 实测约 6.85s ✓）")
    ap.add_argument("--gsettle", type=float, default=0.35,
                    help="**夹爪** 每次开合后等待秒数。⚠️ 舵机转速是**硬件固定**的 ✗"
                         "（MG996R 0.17s/60° ⇒ 走 81° 约 **0.23s**）⇒ 软件只需等够时间 ✓"
                         "默认 0.35s ≈ 1.5 倍物理时间 ✓（原来 0.8s 是白等 ✗）")
    ap.add_argument("--park", nargs=2, type=float, default=None,
                    help="【已停用】结束时的 XY 移动 —— 出于安全，流程结束后一律原地停住 ✗")
    ap.add_argument("--drop", action="store_true",
                    help="⭐ 到放置点后**不下 Z**、夹爪直接开到最大(2140µs)，让货自由下落 ✓")
    ap.add_argument("--yes", action="store_true", help="真的夹紧/张开；不加＝空跑 ✓")
    ap.add_argument("--allow-plusx", action="store_true",
                    help="⚠️ 允许向 +X（右）移动。默认**禁止** ✗（当前已在 +X 极限上）")
    ap.add_argument("--zlift", type=float, default=5.0,
                    help="Z **抬起**后等待秒数。⭐ 用户 2026-10-04 要求改成 **5.0s** ✓"
                         "（与放下同档 ⇒ 加补偿后均为 6.0s ✓）")
    a = ap.parse_args()

    # ⚠️ **每个轴的安全窗口不同** ✗（统一用 600~2400 是错的 ✓）：
    #    Z 轴实测 611~2389µs（固件端点 630/2370）；夹爪实测 722~2167µs（用 750/2140 留余量）。
    #    超窗口 = 顶在舵机内部止挡上堵转 = 烧舵机 ✗（本项目已烧过一颗 ✓）。越界就**自动收进来** ✓。
    def clamp(v, lo, hi):
        return max(lo, min(hi, v))

    # ⭐ 2026-10-04：Z 下限放宽到 **611**（舵机实测最低点 ✓）—— 原来卡 630 会**夹不起矮货** ✗
    #    （蓝色三角锥矮，630 够不着 ⇒ 实测"夹不起来" ✓；用户手动调到 611 ✓）
    a.zup = clamp(a.zup, 611, 2370)
    a.zdn = clamp(a.zdn, 611, 2370)
    a.gopen = clamp(a.gopen, 750, 2140)
    a.gclose = clamp(a.gclose, 750, 2140)
    print("[i] 安全窗口已套用：Z 611~2370µs ｜ 夹爪 750~2140µs")
    print("    Z 抬起=%d 放下=%d    夹爪 张开=%d 夹紧=%d" % (a.zup, a.zdn, a.gopen, a.gclose))
    print("    等待：Z放下 %.1fs（实到 %.1fs）｜ Z抬起 %.1fs ｜ 夹爪 %.1fs ｜ 速度 %dHz"
          % (a.zsettle, a.zsettle + 1.0, a.zlift, a.gsettle, a.speed))

    port = find_port()
    if not port:
        print("[FAIL] 没找到 STM32")
        return 1
    print("[i] STM32 = %s   SMM=%s" % (port, "?"))
    d = Deck(port, a.pause, a.speed, a.zsettle, a.gsettle, a.zlift)
    print("[i] SMM=%d 步/mm   模式=%s" % (d.smm, "真夹" if a.yes else "空跑（不夹）"))

    ix, iy = a.item
    px, py = a.place
    dx, dy = px - ix, py - iy

    # ⛔ 2026-10-04 安全规则（用户明确告知 ✓）：
    #    **起点（脚本启动时的位置）就是 +X 的机械极限** ✗ ⇒
    #    **任何时刻的 X 位置都不得超过 0**（相对起点 ✓）——
    #    这样：正常流程（抓完搬走、再回到起点）**照常允许** ✓，
    #          而"一开始就往 +X 走"或"冲过起点"一律拒绝 ✗✓。
    #    ⚠️ 注意：**不是禁止所有 +X 动作** ✓ —— 搬货回程就是 +X ✓ 允许 ✓；
    #       只有"越过起点"才危险 ✓。
    bad = []
    if ix > 0:
        bad.append("--item X=%.1f > 0（会越过起点 ✗）" % ix)
    if px > 0:
        bad.append("--place X=%.1f > 0（会越过起点 ✗）" % px)
    if bad and not a.allow_plusx:
        print("⛔ 拒绝执行：有 X 位置**超过了起点** ✗✗")
        for b in bad:
            print("   · %s" % b)
        print("   原因：起点即 **+X 机械极限**（用户 2026-10-04 明确告知 ✗）")
        print("   ⇒ 请让所有 X 坐标 ≤ 0（例如 --item 0 0 --place -100 0 ✓）")
        print("   ⇒ 确认 +X 还有余量时才加 --allow-plusx ✓")
        return 3

    print("\n=== 流程 ===")
    print("  货物 (%.1f, %.1f)   放置 (%.1f, %.1f)   偏移 (%.1f, %.1f)mm"
          % (ix, iy, px, py, dx, dy))
    print("  Z: 抬起 %dµs / 放下 %dµs    夹爪: 张开 %dµs / 夹紧 %dµs"
          % (a.zup, a.zdn, a.gopen, a.gclose))

    try:
        d.send("EN 1", wait=0.8)
        d.send("SPD %d" % a.speed)
        d.send("ACC 400")
        d.send("ZERO all", wait=0.5)          # ← 把"当前夹爪位置"定义为 0 点 ✓
        print("\n[1/8] Z 抬起")
        d.z(a.zup, lift=True); d.wait()

        print("[2/8] 移到货物上方 (%.1f, %.1f)" % (ix, iy))
        d.move(ix, iy); d.wait()

        print("[3/8] Z 放下（夹爪套住货）")
        # ⭐ 用户要求"Z 放下后等满 6 秒再夹取" ✓ ⇒ 5.0(zsettle) + 0.25(pause) + **0.75** = **6.0 秒** ✓
        #    （pause 从 0.4 改成 0.25 后，这里必须补到 0.75 才对得上 6.0 ✗）
        d.z(a.zdn); d.wait(0.75)

        print("[4/8] 夹爪%s" % ("夹紧" if a.yes else "（空跑-不夹）"))
        if a.yes:
            d.grip(a.gclose); d.wait(0.6)

        print("[5/8] Z 抬起（提货）")
        # ⭐ 用户 2026-10-04 要求：**Z 抬起到移到放置点之间等 6 秒** ✓
        #    ⇒ zlift 5.0 + pause 0.25 + **0.75** = **6.0 秒** ✓
        d.z(a.zup, lift=True); d.wait(0.75)

        print("[6/8] 移到放置点 (%.1f, %.1f)" % (px, py))
        d.move(dx, dy); d.wait()

        if a.drop:
            # ⭐ 2026-10-04 用户要求：**到放置点后不下 Z，直接把夹爪开到最大让货自由下落** ✓
            #    好处：省一次 Z 往返（Z 是全行程最慢的一环 ✓）⇒ 快很多 ✓
            #    ⚠️ 代价：货从 **Z 抬起的那个高度**落下（离托盘最高 ✓），注意别摔坏/弹飞 ✗
            #    ⚠️ `gopen` 会被强制设为**最大安全开度 2140µs** ✓（用户原话"开到最大" ✓）
            a.gopen = 2140
            print("[7/8] ⭐ 到放置点 → **不下 Z** → 夹爪开到最大 %dµs → 货物自由下落 ✓" % a.gopen)
            if a.yes:
                d.grip(a.gopen); d.wait(0.4)
        else:
            print("[7/8] Z 放下 + 夹爪%s" % ("张开" if a.yes else "（空跑-不开）"))
            d.z(a.zdn); d.wait(0.4)
            if a.yes:
                d.grip(a.gopen); d.wait(0.6)

        print("[8/8] ⭐ Z 抬到最高 → **原地停住**（这就是安全位置，不再做任何 XY 移动 ✓）")
        d.z(a.zup, lift=True); d.wait()
        # ⚠️⚠️ 2026-10-04 严重教训：这里**绝不能**再有任何 XY 移动 ✗✗
        #    原来有个 `--park`，本意是"最后停到某点"，但我把参照物写错了
        #    （相对"货物"而不是相对"放置点"）⇒ 放置完成后又向右多走了 100mm ✗，
        #    用户只能紧急断电 ✓。
        #    ⇒ **正确行为**（也是用户明确定义的）：**放完货 → Z 抬到最高 → 原地停住** ✓。
        #      这一条是安全底线：**流程结束后不许再动机器** ✗。
        print("      Z=抬起 %dµs（最高）  夹爪保持张开 %dµs  位置不变 ✓" % (a.zup, a.gopen))

        print("\n=== 完成 ✓ ===")
        d.report()          # ⭐ 每步实际用时表 ✓（用户要求 ✓）
    except Exception as exc:
        print("\n✗ 中止：%s" % exc)
        try:
            d.report()      # 中止也把已跑过的步骤用时打出来 ✓
        except Exception:
            pass
        try:
            d.send("STOP", wait=0.5)
        except Exception:
            pass
        return 1
    finally:
        try:
            d.lk.close()
        except Exception:
            pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
