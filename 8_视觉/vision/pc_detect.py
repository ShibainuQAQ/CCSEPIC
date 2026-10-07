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


def shape_of(cnt, area):
    """根据轮廓判断形状名（俯视视角 ✓）。"""
    peri = cv2.arcLength(cnt, True)
    if peri <= 0:
        return "?"
    circ = 4.0 * np.pi * area / (peri * peri)          # 圆度：圆=1，方块≈0.785
    approx = cv2.approxPolyDP(cnt, 0.03 * peri, True)
    n = len(approx)
    x, y, w, h = cv2.boundingRect(cnt)
    aspect = w / float(h) if h else 0.0

    if circ > 0.85:
        return "circle(圆)"
    if n == 3:
        return "triangle(三棱柱/三角形)"
    if n == 4:
        if 0.85 <= aspect <= 1.18:
            return "square-ish(正方体/四棱锥/长方体 俯视都是方形)"
        return "rect(长方形)"
    if n == 5:
        return "pentagon(五边形)"
    if n == 6:
        return "hexagon(六棱柱)"
    if n >= 7:
        return "poly%dx(接近圆/多边)" % n
    return "n=%d" % n


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
