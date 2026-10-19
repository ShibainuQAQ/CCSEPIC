"""PC 侧视觉识别：从一张照片里找出货物（颜色 + 形状），输出列表。

这是「校赛方案」的第一步：K210 只当相机（拍照回传 PC ✓），
识别在这里做（PC 上算力足、好调试 ✓）。识别结果后面会：
  · 用标定把「像素坐标 → 龙门毫米」✓
  · 按规则分配盒子（同形同色归同盒 ✓ 单盒≤4件 ✓）
  · 拼成 `@PLAN/@PI/@PLAN END` 下发给 STM32 ✓

用法：
  D:\\python\\python.exe 12_视觉\\vision\\pc_detect.py 12_视觉\\shots\\vision_now.jpg
  ... --annotate 输出图.png        # 画框图，方便肉眼核对 ✓
  ... --min-area 300               # 最小色块面积（像素）
"""
import argparse
import sys
from pathlib import Path

import cv2
import numpy as np

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

# 颜色阈值（**HSV**：H 0~179、S/V 0~255 ✓）。
# ⭐ 2026-10-04 用户确认：**物料总共只有 黄、绿、蓝 三种** ✓
#    ⇒ 色表就只留这三种，**黑色/白色/其它全部忽略** ✓
#      （原来把 black/white 也放进来，结果左侧机构和阴影被当成"货物"误检 ✗）
#    这样误检自动消失 ✓，而且"不认识的颜色"不会再混进计划 ✓。
# ⚠️ 阈值是经验值：真实光照下标定时再微调（尤其蓝色偏青/偏紫要试 ✓）。
COLORS = {
    "yellow": [(20, 90, 90), (35, 255, 255)],
    "green":  [(40, 60, 50), (90, 255, 255)],
    # ⚠️ 2026-10-04 实测修正：蓝三角锥在托盘上被光照洗淡 ⇒ **H=111, S=68** ✗
    #    原来要求 S≥80 ⇒ **明明在画面里却识别不到** ✓（正是"东西在却找不到"的典型 ✗）
    #    ⇒ S 门槛降到 **45**（实测 68，留余量 ✓；白/灰背景 S 通常在 0~30 ✓ 不会误检 ✓）
    "blue":   [(95, 45, 90), (125, 255, 255)],
}


def imread_unicode(path):
    """读图（**支持中文路径** ✓）。

    ⚠️ 坑：Windows 上 `cv2.imread('中文路径.jpg')` 会直接失败 ✗
    （OpenCV 内部用窄字符 API，非 ASCII 路径打不开 ✓）—— 本项目路径里就有中文 ✓，
    所以一律用 `np.fromfile` + `imdecode` 绕过去 ✓。写图同理用 `imencode` + `tofile` ✓。
    """
    try:
        data = np.fromfile(str(path), dtype=np.uint8)
        return cv2.imdecode(data, cv2.IMREAD_COLOR)
    except Exception:
        return None


def imwrite_unicode(path, img):
    try:
        ok, buf = cv2.imencode(Path(path).suffix or ".png", img)
        if ok:
            buf.tofile(str(path))
        return ok
    except Exception:
        return False


def radial_profile(cnt, n=360, simplify=0.02):
    """把轮廓采样成**均匀角度上的半径** r(θ)（返回长度 n 的数组，失败返回 None ✓）。

    三个必须踩对的点（都实测栽过 ✗）：
      ① 用**凸包**：掩膜的小缺口/毛刺被填平 ✓（比原始轮廓稳得多 ✓）；
      ② 半径必须按 **"射线与边求交"解析算** ✗ 不能对顶点半径插值 ——
         凸包的边是**直线**，边中点半径会掉到 R·cos(30°)=0.866R（六边形）✗，
         插值等于把直边抹平成圆弧 ⇒ **六棱柱被算成"圆"** ✗✗（第一版就是这样 ✗）；
      ③ 凸包上常残留几十个**近共线**的噪声点（抗锯齿 + 形态学造成 ✗）⇒
         先用 `approxPolyDP` 按**周长比例**简化，把真正的"角"留下 ✓
         （不简化的话 r(θ) 太光滑，谐波幅度被抹掉 ⇒ 同样判成圆 ✗）。
    """
    hull = cv2.convexHull(cnt)
    if hull is None or len(hull) < 3:
        return None
    peri = cv2.arcLength(hull, True)
    if simplify > 0 and peri > 0:
        app = cv2.approxPolyDP(hull, simplify * peri, True)
        if len(app) >= 3:
            hull = app
    m = cv2.moments(hull)
    if m["m00"] <= 0:
        return None
    cx, cy = m["m10"] / m["m00"], m["m01"] / m["m00"]
    P = hull.reshape(-1, 2).astype(np.float64) - np.array([cx, cy])
    ang = np.arctan2(P[:, 1], P[:, 0])
    rad = np.hypot(P[:, 0], P[:, 1])
    order = np.argsort(ang)
    ang, rad = ang[order], rad[order]
    K = len(ang)

    prof = np.full(n, np.nan)
    grid = np.linspace(ang[0], ang[0] + 2.0 * np.pi, n, endpoint=False)
    for i in range(K):
        a1, r1 = ang[i], rad[i]
        j = (i + 1) % K
        a2 = ang[j] + (2.0 * np.pi if j <= i else 0.0)
        r2 = rad[j]
        span = a2 - a1
        if span <= 1e-12:
            continue
        sel = np.mod(grid - a1, 2.0 * np.pi) < span
        if not np.any(sel):
            continue
        ph = np.mod(grid[sel] - a1, 2.0 * np.pi)
        # r(φ) = r1·r2·sin(Δ) / [ r2·sin(φ) + r1·sin(Δ−φ) ] ✓
        den = r2 * np.sin(ph) + r1 * np.sin(span - ph)
        den = np.where(np.abs(den) < 1e-9, 1e-9, den)
        prof[sel] = r1 * r2 * np.sin(span) / den

    if not np.isfinite(prof).any():
        return None
    if not np.isfinite(prof).all():                 # 极少数没盖到的角度 → 用均值补上 ✓
        prof[~np.isfinite(prof)] = np.nanmean(prof)
    if prof.mean() <= 1e-6:
        return None
    return prof


def symmetry_order(prof, k_max=8):
    """对 r(θ) 做 FFT，返回 (最小显著谐波阶数 k, 该阶幅度, 全部幅度表) ✓。

    为什么看"阶数"而不是数顶点：**正 n 边形 = n 重旋转对称** ✓ ——
    正三角形只有 3、6、9… 阶谐波；正六边形只有 6、12… 阶（**没有 3 阶**，
    因为它绕中心转 180° 不变 ✓）；圆/椭圆则连 3 阶以上都没有 ✓。
    ⇒ "k≥3 里**最小**的显著谐波"就是对称重数，**这是数顶点给不出的稳定信息** ✓✓。
    """
    n = len(prof)
    spec = np.abs(np.fft.rfft(prof / prof.mean() - 1.0)) * (2.0 / n)
    harm = {k: float(spec[k]) for k in range(3, k_max + 1) if k < len(spec)}
    if not harm:
        return None, 0.0, {}
    kmax = max(harm, key=harm.get)
    return kmax, harm[kmax], harm


def ngon_residual(prof, n, steps=48):
    """把 r(θ) 和**正 n 边形**模型对齐后取最小 RMS 残差 ✓（形状判据的核心 ✓）。

    模型：中心到边的距离 r(θ) = cos(π/n) / cos( ((θ−θ0) mod 2π/n) − π/n )，
    再整体归一化到均值 1（**缩放对形状没意义** ✓），θ0 在 [0, 2π/n) 上扫一遍取最优 ✓。
    """
    m = prof / prof.mean()
    th = np.linspace(0.0, 2.0 * np.pi, len(m), endpoint=False)
    best = None
    for t0 in np.linspace(0.0, 2.0 * np.pi / n, steps, endpoint=False):
        u = np.mod(th - t0, 2.0 * np.pi / n) - np.pi / n
        mod = np.cos(np.pi / n) / np.cos(u)
        mod = mod / mod.mean()
        r = float(np.sqrt(np.mean((m - mod) ** 2)))
        if best is None or r < best:
            best = r
    return best if best is not None else 1.0


def ellipse_residual(prof):
    """**椭圆模型**的 RMS 残差 ✓ —— 去掉直流 + 1、2 阶谐波（= 一个椭圆能解释的全部）✗。

    为什么必须有它：相机有俯角 ⇒ **圆柱的顶面在画面里是椭圆，不是圆** ✗；
    而"圆柱"和"六棱柱"的差别不在圆度（六棱柱也很圆 ✗），
    而在"**除了椭圆之外还剩多少结构**"✓ —— 这正是这个残差衡量的东西 ✓。
    """
    n = len(prof)
    th = np.linspace(0.0, 2.0 * np.pi, n, endpoint=False)
    A = np.column_stack([np.ones(n), np.cos(th), np.sin(th),
                         np.cos(2 * th), np.sin(2 * th)])
    y = prof / prof.mean()
    coef, *_ = np.linalg.lstsq(A, y, rcond=None)
    return float(np.sqrt(np.mean((y - A @ coef) ** 2)))


def shape_of(cnt, area):
    """根据轮廓判断形状名（俯视视角 ✓）。

    ⚠️⚠️ 2026-10-08 **重写**：旧判据是"`approxPolyDP` 数顶点 + 圆度"，**实机上极不稳定** ✗✗ ——
       同一天连拍两张，同一件绿色六棱柱会在 `hexagon(六棱柱)` 和 `poly7x(接近圆/多边)`
       之间来回跳 ✗（顶点数对光照/抗锯齿/掩膜边缘的一两个像素太敏感 ✗）。
       后果：`vision_pick.py --shape hex` **时好时坏，空跑直接选不到货** ✗（实测踩到 ✓）。

    新判据 = **角向对称阶数**（见 `radial_profile` / `symmetry_order` 的注释 ✓）：
      · 最小显著谐波 k = **3/4/5/6** ⇒ 三角形 / 正方形 / 五边形 / **六棱柱** ✓
      · k≥3 的谐波**全都很小** ⇒ 圆 / 椭圆（圆柱俯视 + 相机俯角 ✓）
    """
    prof = radial_profile(cnt)
    if prof is None:
        return "?"
    kmax, amp, harm = symmetry_order(prof)
    if kmax is None:
        return "?"

    x, y, w, h = cv2.boundingRect(cnt)
    aspect = w / float(h) if h else 0.0

    # ① **主阶明确** ⇒ 就是多边形，阶数 = 对称重数 ✓
    #    判据 = 最高阶既够大（≥0.030）又明显压过第二阶（≥1.45 倍）✓
    #    为什么不能只看绝对值 ✗：实测**不规则的圆斑**（黄圆柱的掩膜把顶面+侧面连成花生形 ✗）
    #    各阶谐波都在 0.013~0.036 之间、"最高阶"纯属噪声 ✗ ——
    #    而真六棱柱是 0.039（第二阶 0.025）⇒ 两者绝对值几乎撞上 ✗，
    #    只有"**有没有明显压过第二名**"能把它们分开 ✓。
    vals = sorted(harm.values(), reverse=True)
    second = vals[1] if len(vals) > 1 else 0.0
    dominant = (amp >= 0.030) and (second <= 1e-9 or amp >= 1.45 * second)

    # ② 没有主阶：能被**椭圆**解释 ⇒ 圆 / 圆柱（相机有俯角，圆柱顶面在画面里是椭圆 ✓）
    if not dominant:
        if ellipse_residual(prof) < 0.08:
            return "circle(圆/圆柱)"
        # ③ 既没主阶、又不像椭圆 ⇒ **不硬猜**，明确报"不规则"，让人去查掩膜/光照 ✓
        #    （比随便给个名字强 ✓ —— 分拣规则里"形状判错 = 整盒清零" ✗✗）
        return "?irregular(不规则-查掩膜/光照)"

    if kmax == 3:
        return "triangle(三棱柱/三棱锥)"
    if kmax == 4:
        if 0.85 <= aspect <= 1.18:
            return "square-ish(正方体/四棱锥)"
        return "rect(长方形)"
    if kmax == 5:
        return "pentagon(五边形)"
    if kmax == 6:
        return "hexagon(六棱柱)"
    return "poly%dx(接近圆/多边)" % kmax


def detect(img, min_area):
    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
    found = []
    for name, (lo, hi) in COLORS.items():
        mask = cv2.inRange(hsv, np.array(lo, np.uint8), np.array(hi, np.uint8))
        mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
        mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((5, 5), np.uint8))
        cnts, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        for c in cnts:
            a = cv2.contourArea(c)
            if a < min_area:
                continue
            m = cv2.moments(c)
            cx = m["m10"] / m["m00"] if m["m00"] else 0
            cy = m["m01"] / m["m00"] if m["m00"] else 0
            base = name[:-1] if name.endswith("2") else name
            found.append({
                "color": base,
                "shape": shape_of(c, a),
                "px": (round(cx, 1), round(cy, 1)),
                "area": int(a),
                "bbox": cv2.boundingRect(c),
                "cnt": c,
            })
    # 按面积从大到小
    found.sort(key=lambda d: -d["area"])
    return found


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("image")
    ap.add_argument("--annotate", default=None)
    ap.add_argument("--min-area", type=float, default=250)
    a = ap.parse_args()

    img = imread_unicode(a.image)
    if img is None:
        print("读不到图片：%s" % a.image)
        return 1
    print("图片 %s  尺寸 %dx%d" % (Path(a.image).name, img.shape[1], img.shape[0]))

    items = detect(img, a.min_area)
    print("\n找到 %d 个色块：" % len(items))
    print("  #  颜色    形状                                         中心(px)      面积  外框")
    for i, it in enumerate(items, 1):
        print("  %-2d %-6s %-42s (%6.1f,%6.1f) %6d  %s"
              % (i, it["color"], it["shape"], it["px"][0], it["px"][1], it["area"], it["bbox"]))

    if a.annotate:
        out = img.copy()
        for i, it in enumerate(items, 1):
            x, y, w, h = it["bbox"]
            cv2.rectangle(out, (x, y), (x + w, y + h), (0, 0, 255), 2)
            cv2.putText(out, "%d %s" % (i, it["color"]), (x, max(12, y - 4)),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.45, (0, 0, 255), 1)
            cv2.circle(out, (int(it["px"][0]), int(it["px"][1])), 3, (255, 0, 0), -1)
        imwrite_unicode(a.annotate, out)
        print("\n标注图已存：%s" % a.annotate)
    return 0


if __name__ == "__main__":
    sys.exit(main())
