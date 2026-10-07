#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""防滑爪垫生成器（TPU/软胶打印，队内自用）。

为什么要它：开源夹爪的夹爪面是**平面 PLA**，与货物（PLA/ABS 方块、圆柱、三角柱）之间的
静摩擦系数只有 0.25~0.35；贴一层 **TPU/硅胶面** 可到 0.7~1.0，抓取所需夹紧力直接降 2~3 倍
（详见"05_抓取稳定性.md"）。这里生成两块可打印的爪垫。

用法：
  python "05_工具与校核\\grip_pad_gen.py" [输出目录] [--len 70] [--wid 14] [--thk 2]
       [--teeth 5] [--tooth 0.8] [--flat]

输出：
  爪垫_锯齿_<长>x<宽>x<厚>.stl      ← 齿面款（推荐：线接触 + 咬合，稳）
  爪垫_平面_<长>x<宽>x<厚>.stl      ← 平面款（贴 3M 背胶硅胶垫时当底板/量板用）
两块都躺在 Z=0，可直接切片；表面齿是**横向三角齿**，沿夹爪 80mm 长方向排布。
"""

from __future__ import annotations

import struct
import sys
from pathlib import Path

import numpy as np

sys.stdout.reconfigure(encoding='utf-8', errors='replace')


def _tri(a, b, c):
    return np.array([a, b, c], dtype=np.float64)


def box(x0, x1, y0, y1, z0, z1):
    p = [(x0, y0, z0), (x1, y0, z0), (x1, y1, z0), (x0, y1, z0),
         (x0, y0, z1), (x1, y0, z1), (x1, y1, z1), (x0, y1, z1)]
    f = [(0, 1, 2), (0, 2, 3), (4, 6, 5), (4, 7, 6),
         (0, 4, 5), (0, 5, 1), (1, 5, 6), (1, 6, 2),
         (2, 6, 7), (2, 7, 3), (3, 7, 4), (3, 4, 0)]
    return np.array([_tri(p[a], p[b], p[c]) for a, b, c in f])


def tri_prism(x0, x1, y0, y1, apex_y, z0, z1):
    """(y,z) 截面为三角形、沿 X 拉出的三棱柱。"""
    a0, b0, c0 = (x0, y0, z0), (x0, y1, z0), (x0, apex_y, z1)
    a1, b1, c1 = (x1, y0, z0), (x1, y1, z0), (x1, apex_y, z1)
    tris = [_tri(a0, b0, c0), _tri(a1, c1, b1),
            _tri(a0, a1, b1), _tri(a0, b1, b0),
            _tri(b0, b1, c1), _tri(b0, c1, c0),
            _tri(c0, c1, a1), _tri(c0, a1, a0)]
    return np.array(tris, dtype=np.float64)


def write_stl(path: Path, tris: np.ndarray) -> None:
    tris = np.asarray(tris, dtype=np.float64)
    v0, v1, v2 = tris[:, 0], tris[:, 1], tris[:, 2]
    n = np.cross(v1 - v0, v2 - v0)
    ln = np.linalg.norm(n, axis=1)
    ln[ln == 0] = 1.0
    n = n / ln[:, None]
    floats = np.zeros((len(tris), 12), dtype='<f4')
    floats[:, 0:3], floats[:, 3:6], floats[:, 6:9], floats[:, 9:12] = n, v0, v1, v2
    body = np.concatenate([floats.view(np.uint8).reshape(len(tris), 48),
                           np.zeros((len(tris), 2), dtype=np.uint8)], axis=1)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, 'wb') as f:
        f.write(b'grip pad (dsh)'.ljust(80, b' '))
        f.write(struct.pack('<I', len(tris)))
        f.write(body.tobytes())


def build(length=70.0, width=14.0, thick=2.0, teeth=5, tooth=0.8, base=None):
    """齿面爪垫：底板 + N 个横向三角齿（沿 Y 排布、沿 X 拉长）。"""
    base = base if base is not None else max(0.8, thick - tooth)
    parts = [box(0, length, 0, width, 0, base)]
    pitch = width / float(teeth)
    for i in range(teeth):
        y0 = i * pitch
        parts.append(tri_prism(0, length, y0 + 0.05, y0 + pitch - 0.05,
                               y0 + pitch / 2.0, base - 0.2, thick))
    return np.concatenate(parts, axis=0)


def main(argv):
    out = Path(argv[0]) if argv and not argv[0].startswith('--') else Path('.')

    def opt(name, default, cast=float):
        return cast(argv[argv.index(name) + 1]) if name in argv else default

    length = opt('--len', 70.0)
    width = opt('--wid', 14.0)
    thick = opt('--thk', 2.0)
    teeth = int(opt('--teeth', 5))
    tooth = opt('--tooth', 0.8)
    flat = '--flat' in argv

    name = '爪垫_%s_%gx%gx%g.stl' % ('平面' if flat else '锯齿', length, width, thick)
    tris = (box(0, length, 0, width, 0, thick) if flat
            else build(length, width, thick, teeth, tooth))
    # 打印件也镜像一块（两颚对称，用同一块即可；这里只出 1 件，切片时复制 2 份）
    write_stl(out / name, tris)
    lo = tris.reshape(-1, 3).min(axis=0)
    hi = tris.reshape(-1, 3).max(axis=0)
    print('写出 %s  外形 %.1f × %.1f × %.1f mm  面数 %d' % (
        out / name, hi[0] - lo[0], hi[1] - lo[1], hi[2] - lo[2], len(tris)))
    print('印 2 份；齿面朝外。装法：3M 双面胶/502 贴在夹爪 80×16 的夹持面上，'
          '贴之前用砂纸把 PLA 面打粗。')
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
