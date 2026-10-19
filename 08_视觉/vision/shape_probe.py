"""形状判据探针 —— 把每个色块的**凸包点数**与**各阶谐波幅度**打出来，用来标定阈值 ✓。

为什么要有它：形状判据（`pc_detect.shape_of`）的阈值不能拍脑袋定 ✗ ——
2026-10-08 就是"数顶点"的判据在实机上连拍两张就跳变（`hexagon` ↔ `poly7x` ✗），
所以改成"角向对称阶数"之后，**必须能一眼看到每个色块的实际谐波数值** ✓，
才知道阈值该定在哪、以及哪一件是被掩膜搞坏的（而不是判据本身不行 ✓）。

用法：
  D:\\python\\python.exe 08_视觉\\vision\\shape_probe.py 08_视觉\\vision\\_vp.jpg
"""
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import cv2          # noqa: E402
import numpy as np  # noqa: E402
import pc_detect    # noqa: E402

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass


def main():
    path = Path(sys.argv[1]) if len(sys.argv) > 1 else (HERE / "_vp.jpg")
    img = pc_detect.imread_unicode(path)
    if img is None:
        print("[FAIL] 读不到图：%s" % path)
        return 1
    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
    print("图 %s  %dx%d" % (path.name, img.shape[1], img.shape[0]))
    for name, (lo, hi) in pc_detect.COLORS.items():
        mask = cv2.inRange(hsv, np.array(lo, np.uint8), np.array(hi, np.uint8))
        mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
        mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((5, 5), np.uint8))
        cnts, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        for c in cnts:
            a = cv2.contourArea(c)
            if a < 250:
                continue
            hull = cv2.convexHull(c)
            peri = cv2.arcLength(hull, True)
            app = cv2.approxPolyDP(hull, 0.02 * peri, True) if peri > 0 else hull
            prof = pc_detect.radial_profile(c)
            k, amp, harm = pc_detect.symmetry_order(prof) if prof is not None else (None, 0.0, {})
            print("\n%-6s 面积=%-5d 凸包=%d 点 简化后=%d 点  →  **%s**"
                  % (name, int(a), len(hull), len(app), pc_detect.shape_of(c, a)))
            if prof is not None:
                print("        椭圆模型残差 = **%.4f**" % pc_detect.ellipse_residual(prof))
                rs = {n: pc_detect.ngon_residual(prof, n) for n in range(3, 9)}
                print("        正 n 边形残差：%s   → 最优 n=%d（%.4f）"
                      % ("  ".join("n%d=%.4f" % (nn, vv) for nn, vv in sorted(rs.items())),
                         min(rs, key=rs.get), min(rs.values())))
            if harm:
                print("        最高阶 k=%s (幅度 %.3f)；各阶：%s"
                      % (k, amp, "  ".join("k%d=%.3f" % (kk, vv) for kk, vv in sorted(harm.items()))))
            else:
                print("        （算不出谐波）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
