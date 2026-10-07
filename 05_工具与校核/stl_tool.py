#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""STL 批量处理小工具（队内自用，为"方便打印"服务）。

子命令：
  info   <stl...>                       量外形包围盒 / 三角面数 / 连通件数（判断是不是"整版"）
  split  <in.stl> <outdir>              把"整版 STL"按连通域拆成单件（每件一个 STL）
  plate  <out.stl> <in.stl|文件:份数>... 把若干件摆到一张床上（保留原朝向，自动降到 Z=0）
  render <stl> <out.png> [--view top]   正交渲染三视图（看朝向/有没有支撑风险）
  preview <plate.stl> <out.png>         同 render，但画成俯视"摆盘图"

通用参数：
  --bed WxH      床尺寸，默认 220x220
  --gap MM       件间距，默认 4
  --margin MM    边距，默认 8
  --rot90        允许把件绕 Z 转 90° 以提高装床率
  --scale K      统一缩放（默认 1.0）

约定：STL 一律按二进制写回；中文路径安全（不依赖 cv2）。
"""

from __future__ import annotations

import struct
import sys
from pathlib import Path

import numpy as np

sys.stdout.reconfigure(encoding='utf-8', errors='replace')


# ---------------------------------------------------------------- STL 读写
def read_stl(path: Path):
    """返回 (N,3,3) float32 顶点数组。自动识别 ASCII / 二进制。"""
    data = path.read_bytes()
    head = data[:512].lstrip().lower()
    if head[:5] == b'solid' and b'facet' in data[:4096].lower():
        verts = []
        for line in data.decode('utf-8', 'replace').splitlines():
            s = line.strip()
            if s.startswith('vertex'):
                p = s.split()
                verts.append((float(p[1]), float(p[2]), float(p[3])))
        arr = np.asarray(verts, dtype=np.float64)
        return arr.reshape(-1, 3, 3)
    count = struct.unpack('<I', data[80:84])[0]
    if count == 0 or 84 + count * 50 > len(data):
        raise ValueError('%s 不像二进制 STL（面数 %d，文件 %d 字节）' % (path.name, count, len(data)))
    raw = np.frombuffer(data, dtype=np.uint8, count=count * 50, offset=84)
    raw = raw.reshape(count, 50)[:, 12:48].copy().view('<f4').reshape(count, 3, 3)
    return raw.astype(np.float64)


def write_stl(path: Path, tris: np.ndarray) -> None:
    tris = np.asarray(tris, dtype=np.float64)
    v0, v1, v2 = tris[:, 0], tris[:, 1], tris[:, 2]
    n = np.cross(v1 - v0, v2 - v0)
    ln = np.linalg.norm(n, axis=1)
    ln[ln == 0] = 1.0
    n = n / ln[:, None]
    floats = np.zeros((len(tris), 12), dtype='<f4')
    floats[:, 0:3] = n
    floats[:, 3:6] = v0
    floats[:, 6:9] = v1
    floats[:, 9:12] = v2
    # 每个三角形 50 字节 = 12 个 float(48) + 2 字节属性计数
    body = np.concatenate([floats.view(np.uint8).reshape(len(tris), 48),
                           np.zeros((len(tris), 2), dtype=np.uint8)], axis=1)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, 'wb') as f:
        f.write(b'dsh stl_tool'.ljust(80, b' '))
        f.write(struct.pack('<I', len(tris)))
        f.write(body.tobytes())


def bounds(tris: np.ndarray):
    v = tris.reshape(-1, 3)
    return v.min(axis=0), v.max(axis=0)


# ---------------------------------------------------------------- 连通域拆分
def components(tris: np.ndarray, tol: float = 0.001):
    """按"共享顶点"把三角面分成连通件。返回 label 数组。"""
    v = tris.reshape(-1, 3)
    key = np.round(v / tol).astype(np.int64)
    _, inv = np.unique(key, axis=0, return_inverse=True)
    inv = inv.reshape(-1, 3)                       # 每个面的 3 个顶点 id
    parent = np.arange(inv.max() + 1)

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for a, b in ((0, 1), (1, 2), (0, 2)):
        for u, w in zip(inv[:, a], inv[:, b]):
            ru, rw = find(u), find(w)
            if ru != rw:
                parent[rw] = ru
    roots = np.array([find(i) for i in range(len(parent))])
    face_label = roots[inv[:, 0]]
    _, face_label = np.unique(face_label, return_inverse=True)
    return face_label


def cmd_info(files):
    for p in files:
        path = Path(p)
        t = read_stl(path)
        lo, hi = bounds(t)
        size = hi - lo
        lab = components(t)
        n = lab.max() + 1 if len(lab) else 0
        print('%-46s 面=%-7d 件=%-4d 外形 %.1f × %.1f × %.1f  Z:%.2f~%.2f' % (
            path.name, len(t), n, size[0], size[1], size[2], lo[2], hi[2]))
        if n > 1:
            for i in range(n):
                sub = t[lab == i]
                l2, h2 = bounds(sub)
                s2 = h2 - l2
                print('     件%02d  面=%-6d  %.1f × %.1f × %.1f   Z:%.2f~%.2f' % (
                    i + 1, len(sub), s2[0], s2[1], s2[2], l2[2], h2[2]))


def cmd_split(src, outdir, want=None):
    path = Path(src)
    t = read_stl(path)
    lab = components(t)
    out = Path(outdir)
    out.mkdir(parents=True, exist_ok=True)
    order = []
    for i in range(lab.max() + 1):
        sub = t[lab == i]
        lo, hi = bounds(sub)
        order.append((-(hi - lo).prod(), i, sub, lo, hi))
    order.sort()
    names = []
    for rank, (_, i, sub, lo, hi) in enumerate(order, 1):
        s = hi - lo
        name = '%s_%02d_%.0fx%.0fx%.0f.stl' % (path.stem[:24], rank, s[0], s[1], s[2])
        write_stl(out / name, sub - lo)     # 每件平移到原点
        names.append(name)
        print('  %s   面=%d' % (name, len(sub)))
    return names


# ---------------------------------------------------------------- 摆盘
def _rotate_z(tris, k):
    k %= 4
    if k == 0:
        return tris
    out = tris.copy()
    for _ in range(k):
        x = out[:, :, 0].copy()
        out[:, :, 0] = -out[:, :, 1]
        out[:, :, 1] = x
    return out


def cmd_plate(dst, items, bed=(220.0, 220.0), gap=4.0, margin=8.0, rot90=False, scale=1.0):
    """items: [(path, count)]；返回是否全部装下。"""
    parts = []
    for path, count in items:
        t = read_stl(Path(path)) * scale
        for _ in range(count):
            parts.append((Path(path).stem, t.copy()))
    # 大的先放
    parts.sort(key=lambda kv: -(np.ptp(kv[1].reshape(-1, 3)[:, 1]) * np.ptp(kv[1].reshape(-1, 3)[:, 0])))

    placements = []          # (name, tris_已就位)
    rows = []                # 每行: [y0, height, cursor_x, parts]
    W, H = bed
    x = margin
    y = margin
    row_h = 0.0
    ok = True
    for name, t in parts:
        best = None
        for k in ([0, 1, 2, 3] if rot90 else [0]):
            tt = _rotate_z(t, k)
            lo, hi = bounds(tt)
            w, d = hi[0] - lo[0], hi[1] - lo[1]
            if w + 2 * margin > W or d + 2 * margin > H:
                continue
            if x + w > W - margin:
                cand_x, cand_y, cand_rowh = margin, y + row_h + gap, d
            else:
                cand_x, cand_y, cand_rowh = x, y, max(row_h, d)
            if cand_y + d > H - margin:
                continue
            score = cand_y + d
            if best is None or score < best[0]:
                best = (score, tt, k, cand_x, cand_y, w, d, cand_rowh)
        if best is None:
            print('!! 装不下：%s（%.1f×%.1f）' % (name, *[np.ptp(t.reshape(-1, 3)[:, i]) for i in (0, 1)]))
            ok = False
            continue
        _, tt, k, px, py, w, d, newrowh = best
        if px == margin and py != y:
            y = py
            row_h = 0.0
        lo, _ = bounds(tt)
        tt = tt - np.array([lo[0], lo[1], lo[2]])
        tt = tt + np.array([px, py, 0.0])
        placements.append((name, tt))
        x = px + w + gap
        row_h = max(row_h, d)
        if x > W - margin:
            x = margin
            y = y + row_h + gap
            row_h = 0.0
    if placements:
        write_stl(Path(dst), np.concatenate([p[1] for p in placements], axis=0))
        used_y = max(bounds(p[1])[1][1] for p in placements)
        used_x = max(bounds(p[1])[1][0] for p in placements)
        print('写出 %s：%d 件，占用 %.1f × %.1f mm（床 %.0f×%.0f）' % (dst, len(placements), used_x, used_y, W, H))
        print('摆位：' + ', '.join('%s@(%.0f,%.0f)' % (n, bounds(t)[0][0], bounds(t)[0][1]) for n, t in placements))
    return ok


# ---------------------------------------------------------------- 渲染
def cmd_render(src, dst, view='top', res=900, extra=None):
    from PIL import Image, ImageDraw
    tris = read_stl(Path(src))
    extra_tris = read_stl(Path(extra)) if extra else None
    ax = {'top': (0, 1, 2), 'front': (0, 2, 1), 'side': (1, 2, 0)}[view]
    all_t = tris if extra_tris is None else np.concatenate([tris, extra_tris])
    v = all_t.reshape(-1, 3)
    lo = v.min(axis=0)
    hi = v.max(axis=0)
    span = max(hi[ax[0]] - lo[ax[0]], hi[ax[1]] - lo[ax[1]]) or 1.0
    k = (res - 40) / span

    def proj(p):
        px = (p[:, ax[0]] - lo[ax[0]]) * k + 20
        py = (hi[ax[1]] - p[:, ax[1]]) * k + 20
        pz = p[:, ax[2]]
        return px, py, pz

    zbuf = np.full((res, res), -1e18)
    shade = np.zeros((res, res))
    depth_all = all_t[:, :, ax[2]]
    dmin, dmax = depth_all.min(), depth_all.max()
    dspan = (dmax - dmin) or 1.0
    for i in range(len(all_t)):
        tri = all_t[i]
        px, py, pz = proj(tri)
        zc = ((pz.mean() - dmin) / dspan)
        x0, x1 = int(px.min()), int(np.ceil(px.max()))
        y0, y1 = int(py.min()), int(np.ceil(py.max()))
        if x1 <= x0 or y1 <= y0:
            x1, y1 = x0 + 1, y0 + 1
        xs = np.arange(x0, x1 + 1)
        ys = np.arange(y0, y1 + 1)
        if len(xs) == 0 or len(ys) == 0:
            continue
        gx, gy = np.meshgrid(xs, ys)
        d = (py[1] - py[2]) * (px[0] - px[2]) + (px[2] - px[1]) * (py[0] - py[2])
        if abs(d) < 1e-12:
            continue
        w0 = ((py[1] - py[2]) * (gx - px[2]) + (px[2] - px[1]) * (gy - py[2])) / d
        w1 = ((py[2] - py[0]) * (gx - px[2]) + (px[0] - px[2]) * (gy - py[2])) / d
        w2 = 1 - w0 - w1
        m = (w0 >= -1e-9) & (w1 >= -1e-9) & (w2 >= -1e-9)
        if not m.any():
            continue
        sy = np.clip(gy[m], 0, res - 1)
        sx = np.clip(gx[m], 0, res - 1)
        cur = zbuf[sy, sx]
        take = zc >= cur
        zbuf[sy[take], sx[take]] = zc
        shade[sy[take], sx[take]] = 0.55 + 0.45 * zc

    img = np.ones((res, res, 3), dtype=np.uint8) * 255
    inside = zbuf > -1e17
    g = (shade * 235).astype(np.uint8)
    img[inside] = np.stack([g, g, (g * 0.95 + 8).astype(np.uint8)], axis=-1)[inside]
    im = Image.fromarray(img)
    d = ImageDraw.Draw(im)
    size = hi - lo
    d.text((8, 6), '%s  %s  %.1f x %.1f x %.1f mm' % (
        Path(src).name[:40], view, size[0], size[1], size[2]), fill=(0, 0, 0))
    Path(dst).parent.mkdir(parents=True, exist_ok=True)
    im.save(dst)
    print('渲染 %s -> %s (%s, %.1fx%.1f mm)' % (Path(src).name, dst, view, size[ax[0]], size[ax[1]]))


def main(argv):
    if not argv:
        print(__doc__)
        return 0
    def opt(name, default=None, cast=float):
        if name in argv:
            i = argv.index(name)
            v = argv[i + 1]
            return cast(v)
        return default

    cmd = argv[0]
    rest = [a for a in argv[1:] if not a.startswith('--')]
    bed = opt('--bed', '220x220', str)
    W, H = [float(x) for x in bed.lower().split('x')]
    gap, margin, scale = opt('--gap', 4.0), opt('--margin', 8.0), opt('--scale', 1.0)
    rot90 = '--rot90' in argv

    if cmd == 'info':
        cmd_info([a for a in argv[1:] if not a.startswith('--')])
    elif cmd == 'split':
        cmd_split(rest[0], rest[1])
    elif cmd == 'plate':
        items = []
        for spec in rest[1:]:
            if ':' in spec and not spec[1:3] == ':\\':
                p, c = spec.rsplit(':', 1)
                items.append((p, int(c)))
            else:
                items.append((spec, 1))
        return 0 if cmd_plate(rest[0], items, (W, H), gap, margin, rot90, scale) else 1
    elif cmd == 'render':
        view = argv[argv.index('--view') + 1] if '--view' in argv else 'top'
        ex = argv[argv.index('--extra') + 1] if '--extra' in argv else None
        cmd_render(rest[0], rest[1], view, extra=ex)
    elif cmd == 'preview':
        cmd_render(rest[0], rest[1], 'top')
    else:
        print(__doc__)
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
