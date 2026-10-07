"""在照片上取一小块，打印它的 HSV 统计 —— 用来把颜色阈值定准。

为什么要它：HSV 阈值凭感觉给 ✗ 很容易漏检（比如被光照洗淡的蓝色，
饱和度低 ⇒ 卡在 `S >= 80` 外面 ⇒ "东西明明在画面里却识别不到" ✓）。
拿一小块实测一下，阈值就是**量出来的**而不是猜的 ✓。

用法：
  D:\\python\\python.exe 12_视觉\\vision\\hsv_probe.py 图.jpg 150 90        # 取 5x5
  ... 150 90 12                                                          # 取 25x25
"""
import sys
from pathlib import Path

import cv2
import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass


def imread_unicode(path):
    data = np.fromfile(str(path), dtype=np.uint8)
    return cv2.imdecode(data, cv2.IMREAD_COLOR)


def main():
    if len(sys.argv) < 4:
        print(__doc__)
        return 2
    path = sys.argv[1]
    cx, cy = int(sys.argv[2]), int(sys.argv[3])
    rad = int(sys.argv[4]) if len(sys.argv) > 4 else 5

    img = imread_unicode(path)
    if img is None:
        print("读不到图：%s" % path)
        return 1
    h, w = img.shape[:2]
    x0, x1 = max(0, cx - rad), min(w, cx + rad + 1)
    y0, y1 = max(0, cy - rad), min(h, cy + rad + 1)
    patch = img[y0:y1, x0:x1]
    hsv = cv2.cvtColor(patch, cv2.COLOR_BGR2HSV)
    b, g, r = patch[:, :, 0], patch[:, :, 1], patch[:, :, 2]
    H, S, V = hsv[:, :, 0], hsv[:, :, 1], hsv[:, :, 2]

    print("图 %dx%d   取样区 x[%d,%d) y[%d,%d)  (%d 像素)"
          % (w, h, x0, x1, y0, y1, patch.shape[0] * patch.shape[1]))
    print("  BGR 均值 : B=%d G=%d R=%d" % (b.mean(), g.mean(), r.mean()))
    print("  HSV 中位 : H=%d S=%d V=%d" % (np.median(H), np.median(S), np.median(V)))
    print("  HSV 范围 : H=[%d,%d]  S=[%d,%d]  V=[%d,%d]"
          % (H.min(), H.max(), S.min(), S.max(), V.min(), V.max()))
    print("")
    print("  ⇒ 建议阈值（留余量 ✓）：H=[%d,%d] S>=%d V>=%d"
          % (max(0, int(H.min()) - 10), min(179, int(H.max()) + 10),
             max(20, int(np.median(S)) - 45), max(20, int(np.median(V)) - 60)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
