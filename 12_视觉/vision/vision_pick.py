"""视觉引导抓取：拍照 → 识别 → 换算成龙门坐标 → 调用抓放流程。

这条链就是「校赛方案」的完整形态 ✓：
    K210 拍照 ──> PC 识别（颜色+形状）──> 像素→毫米换算 ──> pick_place 抓放
（K210 只当相机 ✓；识别和换算都在 PC 上 ✓ —— 好调、好改、不占 K210 ✓）

⚠️ 标定数据（2026-10-04 用户手动示教的两点 ✓，坐标系原点 = **安全位置** ✓）：
    像素 (244.7, 185.4)  ↔  龙门 (−195, −65) mm     ← 右下绿块
    像素 (100.1,  13.7)  ↔  龙门 (−100, +65) mm     ← 左上绿块
   ⇒ 由此得到：比例 ≈0.717 mm/px、画面相对龙门旋转 ≈180°（含 4° 小倾斜 ✓）
     ⚠️ 若相机被碰动过，必须重新示教这两点 ✗

安全规则（用户 2026-10-04 明确要求 ✓）：
  · **每次抓放开始前，夹爪必须停在"安全位置"** ✓（脚本以当前点为原点 ✓）
  · **安全位置就是 +X 的机械极限** ✗ ⇒ 目标的 X 必须 ≤ 0 ✓
  · 流程结束：Z 抬到最高 + **原地停住** ✓（不做任何多余动作 ✗）

用法：
  D:\\python\\python.exe 12_视觉\\vision\\vision_pick.py --color yellow
  ... --color any       # 选"X 最靠近 0"的那件（移动距离最短、最安全 ✓）
  ... --place 0 0       # 放到哪（默认安全位置 ✓）
  ... --dry             # 只看结果不动机器 ✓
"""
import argparse
import math
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT / "12_视觉"))

import pc_detect  # noqa: E402

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

SHOT = ROOT / "12_视觉" / "shot.py"
PICK = ROOT / "hw_bridge" / "tools" / "pick_place.py"

# 标定点（px ↔ mm）—— 原点 = 安全位置 ✓
CAL = {
    "p1_px": (244.7, 185.4), "p1_mm": (-195.0, -65.0),
    "p2_px": (100.1, 13.7),  "p2_mm": (-100.0, 65.0),
}


def similarity():
    """两点求相似变换：mm = s·R(θ)·(px − p2_px) + p2_mm ✓"""
    (u1, v1), (u2, v2) = CAL["p1_px"], CAL["p2_px"]
    (x1, y1), (x2, y2) = CAL["p1_mm"], CAL["p2_mm"]
    du, dv = u1 - u2, v1 - v2
    dx, dy = x1 - x2, y1 - y2
    dpx = math.hypot(du, dv)
    dmm = math.hypot(dx, dy)
    s = dmm / dpx
    th = math.atan2(dy, dx) - math.atan2(dv, du)
    return s, th


def px_to_mm(px, s, th):
    (u2, v2) = CAL["p2_px"]
    (x2, y2) = CAL["p2_mm"]
    du, dv = px[0] - u2, px[1] - v2
    c, si = math.cos(th), math.sin(th)
    return (x2 + s * (c * du - si * dv), y2 + s * (si * du + c * dv))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--color", default="any", help="yellow/green/blue/any ✓")
    ap.add_argument("--place", nargs=2, type=float, default=[0.0, 0.0])
    ap.add_argument("--speed", type=int, default=5000)   # 固件上限 5000Hz ✓
    ap.add_argument("--zsettle", type=float, default=6.0,
                    help="Z **放下**等待秒数。⭐ 用户 2026-10-04 要求 **6.0s** ✓")
    ap.add_argument("--zlift", type=float, default=5.0,
                    help="Z **抬起**等待秒数。⭐ 用户要求 5.0s ✓（与放下同档 ✓）")
    ap.add_argument("--gsettle", type=float, default=0.35,
                    help="夹爪等待秒数。⚠️ 舵机转速硬件固定 ✗（0.17s/60° ⇒ 81° 约 0.23s）"
                         "⇒ 0.35s 已够 ✓（原来 0.8s 是白等 ✗）")
    ap.add_argument("--zup", type=int, default=2370)
    # ⭐ 2026-10-04：Z 最低点 630 → **611**（用户手动调到舵机实测最低 ✓）
    #    原因：矮货（蓝色三角锥）用 630 **夹不起来** ✗
    ap.add_argument("--zdn", type=int, default=611)
    ap.add_argument("--gopen", type=int, default=1850)
    ap.add_argument("--gclose", type=int, default=950)
    ap.add_argument("--shot-file", default=None, help="用现成照片，不重新拍")
    ap.add_argument("--drop", action="store_true",
                    help="⭐ 到放置点后不下 Z、夹爪开到最大让货自由下落 ✓（用户要求 ✓）")
    ap.add_argument("--dry", action="store_true", help="只算不动机器 ✓")
    a = ap.parse_args()

    s, th = similarity()
    print("[i] 标定：%.4f mm/px，旋转 %.1f°" % (s, math.degrees(th)))

    img_path = Path(a.shot_file) if a.shot_file else (HERE / "_vp.jpg")
    if not a.shot_file:
        r = subprocess.run([sys.executable, str(SHOT), "--port", "COM5", "--out", str(img_path)],
                           capture_output=True, text=True, timeout=180,
                           encoding="utf-8", errors="replace")   # ⚠️ 不加这句在 GBK 控制台会 decode 崩 ✗
        if not img_path.exists():
            print("[FAIL] 拍照失败：\n" + r.stdout[-300:] + r.stderr[-300:])
            return 1
    img = pc_detect.imread_unicode(img_path)
    if img is None:
        print("[FAIL] 读不到照片")
        return 1

    items = pc_detect.detect(img, 250)
    cand = []
    for it in items:
        mm = px_to_mm(it["px"], s, th)
        cand.append((it["color"], it["shape"], it["px"], mm))
    print("\n识别到 %d 件（已换算成龙门坐标 ✓）：" % len(cand))
    print("  颜色    形状                     像素           龙门 mm        可用")
    usable = []
    for c, sh, px, mm in cand:
        ok = mm[0] <= 0.0                     # ⛔ X 必须 ≤ 0（安全位置是 +X 极限 ✗）
        print("  %-6s %-24s (%5.1f,%6.1f)  (%7.1f,%6.1f)  %s"
              % (c, sh, px[0], px[1], mm[0], mm[1], "✓" if ok else "✗ 超 +X 极限"))
        if ok:
            usable.append((c, sh, mm))

    if not usable:
        print("\n[FAIL] 没有可抓的货物（都超 +X 极限 ✗）")
        return 1

    if a.color == "any":
        # 选 X 最靠近 0 的（移动最短、离极限最远 ✓）
        target = max(usable, key=lambda t: t[2][0])
    else:
        pool = [t for t in usable if t[0] == a.color]
        if not pool:
            print("\n[FAIL] 没有 %s 色的货 ✓" % a.color)
            return 1
        target = max(pool, key=lambda t: t[2][0])

    c, sh, (tx, ty) = target
    px, py = a.place
    print("\n⭐ 选中：%s %s   龙门 (%.1f, %.1f) mm → 放到 (%.1f, %.1f)"
          % (c, sh, tx, ty, px, py))

    if a.dry:
        print("(--dry：不动机器 ✓)")
        return 0

    cmd = [sys.executable, str(PICK),
           "--item", "%.1f" % tx, "%.1f" % ty,
           "--place", "%.1f" % px, "%.1f" % py,
           "--zup", str(a.zup), "--zdn", str(a.zdn),
           "--gopen", str(a.gopen), "--gclose", str(a.gclose),
           "--speed", str(a.speed), "--zsettle", str(a.zsettle),
           "--zlift", str(a.zlift), "--gsettle", str(a.gsettle), "--yes"]
    if a.drop:
        cmd.append("--drop")
    print("\n执行：" + " ".join(cmd[1:]) + "\n")
    r = subprocess.run(cmd, text=True, timeout=600, encoding="utf-8", errors="replace")
    return r.returncode


if __name__ == "__main__":
    sys.exit(main())
