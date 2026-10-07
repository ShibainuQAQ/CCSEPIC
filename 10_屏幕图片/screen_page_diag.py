"""屏页面诊断：查「屏当前显示的是哪一页」+「我要写的控件到底存不存在」。

结论依据（2026-10-04）：
  · `vis zzz,1` 回了 `02 FF FF FF` ⇒ 屏**能收到并执行** STM32 的指令 ✓✓
  · 但写 `t0.txt` / `p0.pic` / `n0.val` 后**画面毫无变化** ✗
  ⇒ 强烈怀疑：**这些控件不在屏当前显示的页面上**（淘晶驰控件按页存在 ✓，
    写到非当前页的控件「执行成功但看不见」✓✓）

本脚本依次发：
  ① `vis t0,1`      —— 控件存在 ⇒ **静默**；不存在 ⇒ 回 `02 FF FF FF`
  ② `vis t1,1` / `vis p0,1` / `vis n0,1`   同上
  ③ `sendme`        —— 淘晶驰：**回报当前页面 ID**（回一个数字 ✓）
  ④ `page 0` 后再写一遍 t0/p0/n0（看切页后是否可见 ✓）

用法：
  D:\\python\\python.exe 10_屏幕图片\\screen_page_diag.py
"""
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "hw_bridge" / "tools"))
from step_test import Link  # noqa: E402

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass


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


def read_until_quiet(lk, tries=5, gap=0.8):
    """连续读 LCDRD，直到没有新数据；返回所有读到的文本拼起来 ✓。"""
    seen = []
    last = None
    for _ in range(tries):
        t = " ".join(x.strip() for x in lk.cmd("LCDRD", wait=0.8)[0])
        if t and t != last and "count=0" not in t:
            seen.append(t)
        if t == last:
            break
        last = t
        time.sleep(gap)
    return " | ".join(seen)


def main():
    port = find_stm32()
    if not port:
        print("[FAIL] 没找到 STM32")
        return 1
    print("[i] STM32 = %s" % port)
    lk = Link(port)
    try:
        print("\n[0] 先清空 LCDRD…")
        read_until_quiet(lk, 3)

        for name in ("t0", "t1", "t2", "p0", "n0", "n6", "page0"):
            lk.cmd("LCD vis %s,1" % name, wait=0.9)
            r = read_until_quiet(lk, 2)
            exists = "02 FF FF FF" not in r
            print("    vis %-6s → %-14s   ⇒ %s"
                  % (name, (r[-40:] if r else "(静默)"),
                     "**存在** ✓" if exists else "不存在 ✗"))

        print("\n[1] 问屏当前页面：sendme")
        lk.cmd("LCD sendme", wait=1.0)
        r = read_until_quiet(lk, 3)
        print("    sendme → %s" % (r if r else "(无回复)"))
        print("    （淘晶驰：sendme 会回报**当前页面 ID** ✓）")

        print("\n[2] 切到 page 0 后再写一遍（看是否可见）")
        for c in ('LCD page 0', 'LCD t0.txt="P0-TEST"', 'LCD p0.pic=21', 'LCD n0.val=77'):
            t = lk.cmd(c, wait=0.8)[0]
            print("    %-24s %s" % (c, t[-1].strip() if t else ""))

        print("\n[3] 试 page 1")
        for c in ('LCD page 1', 'LCD t0.txt="P1-TEST"'):
            t = lk.cmd(c, wait=0.8)[0]
            print("    %-24s %s" % (c, t[-1].strip() if t else ""))

        print("\n⇒ 请看屏：")
        print("   · 出现 P0-TEST / 绿五棱锥 / 77  ⇒ 屏显示的就是 page 0 ✓（那再查控件可见性 ✓）")
        print("   · 出现 P1-TEST                   ⇒ 屏显示的是 page 1 ✓（我改发 page 1 ✓）")
        print("   · 都没变                          ⇒ 控件被遮挡 / 屏在别的页 ✓（拍照给我看 ✓）")
        return 0
    finally:
        lk.close()


if __name__ == "__main__":
    sys.exit(main())
