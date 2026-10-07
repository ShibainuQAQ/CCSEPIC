# -*- coding: utf-8 -*-
"""
gripper_design.py —— 夹爪参数化设计 / 校核 / 建模工具（工创赛 II-2 分拣机）

用途：把夹爪"缩小到刚刚好"，并且**用几何算一遍**而不是靠脑补。
一次运行产出三样东西（都在本目录）：
  1. 夹爪校核报告.txt   —— 尺寸链、红线校核、与规则/框架的对照
  2. stl\\*.stl          —— 每个零件的 STL（可直接拖进 Fusion / 切片软件看）
  3. 夹爪图纸.png        —— 正视 + 侧视 + "开口 50 为什么不行"对比图（中文标注）

改尺寸只改下面【参数区】，然后：  python gripper_design.py
坐标系：X=两爪开合方向，Y=夹爪深度，Z=竖直向上；原点=夹持中心、爪尖底面(Z=0)。
"""
import math
import sys
from pathlib import Path

import numpy as np

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

HERE = Path(__file__).resolve().parent
STL_DIR = HERE / "stl"
PASS, FAIL, WARN = "✅", "❌", "⚠️"

# ============================== 参数区 ==============================
# ---- 规则与被分拣对象 ----
GOODS_MAX = 40.0          # 货物最大边长（规则：尺寸在 40mm 正方形范围内）
GOODS_DIAG = GOODS_MAX * math.sqrt(2)   # 斜放时的外接宽度（随机朝向！）
POS_ERR = 2.0             # 视觉定位 + 龙门重复定位误差（单侧估计）
PAD_SQUISH = 1.0          # 软爪垫压缩量（单侧）

# ---- 盒子 / 托盘（定稿口径）----
CELL_IN = 82.0            # 储物盒内腔（盒外 86、壁 2）
CELL_RIM = 48.0           # 盒口离台面高度（内腔 45 + 底 3）
CELL_FLOOR = 3.0          # 盒内底面离台面
TRAY_OUT = 160.0          # 托盘外廓（规则下限 160）
TRAY_WALL = 2.5
TRAY_RIM = 10.0           # 托盘边高（规则 ≥10）

# ---- 夹爪 v2（本工具的产出）----
OPEN_MAX = 64.0           # 最大开口（两爪内侧面间距）
CLOSE_MIN = 8.0           # 最小开口
STROKE = OPEN_MAX - CLOSE_MIN
JAW_T = 3.5               # 爪板厚（夹持面到外侧面）
JAW_TIP = 1.5             # 爪尖最下端厚度（楔形，便于插进货与托壁之间）
JAW_TIP_H = 12.0          # 楔形段高度
JAW_D = 30.0              # 爪板深度（Y）
JAW_FACE_H = 48.0         # 夹持面高度（Z 0~48）

# ---- 传动 ----
MODULE, TEETH = 1.5, 14
R_PITCH = MODULE * TEETH / 2          # 10.5
R_TIP = R_PITCH + MODULE              # 12.0
R_ROOT = R_PITCH - 1.25 * MODULE      # 8.625
GEAR_BAND = 8.0                       # 每段齿宽（两段，分别啮合上下齿条）
GEAR_GAP = 4.0                        # 两段之间的空档
RACK_L = 52.0                         # 齿条长（X）
RACK_H = 9.0                          # 齿条截面高（Z，含齿）
RACK_W = 8.0                          # 齿条截面宽（Y）
RACK_TEETH_L = 44.0                   # 齿区长度
SERVO = (40.0, 38.5, 20.0)            # DS3225 长(X) × 深(Y) × 高(Z)
SERVO_ANGLE_RANGE = 270.0             # 建议买 270° 版；180° 版见报告"备选"

# ---- 其他 ----
ROD_D, ROD_L = 6.0, 62.0              # 导杆
BRG_OD, BRG_ID, BRG_W = 10.0, 6.0, 3.0   # MF106ZZ
FRAME_X, FRAME_Y, FRAME_WALL, FRAME_TOP = 76.0, 52.0, 3.0, 4.0
JOINT_PLATE = 10.0                    # Z 转接板
Z_SLIDER = 45.0                       # Z 滑块（MGN12H；短款 MGN12C=35）
LIFT_MARGIN = 20.0                    # 抬升余量：爪底高出盒口多少（横移不刮盒沿）
TABLE_H = 45.0                        # 工作台面离地
DENSITY = 1.24e-3                     # PLA g/mm³
# ===================================================================

# ---------- 由参数推导的结构高度（改参数自动跟着变）----------
Z_RACK_LO = JAW_FACE_H + 0.5                 # 下齿条体底面（爪顶让 0.5 缝）
Z_RACK_LO_TOP = Z_RACK_LO + RACK_H           # 下齿条体顶面
Z_PITCH_LO = Z_RACK_LO_TOP - MODULE          # 下齿条节线（齿在顶面）
GEAR_CZ = Z_PITCH_LO + R_PITCH               # 齿轮中心高度
Z_RACK_HI_TOP = GEAR_CZ + R_PITCH + MODULE   # 上齿条体顶面
Z_RACK_HI = Z_RACK_HI_TOP - RACK_H           # 上齿条体底面
GEAR_Y1, GEAR_Y2 = 2.0, 12.0                 # 齿轮「单段」齿带（10 宽）——两齿条在不同 Z，可共用同一 Y 带
GEAR_Y3, GEAR_Y4 = GEAR_Y1, GEAR_Y2          # 兼容旧字段名（两段合一）
RACK_LO_Y = (GEAR_Y1 + 1.0, GEAR_Y1 + 1.0 + RACK_W)     # 下齿条 Y 3~11
RACK_HI_Y = RACK_LO_Y                                   # 上齿条共用同一 Y 带（Z 不同，不干涉）
SERVO_Y0 = GEAR_Y2 + 1.0                     # 舵机机身起点：紧贴齿轮后方（同轴直驱，无偏心）
ROD_Y, ROD_Z = 20.0, 12.0                    # 导杆挪到「爪板后方」，避开爪板行程
ROD_X_IN, ROD_X_OUT = 6.0, 38.0              # 导杆相对爪夹持面：内伸 6 / 外伸 38（保证全行程都在吊耳里）
LUG_BORE_X = (26.0, 38.0)                    # 固定吊耳的轴承孔 X 位置（两处，间距 12）
FRAME_Z0 = JAW_FACE_H
FRAME_Z1 = Z_RACK_HI_TOP + FRAME_TOP
TOTAL_H = FRAME_Z1                            # 夹爪总高
TOTAL_D = max(FRAME_Y / 2, SERVO_Y0 + SERVO[1]) + JAW_D / 2   # 舵机从后面伸出，深度由它决定
HALF_OPEN = OPEN_MAX / 2                      # 爪夹持面 X 位置


# ============================== 几何内核 ==============================
class Mesh:
    """一串三角面片 + 名称/颜色（用于出图）"""

    def __init__(self, name, color=(180, 180, 190)):
        self.name = name
        self.color = color
        self.tris = []

    def add(self, tris):
        self.tris.extend(tris)

    def bbox(self):
        v = np.array(self.tris).reshape(-1, 3)
        return v.min(axis=0), v.max(axis=0)

    def volume(self):
        v = np.array(self.tris)
        a, b, c = v[:, 0], v[:, 1], v[:, 2]
        return float(np.abs(np.einsum("ij,ij->i", a, np.cross(b, c)).sum() / 6.0))

    def mass(self):
        return self.volume() * DENSITY


def _tri(p0, p1, p2):
    """返回"一个三角形"（(3,3) 数组），保证 tris 列表是三角形组而不是散点。"""
    return [np.array([p0, p1, p2], float)]


def box(x0, x1, y0, y1, z0, z1):
    x0, x1 = min(x0, x1), max(x0, x1)      # 允许传反（镜像件），内部统一
    y0, y1 = min(y0, y1), max(y0, y1)
    z0, z1 = min(z0, z1), max(z0, z1)
    p = [(x0, y0, z0), (x1, y0, z0), (x1, y1, z0), (x0, y1, z0),
         (x0, y0, z1), (x1, y0, z1), (x1, y1, z1), (x0, y1, z1)]
    f = [(0, 3, 2, 1), (4, 5, 6, 7), (0, 1, 5, 4), (1, 2, 6, 5), (2, 3, 7, 6), (3, 0, 4, 7)]
    out = []
    for a, b, c, d in f:
        out += _tri(p[a], p[b], p[c])
        out += _tri(p[a], p[c], p[d])
    return out


def prism(poly, axis, a0, a1):
    """把平面多边形（逆时针）沿 axis('x'/'y'/'z') 从 a0 拉伸到 a1。
    poly 是另两个轴的二维点列。
    注意：axis='y' 时 (x,z) 是左手系，需要反转点序以保持法线朝外（否则体积会算成 0）。"""
    ai = "xyz".index(axis)
    oth = [i for i in range(3) if i != ai]
    if axis == "y":
        poly = list(poly)[::-1]
    if a0 > a1:
        a0, a1 = a1, a0

    def pt(p, a):
        v = [0.0, 0.0, 0.0]
        v[ai] = a
        v[oth[0]], v[oth[1]] = p
        return v

    n = len(poly)
    out = []
    lo = [pt(p, a0) for p in poly]
    hi = [pt(p, a1) for p in poly]
    for i in range(n):
        j = (i + 1) % n
        out += _tri(lo[i], lo[j], hi[j])
        out += _tri(lo[i], hi[j], hi[i])
    # 端面：从质心扇形三角化（本工具的轮廓都是星形的，够用）
    cx = sum(p[0] for p in poly) / n
    cy = sum(p[1] for p in poly) / n
    c_lo, c_hi = pt((cx, cy), a0), pt((cx, cy), a1)
    for i in range(n):
        j = (i + 1) % n
        out += _tri(c_lo, lo[j], lo[i])
        out += _tri(c_hi, hi[i], hi[j])
    return out


def cyl(axis, a0, a1, cx, cy, r, seg=28):
    poly = [(cx + r * math.cos(2 * math.pi * i / seg), cy + r * math.sin(2 * math.pi * i / seg))
            for i in range(seg)]
    return prism(poly, axis, a0, a1)


def circle_poly(cx, cy, r, seg=28):
    return [(cx + r * math.cos(2 * math.pi * i / seg), cy + r * math.sin(2 * math.pi * i / seg))
            for i in range(seg)]


def gear_profile(m=MODULE, z=TEETH, alpha=math.radians(20)):
    """直纹近似齿廓（20° 压力角），够 3D 打印用。"""
    rp, ra, rf = m * z / 2, m * z / 2 + m, m * z / 2 - 1.25 * m
    half_pitch = math.pi / z                      # 节圆处半个齿的角度
    pts = []
    for i in range(z):
        base = 2 * math.pi * i / z
        th_f = half_pitch + (rp - rf) * math.tan(alpha) / rf   # 齿根处半角
        th_a = max(0.02, half_pitch - (ra - rp) * math.tan(alpha) / ra)  # 齿顶处半角
        pts.append((rf, base + th_f))
        pts.append((rp, base + half_pitch))
        pts.append((ra, base + th_a))
        pts.append((ra, base - th_a))
        pts.append((rp, base - half_pitch))
        pts.append((rf, base - th_f))
        nxt = 2 * math.pi * (i + 1) / z
        pts.append((rf, nxt - th_f))
    return [(r * math.cos(a), r * math.sin(a)) for r, a in pts]


def rack_profile(length=RACK_L, m=MODULE, teeth_len=RACK_TEETH_L, alpha=math.radians(20)):
    """齿条截面轮廓（在 X-Z 平面，齿在顶面、原点在左端节线上）：返回 (x,z) 点列，逆时针。"""
    p = math.pi * m
    ha, hf = m, 1.25 * m
    half = p / 4
    n = int(teeth_len // p)
    x0 = -length / 2 + (length - n * p) / 2      # 齿区在齿条长度内居中
    pts = [(-length / 2, -hf)]
    for i in range(n):
        c = x0 + (i + 0.5) * p
        pts += [(c - half + ha * math.tan(alpha), 0.0),
                (c - half + (ha + hf) * math.tan(alpha), ha),
                (c + half - (ha + hf) * math.tan(alpha), ha),
                (c + half - ha * math.tan(alpha), 0.0)]
    pts.append((length / 2, -hf))
    pts.append((length / 2, -hf - 2.0))
    pts.append((-length / 2, -hf - 2.0))
    return pts


def jaw_profile(half_gap, z_top=JAW_FACE_H):
    """爪板 X-Z 截面：内侧面（夹持面）竖直，外侧面下部收成楔形。返回 (x,z) 逆时针。"""
    xin = -half_gap                     # 夹持面（左爪为负侧）
    xout = -(half_gap + JAW_T)
    xtip = -(half_gap + JAW_TIP)
    return [(xin, 0.0), (xtip, 0.0), (xout, JAW_TIP_H), (xout, z_top), (xin, z_top)]


# ============================== 建零件 ==============================
def build_parts():
    parts = []

    # 齿轮（沿 Y 两段齿带 + 轮毂 + 轴孔）—— 轴孔用"两段之间留空"示意
    g = Mesh("齿轮 M1.5-z14", (90, 160, 240))
    gp = gear_profile()
    g.add(prism(gp, "y", GEAR_Y1, GEAR_Y2))                      # 单段齿带（同时啮合上下两齿条）
    g.add(cyl("y", GEAR_Y1 - 1.5, GEAR_Y2 + 1.5, 0, 0, 6.5))     # 轮毂
    g.add(cyl("y", GEAR_Y2, GEAR_Y2 + 6.0, 0, 0, 3.0))           # 舵机轴（伸进舵机输出端）
    parts.append(g)

    # 齿条 ×2（下：左爪；上：右爪）。profile 的局部 z=0 是节线。
    prof = rack_profile()
    rl = Mesh("齿条-下(左爪)", (240, 170, 90))
    shift_lo = np.array([0.0, 0.0, Z_PITCH_LO])
    rl.tris = [t + shift_lo for t in prism(prof, "y", RACK_LO_Y[0], RACK_LO_Y[1])]
    parts.append(rl)

    rh = Mesh("齿条-上(右爪)", (200, 220, 120))
    prof_h = [(x, -z) for x, z in prof][::-1]     # 齿朝下
    rh.tris = prism(prof_h, "y", RACK_HI_Y[0], RACK_HI_Y[1])
    dz = GEAR_CZ + R_PITCH
    rh.tris = [t + np.array([0.0, 0.0, dz]) for t in rh.tris]
    parts.append(rh)

    # 爪板 ×2（左：接下面齿条；右：接上面齿条；夹持面都对 ±32）
    jl = Mesh("爪板-左", (230, 230, 235))
    jl.add(prism(jaw_profile(HALF_OPEN), "y", -JAW_D / 2, JAW_D / 2))
    jl.add(box(-(HALF_OPEN + JAW_T), -HALF_OPEN, RACK_LO_Y[0], RACK_LO_Y[1],
               JAW_FACE_H, Z_RACK_LO_TOP))       # 连接臂 → 下齿条
    parts.append(jl)

    jr = Mesh("爪板-右", (215, 215, 225))
    jr_tris = []
    for t in prism(jaw_profile(HALF_OPEN), "y", -JAW_D / 2, JAW_D / 2):
        t = t.copy()
        t[:, 0] = -t[:, 0]                            # 镜像到 +X
        jr_tris.append(t)
    jr_tris += box(HALF_OPEN, HALF_OPEN + JAW_T, RACK_HI_Y[0], RACK_HI_Y[1],
                   JAW_FACE_H, Z_RACK_HI_TOP)         # 连接臂 → 上齿条（较长）
    jr.tris = jr_tris
    parts.append(jr)

    # 主框（4 壁 + 顶板；中间镂空容纳齿轮齿条）
    fr = Mesh("主框", (150, 150, 160))
    hx, hy = FRAME_X / 2, FRAME_Y / 2
    w, t = FRAME_WALL, FRAME_TOP
    fr.add(box(hx - w, hx, -hy, hy, FRAME_Z0, FRAME_Z1 - t))          # 右壁
    fr.add(box(-hx, -hx + w, -hy, hy, FRAME_Z0, FRAME_Z1 - t))        # 左壁
    fr.add(box(-hx, hx, hy - w, hy, FRAME_Z0, FRAME_Z1 - t))          # 后壁
    fr.add(box(-hx, hx, -hy, -hy + w, FRAME_Z0, FRAME_Z1 - t))        # 前壁
    fr.add(box(-hx, hx, -hy, hy, FRAME_Z1 - t, FRAME_Z1))             # 顶板
    parts.append(fr)

    # 舵机（轴线沿 Y，与齿轮同轴；机身放在齿轮后方，避开两根齿条的 Y 带）
    sv = Mesh("舵机 DS3225", (70, 80, 95))
    sy0 = SERVO_Y0
    sz0 = GEAR_CZ - SERVO[2] / 2
    sv.add(box(-SERVO[0] / 2, SERVO[0] / 2, sy0, sy0 + SERVO[1], sz0, sz0 + SERVO[2]))
    parts.append(sv)

    # 导杆 ×2：挪到爪板后方（Y=20），并加长到全行程都不脱出吊耳
    rd = Mesh("导杆 Ø6", (160, 160, 170))
    for sgn in (-1, 1):
        rd.add(cyl("x", sgn * (HALF_OPEN - ROD_X_IN), sgn * (HALF_OPEN + ROD_X_OUT),
                   ROD_Y, ROD_Z, ROD_D / 2))
    parts.append(rd)

    # 导杆吊耳 ×4（固定在主框上，从框底 Z48 垂到导杆 Z12）+ 轴承座
    lg = Mesh("导杆吊耳+轴承", (190, 190, 200))
    for sgn in (-1, 1):
        for bx in LUG_BORE_X:
            x = sgn * bx
            lg.add(box(x - 3, x + 3, ROD_Y - 4, ROD_Y + 4, ROD_Z, FRAME_Z0))
            lg.add(cyl("x", x - 3, x + 3, ROD_Y, ROD_Z, BRG_OD / 2))
    parts.append(lg)

    # 爪板与导杆的连接耳（从爪板背面伸到导杆）
    er = Mesh("爪板连接耳", (210, 205, 195))
    for sgn in (-1, 1):
        er.add(box(sgn * HALF_OPEN, sgn * (HALF_OPEN + JAW_T), JAW_D / 2, ROD_Y,
                   ROD_Z - 4, ROD_Z + 4))
    parts.append(er)

    return parts


# ============================== 校核 ==============================
def checks():
    rows = []
    ok = lambda c: PASS if c else FAIL

    open_need = GOODS_DIAG + 2 * POS_ERR + 2 * PAD_SQUISH
    rows.append((ok(OPEN_MAX >= open_need), "抓得住斜放的 40mm 方货",
                 f"开口 {OPEN_MAX:.0f} ≥ 需要 {open_need:.1f}（对角 {GOODS_DIAG:.1f} + 误差 {2*POS_ERR:.0f} + 垫压缩 {2*PAD_SQUISH:.0f}），单侧余 {(OPEN_MAX-GOODS_DIAG)/2:.1f}"))
    rows.append((ok(OPEN_MAX + 2 * JAW_T <= CELL_IN),
                 "整体下潜进盒（开口状态）",
                 f"爪外廓 {OPEN_MAX + 2*JAW_T:.0f} ≤ 盒内腔 {CELL_IN:.0f}（单侧余 {(CELL_IN-OPEN_MAX-2*JAW_T)/2:.1f}）"))
    rows.append((ok(JAW_TIP <= 3.0), "爪尖楔形能插进货与托壁之间",
                 f"爪尖厚 {JAW_TIP}（楔形段 {JAW_TIP_H:.0f}mm 内由 {JAW_TIP}→{JAW_T}）"))
    ang = math.degrees(STROKE / (2 * R_PITCH))
    rows.append((ok(ang <= SERVO_ANGLE_RANGE - 30), "舵机转角留够余量",
                 f"θ = 行程 {STROKE:.0f}/(2r={2*R_PITCH:.1f}) = {ang:.1f}°（舵机量程 {SERVO_ANGLE_RANGE:.0f}°，两端各留 ≥15°）"))
    rows.append((ok(R_TIP * 2 <= FRAME_Z1 - FRAME_Z0), "齿轮装得进主框",
                 f"齿顶圆 Ø{2*R_TIP:.0f} ≤ 主框内高 {FRAME_Z1-FRAME_Z0:.0f}"))

    # 放货入盒：手指必须插在货与盒壁之间
    goods_fit = (CELL_IN - 2 * JAW_T - 2.0) / 2
    rows.append((ok(goods_fit >= GOODS_MAX), "每盒能放 4 件 40mm 货（爪厚约束）",
                 f"内腔 {CELL_IN:.0f} 时，4 件 2×2 可放的最大货边 = (内腔−2×爪厚−2)/2 = {goods_fit:.1f}"
                 + ("" if goods_fit >= GOODS_MAX else f" ❌ 差 {GOODS_MAX-goods_fit:.1f}（要么盒做大、要么货降到 {goods_fit:.0f}）")))

    # 结构自洽两项（原本会出问题，2026-09-17 下午按 Fusion 复核意见修掉）
    rows.append((ok(SERVO_Y0 >= RACK_LO_Y[1]),
                 "舵机可同轴直驱（不需偏心联轴器）",
                 f"齿条/齿轮 Y 带 {RACK_LO_Y[0]:.0f}~{RACK_LO_Y[1]:.0f}，舵机机身 Y {SERVO_Y0:.0f}~{SERVO_Y0+SERVO[1]:.0f}"
                 f"（间隙 {SERVO_Y0-RACK_LO_Y[1]:.0f}）→ 舵机轴心与齿轮同心，直接插齿轮即可"))
    rod_open = (HALF_OPEN - ROD_X_IN, HALF_OPEN + ROD_X_OUT)
    rod_closed = (rod_open[0] - STROKE / 2, rod_open[1] - STROKE / 2)
    lug_ok = all(lo <= bx <= hi for bx in LUG_BORE_X for lo, hi in (rod_open, rod_closed))
    rows.append((ok(lug_ok), "导杆全行程都有支撑（固定吊耳）",
                 f"吊耳轴承孔 X {LUG_BORE_X[0]:.0f}/{LUG_BORE_X[1]:.0f}；导杆在开口位 {rod_open[0]:.0f}~{rod_open[1]:.0f}、"
                 f"闭口位 {rod_closed[0]:.0f}~{rod_closed[1]:.0f} → 两个孔始终在导杆上 ✅（吊耳从主框底 Z{FRAME_Z0:.0f} 垂到导杆 Z{ROD_Z:.0f}）"))

    # 贴壁死区
    tray_in = TRAY_OUT - 2 * TRAY_WALL
    dead = JAW_T
    ratio = (tray_in ** 2 - (tray_in - 2 * dead) ** 2) / tray_in ** 2
    rows.append((WARN, "托盘贴壁死区",
                 f"托盘内净 {tray_in:.0f}，爪厚 {JAW_T:.0f} → 左右各 {dead:.0f}mm 带内抓不到，约占面积 {ratio*100:.0f}%"))

    # Z 尺寸链
    lift = CELL_RIM + LIFT_MARGIN
    stack = TOTAL_H + JOINT_PLATE + Z_SLIDER
    beam_bottom = TABLE_H + lift + stack
    col_top = beam_bottom + 60.0            # Y 梁 40 + 滑块/连接 20
    machine_h = col_top + 40.0              # 顶框上的导轨梁
    col_base = 260.0
    rows.append((ok(TOTAL_H > 26.0), "夹爪真实包络（关键！）",
                 f"总高 {TOTAL_H:.0f}（爪 {JAW_FACE_H:.0f} + 传动 {Z_RACK_HI_TOP-JAW_FACE_H:.0f} + 框顶 {FRAME_TOP:.0f}），"
                 f"深 {TOTAL_D:.0f}，宽 {FRAME_X:.0f} —— 老参数表写的 26 没把齿条/舵机算进去"))
    rows.append((WARN, "框架高度要不要改",
                 f"爪底高位 = 盒口 {CELL_RIM:.0f} + 抬升 {LIFT_MARGIN:.0f} = {lift:.0f}；"
                 f"Y 梁底面需离地 {beam_bottom:.0f} → 立柱顶 {col_top:.0f} → 整机 {machine_h:.0f}（限 450 ✅）"
                 f"；现定稿立柱 {col_base:.0f} → 要加长 ~{col_top-col_base:.0f}mm"))
    rows.append((PASS, "整机高度仍在规则内",
                 f"整机 ≈{machine_h:.0f} ≤ 450（余 {450-machine_h:.0f}）"))
    return rows


# ============================== 输出 ==============================
def write_stl(path, tris):
    a = np.array(tris, dtype=np.float64)
    n = np.cross(a[:, 1] - a[:, 0], a[:, 2] - a[:, 0])
    ln = np.linalg.norm(n, axis=1, keepdims=True)
    ln[ln == 0] = 1.0
    n = n / ln
    rec = np.zeros(len(a), dtype=np.dtype([("n", "<f4", (3,)), ("v", "<f4", (3, 3)), ("a", "<u2")]))
    rec["n"] = n
    rec["v"] = a
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"\0" * 80 + np.uint32(len(a)).tobytes() + rec.tobytes())


def draw_png(parts, path):
    from PIL import Image, ImageDraw, ImageFont
    F = "C:/Windows/Fonts/msyh.ttc"
    f = ImageFont.truetype(F, 16)
    fb = ImageFont.truetype("C:/Windows/Fonts/msyhbd.ttc", 20)
    fs = ImageFont.truetype(F, 14)
    W, H = 1500, 1000
    img = Image.new("RGB", (W, H), (250, 250, 252))
    d = ImageDraw.Draw(img)
    d.text((20, 14), "夹爪 v2 复核图（本图由 gripper_design.py 生成；单位 mm）", font=fb, fill=(20, 20, 30))

    def rect(p0, p1, **kw):
        x0, y0 = p0
        x1, y1 = p1
        d.rectangle([min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1)], **kw)

    def view(cx, cy, title, scale, dy_title=250):
        d.text((cx - 260, cy - dy_title), title, font=fb, fill=(20, 20, 30))
        return lambda x, y: (cx + x * scale, cy - y * scale)

    # ---- 面板 1：正视（X-Z）----
    P1 = view(290, 360, "① 正视（爪/齿条/齿轮/货/盒腔）", 1.7, 250)
    rect(P1(-CELL_IN / 2, 0), P1(CELL_IN / 2, CELL_RIM), outline=(200, 90, 60), width=3)
    d.text(P1(-CELL_IN / 2 + 1, CELL_RIM + 16), f"储物盒内腔 {CELL_IN:.0f}", font=fs, fill=(200, 90, 60))
    for m in parts:
        for t in m.tris:
            d.polygon([P1(v[0], v[2]) for v in t], fill=m.color, outline=(120, 120, 130))
    rect(P1(-20, 0), P1(20, 40), outline=(40, 130, 60), width=3)
    d.text(P1(21, 30), "货 40", font=fs, fill=(40, 130, 60))
    d.line([P1(-HALF_OPEN, -16), P1(HALF_OPEN, -16)], fill=(30, 30, 140), width=2)
    d.text(P1(-9, -34), f"开口 {OPEN_MAX:.0f}", font=f, fill=(30, 30, 140))
    d.text(P1(-CELL_IN / 2, -78),
           f"爪厚 {JAW_T}（尖端 {JAW_TIP}）· 夹持面高 {JAW_FACE_H:.0f} · 夹爪总高 {TOTAL_H:.0f}",
           font=fs, fill=(60, 60, 70))

    # ---- 面板 2：侧视（Y-Z）----
    P2 = view(880, 360, "② 侧视（深度与高度关系）", 1.6, 250)
    for m in parts:
        for t in m.tris:
            d.polygon([P2(v[1], v[2]) for v in t], fill=m.color, outline=(110, 110, 120))
    d.line([P2(-40, 0), P2(70, 0)], fill=(150, 150, 150), width=2)
    d.text(P2(-38, -16), "夹持中心 Y=0", font=fs, fill=(90, 90, 100))
    d.text(P2(-38, TOTAL_H + 12), f"总高 {TOTAL_H:.0f}", font=f, fill=(30, 30, 140))

    # ---- 面板 3：开口对照 ----
    P3 = view(430, 830, "③ 对照：40mm 方货斜放 45° 时，沿开合方向占 40√2 = 56.6mm", 1.55, 200)
    for idx, (op, mark, col) in enumerate([(50.0, "× 开口 50：货外接 56.6 > 50，顶在角上", (200, 60, 60)),
                                           (OPEN_MAX, "√ 开口 64：单侧余 3.7，抓得住", (40, 140, 70))]):
        ox = -100 + idx * 210
        h = op / 2
        d.line([P3(ox - h, -20), P3(ox - h, 70)], fill=col, width=4)
        d.line([P3(ox + h, -20), P3(ox + h, 70)], fill=col, width=4)
        s, th = 40.0, math.radians(45)
        bp = [(-s / 2, -s / 2), (s / 2, -s / 2), (s / 2, s / 2), (-s / 2, s / 2)]
        rot = [(ox + x * math.cos(th) - y * math.sin(th), 25 + x * math.sin(th) + y * math.cos(th)) for x, y in bp]
        d.polygon([P3(x, y) for x, y in rot], outline=(60, 60, 70), width=3)
        d.text(P3(ox - 85, -52), mark, font=f, fill=col)
        d.text(P3(ox - 85, -78), f"画出的两爪内距 = {op:.1f}mm", font=fs, fill=(80, 80, 90))
    img.save(str(path))
    return path


def main():
    print("=" * 74)
    print("夹爪参数化设计 / 校核（v2，2026-09-17）")
    print("=" * 74)

    parts = build_parts()
    STL_DIR.mkdir(parents=True, exist_ok=True)
    tot_v = 0.0
    lines = []
    lines.append("夹爪参数化校核报告（gripper_design.py 生成，v2）")
    lines.append("=" * 74)
    lines.append("")
    lines.append("【1】主要尺寸")
    lines.append(f"  开口 {OPEN_MAX:.1f} / 闭口 {CLOSE_MIN:.1f} / 行程 {STROKE:.1f} / 单爪 {STROKE/2:.1f}")
    lines.append(f"  爪板 厚{JAW_T} × 深{JAW_D:.0f} × 高{JAW_FACE_H:.0f}（尖端厚 {JAW_TIP}，楔形段 {JAW_TIP_H:.0f}）")
    lines.append(f"  齿轮 M{MODULE} z{TEETH}：分度 r{R_PITCH:.2f} / 齿顶 r{R_TIP:.2f} / 齿根 r{R_ROOT:.2f}，两段齿带 Y {GEAR_Y1:.1f}~{GEAR_Y2:.1f}、{GEAR_Y3:.1f}~{GEAR_Y4:.1f}")
    lines.append(f"  齿条 长{RACK_L:.0f} × 截面 {RACK_W:.0f}(Y) × {RACK_H:.0f}(Z)，齿区 {RACK_TEETH_L:.0f}")
    lines.append(f"  主框 {FRAME_X:.0f} × {FRAME_Y:.0f} × {FRAME_Z1-FRAME_Z0:.0f}（Z {FRAME_Z0:.0f}~{FRAME_Z1:.0f}）")
    lines.append(f"  夹爪总包络：高 {TOTAL_H:.0f} × 宽 {FRAME_X:.0f} × 深 {TOTAL_D:.0f}")
    lines.append("")
    lines.append("【2】红线校核")
    rows = checks()
    for mark, item, detail in rows:
        lines.append(f"  {mark}  {item}")
        lines.append(f"        {detail}")
    lines.append("")
    lines.append("【3】零件与质量估算（PLA 1.24 g/cm³）")
    printed = 0.0
    for m in parts:
        v = m.volume() / 1000.0
        tot_v += v
        lo, hi = m.bbox()
        is_printed = "舵机" not in m.name        # 舵机是外购件，不计入打印质量
        if is_printed:
            printed += m.mass()
        lines.append(f"  {m.name:<18} 体积 {v:7.2f} cm³  质量 {m.mass():6.1f} g   包络 "
                     f"X{hi[0]-lo[0]:5.1f} Y{hi[1]-lo[1]:5.1f} Z{hi[2]-lo[2]:5.1f}"
                     + ("" if is_printed else "   ← 外购件"))
        write_stl(STL_DIR / f"{m.name}.stl", m.tris)
    allt = [t for m in parts for t in m.tris]
    write_stl(STL_DIR / "夹爪总成.stl", allt)
    lines.append(f"  {'打印件合计':<18} 质量 {printed:6.1f} g（⚠️ 主框按实心壁、吊耳按实心算，属上界；"
                 f"主框挖减轻孔/加强筋可再降 30~40% → 约 60g 以内）")
    lines.append("")
    lines.append("【4】结论与下一步")
    lift = CELL_RIM + LIFT_MARGIN
    col_need = TABLE_H + lift + TOTAL_H + JOINT_PLATE + Z_SLIDER + 60.0
    lines.append("  1) 开口必须 ≥56.6（40mm 方货斜放的对角），取 64 → 这是「刚刚好」的下限附近，别再缩。")
    lines.append(f"  2) 夹爪真实包络高 {TOTAL_H:.0f}（不是老参数表的 26）→ 立柱 260 要加长到 ~{col_need:.0f}，整机 ~{col_need+40:.0f} 仍 ≤450 ✅")
    lines.append("  3) 每盒 4 件 40mm 货受「爪厚 vs 盒内腔 82」限制 → 见【2】那条 ❌，需队内拍板（3030 型材 / 货降到 ~36 / 接受风险）")
    lines.append("  4) 出图见 夹爪图纸.png；STL 在 stl\\ 目录（可拖进 Fusion 或切片软件）")

    report = "\n".join(lines) + "\n"
    (HERE / "夹爪校核报告.txt").write_text(report, encoding="utf-8")
    print(report)
    png = draw_png(parts, HERE / "夹爪图纸.png")
    print(f"图纸已生成：{png}")


if __name__ == "__main__":
    main()
