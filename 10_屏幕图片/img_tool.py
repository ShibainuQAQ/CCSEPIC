"""工创赛物料图 → 串口屏图片资源：查看尺寸 + **自动裁到实物** + 统一尺寸 + 生成对照表。

为什么需要它
------------
1. 淘晶驰的**图片控件要求图片与控件等尺寸**，否则显示会异常；
2. 屏内图片是按**在上位机里"添加图片"的顺序自动编号**（从 0 开始），
   所以文件名必须能直接排序，编号顺序 = 图片 ID 顺序；
3. 原始素材是手机拍的 1706×1279，实物只占中间一小块、周围全是木纹桌面 ——
   直接缩放会得到一个"小得看不清"的实物。所以先**按颜色把实物框出来**再裁。

自动裁切怎么做的
----------------
文件名里带颜色（绿/黄/蓝）→ 按**色相**阈值找出实物（木纹背景是棕色系，
色相和这三种差得远，再叠加饱和度阈值就能干净分开）→ 取最大连通域的外接矩形
→ 外扩一圈留白 → 补成正方形 → 缩放。三种颜色都失败时退回"居中裁方"。

用法
----
    python img_tool.py report                 # 列出素材尺寸/格式
    python img_tool.py prepare --size 160     # 输出到 发布\\00.jpg … 与 图片ID对照表.md

产物
----
    <项目>\\10_屏幕图片\\发布\\00.jpg … NN.jpg
    <项目>\\10_屏幕图片\\图片ID对照表.md
"""

import argparse
import os
import sys
from pathlib import Path

try:
    import cv2
    import numpy as np
    from PIL import Image
except ImportError:  # pragma: no cover
    sys.stdout.buffer.write("需要 Pillow / opencv-python / numpy\n".encode("utf-8"))
    raise SystemExit(1)

ROOT = Path(__file__).resolve().parent
SRC_DIR = Path(r"C:\Users\22431\Desktop\工创赛智能分拣项目\工创赛物料")
OUT_DIR = ROOT / "发布"
TABLE = ROOT / "图片ID对照表.md"

EXTS = {".jpg", ".jpeg", ".png", ".bmp"}

# 实物颜色的 HSV 色相区间（OpenCV 的 H 是 0~180）
# ⚠️ 木纹背景是棕色（H≈10~25），所以"黄"这一段最容易和背景混，靠高饱和度阈值分开。
HUE_RANGES = {
    "绿": (35, 95),
    "黄": (18, 35),
    "蓝": (95, 140),
}
SAT_MIN = {"绿": 110, "黄": 140, "蓝": 110}
VAL_MIN = 60
MIN_AREA_RATIO = 0.01  # 连通域小于整图的 1% 就认为"没找到实物"
MARGIN = 0.10          # 外扩留白比例（相对实物边长）


def out(text: str = "") -> None:
    """统一用 UTF-8 写 stdout —— 中文 Windows 控制台默认 GBK，直接 print 会炸。"""
    sys.stdout.buffer.write((text + "\n").encode("utf-8"))


def source_files():
    """素材图列表：跳过文件名以 `_` 开头的（例如 `_总览_物料矩阵.jpg`）。"""
    files = [p for p in SRC_DIR.iterdir()
             if p.suffix.lower() in EXTS and not p.name.startswith("_")]
    return sorted(files, key=lambda p: p.name)


def split_name(path: Path):
    parts = path.stem.split("-")
    color = parts[0] if parts else path.stem
    shape = parts[1] if len(parts) > 1 else ""
    return color, shape


def object_bbox(image: Image.Image, color: str):
    """按颜色找出实物的外接矩形；找不到返回 None（调用方退回居中裁方）。"""
    # ⚠️ 文件名里的颜色是"绿色/黄色/蓝色"（两个字），而表里的键是单字色相名 —— 取首字。
    key = color[0] if color else ""
    span = HUE_RANGES.get(key)
    if span is None:
        return None

    rgb = np.array(image.convert("RGB"))
    hsv = cv2.cvtColor(rgb, cv2.COLOR_RGB2HSV)
    hue, sat, val = cv2.split(hsv)

    low, high = span
    mask = ((hue >= low) & (hue <= high) &
            (sat >= SAT_MIN.get(key, 110)) & (val >= VAL_MIN)).astype(np.uint8) * 255

    kernel = np.ones((11, 11), np.uint8)
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel)
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel)

    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        return None

    box = cv2.boundingRect(max(contours, key=cv2.contourArea))
    _x, _y, w, h = box
    if w * h < MIN_AREA_RATIO * rgb.shape[0] * rgb.shape[1]:
        return None
    return box


def square_crop(image: Image.Image, box=None, margin: float = MARGIN) -> Image.Image:
    """裁成正方形：给了实物框就以它为中心外扩留白，否则取整图正中。"""
    width, height = image.size

    if box is None:
        side = min(width, height)
        left = (width - side) // 2
        top = (height - side) // 2
        return image.crop((left, top, left + side, top + side))

    x, y, w, h = box
    pad = int(max(w, h) * margin)
    cx, cy = x + w // 2, y + h // 2
    side = max(w, h) + 2 * pad

    left = max(0, cx - side // 2)
    top = max(0, cy - side // 2)
    right = min(width, left + side)
    bottom = min(height, top + side)

    # 贴到边界时把另一边补回来，尽量保住正方形
    if right - left < side:
        left = max(0, right - side)
    if bottom - top < side:
        top = max(0, bottom - side)
    return image.crop((left, top, right, bottom))


def cmd_report(_args) -> int:
    files = source_files()
    out(f"素材目录：{SRC_DIR}")
    out(f"可用素材：{len(files)} 张（已跳过 `_` 开头的）\n")
    out(f"{'文件名':<26}{'尺寸':<14}{'格式':<8}{'体积':>9}")
    out("-" * 60)
    for path in files:
        with Image.open(path) as image:
            size = f"{image.width}x{image.height}"
            fmt = image.format or "?"
            mode = image.mode
        out(f"{path.name:<26}{size:<14}{fmt:<8}{os.path.getsize(path) / 1024:>7.1f}K  {mode}")
    return 0


def cmd_prepare(args) -> int:
    files = source_files()
    size = int(args.size)
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    rows = []
    fallback = []
    for index, path in enumerate(files):
        color, shape = split_name(path)
        with Image.open(path) as raw:
            image = raw.convert("RGB")
            box = object_bbox(image, color)
            if box is None:
                fallback.append(path.name)
            squared = square_crop(image, box)
            fitted = squared.resize((size, size), Image.LANCZOS)

        name = f"{index:02d}.jpg"
        fitted.save(OUT_DIR / name, "JPEG", quality=88, optimize=True)
        rows.append((index, name, color, shape, path.name,
                     os.path.getsize(OUT_DIR / name), box is not None))

    lines = [
        "# 货物图片 ID 对照表（串口屏）",
        "",
        f"> 由 `img_tool.py prepare --size {size}` 生成，共 **{len(rows)}** 张，尺寸统一 **{size}×{size}**。",
        "> 处理方式：**按颜色自动框出实物 → 外扩留白 → 补正方形 → 缩放**（失败则退回居中裁方）。",
        "",
        "> ⚠️ **图片 ID = 在上位机里「添加图片」的顺序（从 0 开始）** —— 请**按文件名顺序**（`00.jpg`、`01.jpg`…）添加，不要插队；插一张会把后面所有 ID 往后挤。",
        "> ⚠️ 运行时只按 **ID** 调用：`LCD pic0.pic=<ID>`（ASCII，直接用 `LCD` 发）。",
        "> ⚠️ 屏的**图片控件尺寸必须与这些图一致**（当前设定 {size}×{size}），否则显示会异常。".format(size=size),
        "",
        "| 图片 ID | 文件 | 颜色 | 形状 | 原始素材 | 处理后 | 自动框选 |",
        "|---|---|---|---|---|---|---|",
    ]
    for index, name, color, shape, origin, nbytes, auto in rows:
        mark = "✅" if auto else "⚠️ 退回居中"
        lines.append(f"| **{index}** | `{name}` | {color} | {shape} | `{origin}` | {nbytes / 1024:.1f}K | {mark} |")
    lines.append("")
    lines.append("## 中文文本怎么发（GBK）")
    lines.append("")
    lines.append("中文要发 **GBK 字节**（见 `09_固件/固件设计-协议与引脚.md` §7），用 `lcd_hex.py` 生成：")
    lines.append("")
    lines.append("```powershell")
    lines.append("python 09_固件/tools/lcd_hex.py --file <把指令写进这个文件>")
    lines.append("```")
    TABLE.write_text("\n".join(lines), encoding="utf-8")

    out(f"✅ 已生成 {len(rows)} 张 → {OUT_DIR}")
    out(f"✅ 对照表 → {TABLE}\n")
    for index, name, color, shape, _origin, nbytes, auto in rows:
        flag = "" if auto else "   ⚠️ 退回居中裁方"
        out(f"  {index:>2}  {name}  {color}-{shape}  ({nbytes / 1024:.1f}K){flag}")
    if fallback:
        out(f"\n⚠️ 自动框选失败（退回居中裁方）的有 {len(fallback)} 张：")
        for name in fallback:
            out(f"    {name}")
        out("   → 请人工看一眼这几张的成品（在 发布\\ 里），必要时单独重做。")
    return 0


def cmd_sheet(args) -> int:
    """把 `发布\\` 里所有成品拼成一张总览图 —— 人（和我）一次就能全看完。"""
    files = sorted(OUT_DIR.glob("*.jpg"))
    if not files:
        out("发布\\ 里还没有图，先跑 `prepare`")
        return 1

    cols = int(args.cols)
    cell = int(args.cell)
    rows = (len(files) + cols - 1) // cols
    sheet = Image.new("RGB", (cols * cell, rows * cell), (32, 32, 32))

    for index, path in enumerate(files):
        with Image.open(path) as image:
            sheet.paste(image.convert("RGB").resize((cell, cell), Image.LANCZOS),
                        ((index % cols) * cell, (index // cols) * cell))

    target = ROOT / "总览_成品拼图.png"
    sheet.save(target)
    out(f"✅ {target}（{len(files)} 张，{cols} 列，按 ID 顺序从左到右、从上到下）")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="工创赛物料图 → 串口屏图片资源")
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("report", help="列出素材图尺寸/格式").set_defaults(func=cmd_report)

    prep = sub.add_parser("prepare", help="自动裁到实物 + 统一尺寸 + 生成对照表")
    prep.add_argument("--size", default=160, help="输出边长（像素），默认 160")
    prep.set_defaults(func=cmd_prepare)

    sheet = sub.add_parser("sheet", help="把成品拼成一张总览图，便于人工复核")
    sheet.add_argument("--cols", default=5, help="列数，默认 5")
    sheet.add_argument("--cell", default=160, help="每格边长，默认 160")
    sheet.set_defaults(func=cmd_sheet)

    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
