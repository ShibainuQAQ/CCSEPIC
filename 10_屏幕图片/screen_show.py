"""给串口屏发"夹取某件货物"的显示指令（图片 + 中文文字 + 计数）。

⚠️ 三个必须遵守的点（都踩过 ✓）：
  ① **中文必须 GBK** ✗ —— 固件 `LCD` 是**原样透传**，发 UTF-8 上去屏上就是乱码 ✓
     ⇒ 中文一律走 `LCDRAW <hex>`（自己 GBK 编码 + 带上结尾）✓
  ② **每条指令必须以 `FF FF FF` 结尾** ✓（淘晶驰＝Nextion 系）
     · `LCD <文本>` 固件会**自动补** ✓
     · `LCDRAW <hex>` 必须**自己带** ✓
  ③ 图片 ID 以 `10_屏幕图片\\图片ID对照表.md` 为准 ✓（黄色-圆柱 = **2** ✓）

用法：
  D:\\python\\python.exe 10_屏幕图片\\screen_show.py --color 黄色 --shape 圆柱 --count 1
  ... --col 0            # 用第几列控件（t0/p0/n6 ✓ 默认 0）
  ... --text-only        # 只发文字
"""
import argparse
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT / "hw_bridge" / "tools"))
from step_test import Link  # noqa: E402

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

# 货物 → 图片 ID（权威表 ✓）
PIC_ID = {
    ("黄色", "圆柱"): 2, ("黄色", "正方体"): 3, ("黄色", "正六棱柱"): 4,
    ("黄色", "正三棱柱"): 5, ("黄色", "正四棱锥"): 6, ("黄色", "正四面体"): 7,
    ("黄色", "正五棱锥"): 8,
    ("蓝色", "圆柱"): 9, ("蓝色", "正六棱柱"): 10, ("蓝色", "正三棱柱"): 11,
    ("蓝色", "正四棱锥"): 12, ("蓝色", "正四面体"): 13, ("蓝色", "正五棱锥"): 14,
    ("绿色", "圆柱"): 15, ("绿色", "正方体"): 16, ("绿色", "正六棱柱"): 17,
    ("绿色", "正三棱柱"): 18, ("绿色", "正四棱锥"): 19, ("绿色", "正四面体"): 20,
    ("绿色", "正五棱锥"): 21,
}


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
    ap.add_argument("--color", default="黄色")
    ap.add_argument("--shape", default="圆柱")
    ap.add_argument("--count", type=int, default=1)
    ap.add_argument("--col", type=int, default=0, help="用第几列控件（t0/p0/n6）")
    ap.add_argument("--text-only", action="store_true")
    a = ap.parse_args()

    key = (a.color, a.shape)
    if key not in PIC_ID:
        print("不认识的货物：%s-%s" % key)
        print("可用：" + ", ".join("%s-%s" % k for k in list(PIC_ID)[:6]) + " …")
        return 2
    pic = PIC_ID[key]
    text = "夹取%s%s %d 个" % (a.color, a.shape, a.count)

    port = find_stm32()
    if not port:
        print("[FAIL] 没找到 STM32")
        return 1
    print("[i] STM32 = %s ｜ 货物 %s-%s → 图片 ID %d ｜ 文字「%s」" % (port, a.color, a.shape, pic, text))
    lk = Link(port)
    try:
        # ① 页面
        print(lk.cmd("LCD page 0", wait=0.6)[0][-1].strip())
        # ② 图片（ASCII ⇒ LCD 直接发，固件自动补 FF FF FF ✓）
        if not a.text_only:
            print(lk.cmd("LCD p%d.pic=%d" % (a.col, pic), wait=0.6)[0][-1].strip())
        # ③ 中文文字（GBK ⇒ LCDRAW，自己带 FF FF FF ✓）
        payload = ('t%d.txt="%s"' % (a.col, text)).encode("gbk")
        raw = payload.hex().upper() + "FFFFFF"
        for l in lk.cmd("LCDRAW " + raw, wait=1.0)[0]:
            if "lcd" in l.lower() or "OK" in l:
                print("  " + l.strip())
        # ④ 计数
        if not a.text_only:
            print(lk.cmd("LCD n%d.val=%d" % (6 + a.col, a.count), wait=0.6)[0][-1].strip())
        print("\n✅ 已发送。屏上应显示：图片(黄色圆柱) + 「%s」" % text)
        print("   ⚠️ 若屏无反应：确认屏的波特率 = 9600 ✓（固件侧默认 9600 ✓）")
        return 0
    finally:
        lk.close()


if __name__ == "__main__":
    sys.exit(main())
