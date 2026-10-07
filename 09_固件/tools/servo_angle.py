"""舵机角度控制 —— 按"角度"驱动 Z 轴 / 夹爪舵机（线路 A 自备工具）。

⚠️⚠️ **当前装的是 MG996R**（用户 2026-10-02 确认）—— 它是**标准 ~180° 模拟舵机**，
**不是**设计里要求的 DS3225 **270° 版**！所以：
  · 默认按 **500µs↔0°、2500µs↔180°** 换算（`--deg-span` 可改）；
  · ⚠️ **而且这个 180° 也是标称值** —— 按实测数据反推（1400→2400µs 走了 23.2mm 爪口），
    若齿轮是设计里的 M1.5-z14（r=10.5）则只有 ≈127°，若 r≈8 则≈166°。
    **真实角度必须直接量舵盘**（贴基准线 → 发 600 / 2400 → 量夹角差 × 2000/1800）。

换算：脉宽(µs) = `--us-at-0` + 角度 × (`--us-span` / `--deg-span`)

用法（项目根目录）：
  D:\\python\\python.exe 09_固件\\tools\\servo_angle.py --list
  D:\\python\\python.exe 09_固件\\tools\\servo_angle.py --axis grip --angle 90
  D:\\python\\python.exe 09_固件\\tools\\servo_angle.py --axis grip --interactive
      交互里还能输入：`us 1450`（直接给脉宽）｜`open`/`close`（预设）｜`z 90`（切 Z 轴）｜`table`｜`q`

⚠️ 范围（2026-10-02 按用户要求**两侧全部放开到 500~2500µs**，不拦截）：
    ⭐ **但用户实测该 MG996R 真正可达的只有 20°~150°（≈722~2167µs，行程 130°）** ——
    超出就是顶舵机内部止挡（这也解释了历史上"700µs 顶死"和"2500µs 顶死"两次事故）。
    工具在低位 <800µs / 高位 >2100µs 时**大声告警但不拦**，贴边（722/2167）也发得出去。
    换算口径：MG996R 按**标称 180°** 算（`µs = 500 + 角度 × 2000/180`），可用 `--deg-span` 改。
"""
import argparse
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
for cand in (HERE.parent.parent / "hw_bridge" / "vendor", Path(r"D:\python\Lib\site-packages")):
    if cand.exists():
        sys.path.insert(0, str(cand))
try:
    import serial
    import serial.tools.list_ports as list_ports
except ImportError:
    print("找不到 pyserial：请用 D:\\python\\python.exe 运行，或确认 hw_bridge\\vendor 存在")
    raise SystemExit(1)

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

# ── 参数（可由命令行覆盖：--us-at-0 / --us-span / --deg-span）────────
US_AT_0DEG = 500.0      # 0° 对应脉宽
US_AT_SPAN = 2000.0     # 覆盖的脉宽跨度（500→2500）
DEG_SPAN = 180.0        # ⚠️ MG996R 标称 ~180°（**不是** DS3225 的 270°）；真实值待实测
US_PER_DEG = US_AT_SPAN / DEG_SPAN

# 实测标定（2026-10-02，空载建表）：脉宽 → 爪口内宽
MEASURED = [
    (1400, 36.8), (1600, 41.1), (1800, 45.7),
    (2000, 50.7), (2200, 54.5), (2400, 60.0),
]

WINDOW = {           # (下限 µs, 上限 µs) —— **两端都放到舵机原始范围 500~2500**
    "grip": (500, 2500),
    "z": (500, 2500),
}
# "小心区"：不拦截，只在进入时**大声告警**（两侧都是靠机械止挡的方向）
# ⭐ 2026-10-02 用户实测：**该 MG996R 可达范围只有 20°~150°**（≈722~2167µs，行程 130°），
#    超出就顶内部止挡 —— 所以两侧的告警线就设在**实测止挡点往内留一点**：
#    · 低位 800µs（=24°）／高位 2100µs（=144°）；真要贴边（722/2167）也发得出去，只是告警。
USABLE_DEG = (20.0, 150.0)      # 实测可达最小/最大角度
CAUTION_LO = {"grip": 800, "z": 800}
CAUTION_HI = {"grip": 2100, "z": 2100}
PRESET = {"open": 2400, "close": 1450}   # grip 的"全开 / 夹住"预设（暂定，等实测确认）


def deg_to_us(deg):
    return US_AT_0DEG + deg * US_PER_DEG


def us_to_deg(us):
    return (us - US_AT_0DEG) * DEG_SPAN / US_AT_SPAN


def mm_of(us):
    """用实测 6 点线性拟合估爪口内宽：mm ≈ 48.13 + 0.02303×(µs−1900)"""
    return 48.13 + 0.023029 * (us - 1900.0)


def show_table():
    print("  角度(°)   脉宽(µs)   夹爪爪口(mm, 估算)")
    d = 0.0
    while d < DEG_SPAN:
        us = deg_to_us(d)
        print("  %6.1f   %7.0f   %s" % (d, us, ("%.1f" % mm_of(us)) if 1400 <= us <= 2450 else "--"))
        d += 15.0
    us = deg_to_us(DEG_SPAN)
    print("  %6.1f   %7.0f   %s" % (DEG_SPAN, us, ("%.1f" % mm_of(us)) if 1400 <= us <= 2450 else "--"))
    print("  实测点：", "  ".join("%dµs=%.1fmm" % (u, m) for u, m in MEASURED))


class Servo:
    def __init__(self, port, baud=115200, unsafe=False):
        self.unsafe = unsafe
        self.ser = serial.Serial(port, baud, parity=serial.PARITY_EVEN, timeout=0.2,
                                 write_timeout=3, dsrdtr=False, rtscts=False)
        try:
            self.ser.dtr = False
            self.ser.rts = False
        except Exception:
            pass
        time.sleep(0.3)
        # 等板子启动完（开串口会复位 MCU），把它的启动横幅读掉
        self.drain(2.0)

    def drain(self, secs=1.0):
        end = time.time() + secs
        buf = bytearray()
        while time.time() < end:
            n = self.ser.in_waiting
            if n:
                buf += self.ser.read(n)
            else:
                time.sleep(0.02)
        return bytes(buf).decode("utf-8", "replace")

    def send(self, axis, us, note=""):
        cmd = ("GSET" if axis == "grip" else "ZSET") + " %d" % us
        self.ser.write(cmd.encode() + b"\r\n")
        out = self.drain(0.8)
        reply = " ".join(l.strip() for l in out.splitlines() if l.strip().startswith(("OK", "ERR")))
        extra = ("  爪口≈%.1fmm" % mm_of(us)) if axis == "grip" and 1300 <= us <= 2500 else ""
        print("  -> %-6s %4dµs = %6.1f°  %s%s%s"
              % (axis, us, us_to_deg(us), reply or "(无应答)", extra, ("  " + note) if note else ""))
        return reply

    def raw(self, text):
        """原样发一条**任意命令**（不是舵机脉宽），并把回复打出来。

        为什么加这个：交互模式端口只开一次，测试时最顺手 ——
        不用为了发一条 `EN 1` / `STEP x 800` 就退出脚本换别的工具（退出还会再复位一次板子）。
        协议与本工具一致：115200 **8E1**（USART1 调试口）。
        """
        self.ser.write(text.encode() + b"\r\n")
        out = self.drain(0.8)
        lines = [l.strip() for l in out.splitlines()
                 if l.strip() and not l.strip().startswith(("LOG boot", "LOG diag", "EVT ", "HB "))]
        for l in lines:
            print("    " + l)
        if not lines:
            print("    (无应答)")
        return out

    def close(self):
        self.ser.close()


def clamp(axis, us, unsafe):
    """只挡"舵机物理范围之外"（默认 500~2500）；**两侧的机械危险区都只告警不拦**。

    2026-10-02 用户要求：下限、上限都放开 —— 因为舵盘初始位置不对，
    需要用更宽（甚至贴边）的脉宽把机构掰到正确位置。"""
    lo, hi = WINDOW[axis]
    if not unsafe:
        if us < lo:
            return lo, "低于舵机下限 %dµs（原值 %d），已夹到下限" % (lo, us)
        if us > hi:
            return hi, "高于舵机上限 %dµs（原值 %d），已夹到上限" % (hi, us)
    if us < CAUTION_LO[axis]:
        return us, ("⚠️ 进入低位小心区（<%dµs）：这个方向靠机械止挡，顶住会嗡嗡响/发热；"
                    "**一听到嗡嗡声立刻往回收**（上次 700µs 顶死过一次）" % CAUTION_LO[axis])
    if us > CAUTION_HI[axis]:
        return us, ("⚠️ 进入高位小心区（>%dµs）：贴着舵机内部止挡，长期顶住会堵转发热；"
                    "**只在需要时才短暂给**" % CAUTION_HI[axis])
    return us, None


def find_stm32_port():
    """找 STM32 的串口。

    分两步（2026-10-04 换板后必须这样 ✗）：
      ① 板载 CH340K = VID:PID `1A86:7522`（老 RCT6 板是这样 ✓）
      ② 新板（C8T6）**没有板载 USB 转串口** ✗，外接的 USB-TTL 是 CH340 `1A86:7523`
         —— **和 K210 的芯片同型号** ✓，光看 VID:PID **区分不开** ✗✗。
         所以退一步：**逐个串口用 8E1 发 PING，谁回 `OK pong` 谁就是 STM32** ✓✓。
         这也顺带证明了"线接对了、波特率对了" ✓。
    """
    import time

    import serial

    cands = []
    for p in list_ports.comports():
        if "1A86:7522" in (p.hwid or "").upper():
            return p.device          # 板载 CH340K，直接认定 ✓
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
            data = s.read(300)
            s.close()
            if b"pong" in data:
                return dev
        except Exception:
            continue
    return None


def resolve_port(want):
    """端口自动识别。

    为什么需要：这块板子的 COM 号一天能变 5 次（CH340 系列**无序列号**，
    插拔顺序一变、或者旁边插了别的串口设备，号就换）—— 每次人肉查端口太折磨。
    规则：没给 `--port` 就自动扫；给了但打不开也会回退到自动扫到的那个。
    """
    if want:
        return want, None
    dev = find_stm32_port()
    if dev:
        return dev, "自动识别到 STM32（板载 CH340K 或 PING 探测到 OK pong）"
    return "COM6", "⚠️ 没扫到 CH340K，退回默认 COM6"


def open_servo(port, baud, unsafe):
    """带一次自动回退的开端口（见 resolve_port 的说明）。"""
    try:
        return Servo(port, baud, unsafe)
    except Exception as exc:
        dev = find_stm32_port()
        if dev and dev != port:
            print("  [i] %s 打不开（%s）→ 改用自动识别的 %s" % (port, exc, dev))
            return Servo(dev, baud, unsafe)
        print("  ✗ 打不开 %s：%s" % (port, exc))
        print("    当前串口：")
        for p in list_ports.comports():
            print("      %-6s | %s | %s" % (p.device, p.description, p.hwid))
        print("    STM32 是 CH340K(1A86:7522) 那个；K210 是 CH340(1A86:7523)。")
        raise


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", default=None, help="不给就自动识别 CH340K(1A86:7522) = STM32")
    ap.add_argument("--baud", type=int, default=115200)
    ap.add_argument("--axis", default="grip", choices=["grip", "z"])
    ap.add_argument("--angle", type=float, help="一次性：给角度")
    ap.add_argument("--us", type=float, help="一次性：直接给脉宽")
    ap.add_argument("--interactive", action="store_true")
    ap.add_argument("--unsafe", action="store_true", help="关掉安全窗口（危险！）")
    ap.add_argument("--deg-span", type=float, default=180.0,
                    help="舵机标称总转角（MG996R≈180，DS3225 270°版=270）")
    ap.add_argument("--us-at-0", type=float, default=500.0)
    ap.add_argument("--us-span", type=float, default=2000.0)
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--dry-run", action="store_true", help="不开端口，只打印换算（验算/给别人看用）")
    a = ap.parse_args()

    global US_AT_0DEG, US_AT_SPAN, DEG_SPAN, US_PER_DEG
    US_AT_0DEG, US_AT_SPAN, DEG_SPAN = a.us_at_0, a.us_span, a.deg_span
    US_PER_DEG = US_AT_SPAN / DEG_SPAN

    if a.list:
        for p in list_ports.comports():
            print("  %-6s | %s | %s" % (p.device, p.description, p.hwid))
        return 0

    print("=== 舵机角度控制 ===")
    print("  当前舵机：**MG996R**（标准 ~180° 模拟舵机，不是 270°）")
    print("  角度↔脉宽（标称）：µs = %.0f + 角度 × %.4f   （%.0fµs=0°，%.0fµs=%.0f°）"
          % (US_AT_0DEG, US_PER_DEG, US_AT_0DEG, US_AT_0DEG + US_AT_SPAN, DEG_SPAN))
    print("  ⚠️ 这个刻度是**标称值**：真实总转角要用舵盘基准线实测（1000µs 实测走了 23.2mm 爪口，反推约 127°~166°，见文件头）")
    print("  ⭐ 实测可达角度：%.0f° ~ %.0f°（= %.0f ~ %.0f µs，行程仅 %.0f°）—— 超出即顶内部止挡"
          % (USABLE_DEG[0], USABLE_DEG[1], deg_to_us(USABLE_DEG[0]), deg_to_us(USABLE_DEG[1]),
             USABLE_DEG[1] - USABLE_DEG[0]))
    print("  范围：夹爪 %s µs / Z 轴 %s µs（两端都放开到舵机原始值，不拦截）" % (WINDOW["grip"], WINDOW["z"]))
    print("  小心区（只告警不拦）：低位 < %dµs ／ 高位 > %dµs"
          % (CAUTION_LO["grip"], CAUTION_HI["grip"]))
    show_table()

    if a.dry_run:
        us = a.us if a.us is not None else (deg_to_us(a.angle) if a.angle is not None else None)
        if us is not None:
            us = int(round(us))
            print("  [dry-run] 角度/脉宽 -> %dµs = %.1f°  爪口≈%.1fmm（没有开端口，什么都没发）"
                  % (us, us_to_deg(us), mm_of(us)))
        else:
            print("  [dry-run] 只列表，没给 --angle/--us")
        return 0

    port, note = resolve_port(a.port)
    if note:
        print("  [i] %s → %s" % (note, port))

    sv = open_servo(port, a.baud, a.unsafe)
    print("  已连 %s（端口已打开，之后连发不会再复位板子）" % sv.ser.port)
    try:
        axis = a.axis

        if a.angle is not None or a.us is not None:
            us = a.us if a.us is not None else deg_to_us(a.angle)
            us, warn = clamp(axis, int(round(us)), a.unsafe)
            if warn:
                print("  ⚠️ %s" % warn)
            sv.send(axis, us)
            return 0

        if not a.interactive:
            print("  没给 --angle/--us/--interactive，什么都没做。")
            return 0

        print("\n  输入角度回车即可（q 退出，table 看表，us 1450 直接给脉宽，z 90 切 Z 轴）")
        print("  也支持原样发命令：cmd VER ／ cmd EN 1 ／ cmd SPD 800 ／ cmd STEP x 800 ／ cmd POS")
        while True:
            try:
                line = input("  [%s] > " % axis).strip()
            except (EOFError, KeyboardInterrupt):
                break
            if not line:
                continue
            if line[:4].lower() == "cmd ":
                sv.raw(line[4:].strip())
                continue
            low = line.lower()
            if low in ("q", "quit", "exit"):
                break
            if low == "table":
                show_table()
                continue
            if low in PRESET:
                us, warn = clamp(axis, PRESET[low], a.unsafe)
                if warn:
                    print("  ⚠️ %s" % warn)
                sv.send(axis, us, note="(预设 %s)" % low)
                continue
            parts = low.split()
            if parts[0] == "us" and len(parts) == 2:
                try:
                    us = int(float(parts[1]))
                except ValueError:
                    print("  用法：us 1450")
                    continue
                us, warn = clamp(axis, us, a.unsafe)
                if warn:
                    print("  ⚠️ %s" % warn)
                sv.send(axis, us)
                continue
            if parts[0] in ("z", "grip") and len(parts) == 2:
                try:
                    d = float(parts[1])
                except ValueError:
                    print("  用法：z 90 / grip 120")
                    continue
                old, axis = axis, parts[0]
                us, warn = clamp(axis, int(round(deg_to_us(d))), a.unsafe)
                if warn:
                    print("  ⚠️ %s" % warn)
                sv.send(axis, us)
                axis = old
                continue
            try:
                d = float(line)
            except ValueError:
                print("  不认识的输入：%s" % line)
                continue
            us, warn = clamp(axis, int(round(deg_to_us(d))), a.unsafe)
            if warn:
                print("  ⚠️ %s" % warn)
            sv.send(axis, us)
        return 0
    finally:
        sv.close()


if __name__ == "__main__":
    sys.exit(main())
