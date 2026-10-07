#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""MayCAD 场景三视图渲染器（OpenCV 版，免装 matplotlib）—— 把 .scene 画成 俯视/前视/侧视 投影图。

配合 maycad_check.py 用：校核器出"尺寸对不对"，这个出"长什么样"。
场景件一多（>10 根）光看数字很难脑补结构，画出来一眼看出谁是谁。
用本机已装的 opencv 画图，不用装任何新东西。

用法：
  python maycad_view.py <文件.scene> [输出.png]
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from maycad_check import parse_scene, geometry  # noqa: E402

# 视图：(横轴, 纵轴, 标题, 纵轴是否翻转)。MayCAD 里 Y 朝上 → 前/侧视要翻转；俯视让 +Z 朝下。
VIEWS = [
    (0, 2, "Top X-Z", False),
    (0, 1, "Front X-Y", True),
    (2, 1, "Side Z-Y", True),
]
COLOR_4040 = (176, 108, 43)   # 蓝（BGR）
COLOR_OTHER = (32, 107, 221)  # 橙（BGR）


def render(scene_path: Path, out_path: Path) -> None:
    import cv2
    import numpy as np

    members = parse_scene(scene_path)
    geoms = [(m, *geometry(m)) for m in members]

    panels = []
    for xa, ya, title, flip in VIEWS:
        xs = [g[4][xa] for g in geoms]
        ys = [g[4][ya] for g in geoms]
        xmin = min(v[0] for v in xs)
        xmax = max(v[1] for v in xs)
        ymin = min(v[0] for v in ys)
        ymax = max(v[1] for v in ys)
        width = height = 460
        margin = 34
        scale = min((width - 2 * margin) / max(1e-6, xmax - xmin),
                    (height - 2 * margin) / max(1e-6, ymax - ymin))

        def px(x, y):
            ix = int(margin + (x - xmin) * scale)
            iy = int(margin + (y - ymin) * scale)
            if flip:
                iy = height - iy
            return ix, iy

        img = np.full((height, width, 3), 255, np.uint8)
        cv2.putText(img, title, (10, 22), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (0, 0, 0), 1, cv2.LINE_AA)
        for m, start, end, axis, bbox in geoms:
            color = COLOR_4040 if m["profile"] == "PROF40-4040L" else COLOR_OTHER
            p0 = px(bbox[xa][0], bbox[ya][0])
            p1 = px(bbox[xa][1], bbox[ya][1])
            cv2.rectangle(img, p0, p1, color, -1)
            cv2.rectangle(img, p0, p1, (25, 25, 25), 1)
            cx, cy = (p0[0] + p1[0]) // 2, (p0[1] + p1[1]) // 2
            cv2.putText(img, str(m["id"]), (cx - 7, cy + 3),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.32, (15, 15, 15), 1, cv2.LINE_AA)
        panels.append(img)

    combined = np.hstack(panels)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    # cv2.imwrite 在中文路径下会失败 → 用 imencode 编码成字节，再用 Python 写（支持中文路径）
    ok, buffer = cv2.imencode(".png", combined)
    if not ok:
        raise RuntimeError("PNG 编码失败")
    out_path.write_bytes(buffer.tobytes())
    print("已存：%s" % out_path)


def main(argv: list[str]) -> int:
    if not argv:
        print(__doc__)
        return 0
    scene = Path(argv[0])
    out = Path(argv[1]) if len(argv) > 1 else scene.with_name(scene.stem + "_view.png")
    try:
        render(scene, out)
    except ImportError as error:
        print("需要 opencv（本机 hw_bridge 已在用，不该缺）：%s" % error)
        return 1
    return 0


if __name__ == "__main__":
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    sys.exit(main(sys.argv[1:]))
