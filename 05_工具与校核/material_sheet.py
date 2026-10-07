# -*- coding: utf-8 -*-
r"""把 工创赛物料/ 里的散照片拼成一张"7 形状 × 3 色"矩阵总览图（带中文标签）。

用法： python 05_工具与校核\material_sheet.py
输出： 工创赛物料\_总览_物料矩阵.jpg
"""
import os
import sys
import glob
import numpy as np
import cv2
from PIL import Image, ImageDraw, ImageFont

sys.stdout.reconfigure(encoding='utf-8', errors='replace')

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, '工创赛物料')
OUT = os.path.join(SRC, '_总览_物料矩阵.jpg')

COLORS = ['绿色', '黄色', '蓝色']
SHAPES = ['正方体', '正三棱柱', '正六棱柱', '圆柱', '正四棱锥', '正五棱锥', '正四面体']

CW, CH = 360, 300          # 单元格（照片区）宽高
BAR = 40                   # 标签条高
PAD = 8
TOP = 70

FONT = r'C:\Windows\Fonts\msyh.ttc'


def imread_u(path):
    return cv2.imdecode(np.fromfile(path, dtype=np.uint8), cv2.IMREAD_COLOR)


def font(size):
    try:
        return ImageFont.truetype(FONT, size)
    except OSError:
        return ImageFont.load_default()


def fit(img, w, h):
    """等比缩放并居中裁剪到 w×h。"""
    ih, iw = img.shape[:2]
    s = max(w / iw, h / ih)
    nw, nh = int(round(iw * s)), int(round(ih * s))
    img = cv2.resize(img, (nw, nh), interpolation=cv2.INTER_AREA)
    x0, y0 = (nw - w) // 2, (nh - h) // 2
    return img[y0:y0 + h, x0:x0 + w]


W = PAD + len(COLORS) * (CW + PAD)
H = TOP + len(SHAPES) * (CH + BAR + PAD) + PAD
sheet = Image.new('RGB', (W, H), (250, 250, 250))
d = ImageDraw.Draw(sheet)
f_title = font(34)
f_head = font(26)
f_cell = font(24)

d.text((PAD, 16), '工创赛 II-2 物料矩阵（7 形状 × 3 色）', font=f_title, fill=(20, 20, 20))

# 表头
for j, c in enumerate(COLORS):
    x = PAD + j * (CW + PAD)
    d.text((x + CW // 2 - 40, TOP - 38), c, font=f_head, fill=(30, 30, 30))

miss = []
for i, sh in enumerate(SHAPES):
    y = TOP + i * (CH + BAR + PAD)
    for j, c in enumerate(COLORS):
        x = PAD + j * (CW + PAD)
        p = os.path.join(SRC, f'{c}-{sh}.jpg')
        if os.path.exists(p):
            img = fit(imread_u(p), CW, CH)
            sheet.paste(Image.fromarray(cv2.cvtColor(img, cv2.COLOR_BGR2RGB)), (x, y))
            label, fill = f'{c}-{sh}', (40, 40, 40)
        else:
            d.rectangle([x, y, x + CW, y + CH], fill=(225, 225, 225), outline=(170, 170, 170))
            d.text((x + CW // 2 - 30, y + CH // 2 - 16), '缺照片', font=f_cell, fill=(150, 60, 60))
            label, fill = f'{c}-{sh}（缺）', (170, 40, 40)
            miss.append(label)
        d.rectangle([x, y + CH, x + CW, y + CH + BAR], fill=(238, 238, 238))
        d.text((x + 10, y + CH + 7), label, font=f_cell, fill=fill)

sheet.save(OUT, quality=88)
print(f'[OK] {OUT}  {W}x{H}  缺失 {len(miss)}: {miss}')
