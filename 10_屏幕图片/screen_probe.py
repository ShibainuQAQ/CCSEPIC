"""屏链路诊断：分清「屏没收到」还是「收到了但指令不对」。

背景（2026-10-04）：给屏发了 page/pic/txt/nval 四条指令，屏**内容毫无变化** ✗，
但 `LCDRD` 能读到屏发回来的字节 ✓（40 字节）⇒ 说明**屏能发**、但不确定**屏能不能收** ✗。

做法（用控件表里给的探针 ✓）：
  ① 先把 `LCDRD` 的旧数据读空 ✓
  ② 发 `LCD vis zzz,1`（**故意用不存在的控件名** ✓）
     —— 淘晶驰：**控件不存在 ⇒ 回 `02 FF FF FF`**；存在 ⇒ 静默 ✓
     ⇒ **只要屏回 `02`，就证明它收到了 STM32 的指令** ✓✓（这是"单向"问题的分水岭 ✓）
  ③ 再发一条**肉眼可见**的 ASCII 指令 `LCD t0.txt="ABC"` ✓ —— 屏上第 1 列名称应变成 ABC ✓
  ④ 再发一条**中文**（GBK，走 LCDRAW）✓

用法：
  D:\\python\\python.exe 10_屏幕图片\\screen_probe.py
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


def drain(lk, n=6):
    """把 LCDRD 的旧数据读空（屏的触摸事件会一直往里塞 ✗）。"""
    last = None
    for _ in range(n):
        t = " ".join(x.strip() for x in lk.cmd("LCDRD", wait=0.9)[0])
        if t == last:
            break
        last = t
    return last


def main():
    port = find_stm32()
    if not port:
        print("[FAIL] 没找到 STM32")
        return 1
    print("[i] STM32 = %s" % port)
    lk = Link(port)
    try:
        print("\n[1] 清 LCDRD 缓冲…")
        drain(lk)
        d0 = drain(lk, 2)
        print("    清后：%s" % (d0[-90:] if d0 else "(空)"))
        base = d0

        print("\n[2] 探针：LCD vis zzz,1 （不存在的控件 ⇒ 应回 02 FF FF FF）")
        lk.cmd("LCD vis zzz,1", wait=1.2)
        time.sleep(0.6)
        # ⚠️ 2026-10-04 修正：原来拿"清空后"的读数去比 ⇒ 把**刚到的 02**当成旧数据滤掉了 ✗，
        #    于是明明屏回了 02 FF FF FF 却报"屏没有回新东西" ✗（误判）。
        #    正确做法：**每读一次就判一次**，看到 02 就立刻认定"屏收到了" ✓。
        got02 = False
        for _ in range(4):
            t = " ".join(x.strip() for x in lk.cmd("LCDRD", wait=0.9)[0])
            print("    LCDRD：%s" % (t[-90:] if t else "(空)"))
            if "02 FF FF FF" in t:
                got02 = True
                break
            if t.endswith("count=0"):
                break
        if got02:
            print("    ✅ 屏**收到了** STM32 的指令 ✓✓（STM32→屏 这条线是通的 ✓）")
            print("       ⇒ 那问题只在**指令内容/控件名/页面**上 ✓")
        else:
            print("    ⚠️ 没看到 02 应答 ✗ —— 查 PA2 → 屏 RX、GND 共地 ✓")

        print("\n[3] 肉眼测试：LCD t0.txt=\"ABC\"（ASCII ✓ 屏上第1列名称应变 ABC）")
        print("    " + lk.cmd('LCD t0.txt="ABC"', wait=1.0)[0][-1].strip())
        time.sleep(1.0)

        print("\n[4] 中文测试：LCDRAW t0.txt=\"黄-圆柱\"（GBK ✓）")
        payload = 't0.txt="黄-圆柱"'.encode("gbk").hex().upper() + "FFFFFF"
        print("    " + lk.cmd("LCDRAW " + payload, wait=1.2)[0][-1].strip())

        print("\n⇒ **看一下屏**：第 1 列名称现在显示什么？")
        print("   · 变了（ABC 或 黄-圆柱）⇒ 屏完整可用 ✓✓")
        print("   · 完全没变 ⇒ STM32→屏 方向不通 ✗（重点查 PA2 → 屏 RX + 共地 ✓）")
        return 0
    finally:
        lk.close()


if __name__ == "__main__":
    sys.exit(main())
