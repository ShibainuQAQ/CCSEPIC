# -*- coding: utf-8 -*-
r"""工创赛物料照片分析：颜色 HSV 统计 + 轮廓/比例特征。

用法： python 05_工具与校核\material_analysis.py
输出： 05_工具与校核\_物料分析.txt （UTF-8，可直接贴进文档）
"""
import sys, os, glob
import numpy as np
import cv2

sys.stdout.reconfigure(encoding='utf-8', errors='replace')

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, '工创赛物料')
OUT = os.path.join(ROOT, '05_工具与校核', '_物料分析.txt')

# 色相窗（OpenCV H: 0~179）。木地板 H≈5~20 棕色，三窗都不覆盖，天然被排除。
HUES = {'绿': (35, 90), '黄': (20, 35), '蓝': (90, 135)}

VIEW_NAMES = {'绿色': '绿', '黄色': '黄', '蓝色': '蓝'}


def imread_u(path):
    """cv2.imread 读不了中文路径。"""
    return cv2.imdecode(np.fromfile(path, dtype=np.uint8), cv2.IMREAD_COLOR)


def parse(name):
    """'绿色-正方体' -> ('绿', '正方体')"""
    for zh, short in VIEW_NAMES.items():
        if name.startswith(zh + '-'):
            return short, name[len(zh) + 1:]
    return '?', name


lines = []
def emit(s=''):
    lines.append(s)
    print(s)

emit('# 工创赛物料照片分析（自动生成，勿手改）')
emit()
emit(f'源目录：工创赛物料/  共 {len(glob.glob(os.path.join(SRC, "*.jpg")))} 张')
emit()

rows = []
for p in sorted(glob.glob(os.path.join(SRC, '*.jpg'))):
    base = os.path.splitext(os.path.basename(p))[0]
    color, shape = parse(base)
    img = imread_u(p)
    if img is None:
        emit(f'!! 读不出 {base}')
        continue
    H, W = img.shape[:2]
    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
    lo, hi = HUES[color]
    mask = cv2.inRange(hsv, (lo, 60, 60), (hi, 255, 255))
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((7, 7), np.uint8))
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((9, 9), np.uint8))
    cnts, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not cnts:
        emit(f'!! 没分割出物体 {base}')
        continue
    c = max(cnts, key=cv2.contourArea)
    x, y, w, h = cv2.boundingRect(c)
    area = cv2.contourArea(c)
    (cx, cy), (rw, rh), ang = cv2.minAreaRect(c)
    rw, rh = max(rw, rh), min(rw, rh)
    box_area = max(rw * rh, 1.0)
    sel = mask[y:y + h, x:x + w] > 0
    sub = hsv[y:y + h, x:x + w][sel]
    hm, sm, vm = (np.median(sub[:, i]) for i in range(3))
    h5, h95 = np.percentile(sub[:, 0], [5, 95])
    rows.append(dict(color=color, shape=shape, file=os.path.basename(p),
                     px_area=area, bbox=(w, h), hw=h / w, fill=area / box_area,
                     img=(W, H), cov=area / (W * H),
                     hsv=(hm, sm, vm), hrange=(h5, h95), minrect=(rw, rh)))

# ---------- 表1：逐张 ----------
emit('## 表1 逐张轮廓（像素量，仅用于相对比较）')
emit()
emit('| 颜色 | 形状 | 像素面积 | 外接矩形 W×H | H/W | 最小外接矩形 长×宽 | 填充率 | 占图比 | H中位 | S中位 | V中位 |')
emit('|---|---|---|---|---|---|---|---|---|---|---|')
for r in rows:
    w, h = r['bbox']
    rw, rh = r['minrect']
    emit(f"| {r['color']} | {r['shape']} | {r['px_area']:.0f} | {w}×{h} | {r['hw']:.2f} | "
         f"{rw:.0f}×{rh:.0f} | {r['fill']:.2f} | {r['cov']*100:.2f}% | "
         f"{r['hsv'][0]:.0f} | {r['hsv'][1]:.0f} | {r['hsv'][2]:.0f} |")
emit()

# ---------- 表2：形状汇总（跨颜色平均，验证同形状同尺寸） ----------
emit('## 表2 按形状汇总（三色取平均）')
emit()
emit('| 形状 | 张数 | H/W 均值 | H/W 极差 | 填充率 均值 | 判定 |')
emit('|---|---|---|---|---|---|')
by_shape = {}
for r in rows:
    by_shape.setdefault(r['shape'], []).append(r)
shape_order = sorted(by_shape, key=lambda s: -np.mean([x['hw'] for x in by_shape[s]]))
for s in shape_order:
    g = by_shape[s]
    hws = [x['hw'] for x in g]
    fills = [x['fill'] for x in g]
    emit(f"| {s} | {len(g)} | {np.mean(hws):.2f} | {max(hws)-min(hws):.2f} | "
         f"{np.mean(fills):.2f} | 高瘦←→扁矮 |")
emit()

# ---------- 表3：颜色标定 ----------
emit('## 表3 颜色标定（HSV，OpenCV 口径 H:0~179 / S,V:0~255）')
emit()
emit('| 颜色 | 张数 | H 中位 | H 5%~95% | S 中位 | V 中位 | 建议识别窗 |')
emit('|---|---|---|---|---|---|---|')
for color in ('绿', '黄', '蓝'):
    g = [r for r in rows if r['color'] == color]
    if not g:
        continue
    hm = np.mean([r['hsv'][0] for r in g])
    h5 = np.mean([r['hrange'][0] for r in g])
    h95 = np.mean([r['hrange'][1] for r in g])
    sm = np.mean([r['hsv'][1] for r in g])
    vm = np.mean([r['hsv'][2] for r in g])
    emit(f'| {color} | {len(g)} | {hm:.0f} | {h5:.0f}~{h95:.0f} | {sm:.0f} | {vm:.0f} | '
         f'H {max(0,int(h5)-6)}~{min(179,int(h95)+6)} & S≥{max(40,int(sm)-60)} & V≥{max(40,int(vm)-70)} |')
emit()

# ---------- 缺失组合 ----------
all_shapes = set(by_shape)
emit('## 表4 完整度（3 色 × 7 形状 = 21 应到）')
emit()
emit('| 形状 | 绿 | 黄 | 蓝 |')
emit('|---|---|---|---|')
for s in shape_order:
    have = {r['color'] for r in by_shape[s]}
    emit('| ' + s + ' | ' + ' | '.join('✅' if c in have else '❌' for c in ('绿', '黄', '蓝')) + ' |')
emit()
emit(f'实到 {len(rows)} 张；形状 {len(all_shapes)} 种。缺失：'
     + ('、'.join(f'{c}-{s}' for s in shape_order for c in ('绿', '黄', '蓝')
                  if c not in {r['color'] for r in by_shape[s]}) or '无'))
emit()

with open(OUT, 'w', encoding='utf-8') as f:
    f.write('\n'.join(lines) + '\n')
print(f'\n[OK] 写出 {OUT}  行数={len(lines)}')
