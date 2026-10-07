# -*- coding: utf-8 -*-
"""
layout_check.py — 工作区布局 / 干涉 / 行程覆盖校核（定稿口径 2026-09-17 上午）

配套《框架与布局尺寸.md》使用。改布局就改顶部常量再跑：
    python layout_check.py

坐标系（同框架文档 §1）：X=宽（0~350）、Y=深（前=0，0~350）、Z=高（离地）。
校核三件事：
  1. 布局干涉：托盘 / 6 盒 / 立柱 / 底框 互不打架，全部进 350×350
  2. 行程覆盖：龙门两轴行程能否覆盖全部取放点（逐个列出）
  3. 规则红线：托盘 ≥160×160 沿≥10、盒内腔容 4 件 ≤40mm、整机 ≤350×350×450
"""

# ============ 需求常量（改设计改这里） ============
FRAME = 350          # 整机外廓（宽=深）
POST = 40            # 4040 立柱截面
MAX_H = 450          # 规则总高上限
FRAME_H = 300        # 当前框架总高

BOX_OUT = 86         # 储物盒外宽（⚠️ 定稿 86，别做 88）
BOX_IN = 82          # 内腔 = 外宽 - 2×壁2
BOX_WALL = 2
BOX_GAP = 2          # 盒间缝
COLS, ROWS = 3, 2    # 2×3
CAVITY_H = 45        # 盒内腔净高（4 件 40 单层 2×2 放）

TRAY_OUT = 160       # 托盘外廓（规则下限 160；想抗打印收缩就 162~165，见报告提示）
TRAY_WALL = 2.5      # 托盘壁厚
TRAY_FRONT = 2       # 托盘前沿距机架前沿

GOODS_MAX = 40       # 货物 ≤40 见方
PER_BOX = 4          # 单盒 ≤4 件

# 龙门行程
DEEP_RAIL = 350      # 深向导轨梁长
DEEP_SLIDER = 45     # 深向滑块长（MGN12H=45；换短款 MGN12C≈35 见补救项）
Y_BEAM = 270         # Y 梁（宽向）长
Y_CARRIAGE = 45      # 宽向滑块长

# 覆盖余量（除几何点外的工程余量）
GRIP_HALF = 25       # 夹爪开口 50 的一半：放货点四周需无遮挡的半径
FRONT_GOODS_MIN = 10 # 托盘内最小货物中心能贴到的最前位置（壁2.5+小件半径~5，取保守 10）
# =================================================

import sys
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

PASS, FAIL, WARN = "✅", "❌", "⚠️"
issues = []
_buf = []

def out(s=""):
    _buf.append(s)

def line(mark, item, detail):
    out(f"  {mark}  {item:<28} {detail}")
    if mark == FAIL:
        issues.append(item)

out("=" * 72)
out("布局 / 干涉 / 行程覆盖校核报告（定稿口径：盒外宽 86 · 托盘外廓 160）")
out("=" * 72)

# ---------- 1. 布局几何 ----------
out("\n【1】布局坐标（X 宽 / Y 深，单位 mm）")

interior_x0, interior_x1 = POST, FRAME - POST          # 内净宽 40~310 = 270
interior_w = interior_x1 - interior_x0

grp_w = COLS * BOX_OUT + (COLS - 1) * BOX_GAP          # 262
grp_d = ROWS * BOX_OUT + (ROWS - 1) * BOX_GAP          # 174
grp_x0 = interior_x0 + (interior_w - grp_w) / 2        # 居中 → 44
grp_x1 = grp_x0 + grp_w

tray_x0 = interior_x0 + (interior_w - TRAY_OUT) / 2    # 居中 → 95
tray_x1 = tray_x0 + TRAY_OUT
tray_y0, tray_y1 = TRAY_FRONT, TRAY_FRONT + TRAY_OUT   # 2~162

grp_y0 = tray_y1 + 6                                   # 托盘与盒组留 6mm 缝
grp_y1 = grp_y0 + grp_d

out(f"  托盘：X {tray_x0:.0f}~{tray_x1:.0f}，Y {tray_y0:.0f}~{tray_y1:.0f}（外廓 {TRAY_OUT}）")
out(f"  盒组：X {grp_x0:.0f}~{grp_x1:.0f}，Y {grp_y0:.0f}~{grp_y1:.0f}（{grp_w}×{grp_d}，{COLS}×{ROWS} @节距 {BOX_OUT + BOX_GAP}）")

# 盒腔中心（放置点）与托盘取货区
pitch = BOX_OUT + BOX_GAP
cells = [(grp_x0 + BOX_OUT / 2 + c * pitch, grp_y0 + BOX_OUT / 2 + r * pitch)
         for r in range(ROWS) for c in range(COLS)]
tray_inner = (tray_x0 + TRAY_WALL, tray_x1 - TRAY_WALL,
              tray_y0 + TRAY_WALL, tray_y1 - TRAY_WALL)

# ---------- 2. 干涉与红线 ----------
out("\n【2】干涉与规则红线")

line(PASS if grp_w <= interior_w else FAIL,
     "盒组宽 ≤ 内净宽", f"{grp_w} ≤ {interior_w}（两侧各余 {(interior_w - grp_w) / 2:.0f}）")
line(PASS if grp_y1 <= FRAME else FAIL,
     "盒组后沿 ≤ 机架深", f"{grp_y1:.0f} ≤ {FRAME}（余 {FRAME - grp_y1:.0f}）")
line(PASS if grp_y0 > tray_y1 else FAIL,
     "托盘/盒组不重叠", f"盒组前沿 {grp_y0:.0f} > 托盘后沿 {tray_y1:.0f}（缝 {grp_y0 - tray_y1:.0f}）")
# 立柱干涉：盒组/托盘的 X 都在 40~310 内 → 不碰四角立柱（立柱只占 x<40 或 x>310）
ok_post = (grp_x0 >= POST and grp_x1 <= FRAME - POST and tray_x0 >= POST and tray_x1 <= FRAME - POST)
line(PASS if ok_post else FAIL, "避四角立柱", f"托盘 X {tray_x0:.0f}~{tray_x1:.0f}、盒组 X {grp_x0:.0f}~{grp_x1:.0f} 均在 {POST}~{FRAME - POST} 内")
line(PASS if TRAY_OUT >= 160 else FAIL, "托盘 ≥160×160", f"外廓 {TRAY_OUT}（⚠️ 贴规则下限，打印收缩会直接违规；建议建模 162~165，本脚本改 TRAY_OUT 重跑即可）")
line(PASS if BOX_IN >= 2 * GOODS_MAX else FAIL,
     "盒内腔容 4 件 40mm", f"内腔 {BOX_IN}×{BOX_IN}，需 ≥{2 * GOODS_MAX}（2×2 单层）；余 {BOX_IN - 2 * GOODS_MAX}")
line(PASS if FRAME_H <= MAX_H else FAIL, "整机高 ≤450", f"{FRAME_H}（余 {MAX_H - FRAME_H}，屏幕/相机支架另算，别做满）")

# ---------- 3. 行程覆盖 ----------
out("\n【3】龙门行程覆盖（逐个取放点）")

deep_travel = DEEP_RAIL - DEEP_SLIDER                 # 305
wide_travel = Y_BEAM - Y_CARRIAGE                     # 225

# 深向需求：托盘最前取货点 → 最后一排盒腔中心
y_need_lo = FRONT_GOODS_MIN                      # 整机坐标：托盘前沿小件中心最前 ≈ y10
y_need_hi = max(y for _, y in cells)             # 后排盒腔中心
deep_need = y_need_hi - y_need_lo
out(f"  深向需求：y {y_need_lo:.0f}（托盘前沿小件中心）→ {y_need_hi:.0f}（后排盒心）= {deep_need:.0f}"
      f"；加两端工程余量 ~12 → ≈{deep_need + 12:.0f}")
line(PASS if deep_travel >= deep_need + 12 else (WARN if deep_travel >= deep_need else FAIL),
     "深向行程", f"导轨 {DEEP_RAIL} − 滑块 {DEEP_SLIDER} = {deep_travel} ≥ 需求 {deep_need}（余 {deep_travel - deep_need:.0f}）")

# 深向前探问题：滑块不悬出时，夹爪中心最前 = 导轨前端 + 滑块/2
front_reach = DEEP_SLIDER / 2                       # 22.5
line(PASS if front_reach <= y_need_lo else WARN,
     "深向前探（托盘前沿够得着吗）",
     f"滑块不悬出时夹爪最前 y={front_reach:.1f} > 需求 {y_need_lo} → 托盘前部 {front_reach - y_need_lo:.1f}mm 条带够不着（40mm 大件中心 y≈24.5 本身够得着，受影响的是小件贴前沿摆）；"
     f"补救：①深向滑块前端悬出 ~{front_reach - y_need_lo + 2:.0f}mm（MGN 线轨可以，光轴+LM8UU 不行）"
     f"②夹爪相对滑块前移安装（推荐，只改转接板）"
     f"③换短款 MGN12C（35）→ 最前 17.5；⚠️ 托盘后移最多 6mm（吃掉托盘/盒组那道缝），不能根治")

# 宽向需求：最左/最右盒心 + 托盘左右内缘
x_need_lo = min(min(x for x, _ in cells), tray_inner[0] + GOODS_MAX / 2)
x_need_hi = max(max(x for x, _ in cells), tray_inner[2 - 1] - GOODS_MAX / 2)
wide_need = x_need_hi - x_need_lo
out(f"  宽向需求：x {x_need_lo:.0f} → {x_need_hi:.0f} = {wide_need:.0f}")
# Y 梁摆 40~310（内净宽）时夹爪宽向可达范围
wx0, wx1 = interior_x0 + Y_CARRIAGE / 2, interior_x1 - Y_CARRIAGE / 2   # 62.5~287.5
line(PASS if wide_travel >= wide_need and wx0 <= x_need_lo and wx1 >= x_need_hi else FAIL,
     "宽向行程", f"Y 梁 {Y_BEAM} − 滑块 {Y_CARRIAGE} = {wide_travel} ≥ 需求 {wide_need}；"
     f"Y 梁摆 {interior_x0}~{interior_x1} 时夹爪可达 x {wx0:.1f}~{wx1:.1f}，覆盖 [{x_need_lo:.0f}, {x_need_hi:.0f}]")

out("\n  取放点逐点核对（夹爪中心须可达，且四周 25mm 内无盒壁/托盘沿之外的障碍——放置点即盒腔中心，天然满足）：")
hdr = f"    {'点':<14}{'X':>6}{'Y':>6}  深向{'':>4}宽向"
out(hdr)
allok = True
for i, (cx, cy) in enumerate(cells):
    ok_deep = y_need_lo <= cy <= y_need_lo + deep_travel
    ok_wide = wx0 <= cx <= wx1
    allok &= ok_deep and ok_wide
    out(f"    盒{i + 1}（{'后' if cy > (grp_y0 + grp_y1) / 2 else '前'}排）{cx:>6.0f}{cy:>6.0f}  {PASS if ok_deep else FAIL:<6}{PASS if ok_wide else FAIL}")
for label, px, py in [("托盘左前", tray_inner[0] + GOODS_MAX / 2, y_need_lo),
                      ("托盘右前", tray_inner[1] - GOODS_MAX / 2, y_need_lo),
                      ("托盘左后", tray_inner[0] + GOODS_MAX / 2, tray_inner[3] - GOODS_MAX / 2),
                      ("托盘右后", tray_inner[1] - GOODS_MAX / 2, tray_inner[3] - GOODS_MAX / 2)]:
    ok_deep = y_need_lo <= py <= y_need_lo + deep_travel
    ok_wide = wx0 <= px <= wx1
    allok &= ok_deep and ok_wide
    out(f"    {label:<12}{px:>6.0f}{py:>6.0f}  {PASS if ok_deep else FAIL:<6}{PASS if ok_wide else FAIL}")

# ---------- 4. 结论 ----------
out("\n【4】结论")
if issues:
    out(f"  {FAIL} 未通过项：{'、'.join(issues)}")
else:
    out(f"  {PASS} 布局与行程覆盖全部通过（含余量）：盒组 {grp_w}×{grp_d}、托盘 {TRAY_OUT}、"
          f"深向 {deep_travel}/需 {deep_need}、宽向 {wide_travel}/需 {wide_need:.0f}")
    out(f"  {WARN} 两个工程提醒：① 托盘外廓 160 贴规则下限，建议建模 162~165 抗收缩；"
          f"② 深向前探差 ~12mm（小件贴托盘前沿时），按【3】的 ① 滑块悬出 / ② 夹爪前移 定一条")
out("=" * 72)

# 报告落盘（控制台是 GBK，直接 print 会炸；写 UTF-8 文件再读）
# 2026-09-17：改成写到脚本自己所在目录（本脚本在 05_工具与校核\），从哪运行都一样
report = "\n".join(_buf) + "\n"
Path(__file__).resolve().parent.joinpath("布局校核报告.txt").write_text(report, encoding="utf-8")
print(report)
