"""两点标定：把「相机像素」换算成「龙门毫米」。

原理（不需要任何绝对原点 ✓）：
  ① 拍一张照片 → 找到某件货的像素坐标 P1
  ② 让龙门沿 **+X 走已知距离 D**（默认 40mm，慢速 ✓）
  ③ 再拍一张 → 同一件货的像素坐标变成 P2
  ④ ⇒ 像素位移 |P2-P1| 对应物理位移 D ⇒ 得到 **mm/px 比例** 和 **X 方向在画面里的方向** ✓
  ⑤ 再把龙门**原路退回** ✓（位置复原 ✓），结束时**只停住** ✓（不做任何多余动作 ✗）

⚠️ 安全约定（2026-10-04 教训 ✓）：
  · Z 先抬到最高 ✓（视野不被挡 ✓ 且是安全姿态 ✓）
  · 只走一小段（默认 40mm ✓，慢速 600Hz ✓）
  · **无论成功失败，最后都退回原位** ✓
  · 全程只动 X，不动 Y ✓

用法：
  D:\\python\\python.exe 12_视觉\\vision\\calibrate_2point.py
  ... --dist 40 --k210 COM5
输出：
  标定结果打印出来 ✓，并存到 12_视觉\\vision\\calib.json ✓ 供后续换算使用 ✓
"""
import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT / "12_视觉"))
sys.path.insert(0, str(ROOT / "hw_bridge" / "tools"))

import pc_detect  # noqa: E402

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

SHOT = ROOT / "12_视觉" / "shot.py"


def find_stm32():
    """板载 CH340K 直认；否则逐个串口 8E1 发 PING，谁回 OK pong 谁是 STM32 ✓。"""
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


def photo(port, out):
    r = subprocess.run([sys.executable, str(SHOT), "--port", port, "--out", str(out)],
                       capture_output=True, text=True, timeout=180)
    if not Path(out).exists():
        print(r.stdout[-400:])
        print(r.stderr[-400:])
        return None
    return pc_detect.imread_unicode(out)


def blobs(img, min_area=250):
    """返回 [(颜色, 中心x, 中心y, 面积), ...]，按面积降序 ✓。"""
    out = []
    for it in pc_detect.detect(img, min_area):
        out.append((it["color"], it["px"][0], it["px"][1], it["area"]))
    return out


def match(a, b):
    """在两张图的色块里找**同一件货**：同色 + 像素距离最近 ✓。"""
    best = None
    for ca, xa, ya, aa in a:
        for cb, xb, yb, ab in b:
            if ca != cb:
                continue
            d = ((xa - xb) ** 2 + (ya - yb) ** 2) ** 0.5
            if d < 5:
                continue                      # 没动过，说明不是同一件或没走
            if d > 200:
                continue
            if best is None or d > best[3]:   # 位移越大越可靠 ✓
                best = (ca, (xa, ya), (xb, yb), d)
    return best


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dist", type=float, default=-40.0,
                    help="标定用的 X 位移(mm)。⚠️ 必须 ≤0（**只能往 −X 走** ✗）")
    ap.add_argument("--speed", type=int, default=4000)
    ap.add_argument("--k210", default="COM5")
    a = ap.parse_args()

    # ⛔⛔ 2026-10-04 硬约束（用户明确告知）：**当前位置已在 +X 机械极限上** ✗
    #    ⇒ 本脚本**只允许向 −X 移动** ✓，+X 一律拒绝 ✗（程序层拦截，不靠人记 ✓）
    if a.dist > 0:
        print("⛔ 拒绝：--dist 不能为正数 ✗")
        print("   原因：当前夹爪已在 **+X 极限**上（用户 2026-10-04 明确告知 ✗）")
        print("   标定请用 --dist -40（往 −X 走）✓")
        return 2

    port = find_stm32()
    if not port:
        print("[FAIL] 没找到 STM32")
        return 1
    print("[i] STM32 = %s" % port)
    from step_test import Link
    lk = Link(port)

    d1 = HERE / "_cal1.jpg"
    d2 = HERE / "_cal2.jpg"
    moved = False
    try:
        # Z 抬到最高（安全姿态 + 不挡相机 ✓）；Z 不影响 X/Y ✓
        for l in lk.cmd("TIM3?", wait=0.9)[0]:
            if "ccr3=" in l:
                pass
        lk.cmd("EN 1", wait=0.8)
        lk.cmd("ZSET 2370", wait=0.6)
        time.sleep(2.0)
        lk.cmd("ZERO all", wait=0.5)

        print("[1/4] 拍第一张…")
        img1 = photo(a.k210, d1)
        if img1 is None:
            print("[FAIL] 拍不到照片（K210 没连上？）")
            return 1
        b1 = blobs(img1)
        print("      找到 %d 个色块：%s" % (len(b1), [(c, round(x), round(y)) for c, x, y, _ in b1]))
        if not b1:
            print("[FAIL] 画面里没有黄/绿/蓝色块 —— 先放一件货在画面里 ✗")
            return 1

        steps = int(round(a.dist * 80))       # SMM=80 步/mm ✓（a.dist 为负 ⇒ 往 −X ✓）
        print("[2/4] 龙门 X 走 %+.0fmm（%d 步，慢速 %dHz）…" % (a.dist, steps, a.speed))
        lk.cmd("SPD %d" % a.speed)
        lk.cmd("ACC 300")
        lk.cmd("STEP x %d" % steps, wait=0.4)
        moved = True
        lk.wait_idle(timeout=max(10.0, steps / float(a.speed) * 3.0 + 5.0))
        time.sleep(0.5)

        print("[3/4] 拍第二张…")
        img2 = photo(a.k210, d2)
        if img2 is None:
            print("[FAIL] 第二张没拍到")
            return 1
        b2 = blobs(img2)
        print("      找到 %d 个色块：%s" % (len(b2), [(c, round(x), round(y)) for c, x, y, _ in b2]))

        m = match(b1, b2)
        if not m:
            print("[FAIL] 两图里找不到同一件货（位移太小 / 出画面了 ✗）")
            print("       试试 --dist 更大或更小")
            return 1
        color, (x1, y1), (x2, y2), dpx = m
        dx, dy = x2 - x1, y2 - y1
        scale = a.dist / dpx                  # mm 每像素 ✓
        print("\n=== 标定结果 ===")
        print("  同一件货（%s）：(%.1f, %.1f) → (%.1f, %.1f)px   位移 %.1fpx"
              % (color, x1, y1, x2, y2, dpx))
        print("  像素位移向量 (dx, dy) = (%.1f, %.1f) px" % (dx, dy))
        print("  ⇒ 比例：**%.4f mm/像素**  （反算：%.2f 像素/mm）" % (scale, 1.0 / scale))
        ang = __import__("math").degrees(__import__("math").atan2(dy, dx))
        print("  ⇒ 画面里 +X 的方向 = (%.3f, %.3f)，与图像水平轴夹角 %.1f°"
              % (dx / dpx, dy / dpx, ang))
        print("  ⚠️ 若夹角明显不为 0/180°，说明相机装歪了（可用旋转补偿 ✓）")

        out = HERE / "calib.json"
        out.write_text(json.dumps({
            "mm_per_px": scale, "dist_mm": a.dist, "px_shift": dpx,
            "dir_x": [dx / dpx, dy / dpx], "color": color,
            "p1": [x1, y1], "p2": [x2, y2],
        }, indent=2), encoding="utf-8")
        print("  已存 %s ✓" % out)
        return 0
    finally:
        # ⭐ 无论成功失败，**退回原位**，然后停住 ✓（教训：流程结束不做多余动作 ✗）
        if moved:
            try:
                print("[4/4] 退回原位…")
                lk.cmd("STEP x %d" % (-steps), wait=0.4)
                lk.wait_idle(timeout=max(10.0, steps / float(a.speed) * 3.0 + 5.0))
                print("      已回原位 ✓（Z 最高 + 原地停住 ✓）")
            except Exception as exc:
                print("⚠️ 退回失败：%r —— 请手动检查 ✗" % (exc,))
        lk.close()


if __name__ == "__main__":
    sys.exit(main())
