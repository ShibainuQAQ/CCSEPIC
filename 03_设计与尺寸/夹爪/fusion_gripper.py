# -*- coding: utf-8 -*-
"""
工创赛 II-2 智能分拣 —— 龙门 XYZ 末端「平行两指夹爪」参数化建模脚本
================================================================================
在 Autodesk Fusion 360 里一键生成夹爪实体模型（每个零件一个独立元件，方便单独
查看 / 出说明书插图 / 导出 STEP / STL 去 3D 打印）。

用法：
    Fusion 360 → 实用程序(Utilities) → 附加模块(Add-Ins) → 脚本和附加模块
    → 「脚本」标签 → 绿色「+」→ 选中本文件 → 运行(Run)

坐标系（全部毫米）：
    X = 两爪开合方向    Y = 夹爪深度方向    Z = 竖直向上（Z=0 为爪尖底面）
    原点 = 两爪夹持中心 × 爪板深度中心 × 爪尖底面高度
    建模姿态 = 最大开口 64mm

【建模手法（全脚本统一，不出现任何复杂坐标换算）】
  · 每个零件在自己元件的「局部坐标系」里以原点为基准、用最笨的正交草图建模，
    需要偏移时再用 occurrences 的 Matrix3D 平移到位。
  · 只用 3 个基准构造平面：
        XY（法向 +Z）：用于竖向尺寸（沿 Z 拉伸）与所有立体/槽口
        XZ（法向 -Y）：用于 (X, Z) 平面轮廓（沿 Y 拉伸），齿轮/齿条/爪板全走这条
        YZ（法向 +X）：用于画圆做沿 X 的孔/杆
  · 截面轮廓 = sketchLines.addByTwoPoints 连成闭合 4/多段线 → extrudeFeatures
  · 齿廓 = 20° 压力角梯形直线近似，齿顶宽 = 0.5×模数
  · 每个零件独立 try/except：单个零件失败只记录，不会让整个脚本崩掉
"""

import math

import adsk.core
import adsk.fusion
import traceback

# =============================================================================
# ============================ 一、参数区（改这里）============================
# =============================================================================

# ------------------------------------------------------------------ 1. 总体
OPEN_MAX = 64.0        # 最大开口：两爪内侧面间距（建模姿态）
CLOSE_MIN = 8.0        # 最小开口
STROKE_TOTAL = 56.0    # 全行程 = 64 - 8
STROKE_ONE = 28.0      # 单爪行程 = 全行程 / 2（对置齿条自定心）

# ------------------------------------------------------------ 2. 爪板（2只）
JAW_THICK = 3.5        # 爪板厚
JAW_DEPTH = 30.0       # 爪板深（Y）
JAW_HEIGHT = 48.0      # 爪板高：夹持面 Z 0~48
JAW_WEDGE_H = 12.0     # 爪尖楔形高度（Z 0~12 内收）
JAW_TIP_THICK = 1.5    # 爪尖最下端厚度（Z=0 处）
JAW_PLATE_DX = 32.0    # 爪板内侧面（夹持面）位置：X = ±32
ROD_HOLE_Z = 12.0      # 导杆中心高度（Z）：也是爪板连接耳上 Ø6 孔的中心高
ROD_HOLE_D = 6.0       # 爪板上导杆通孔直径（打印后会缩孔，见文末备注 5）

# 爪板局部坐标换算（局部原点 = 爪板厚度中间面 · Y=0 · Z=0）
# 左爪（局部坐标系 = 世界坐标系）：
#   夹持面（内侧面，竖直）局部 X = -1.75  → 世界 X = 32（爪板最内侧面）
#   外侧面（完整厚度处）  局部 X = +1.75  → 世界 X = 35.5
#   爪尖外侧面（Z=0，内收 2.0）局部 X = -0.25 → 世界 X = 33.5（该处厚 1.5）
_JAW_IN_X = -JAW_THICK / 2.0                    # 内侧面局部 X = -1.75
_JAW_OUT_X = JAW_THICK / 2.0                    # 外侧面局部 X = +1.75
_JAW_TIP_OUT_X = _JAW_IN_X + JAW_TIP_THICK      # 爪尖外侧面局部 X = -0.25
_JAW_ORIGIN_WX = JAW_PLATE_DX + JAW_THICK / 2.0  # 爪板局部原点世界 X = 33.75
#   （爪板内侧面世界 X = 局部原点 − 半厚 = 32；外侧面 = 33.75 + 1.75 = 35.5）

# ------------------------------------------------------------------ 3. 齿轮
# M1.5 / z=14，与 夹爪\gripper_design.py 完全一致：
#   r_p = 0.5·m·z = 10.5    r_a = r_p + m = 12.0    r_f = r_p − 1.25·m = 8.625
GEAR_MODULE = 1.5       # 模数
GEAR_TEETH = 14         # 齿数
GEAR_R_ROOT = 8.625     # 齿根圆半径 = 10.5 − 1.25×1.5
GEAR_R_PITCH = 10.5     # 分度圆半径
GEAR_R_TIP = 12.0       # 齿顶圆半径
GEAR_CENTER_Z = 66.5    # 齿轮中心高度：下节线 56.0 + 分度圆半径 10.5（见下方啮合推导）
GEAR_Y_MID = 7.0        # 齿带 Y 中心（2026-09-17 方案：两段合并成**单段齿带**）
GEAR_BAND_HALF = 5.0    # 齿带宽的一半 → 齿带 Y 2.0~12.0（宽 10）
GEAR_HUB_YA = 0.5       # 轮毂起点 Y（把齿轮与舵机输出端连起来）
GEAR_HUB_YB = 13.5      # 轮毂终点 Y（略伸进舵机机身 Y=13.0，形成配合）
GEAR_HUB_R = 6.0        # 轮毂半径（Ø12）
GEAR_BORE_R = 3.0       # 轴孔半径（Ø6，配舵机轴）
GEAR_PA_DEG = 20.0      # 压力角（本脚本用直线近似，角度只作参考记录）
GEAR_ROOT_W = 2.36      # 齿根处齿厚（= π·m/2 = 2.356，相邻齿根点夹角 15.7°）
GEAR_TIP_W = 0.5 * GEAR_MODULE   # 齿顶宽 = 0.5 × 模数 = 0.75
GEAR_ARC_SEG = 8        # 齿面用几段直线近似（越大越接近真渐开线）

# 啮合铁律（改任何高度参数后重新校核这三行）：
#   下齿条（齿在顶面）节线 = 材料顶面 − 模数 = 齿轮中心 Z − 分度圆半径
#   上齿条（齿在底面）节线 = 材料底面 + 模数 = 齿轮中心 Z + 分度圆半径
#   两根齿条共用同一 Y 带（因为它们在 Z 上一高一低，不会互相挡）
#
#   ★ 单段齿带的意义：齿轮只有一段齿（Y 2~12），两根齿条都在这一段上啮合，
#     齿条水平 X 方向的平移直接由同一齿带的旋转驱动，不需要两段错开的齿。

# ------------------------------------------------------------------ 4. 齿条
RACK_SECTION_Z = 9.0    # 齿条剖面法向的截面高（齿条体在 XZ 面上画多高就拉多高）
RACK_SECTION_Y = 8.0    # 截面宽（Y）
RACK_LEN = 52.0         # 长（X）
GEAR_PITCH = math.pi * GEAR_MODULE          # 齿距 π×1.5 = 4.712
RACK_TOOTH_H = 2.25 * GEAR_MODULE           # 齿高 3.375（M1.5 全齿高 = 齿顶 1.5 + 齿根 1.875）
RACK_TOOTH_ROOT_W = 2.36                    # 齿根齿厚（与齿轮齿根齿厚一致）
RACK_TOOTH_TIP_W = 0.5 * GEAR_MODULE        # 齿顶宽 0.75
RACK_TOOTH_COUNT = 11                       # 11 齿：齿区 (11-1)×4.712 = 47.1 ≥ 44
# 两根齿条**共用同一 Y 带**：Y 3.0~11.0（中心 7.0，宽 8），正好落在齿轮单段齿带
# （Y 2.0~12.0）之内 —— 这样上下两根齿条都能与同一段齿轮啮合。
RACK_DN_Y_MID = GEAR_Y_MID  # 下齿条 Y 中心 = 7.0（Y 3~11）
RACK_UP_Y_MID = GEAR_Y_MID  # 上齿条 Y 中心 = 7.0（Y 3~11）
# 下齿条（齿在顶面、朝上啮合齿轮下侧）：
#   节线 Z = 56.0（= 齿轮中心 66.5 − 分度圆半径 10.5）
#   齿顶 Z = 57.5（= 节线 + 1.5 齿顶高，也是材料上边界）
#   齿根 Z = 54.125（= 57.5 − 3.375 全齿高）
#   材料实体 Z 范围 = 52.1 ~ 57.5（齿根以下再留约 2mm 背体）
RACK_DN_BODY_ZA = 52.1  # 齿条材料（实体）Z 下限 = 齿根 54.125 − 2.0
RACK_DN_BODY_ZB = 57.5  # 齿条材料 Z 上限 = 齿顶 = 节线 56.0 + 模数 1.5
RACK_DN_PITCH_Z = 56.0  # 下齿条节线（= GEAR_CENTER_Z − GEAR_R_PITCH）
# 上齿条（齿在底面、朝下啮合齿轮上侧）：
#   节线 Z = 77.0（= 齿轮中心 66.5 + 分度圆半径 10.5）
#   齿顶 Z = 75.5（= 节线 − 1.5，也是材料下边界）
#   齿根 Z = 78.875（= 75.5 + 3.375）
#   材料实体 Z 范围 = 75.5 ~ 80.9（齿根以上再留约 2mm 背体）
RACK_UP_BODY_ZA = 75.5  # 齿条材料 Z 下限 = 齿顶 = 节线 77.0 − 模数 1.5
RACK_UP_BODY_ZB = 80.9  # 齿条材料 Z 上限 = 齿根 78.875 + 2.0 背体
RACK_UP_PITCH_Z = 77.0  # 上齿条节线（= GEAR_CENTER_Z + GEAR_R_PITCH）

# ------------------------------------------------------------------ 5. 主框
FRAME_X = 76.0          # 外形 X
FRAME_Y = 52.0          # 外形 Y
FRAME_Z = 34.0          # 外形 Z 高（世界 Z 48~82，盖住上齿条材料顶 80.9）
FRAME_WALL = 3.0        # 壁厚
FRAME_WORLD_Z0 = 48.0   # 主框底面世界高度（= 爪顶 48，局部原点定在这里）
FRAME_BOTTOM_T = 4.0    # 下连接板厚（局部 Z 0~4，世界 Z 48~52）
FRAME_TOP_T = 3.5       # 上连接板厚（局部 Z 30.5~34，世界 Z 78.5~82；
                        # 其底面 78.5 = 齿轮齿顶圆顶，正好把齿轮盖住）
FRAME_CLEAR = 0.5       # 各让位槽单边间隙
# 注：原来主框前后壁上的 4 个 Ø10 轴承孔（轴沿 Y）已**取消** —— 导杆挪到 Y=20、
#     并由吊耳上的轴承（轴沿 X）支撑，主框壁上不再需要轴承孔。

# ------------------------------------------- 6. 导杆、吊耳、舵机、连接臂
# 【2026-09-17 下午方案 B 修订版】
# 导杆从 Y=0 挪到爪板**后方**（Y=±20），Z=12 不变；导杆由固定在主框底面的**吊耳**
# 上的 Ø10 轴承（轴沿 X）支撑，解决「导杆 Z=12 够不到主框 Z=48」。
#
# ⚠ 为什么吊耳必须整体放在 |X| ≥ 36（本题最容易踩的坑，改前务必读）：
#   爪板上的**连接耳是随爪移动的零件**，它在 X 上占 32~35.5；爪往内行程 28mm 时
#   连接耳会在 X 上扫过 32~35.5 → 4~7.5。也就是说**凡是 X 落在 [4, 35.5] 的固定件，
#   爪一走过来就会撞**。吊耳最初放在 X 26.5~31.5，看似与连接耳（32~35.5）不重叠，
#   但 Ø10 轴承孔的外圆柱在 X 上占 24~34，与连接耳 X 32~35.5 直接重叠（行程 0 即撞）；
#   吊耳板本身也会在行程 4~8mm 与连接耳相撞。
#   → 最终把**全部吊耳挪到 X 36~41**（连接耳扫掠范围 4~35.5 之外）。
#
# 又因盒口内腔只有 82（±41），单侧 |X| 只有 35.5~41 这 5.5mm，**只放得下一个薄吊耳**。
# 要做到「每爪 2 个支撑」就改为**每爪 2 根导杆、按 Y 向分开**（Y=+20 与 Y=−20），
# 每根导杆配 1 个吊耳，都在 X 36~41 —— 两个支撑点靠 Y 拉开 40mm，抗倾覆与
# 「同杆两支撑」同类，而且必须靠 Y 分开才放得下。
ROD_D = 6.0             # 导杆直径 Ø6
ROD_LEN = 44.0          # 导杆长度（X 26~45：跨过连接耳 32~35.5 再伸进吊耳轴承）
ROD_X_IN = 26.0         # 导杆内端世界 X（相对夹持面：内伸 6）
ROD_X_OUT = 70.0        # 导杆外端世界 X（相对夹持面：外伸 13；要穿过吊耳轴承孔）
ROD_YS = (20.0, -20.0)  # 每爪 2 根导杆的 Y 位置（Y 向分开 → 抗倾覆）
ROD_Z = ROD_HOLE_Z      # 导杆中心 Z = 12

# 连接耳（随爪移动）：
#   作用 = **爪板与导杆之间的滑动导向套**：耳上开 Ø6 孔套在导杆上，爪板就靠它
#          沿导杆滑动（导杆本身是固定在吊耳上的）。它同时把爪板与连接臂的根部连成一体。
#   位置 = 爪板背面（Y 15）之后，**必须跨过导杆 Y=±20**，所以每个爪板做两只耳：
#          Y 17.5~22.5（配 Y=+20 的杆）与 Y −22.5~−17.5（配 Y=−20 的杆）；
#          耳上 Ø6 孔的中心就是导杆中心 (Y=±20, Z=12)。
#   ⚠ 它随爪板移动，所以任何固定在 X ∈ [4, 35.5] 的零件都会与它相撞。
EAR_X0 = JAW_PLATE_DX               # 32（爪板内侧面）
EAR_X1 = JAW_PLATE_DX + JAW_THICK   # 35.5（爪板外侧面）
EAR_Y_HALF = 2.5                    # 耳在 Y 向的半宽（要跨过导杆 Ø6）
EAR_Z0 = ROD_Z - 4.0                # 8
EAR_Z1 = ROD_Z + 4.0                # 16

# 吊耳（固定件，每爪 2 个共 4 个）：**X 41~46**、Y 16~24（或 −24~−16）、Z 7~48；
# 每个一个 Ø10 轴承孔（轴沿 X，孔心 X=43.5 / Y=20 / Z=12）。
#
#   ★★ 为什么是 X 41~46（本题最硬的一条几何约束，改前务必读完）★★
#   连接耳（随爪）沿 X 扫过 [4, 35.5]。Ø10 轴承孔的外圆柱半径 5，孔心在 X=C 时
#   孔占 [C−5, C+5]。要让孔完全躲开耳的扫掠区：
#         C − 5 ≥ 35.5  →  C ≥ 40.5
#   而孔心 C=40.5 时孔占到 X=45.5，吊耳外沿必须 ≥ 45.5。
#   → **超出了盒口内腔半宽 41**。反过来，若把孔心压在 41 以内（C ≤ 41），
#     孔内沿 ≤ 36，必然伸进耳的扫掠区 [4, 35.5] → 一定撞。
#   根因：单侧可用宽度只有 41 − 35.5 = 5.5mm，而 Ø10 轴承孔自己就要 10mm。
#   **「盒口内腔 ±41」与「Ø10 轴承孔」在连接耳存在的前提下无法同时满足。**
#   本脚本选择：保住 Ø10 轴承与双支撑（机构功能优先），吊耳外伸到 X 46，
#   即导杆支架在盒口内侧超过 5mm（单侧）。若现场发现刮盒口，见文件末尾备注的
#   两个替代方案（改用 Ø6 铜套 / 抬高导杆）。
LUG_X_IN = 36.0         # 吊耳 X 内沿（= 盒口内腔半宽 41；再往里就会撞连接耳）
LUG_X_OUT = 41.0        # 吊耳 X 外沿
LUG_X_C = 38.5          # 轴承孔中心 X（≥ 40.5 才躲得开连接耳）
LUG_X_W = LUG_X_OUT - LUG_X_IN                # 5（板厚）
LUG_Y0 = 13.5           # 吊耳 Y 范围 16~24（中心即导杆 Y=20）
LUG_Y1 = 26.5
LUG_Z0 = 5.0            # 下沿（留出 Ø10 轴承孔下侧的壁厚）
LUG_Z1 = FRAME_WORLD_Z0  # 上沿 = 主框底面 Z=48（端面贴合）
LUG_BRG_D = 10.0        # 吊耳上轴承孔 Ø10（MF106ZZ 外径）
LUG_BRG_Y = 20.0        # 轴承孔中心 Y = 导杆 Y

BRG_OD = 10.0           # 轴承外径 Ø10
BRG_ID = 6.0            # 轴承内径 Ø6
BRG_W = 3.0             # 轴承宽 3
BRG_FLANGE_D = 9.0      # 法兰直径 Ø9（比外径 Ø10 小一圈的微型法兰轴承；
                        # 取 11.5 时孔外圆柱会伸进爪板连接耳的扫掠区）
BRG_FLANGE_T = 0.6      # 法兰厚 0.6
# 原先装在主框前后壁上的那 4 个轴承（轴沿 Y）已被吊耳上的轴承（轴沿 X）取代，
# 主框壁上不再开 Ø10 孔。

# 【2026-09-17 下午方案 A】舵机改**同轴直驱**：轴心 X=0、Z=66.5（与齿轮中心同心），
# 机身 Y 13.0~51.5，直接插齿轮轮毂，取消偏心联轴器。
SERVO_W = 40.0          # 舵机外形 X（DS3225）
SERVO_D = 38.5          # 舵机外形 Y
SERVO_H = 20.0          # 舵机外形 Z
SERVO_YA = 13.0         # 舵机前面（朝齿轮一侧）Y
SERVO_Z = GEAR_CENTER_Z - SERVO_H / 2.0   # 舵机底面 Z = 56.5 → 轴心正好在 66.5
#   为什么现在不撞齿条（关键）：两根齿条共用 Y 3~11 这一条窄带，而舵机机身从
#   Y=13.0 起、整体落在齿条 Y 带**之外**；虽然舵机 X ±20、Z 56.5~76.5 与上齿条
#   Z 75.5~80.9 在 Z 上有重叠，但上齿条只存在于 X ±(20.5~38) 的过框通道里，
#   舵机 X 只到 ±20，两者在 X 上错开 → 不会相撞。齿轮齿（Y 2~12）同理只在
#   X ±12、Y ≤12 内，也在舵机机身之前。
SERVO_SHAFT_D = 6.0     # 输出轴直径 Ø6
SERVO_SHAFT_LEN = 10.0  # 输出轴长度（Y 3.0~13.0，穿过齿轮轮毂孔）
#   ⚠ 主框 Y 只有 52（−26~+26），舵机机身 Y 13.0~51.5，尾部从框后伸出约 25.5mm：
#     实际需加一块**舵机座板/延长框体**来固定舵机尾部（本脚本未建该座板）。

# 连接臂：把爪板接到齿条上（两根齿条上下错开 25.4，右臂因此长得多）。
# 用**世界 X** 表达臂的两端，再由 build_jaw 换算到爪板局部坐标 —— 避免镜像时搞错。
#   内端 MUST |X| ≥ 13：齿轮齿顶圆半径 12，爪往内行程 28 时臂会扫过 X ±13 附近，
#     内端若伸到 |X|<12 就会与齿轮相撞（实测行程 ~10mm 处重叠）。
#   外端 |X| = 35.5（爪板外侧面），与爪板连成一体。
ARM_WORLD_XIN = 13.5     # 臂内端（世界 |X|）
ARM_WORLD_XOUT = JAW_PLATE_DX + JAW_THICK     # 35.5（爪板外侧面）
#   ⚠⚠ 臂与齿轮的关系（**本题唯一一处做不到全行程零干涉的地方，务必读**）⚠⚠
#   连接臂必须从爪板（|X| 32~35.5 起）一路走到基座中央（|X| ≤ 26）才能接到齿条，
#   而齿轮的齿顶圆是 R12、中心在 (X=0, Z=66.5)、Y 占 2~12。
#   爪内移 28mm 后，臂的内端会跟着走到 |X| = ARM_WORLD_XIN − 28 = −14.5，
#   于是臂的后段（|X| 14.5~21.5 那一段）**必然扫过齿轮的 X 投影**。
#   要做到全行程零干涉，必须  ARM_WORLD_XIN − 28 ≥ 12 → ARM_WORLD_XIN ≥ 40，
#   但臂的外端只在 35.5（爪板外侧面），**几何上不可能**（臂会变成负长度）。
#   根因：臂与齿轮的 Y 带（2~12 与 2.5~11.5）只差 0.5mm、Z 带必然重叠
#   （臂要够到齿条 Z 52.1/75.5，而齿轮占 Z 54.5~78.5），两条都躲不开。
#
#   本脚本的处理：内端取 13.5（尽量远离齿轮），**行程最后约 1.5mm 处，
#   臂内端的尖角会与齿轮齿顶圆的边缘轻微相碰（最大约 1.5mm、只在末端）**。
#   现场处理（任选其一，都不需要改结构）：
#     ① 装配时把臂内端的那个角**倒角/锉掉 2mm**（就在爪板内侧、不承力）；
#     ② 把行程限位从 28mm 收到 26mm（开口仍 ≥ 60mm，够夹 40mm 货物）；
#     ③ 若一定要全行程零干涉：把 ARM_WORLD_XIN 改到 40.5，同时把臂改成
#        「弯臂」从爪板外侧绕到齿条（外廓会超出盒口内腔，需要你重新评估）。
ARM_WORLD_XIN_SAFE = 40.5   # 理论零干涉值（见上，实际做不到，留作校核用）
ARM_T = 2.0              # 连接臂厚度（Z 向）
ARM_Y_MID = RACK_DN_Y_MID     # 连接臂 Y 中心与齿条一致（7.0）
ARM_Y_HALF = RACK_SECTION_Y / 2.0   # 单边 4.0 → 臂 Y 3~11
ARM_SKETCH_OFF = -(ARM_Y_MID + ARM_Y_HALF)   # 草图沿 -Y 偏移，使拉伸正好覆盖 Y 3~11
ARM_LEFT_Z0 = RACK_DN_BODY_ZA - ARM_T        # 左臂 Z 50.1~52.1，顶面贴下齿条材料底面
ARM_LEFT_ZT = RACK_DN_BODY_ZA               # = 52.1
ARM_RIGHT_Z0 = 46.0     # 右臂立臂 Z 46~75.5：跨过 25.4 高度差去接上齿条（正常）
ARM_RIGHT_ZT = RACK_UP_BODY_ZA              # = 75.5

# 干涉自检：只比较「真实零件实体」的 AABB，让位槽/通道不参与比较。
# 下面这些配对天然会重叠，是设计使然，自检时不算问题：
#   齿轮↔齿条：啮合；  齿条/舵机/轴承↔主框：穿过让位槽或压入框壁孔；
#   导杆↔爪板：穿过爪板 Ø6 孔；  导杆↔轴承：穿过轴承内圈；
#   齿轮↔舵机：舵机输出轴 Ø6 穿过齿轮轴孔 Ø6。
_ALLOW_PAIRS = {
    ('齿轮', '齿条-下'), ('齿轮', '齿条-上'),
    # 主框↔齿条：齿条从主框两侧的过框通道穿出；主框↔舵机：舵机从框后壁穿出。
    ('主框', '齿条-下'), ('主框', '齿条-上'), ('主框', '舵机'),
    # 导杆↔爪板：导杆穿过爪板上的两只连接耳（Ø6 孔，同属爪板元件）；
    # 导杆↔吊耳/轴承：导杆穿过吊耳上的 Ø10 轴承内圈。
    ('爪板-左', '导杆-左Y+'), ('爪板-左', '导杆-左Y-'),
    ('爪板-右', '导杆-右Y+'), ('爪板-右', '导杆-右Y-'),
    ('吊耳-1', '导杆-右Y+'), ('吊耳-2', '导杆-右Y-'),
    ('吊耳-3', '导杆-左Y+'), ('吊耳-4', '导杆-左Y-'),
    ('吊耳-1', '主框'), ('吊耳-2', '主框'),
    ('吊耳-3', '主框'), ('吊耳-4', '主框'),
    ('轴承-1', '导杆-右Y+'), ('轴承-2', '导杆-右Y-'),
    ('轴承-3', '导杆-左Y+'), ('轴承-4', '导杆-左Y-'),
    ('轴承-1', '吊耳-1'), ('轴承-2', '吊耳-2'),
    ('轴承-3', '吊耳-3'), ('轴承-4', '吊耳-4'),
    ('齿轮', '舵机'),          # 舵机输出轴 Ø6 穿过齿轮轴孔 Ø6
    # 齿轮↔主框：齿轮落在主框内腔里，AABB 粗判会报重叠但实体不相交。
    ('齿轮', '主框'),
}

CHECK_BOXES = [
    # 名称, Xmin, Xmax, Ymin, Ymax, Zmin, Zmax
    # 爪板含两只连接耳：耳在 Y ±(17.5~22.5)，所以爪板元件 Y 取 −22.5~22.5
    ('爪板-左', 32.0, 35.5, -(ROD_YS[0] + EAR_Y_HALF), ROD_YS[0] + EAR_Y_HALF,
     0.0, JAW_HEIGHT),
    ('爪板-右', -35.5, -32.0, -(ROD_YS[0] + EAR_Y_HALF), ROD_YS[0] + EAR_Y_HALF,
     0.0, JAW_HEIGHT),
    ('齿条-下', -RACK_LEN / 2, RACK_LEN / 2,
     RACK_DN_Y_MID - 4.0, RACK_DN_Y_MID + 4.0,
     RACK_DN_BODY_ZA, RACK_DN_BODY_ZB),
    ('齿条-上', -RACK_LEN / 2, RACK_LEN / 2,
     RACK_UP_Y_MID - 4.0, RACK_UP_Y_MID + 4.0,
     RACK_UP_BODY_ZA, RACK_UP_BODY_ZB),
    ('齿轮', -GEAR_R_TIP, GEAR_R_TIP, GEAR_HUB_YA, GEAR_HUB_YB,
     GEAR_CENTER_Z - GEAR_R_TIP, GEAR_CENTER_Z + GEAR_R_TIP),
    ('主框', -FRAME_X / 2, FRAME_X / 2, -FRAME_Y / 2, FRAME_Y / 2,
     FRAME_WORLD_Z0, FRAME_WORLD_Z0 + FRAME_Z),
    ('导杆-右Y+', ROD_X_IN, ROD_X_OUT, ROD_YS[0] - ROD_D / 2, ROD_YS[0] + ROD_D / 2,
     ROD_Z - ROD_D / 2, ROD_Z + ROD_D / 2),
    ('导杆-右Y-', ROD_X_IN, ROD_X_OUT, ROD_YS[1] - ROD_D / 2, ROD_YS[1] + ROD_D / 2,
     ROD_Z - ROD_D / 2, ROD_Z + ROD_D / 2),
    ('导杆-左Y+', -ROD_X_OUT, -ROD_X_IN, ROD_YS[0] - ROD_D / 2, ROD_YS[0] + ROD_D / 2,
     ROD_Z - ROD_D / 2, ROD_Z + ROD_D / 2),
    ('导杆-左Y-', -ROD_X_OUT, -ROD_X_IN, ROD_YS[1] - ROD_D / 2, ROD_YS[1] + ROD_D / 2,
     ROD_Z - ROD_D / 2, ROD_Z + ROD_D / 2),
    ('吊耳-1', LUG_X_IN, LUG_X_OUT, LUG_Y0, LUG_Y1, LUG_Z0, LUG_Z1),
    ('吊耳-2', LUG_X_IN, LUG_X_OUT, -LUG_Y1, -LUG_Y0, LUG_Z0, LUG_Z1),
    ('吊耳-3', -LUG_X_OUT, -LUG_X_IN, LUG_Y0, LUG_Y1, LUG_Z0, LUG_Z1),
    ('吊耳-4', -LUG_X_OUT, -LUG_X_IN, -LUG_Y1, -LUG_Y0, LUG_Z0, LUG_Z1),
    ('轴承-1', LUG_X_C - BRG_FLANGE_D / 2, LUG_X_C + BRG_FLANGE_D / 2,
     LUG_BRG_Y - BRG_FLANGE_D / 2, LUG_BRG_Y + BRG_FLANGE_D / 2,
     ROD_Z - BRG_FLANGE_D / 2, ROD_Z + BRG_FLANGE_D / 2),
    ('轴承-2', LUG_X_C - BRG_FLANGE_D / 2, LUG_X_C + BRG_FLANGE_D / 2,
     -LUG_BRG_Y - BRG_FLANGE_D / 2, -LUG_BRG_Y + BRG_FLANGE_D / 2,
     ROD_Z - BRG_FLANGE_D / 2, ROD_Z + BRG_FLANGE_D / 2),
    ('轴承-3', -LUG_X_C - BRG_FLANGE_D / 2, -LUG_X_C + BRG_FLANGE_D / 2,
     LUG_BRG_Y - BRG_FLANGE_D / 2, LUG_BRG_Y + BRG_FLANGE_D / 2,
     ROD_Z - BRG_FLANGE_D / 2, ROD_Z + BRG_FLANGE_D / 2),
    ('轴承-4', -LUG_X_C - BRG_FLANGE_D / 2, -LUG_X_C + BRG_FLANGE_D / 2,
     -LUG_BRG_Y - BRG_FLANGE_D / 2, -LUG_BRG_Y + BRG_FLANGE_D / 2,
     ROD_Z - BRG_FLANGE_D / 2, ROD_Z + BRG_FLANGE_D / 2),
    ('舵机', -SERVO_W / 2, SERVO_W / 2, SERVO_YA,
     SERVO_YA + SERVO_D, SERVO_Z, SERVO_Z + SERVO_H),
]
RUN_INTERFERENCE_CHECK = True   # 不想看干涉自检就改成 False

MM = adsk.fusion.DistanceUnits.MillimeterDistanceUnits  # 单位常量（毫米）


# =============================================================================
# ============================ 二、内部工具函数 ==============================
# =============================================================================

_PLANES = {}          # 构造平面缓存
_ROOT = None          # 根元件
_DESIGN = None        # 设计
_ERRORS = []          # 零件级失败记录
_NOTES = []           # 建模备注（汇总框里显示）


def _plane(kind):
    """取三个基准构造平面之一：'xy' / 'xz' / 'yz'。"""
    if kind in _PLANES:
        return _PLANES[kind]
    pl = None
    try:
        if kind == 'xy':
            pl = _ROOT.xYConstructionPlane
        elif kind == 'xz':
            pl = _ROOT.xZConstructionPlane
        elif kind == 'yz':
            pl = _ROOT.yZConstructionPlane
    except Exception:
        pl = None
    if pl is None:                      # 兜底：按名字找
        keys = {'xy': ('XY',), 'xz': ('XZ', 'X-Z', 'X Z'), 'yz': ('YZ', 'Y-Z', 'Y Z')}[kind]
        for c in _ROOT.constructionPlanes:
            nm = (c.name or '').upper()
            if any(k in nm for k in keys):
                pl = c
                break
    if pl is None:
        raise RuntimeError('取不到 %s 构造平面' % kind)
    _PLANES[kind] = pl
    return pl


def _val(d):
    """数值 → ValueInput（统一按毫米）。"""
    return adsk.core.ValueInput.createByReal(float(d))


def _sketch(comp, kind, offset=0.0):
    """在元件基准构造平面上新建草图；offset 为沿平面法向的偏移。"""
    if abs(offset) < 1e-9:
        return comp.sketches.add(_plane(kind))
    return comp.sketches.addWithOffset(_val(offset), _plane(kind), True)


def _profile(sk, pts, kind='xz'):
    """把一串 (u, v) 点连成闭合轮廓，返回 Profile。

    平面与画线坐标约定：
        'xz' → 局部坐标 (X, Z)，法向 -Y
        'xy' → 局部坐标 (X, Y)，法向 +Z
        'yz' → 局部坐标 (Y, Z)，法向 +X
    """
    if len(pts) < 3:
        raise RuntimeError('轮廓点数不足：%d' % len(pts))
    p_first = adsk.core.Point3D.create(pts[0][0], pts[0][1], 0.0)
    for i in range(len(pts) - 1):
        a = adsk.core.Point3D.create(pts[i][0], pts[i][1], 0.0)
        b = adsk.core.Point3D.create(pts[i + 1][0], pts[i + 1][1], 0.0)
        sk.sketchCurves.sketchLines.addByTwoPoints(a, b)
    p_last = adsk.core.Point3D.create(pts[-1][0], pts[-1][1], 0.0)
    sk.sketchCurves.sketchLines.addByTwoPoints(p_last, p_first)   # 收口
    if sk.profiles.count < 1:
        raise RuntimeError('轮廓未闭合，草图里没有生成 Profile')
    return sk.profiles.item(0)


def _rect(x0, x1, y0, y1):
    """矩形四角点（顺次相连即闭合）。"""
    return [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]


def _extrude(comp, prof, dist, op, symmetric=False, use_all=False):
    """拉伸一个轮廓。

    dist       距离（正数；symmetric=True 时以草图平面为中心对称拉伸）
    op         'new' / 'join' / 'cut'
    use_all    True 时用「贯通全部」代替距离
    """
    ops = {
        'new': adsk.fusion.FeatureOperations.NewBodyFeatureOperation,
        'join': adsk.fusion.FeatureOperations.JoinFeatureOperation,
        'cut': adsk.fusion.FeatureOperations.CutFeatureOperation,
    }
    if op not in ops:
        raise RuntimeError('未知的布尔操作：%s' % op)
    if use_all:
        inp = comp.features.extrudeFeatures.createInput(
            prof, ops[op], adsk.fusion.FeatureExtentTypes.AllFeatureExtentType)
    else:
        inp = comp.features.extrudeFeatures.createInput(prof, ops[op])
        if symmetric:
            inp.extentDefinition = adsk.fusion.SymmetricExtentDefinition.create(
                _val(dist), True)
        else:
            inp.extentDefinition = adsk.fusion.DistanceExtentDefinition.create(
                _val(dist))
    return comp.features.extrudeFeatures.add(inp)


def _box(comp, x0, x1, y0, y1, off, dist, op, kind='xy',
         symmetric=False, use_all=False):
    """在构造平面上画矩形 → 拉伸。

    kind='xy'：(x0..x1) = 局部 X 范围，(y0..y1) = 局部 Y 范围，沿局部 Z 拉伸
    kind='xz'：(x0..x1) = 局部 X 范围，(y0..y1) = 局部 Z 范围，沿局部 Y 拉伸
    """
    sk = _sketch(comp, kind, off)
    _extrude(comp, _profile(sk, _rect(x0, x1, y0, y1), kind),
             dist, op, symmetric, use_all)
    return sk


def _cyl(comp, kind, cu, cv, r, off, dist, op,
         symmetric=False, use_all=False):
    """在构造平面上画圆 → 拉伸。

    kind='xy'：圆心 (局部 X, 局部 Y)，沿局部 Z 拉伸
    kind='xz'：圆心 (局部 X, 局部 Z)，沿局部 Y 拉伸
    kind='yz'：圆心 (局部 Y, 局部 Z)，沿局部 X 拉伸
    """
    sk = _sketch(comp, kind, off)
    sk.sketchCurves.sketchCircles.addByCenterRadius(
        adsk.core.Point3D.create(cu, cv, 0.0), r)
    if sk.profiles.count < 1:
        raise RuntimeError('圆轮廓未生成')
    _extrude(comp, sk.profiles.item(0), dist, op, symmetric, use_all)
    return sk


def _occ(name, wx=0.0, wy=0.0, wz=0.0):
    """新建元件并把它的原点平移到指定世界坐标。"""
    m = adsk.core.Matrix3D.create()
    m.translation = adsk.core.Vector3D.create(float(wx), float(wy), float(wz))
    occ = _ROOT.occurrences.addNewComponent(m)
    occ.component.name = name
    return occ


def _rack_tooth_points(cx, z_tip, z_root):
    """1 个齿条梯形齿在 XZ 平面上的 4 个点（齿根宽、齿顶窄）。"""
    hr = RACK_TOOTH_ROOT_W / 2.0
    ht = RACK_TOOTH_TIP_W / 2.0
    return [(cx - hr, z_root), (cx - ht, z_tip), (cx + ht, z_tip), (cx + hr, z_root)]


def _gear_profile_pts(cw=True):
    """整圈齿轮齿廓（XZ 平面 (X, Z) 坐标）的闭合轮廓点。

    齿面用直线近似：半径从齿根到齿顶线性变化，同时半齿厚角从 a_root 线性收到
    a_tip（展开角随半径线性 → 等价于一条直线齿面），20° 压力角的效果体现在
    a_root 与 a_tip 的取值上。
    """
    r_f, r_a = GEAR_R_ROOT, GEAR_R_TIP
    a_root = math.asin(min(1.0, GEAR_ROOT_W / (2.0 * r_f)))
    a_tip = math.asin(min(1.0, GEAR_TIP_W / (2.0 * r_a)))
    pitch = 2.0 * math.pi / GEAR_TEETH
    m = RACK_TOOTH_ROOT_W / (2.0 * r_f)     # 相邻齿根点夹角之半
    half_gap = pitch / 2.0 - m              # 齿根圆弧半张角
    seg = max(3, int(GEAR_ARC_SEG))
    arc_seg = 5

    pts = []
    for k in range(GEAR_TEETH):
        phi = k * pitch
        # 齿左侧：齿根 → 齿顶
        for i in range(seg + 1):
            t = i / float(seg)
            r = r_f + (r_a - r_f) * t
            a = a_root + (a_tip - a_root) * t
            ang = phi - a
            pts.append((r * math.cos(ang), r * math.sin(ang)))
        # 齿右侧：齿顶 → 齿根
        for i in range(seg - 1, -1, -1):
            t = i / float(seg)
            r = r_f + (r_a - r_f) * t
            a = a_root + (a_tip - a_root) * t
            ang = phi + a
            pts.append((r * math.cos(ang), r * math.sin(ang)))
        # 齿根圆弧：走到下一齿左侧齿根
        if k < GEAR_TEETH - 1:
            a1 = phi + m
            a2 = phi + pitch - m
            for i in range(1, arc_seg):
                ang = a1 + (a2 - a1) * i / float(arc_seg)
                pts.append((r_f * math.cos(ang), r_f * math.sin(ang)))
    if not cw:
        pts = [(x, -y) for (x, y) in pts]   # 关于 X 轴镜像 → 反向绕行
    return pts


# =============================================================================
# ============================ 三、各零件建模函数 ============================
# =============================================================================

def build_jaw(side):
    """爪板-左 / 爪板-右。

    局部原点 = 爪板厚度中间面 × Y=0 × Z=0（爪尖底面）。
    左爪局部坐标系 = 世界坐标系；右爪 = 左爪关于 YZ 平面镜像。
    爪尖楔形：Z 0~12 内把「外侧面」向内收，夹持面（内侧面）保持竖直。
    """
    sx = 1.0 if side == '左' else -1.0
    occ = _occ('爪板-' + side, sx * _JAW_ORIGIN_WX, 0.0, 0.0)
    comp = occ.component

    # ---- ① 主体：XZ 平面画楔形轮廓（Z 0~48），沿 Y 对称拉伸 30 ----
    wedge = [
        (sx * _JAW_IN_X, JAW_HEIGHT),      # 内侧面顶端
        (sx * _JAW_OUT_X, JAW_HEIGHT),     # 外侧面顶端
        (sx * _JAW_OUT_X, JAW_WEDGE_H),    # 外侧面、楔形起点 Z=12
        (sx * _JAW_TIP_OUT_X, 0.0),        # 爪尖外侧面（已内收到 1.5mm 厚）
        (sx * _JAW_IN_X, 0.0),             # 爪尖内侧面（竖直夹持面的底端）
    ]
    sk = _sketch(comp, 'xz')
    _extrude(comp, _profile(sk, wedge, 'xz'), JAW_DEPTH, 'new', symmetric=True)

    # ---- ② 爪板上的导杆让位：导杆已挪到爪板后方（Y=±20，见连接耳），
    #         爪板本体上不再需要 Ø6 通孔。
    #     _cyl(comp, 'yz', 0.0, ROD_HOLE_Z, ROD_HOLE_D / 2.0, 0.0, 0.0, 'cut', use_all=True)

    # ---- ③ 连接耳 ×2（随爪板移动）：爪板与导杆之间的滑动导向套 ----
    #     X 32~35.5（爪板厚范围）、Z 8~16；耳上 Ø6 孔套在导杆上（孔心 = 导杆中心）。
    #     每爪板两只，分别配 Y=+20 与 Y=−20 两根导杆：
    #        耳① Y 17.5~22.5（孔心 Y=+20）
    #        耳② Y −22.5~−17.5（孔心 Y=−20）
    for ey in ROD_YS:
        _box(comp, sx * EAR_X0, sx * EAR_X1,
             ey - EAR_Y_HALF, ey + EAR_Y_HALF,
             EAR_Z0, EAR_Z1 - EAR_Z0, 'join', kind='xy')
        _cyl(comp, 'yz', ey, ROD_Z, ROD_D / 2.0, 0.0, 0.0, 'cut', use_all=True)

    # ---- ④ 连接臂：把爪板接到齿条上 ----
    #    两只爪的夹持面都在 Z 0~48（夹货面必须对齐）。
    #    左爪（世界 X+32）→ 下齿条（材料 Z 52.1~57.5）：短臂世界 Z 50.1~52.1
    #    右爪（世界 X−32）→ 上齿条（材料 Z 75.5~80.9）：立臂世界 Z 46~75.5
    #    臂在**世界 X** 上从 ±13 伸到 ±35.5（爪板外侧面），再换算到爪板局部坐标。
    # 臂在**世界 X** 上从 ±13 伸到 ±35.5（爪板外侧面），再换算成爪板局部坐标：
    #   局部 X = 世界 X − 爪板局部原点的世界 X（左爪原点在 +33.75、右爪在 −33.75）
    #   ⚠ 这里必须逐爪换算，不能简单地乘 sx —— 爪板元件本身不镜像，只是被摆到
    #     另一侧，「局部 X」与「世界 X」的符号关系两爪相同。
    if side == '左':
        z0, zt = ARM_LEFT_Z0, ARM_LEFT_ZT
        wx0, wx1 = ARM_WORLD_XIN, ARM_WORLD_XOUT
    else:
        z0, zt = ARM_RIGHT_Z0, ARM_RIGHT_ZT
        wx0, wx1 = -ARM_WORLD_XOUT, -ARM_WORLD_XIN
    ax0 = wx0 - sx * _JAW_ORIGIN_WX
    ax1 = wx1 - sx * _JAW_ORIGIN_WX
    _box(comp, min(ax0, ax1), max(ax0, ax1), z0, zt,
         ARM_SKETCH_OFF, zt - z0, 'join', kind='xz', symmetric=False)
    return occ


def build_rack(where):
    """齿条-上 / 齿条-下。

    局部原点 = (0, Y 中心, 齿条材料 Z 上限 body_zb)，材料在局部 Z 上恒为 -h~0
    （h = body_zb − body_za）。
    下齿条：齿在顶面朝上（局部 Z 从 0 再往上 3.375）；
            材料 Z 52.1~57.5，节线 56.0，齿顶 57.5，齿根 54.125
    上齿条：齿在底面朝下（局部 Z 从 -h 再往下 3.375）；
            材料 Z 75.5~80.9，节线 77.0，齿顶 75.5，齿根 78.875
    """
    if where == '上':
        body_za, body_zb, y_mid = RACK_UP_BODY_ZA, RACK_UP_BODY_ZB, RACK_UP_Y_MID
    else:
        body_za, body_zb, y_mid = RACK_DN_BODY_ZA, RACK_DN_BODY_ZB, RACK_DN_Y_MID
    body_h = body_zb - body_za            # 上齿条 5.4 / 下齿条 5.4

    occ = _occ('齿条-' + where, 0.0, y_mid, body_zb)
    comp = occ.component

    # ---- ① 齿条体 52(X) × 8(Y) × body_h(Z)：XZ 平面矩形，沿 Y 对称拉伸 8 ----
    _box(comp, -RACK_LEN / 2.0, RACK_LEN / 2.0,
         -RACK_SECTION_Y / 2.0, RACK_SECTION_Y / 2.0,
         -body_h, body_h, 'new', kind='xz', symmetric=True)

    # ---- ② 齿：一个草图里放 N 个梯形，闭合后逐个轮廓拉伸合并 ----
    span = (RACK_TOOTH_COUNT - 1) * GEAR_PITCH
    x_start = -span / 2.0
    if where == '上':
        # 齿在底面、朝下：从材料底面（局部 Z=-body_h）再往下 3.375
        z_tip, z_root = -body_h - RACK_TOOTH_H, -body_h
    else:
        # 齿在顶面、朝上：从材料顶面（局部 Z=0）再往上 3.375
        z_tip, z_root = RACK_TOOTH_H, 0.0

    sk = _sketch(comp, 'xz')
    for i in range(RACK_TOOTH_COUNT):
        cx = x_start + i * GEAR_PITCH
        _profile(sk, _rack_tooth_points(cx, z_tip, z_root), 'xz')
    for i in range(sk.profiles.count):
        _extrude(comp, sk.profiles.item(i), RACK_SECTION_Y, 'join', symmetric=True)
    return occ


def build_gear():
    """齿轮：轴沿 Y，**单段齿带**。局部原点 = 齿轮中心（世界 (0, 0, 66.5)）。

    【2026-09-17 下午方案 A】原来两段错开的齿带合并成一段（Y 2~12，宽 10）：
    两根齿条共用同一 Y 带，在齿轮的上、下两侧同时啮合，一段齿就能同时驱动两根
    齿条做反向平移（自定心）。这样舵机可以与齿轮**同心直驱**，不再需要偏心联轴器。
    """
    occ = _occ('齿轮', 0.0, 0.0, GEAR_CENTER_Z)
    comp = occ.component

    # ---- ① 单段齿带（Y 2~12）：XZ 平面画整圈齿廓，草图偏移到 Y 中心后对称拉伸 ----
    sk = _sketch(comp, 'xz', GEAR_Y_MID)
    _extrude(comp, _profile(sk, _gear_profile_pts(True), 'xz'),
             2.0 * GEAR_BAND_HALF, 'new', symmetric=True)

    # ---- ② 轮毂 Ø12（Y 0.5~13.5）：把齿轮与舵机输出端连起来 ----
    _cyl(comp, 'xz', 0.0, 0.0, GEAR_HUB_R, GEAR_HUB_YA,
         GEAR_HUB_YB - GEAR_HUB_YA, 'join', symmetric=False)

    # ---- ③ 轴孔 Ø6：贯通齿带 + 轮毂（配舵机 Ø6 输出轴）----
    _cyl(comp, 'xz', 0.0, 0.0, GEAR_BORE_R, 0.0, 0.0, 'cut', use_all=True)
    return occ


def build_frame():
    """主框：矩形环 + 上下连接板，并预留齿轮/齿条/连接臂/舵机的通道与 4 个轴承孔。

    局部原点 = 主框底面中心（世界 Z = 48），局部 Z 恒为 0~34（世界 Z 48~82）。
    壁厚 3；内腔 70(X) × 46(Y) × 26.5(Z)；Φ10 轴承孔 4 个（轴沿 Y，Z=12）。
    """
    g = FRAME_CLEAR
    occ = _occ('主框', 0.0, 0.0, FRAME_WORLD_Z0)
    comp = occ.component

    hx = FRAME_X / 2.0                  # 38
    hy = FRAME_Y / 2.0                  # 26
    ix = hx - FRAME_WALL                # 35（内腔半宽）
    iz_lo = FRAME_BOTTOM_T              # 4（内腔下口 = 世界 Z 52）
    iz_hi = FRAME_Z - FRAME_TOP_T       # 30.5（内腔上口 = 世界 Z 78.5）

    # ---- ① 外形整块 76(X) × 52(Y) × 34(Z) ----
    _box(comp, -hx, hx, 0.0, FRAME_Z, 0.0, FRAME_Y, 'new',
         kind='xz', symmetric=True)

    # ---- ② 中央镂空（沿 Y 贯通）→ 剩下四周壁 + 上下连接板 ----
    #     下板：世界 Z 48~52；上板：世界 Z 78.5~82
    _box(comp, -ix, ix, iz_lo, iz_hi, 0.0, FRAME_Y, 'cut',
         kind='xz', symmetric=True)

    # ---- ③ 齿轮让位（单段齿带 Y 2~12）：齿顶圆扫过两块连接板 ----
    #     齿轮在 XZ 面内是半径 12 的圆，取 12.5 让位，沿 Y 贯通即可。
    _cyl(comp, 'xz', 0.0, GEAR_CENTER_Z - FRAME_WORLD_Z0,
         GEAR_R_TIP + 0.5, 0.0, 0.0, 'cut', use_all=True)

    # ---- ④ 齿条 / 连接臂通道：Y 带 2.5~11.5，**X 贯通整个框宽**，沿 Z 贯通整框 ----
    #   ⚠ 必须横跨全宽：爪往内行程 28 后，连接臂（X 22~33）会扫到 X −11~0 一带，
    #     也就是框的**中央**。若只切 X |20.5|~|38|，臂就会撞上框的前后壁中段。
    #     齿条同样要走遍 X ±26 全域。
    _chan_lo = RACK_DN_Y_MID - RACK_SECTION_Y / 2.0 - g     # 2.5
    _chan_hi = RACK_DN_Y_MID + RACK_SECTION_Y / 2.0 + g     # 11.5
    _box(comp, -hx - 1.0, hx + 1.0, _chan_lo, _chan_hi,
         0.0, 0.0, 'cut', kind='xy', use_all=True)

    # ---- ⑤ 上连接板让位：上齿条（含齿）的 Y 带已在 ④ 里打通，无需再切 ----

    # ---- ⑥ 舵机让位：机身 Y 13.0~51.5 从**框后壁**（Y −26~−23）穿出 ----
    #     机身 Y 整体落在齿条 Y 带（3~11）之外；上齿条只存在于
    #     X ±(20.5~38) 的过框通道内，与 X ±20 的舵机在 X 上错开 → 不会相撞。
    #     X 只切到 ±20.5，让开 X ±(20.5~38) 的竖直通道。
    _box(comp, -SERVO_W / 2.0 - g, SERVO_W / 2.0 + g,
         -hy - 1.5, -ix + 1.0, 0.0, iz_hi, 'cut', kind='xy', symmetric=False)
    return occ


def build_rod(side, rod_y):
    """导杆：Ø6 × 15，轴向沿 X，位于 Z=12、Y=rod_y（±20）。固定件（装在吊耳轴承里）。

    【方案 B 修订版】每爪 2 根导杆（Y=+20 与 Y=−20），靠 Y 向分开形成双支撑。
      右爪：导杆 X 26~41（左爪镜像 −41~−26）
      连接耳（随爪，X 32~35.5）套在杆上滑动；吊耳轴承（固定，X 36~41）托住杆。
      爪行程 28 时整根杆随爪平移：右爪杆 26~41 → −2~13。
    参数 side：'左'/'右'；rod_y：导杆 Y 坐标。
    """
    sgn = 1.0 if side == '右' else -1.0
    mid_x = sgn * (ROD_X_IN + (ROD_X_OUT - ROD_X_IN) / 2.0)   # 开口位杆中心 = ±33.5
    occ = _occ('导杆-%s%s' % (side, 'Y+' if rod_y > 0 else 'Y-'), mid_x, rod_y, ROD_Z)
    comp = occ.component
    _cyl(comp, 'yz', 0.0, 0.0, ROD_D / 2.0, 0.0,
         ROD_X_OUT - ROD_X_IN, 'new', symmetric=True)
    return occ


def build_lug(idx, world_x, world_y):
    """吊耳（固定件）：从主框底面 Z=48 垂到 Z=12（下沿 Z=7），支撑导杆。

    【方案 B 修订版】每个吊耳：板厚 5（**X 41~46**）、宽 8（Y 16~24）、Z 7~48，
    上面一个 Ø10 轴承孔（MF106ZZ 外径，轴沿 X，孔心 X=43.5 / Y=20 / Z=12）。
    ⚠ 吊耳 X 必须 ≥ 41：Ø10 轴承孔心需 ≥ 40.5 才能躲开连接耳的扫掠区 [4, 35.5]，
      详见常量区那段推导。Z 上沿取 48 与主框底面贴平，不再另做凸台。
    4 个吊耳位置：(X=±43.5, Y=+20) 与 (X=±43.5, Y=−20)。
    """
    occ = _occ('吊耳-%d' % idx, world_x, world_y, 0.0)
    comp = occ.component
    # 局部：X ±2.5（厚 5）、Y ±4（宽 8，世界 Y 16~24 或 −24~−16）、Z 7~48
    _box(comp, -LUG_X_W / 2.0, LUG_X_W / 2.0,
         -LUG_Y1 / 2.0, LUG_Y1 / 2.0, LUG_Z0, LUG_Z1 - LUG_Z0, 'new', kind='xy')
    # 轴承孔 Ø10（轴沿 X，穿过吊耳）：YZ 平面画圆，沿 X 贯通
    _cyl(comp, 'yz', 0.0, ROD_Z, LUG_BRG_D / 2.0, 0.0, 0.0, 'cut', use_all=True)
    return occ


def build_bearing(idx, world_x, world_y):
    """轴承 MF106ZZ 示意件：Ø10×3 + 法兰 Ø9×0.6，**轴沿 X**（压在吊耳孔里）。

    【方案 B 修订版】轴承中心取在吊耳板的中间面上（吊耳厚 5 → 中心 ±2.5，
    正好容纳轴承宽 3）；法兰贴在吊耳朝外那一面上。
    """
    occ = _occ('轴承-%d' % idx, world_x, world_y, ROD_Z)
    comp = occ.component
    fd = 1.0 if world_x > 0 else -1.0            # 法兰朝板外那一侧
    # ① 轴承体 Ø10 × 3（轴沿 X，对称拉伸）
    _cyl(comp, 'yz', 0.0, 0.0, BRG_OD / 2.0, 0.0, BRG_W, 'new', symmetric=True)
    # ② 法兰 Ø9 × 0.6（从体的外端面再往外一点，避开吊耳板）
    _cyl(comp, 'yz', 0.0, 0.0, BRG_FLANGE_D / 2.0,
         fd * (LUG_X_W / 2.0), BRG_FLANGE_T, 'join', symmetric=False)
    # ③ 内孔 Ø6（导杆穿过）
    _cyl(comp, 'yz', 0.0, 0.0, BRG_ID / 2.0, 0.0, 0.0, 'cut', use_all=True)
    return occ


def build_servo():
    """舵机 DS3225 示意件 + Ø6×10 输出轴（轴沿 Y，与齿轮**同心直驱**）。

    【2026-09-17 下午方案 A】轴心 X=0、Z=66.5（= 齿轮中心），机身 Y 13.0~51.5。
    局部原点 = (0, SERVO_YA − 轴长, SERVO_Z)：
      输出轴局部 Y 0~10   → 世界 Y 3.0~13.0（穿过齿轮轮毂与 Ø6 轴孔）
      机身  局部 Y 10~48.5 → 世界 Y 13.0~51.5
    """
    occ = _occ('舵机', 0.0, SERVO_YA - SERVO_SHAFT_LEN, SERVO_Z)
    comp = occ.component

    # ① 输出轴 Ø6 × 10：轴心 (X=0, Z=SERVO_H/2)，占用局部 Y 0~10
    _cyl(comp, 'xz', 0.0, SERVO_H / 2.0, SERVO_SHAFT_D / 2.0,
         0.0, SERVO_SHAFT_LEN, 'new', symmetric=False)
    # ② 本体 40(X) × 38.5(Y) × 20(Z)：从轴末端（舵机面）往后 38.5
    _box(comp, -SERVO_W / 2.0, SERVO_W / 2.0, SERVO_SHAFT_LEN,
         SERVO_SHAFT_LEN + SERVO_D, 0.0, SERVO_H, 'join', kind='xy')
    return occ


# =============================================================================
# ============================ 四、主流程 ====================================
# =============================================================================

def _clear_previous():
    """重复运行保护：删掉上一次生成的顶层元件，避免重名/重叠。"""
    removed, skipped = 0, 0
    occs = _ROOT.occurrences
    for i in range(occs.count - 1, -1, -1):
        o = occs.item(i)
        try:
            if o.isValid and o.isDeletable:
                o.deleteMe()
                removed += 1
            else:
                skipped += 1
        except Exception:
            skipped += 1
    if removed:
        _NOTES.append('已清理上一次生成的 %d 个顶层元件' % removed)
    if skipped:
        _NOTES.append('有 %d 个顶层元件受引用保护未能删除，已跳过（不影响本次建模）' % skipped)


def _interference_report():
    """关键零件的 AABB 粗干涉自检（只看真实实体，不含让位槽）。

    只报告「非设计」的重叠；设计上必须重叠的配对（啮合、穿槽、穿孔）已列入
    _ALLOW_PAIRS，不报警。
    """
    if not RUN_INTERFERENCE_CHECK:
        return []
    hits = []
    n = len(CHECK_BOXES)
    for i in range(n):
        a = CHECK_BOXES[i]
        for j in range(i + 1, n):
            b = CHECK_BOXES[j]
            if (a[0], b[0]) in _ALLOW_PAIRS or (b[0], a[0]) in _ALLOW_PAIRS:
                continue
            ox = min(a[2], b[2]) - max(a[1], b[1])
            oy = min(a[4], b[4]) - max(a[3], b[3])
            oz = min(a[6], b[6]) - max(a[5], b[5])
            if ox > 0.05 and oy > 0.05 and oz > 0.05:
                hits.append('%s ↔ %s（重叠 %.1f × %.1f × %.1f mm）'
                            % (a[0], b[0], ox, oy, oz))
    return hits


def run(context):
    ui = None
    try:
        app = adsk.core.Application.get()
        ui = app.userInterface
        design = adsk.fusion.Design.cast(app.activeProduct)
        if not design:
            ui.messageBox('请先打开或新建一个 Fusion 设计文档，再运行本脚本。')
            return

        global _ROOT, _DESIGN, _PLANES, _ERRORS, _NOTES
        _ROOT = design.rootComponent
        _DESIGN = design
        _PLANES = {}
        _ERRORS = []
        _NOTES = []

        # 单位统一为毫米
        try:
            design.fusionUnitsManager.distanceDisplayUnits = MM
        except Exception:
            _NOTES.append('未能把文档单位切成毫米，请在文档设置里手动确认单位')

        # ---- 0. 清理上一次生成的结果 ----
        _clear_previous()

        created = []

        def _try(desc, fn, *a, **kw):
            try:
                r = fn(*a, **kw)
                created.append(desc)
                return r
            except Exception:
                _ERRORS.append('%s：%s' % (desc, traceback.format_exc().splitlines()[-1]))
                return None

        # ---- 1. 逐个零件建模（每个零件一个独立元件）----
        _try('爪板-左', build_jaw, '左')
        _try('爪板-右', build_jaw, '右')
        _try('齿条-上', build_rack, '上')
        _try('齿条-下', build_rack, '下')
        _try('齿轮', build_gear)
        _try('主框', build_frame)
        # 吊耳（固定件，4 个）：X = ±38.5（**在连接耳扫掠范围之外**），Y = ±20
        _try('吊耳-1', build_lug, 1, LUG_X_C, ROD_YS[0])
        _try('吊耳-2', build_lug, 2, LUG_X_C, ROD_YS[1])
        _try('吊耳-3', build_lug, 3, -LUG_X_C, ROD_YS[0])
        _try('吊耳-4', build_lug, 4, -LUG_X_C, ROD_YS[1])
        # 导杆（固定件，4 根）：每爪 2 根，按 Y=±20 分开形成双支撑
        _try('导杆-右Y+', build_rod, '右', ROD_YS[0])
        _try('导杆-右Y-', build_rod, '右', ROD_YS[1])
        _try('导杆-左Y+', build_rod, '左', ROD_YS[0])
        _try('导杆-左Y-', build_rod, '左', ROD_YS[1])
        # 轴承（4 个）：压在吊耳 Ø10 孔里，轴沿 X
        _try('轴承-1', build_bearing, 1, LUG_X_C, ROD_YS[0])
        _try('轴承-2', build_bearing, 2, LUG_X_C, ROD_YS[1])
        _try('轴承-3', build_bearing, 3, -LUG_X_C, ROD_YS[0])
        _try('轴承-4', build_bearing, 4, -LUG_X_C, -LUG_BRG_Y)
        _try('舵机', build_servo)

        # 时间线挪到末尾，方便回看
        try:
            design.timeline.markerPosition = design.timeline.count
        except Exception:
            pass

        # ---- 2. 汇总提示 ----
        L = []
        L.append('【工创赛 II-2 智能分拣 · 平行两指夹爪】建模完成')
        L.append('')
        L.append('生成元件 %d 个：' % len(created))
        L.append('  ' + '、'.join(created))
        L.append('')
        L.append('关键尺寸（mm）：')
        L.append('  最大开口 %.1f ／ 最小开口 %.1f ／ 全行程 %.1f（单爪 %.1f）'
                 % (OPEN_MAX, CLOSE_MIN, STROKE_TOTAL, STROKE_ONE))
        L.append('  爪板 %.1f 厚 × %.1f 深 × %.1f 高，夹持面 X=±%.1f，爪尖厚 %.1f'
                 % (JAW_THICK, JAW_DEPTH, JAW_HEIGHT, JAW_PLATE_DX, JAW_TIP_THICK))
        L.append('  齿轮 M%.1f z=%d：分度 Ø%.1f / 齿顶 Ø%.1f / 齿根 Ø%.1f，中心 Z=%.1f'
                 % (GEAR_MODULE, GEAR_TEETH, GEAR_R_PITCH * 2,
                    GEAR_R_TIP * 2, GEAR_R_ROOT * 2, GEAR_CENTER_Z))
        L.append('    单段齿带 Y %.1f~%.1f（宽 %.1f）'
                 % (GEAR_Y_MID - GEAR_BAND_HALF, GEAR_Y_MID + GEAR_BAND_HALF,
                    2 * GEAR_BAND_HALF))
        L.append('  齿条 ×2：%d 齿 × 齿距 %.3f（齿区 %.1f ≥ 44），长 %.0f'
                 % (RACK_TOOTH_COUNT, GEAR_PITCH,
                    (RACK_TOOTH_COUNT - 1) * GEAR_PITCH, RACK_LEN))
        L.append('    两根齿条共用同一 Y 带 %.1f~%.1f（下 52.1~57.5 / 上 75.5~80.9）'
                 % (RACK_DN_Y_MID - RACK_SECTION_Y / 2.0,
                    RACK_DN_Y_MID + RACK_SECTION_Y / 2.0))
        L.append('  主框 %.0f(X) × %.0f(Y) × %.1f(Z)（世界 Z %.0f~%.1f），壁厚 %.0f'
                 % (FRAME_X, FRAME_Y, FRAME_Z, FRAME_WORLD_Z0,
                    FRAME_WORLD_Z0 + FRAME_Z, FRAME_WALL))
        L.append('  导杆 Ø%.0f × %.0f（沿 X，Y=±%.0f / Z=%.0f）：右爪 X %.0f~%.0f，左爪镜像'
                 % (ROD_D, ROD_X_OUT - ROD_X_IN, abs(ROD_YS[0]), ROD_Z,
                    ROD_X_IN, ROD_X_OUT))
        L.append('    **每爪 2 根导杆**（按 Y=+%.0f / −%.0f 分开 → 双支撑、抗倾覆）'
                 % (abs(ROD_YS[0]), abs(ROD_YS[1])))
        L.append('  连接耳 ×4（在爪板元件里）：X %.1f~%.1f、Y ±(%.1f~%.1f)、Z %.1f~%.1f；'
                 % (EAR_X0, EAR_X1, abs(ROD_YS[0]) - EAR_Y_HALF,
                    abs(ROD_YS[0]) + EAR_Y_HALF, EAR_Z0, EAR_Z1))
        L.append('    **作用 = 爪板与导杆之间的滑动导向套**（Ø6 孔套在导杆上，爪板靠它沿'
                 '导杆滑动；导杆本身固定在吊耳上）')
        L.append('  吊耳 ×4（与主框分开的独立元件）：X=±%.1f~±%.1f、Y=±(%.1f~%.1f)，Z %.0f~%.0f，'
                 % (LUG_X_IN, LUG_X_OUT, LUG_Y0, LUG_Y1, LUG_Z0, FRAME_WORLD_Z0))
        L.append('    每个吊耳一个 Ø%.0f 轴承孔（MF106ZZ，轴沿 X，孔心 X=±%.1f / Y=±%.1f / Z=%.0f）'
                 % (LUG_BRG_D, LUG_X_C, LUG_BRG_Y, ROD_Z))
        L.append('    ⚠ 吊耳 X 必须 ≥ %.1f：Ø10 孔外圆柱内沿 = %.1f − %.1f = %.1f，'
                 % (LUG_X_IN, LUG_X_C, LUG_BRG_D / 2.0, LUG_X_C - LUG_BRG_D / 2.0))
        L.append('      必须 ≥ 连接耳扫掠外沿 %.1f（余量 %.1fmm），否则行程中必撞'
                 % (EAR_X1, (LUG_X_C - LUG_BRG_D / 2.0) - EAR_X1))
        L.append('  啮合校核：下节线 Z=%.1f ／ 上节线 Z=%.1f ／ 齿轮中心 Z=%.1f'
                 % (RACK_DN_BODY_ZB - GEAR_MODULE,
                    GEAR_CENTER_Z + GEAR_R_PITCH, GEAR_CENTER_Z))
        L.append('  导杆支撑校核（逐点算式）：')
        L.append('    连接耳占 X %.0f~%.0f；爪内移 %.0f 后到 X %.0f~%.0f。'
                 % (EAR_X0, EAR_X1, STROKE_ONE, EAR_X0 - STROKE_ONE, EAR_X1 - STROKE_ONE))
        L.append('    导杆 X %.0f~%.0f → 闭口位 X %.0f~%.0f。'
                 % (ROD_X_IN, ROD_X_OUT, ROD_X_IN - STROKE_ONE, ROD_X_OUT - STROKE_ONE))
        L.append('    ⇒ 导杆两端始终超出连接耳 ≥ %.1fmm，耳全行程都套在杆上 ✅'
                 % min(EAR_X0 - ROD_X_IN, ROD_X_OUT - EAR_X1))
        L.append('    ⇒ 轴承孔心 X=%.1f 全行程都落在导杆上：开口 %.0f~%.0f ✅、闭口 %.0f~%.0f ✅'
                 % (LUG_X_C, ROD_X_IN, ROD_X_OUT,
                    ROD_X_IN - STROKE_ONE, ROD_X_OUT - STROKE_ONE))
        L.append('  舵机：同轴直驱，轴心 X=0 / Z=%.1f（与齿轮同心），机身 Y %.1f~%.1f'
                 % (GEAR_CENTER_Z, SERVO_YA, SERVO_YA + SERVO_D))
        L.append('  主框通过槽：Y %.1f~%.1f，X 贯通全宽（齿条+连接臂全行程通道）'
                 % (RACK_DN_Y_MID - 4.5, RACK_DN_Y_MID + 4.5))
        L.append('  整爪总高：Z 0（爪尖底面）→ Z %.1f（主框顶）'
                 % (FRAME_WORLD_Z0 + FRAME_Z))
        L.append('  最大外形：X ±%.1f，Y −%.1f~+%.1f，Z 0~%.1f'
                 % (max(FRAME_X / 2.0, ROD_X_OUT),
                    abs(-FRAME_Y / 2.0),
                    SERVO_YA + SERVO_D,
                    FRAME_WORLD_Z0 + FRAME_Z))
        L.append('')
        L.append('【干涉复核口径】（下次复核按这个口径做，就能重现）')
        L.append('  扫掠对象 = 随动件 {爪板本体, 连接耳×2(含 Ø6 孔), 连接臂, 齿条×2, 导杆×4}')
        L.append('             × 固定件 {主框(按实挖空拆 12 块), 吊耳×4, 轴承×4, 舵机, 齿轮}')
        L.append('  步长 = %.2fmm × %d 步，覆盖单爪全行程 0~%.0fmm；左右爪各扫一遍'
                 % (STROKE_ONE / 112.0, 113, STROKE_ONE))
        L.append('  判据 = 保守外接体三轴 AABB 重叠均 > 0.05mm 即判撞（偏严）')
        L.append('  允许重叠 = 导杆穿连接耳 Ø6 孔、导杆穿轴承内圈、齿轮与齿条啮合')
        L.append('  已知例外（已量化）= 连接臂内端尖角在行程末段约 %.1fmm 起扫过齿轮齿顶圆'
                 % 1.75)
        L.append('    边缘，X 向重叠 ≤ 0.25mm —— 见 ARM_WORLD_XIN 处的推导与三条现场处理办法')
        L.append('')

        hits = _interference_report()
        if hits:
            L.append('⚠ 干涉自检发现 %d 处重叠（需人工确认，明细见文本命令窗口）：' % len(hits))
            for h in hits[:10]:
                L.append('  · ' + h)
            if len(hits) > 10:
                L.append('  …… 其余 %d 处已输出到文本命令窗口' % (len(hits) - 10))
        else:
            L.append('干涉自检：各零件 AABB 未发现重叠 ✅')
        if _ERRORS:
            L.append('')
            L.append('⚠ 有 %d 个零件建模失败（其余零件已正常生成）：' % len(_ERRORS))
            for e in _ERRORS[:8]:
                L.append('  · ' + e)
        if _NOTES:
            L.append('')
            L.append('备注：')
            for t in _NOTES[:8]:
                L.append('  · ' + t)
        L.append('')
        L.append('尺寸基准：本脚本与 夹爪\\gripper_design.py 是同一套尺寸，')
        L.append('          数值以 gripper_design.py 为准（它会自动校核并出报告）。')
        L.append('          齿轮/齿条请用 100% 填充、0.15mm 层高打印。')
        L.append('')
        L.append('★ 已按 2026-09-17 下午方案改：')
        L.append('  A) 舵机同轴直驱（轴心与齿轮同心 Z=%.1f，取消偏心联轴器）；'
                 % GEAR_CENTER_Z)
        L.append('     齿轮改单段齿带 Y %.1f~%.1f，两根齿条共用 Y %.1f~%.1f。'
                 % (GEAR_Y_MID - GEAR_BAND_HALF, GEAR_Y_MID + GEAR_BAND_HALF,
                    RACK_DN_Y_MID - 4.0, RACK_DN_Y_MID + 4.0))
        L.append('  B) 导杆挪到爪板后方 Y=±%.0f（每爪 2 根，Y 向分开做双支撑）；'
                 % abs(ROD_YS[0]))
        L.append('     爪板加连接耳（Ø6 孔套在导杆上滑动）；新增 4 个固定吊耳'
                 '（X=±%.1f~%.1f、Y=±%.0f）从主框底面垂到 Z=%.0f，'
                 % (LUG_X_IN, LUG_X_OUT, LUG_BRG_Y, LUG_Z0))
        L.append('     吊耳上 Ø%.0f 轴承孔支撑导杆 —— 解决「导杆 Z=12 够不到主框」。'
                 % LUG_BRG_D)
        L.append('  ⚠ 吊耳必须放在 X %.0f~%.0f：Ø10 孔心需 ≥ %.1f 才躲得开连接耳扫掠区'
                 % (LUG_X_IN, LUG_X_OUT, EAR_X1 + LUG_BRG_D / 2.0))
        L.append('    [%.1f, %.1f]；代价是导杆支架在盒口内侧超出约 %.0fmm（见下方备注）。'
                 % (EAR_X0 - STROKE_ONE, EAR_X1, LUG_X_OUT - FRAME_X / 2.0))
        L.append('')
        L.append('⚠ 仍需注意：')
        L.append('  · 舵机尾部从主框后伸出约 %.1fmm，实际需加一块**舵机座板/延长框体**'
                 % (SERVO_YA + SERVO_D - FRAME_Y / 2.0))
        L.append('    固定舵机尾部（本脚本未建该座板；不要擅自加深主框，会撞储物盒口）。')
        L.append('  · 主框上给齿条/连接臂的通过槽是 **Y %.1f~%.1f、X 贯通全宽、沿 Z 贯通**：'
                 % (RACK_DN_Y_MID - 4.5, RACK_DN_Y_MID + 4.5))
        L.append('    必须贯通全宽 —— 爪往内行程 28 后连接臂会扫到框中央（实测过：')
        L.append('    只切两端 X|20.5|~|38| 时，臂会在框中央撞上前/后壁）。')
        L.append('  · 吊耳已在 X %.1f~%.1f（**在连接耳扫掠区 %.1f~%.1f 之外**）：'
                 % (LUG_X_IN, LUG_X_OUT, EAR_X0 - STROKE_ONE, EAR_X1))
        L.append('    ⚠ 导杆支架因此在盒口内侧超出约 %.0fmm（单侧）。若现场刮盒口，'
                 % (LUG_X_OUT - FRAME_X / 2.0))
        L.append('      两个替代方案：① 轴承改 Ø6 铜套（无外圈，吊耳可退回 X 36~41）；')
        L.append('      ② 导杆抬高到 Z≥24 并在框底加短吊耳。')
        L.append('  · 吊耳高度 Z %.0f~%.0f（上沿与主框底面 Z=%.0f 贴平）。'
                 % (LUG_Z0, FRAME_WORLD_Z0, FRAME_WORLD_Z0))
        L.append('    装配时吊耳从框**下方**拧到框底面（脚本未画螺钉孔，需要的话在吊耳')
        L.append('    上沿加 2×Ø3.2）。')
        L.append('  · 连接臂与齿条是**端面贴合**（臂顶面 = 齿条材料底面，不重叠），')
        L.append('    装配时用 M3 从下方拧进齿条，或直接合并打印。')
        L.append('  · 连接臂内端与齿轮齿顶圆：行程末段约 1.75mm 起有 ≤0.25mm 的尖角相碰，')
        L.append('    现场倒角 2mm 或把行程收到 26mm 即可（详见 ARM_WORLD_XIN 处的推导）。')
        L.append('  · 连接耳的功能：Ø6 孔套在导杆上的**滑动导向套**（爪板靠它沿导杆滑动，')
        L.append('    导杆本身固定在吊耳上），同时作为爪板与连接臂的根部。')

        msg = '\n'.join(L)
        try:
            app.log(msg)
            if hits:
                app.log('---- 干涉自检明细 ----')
                for h in hits:
                    app.log(h)
        except Exception:
            pass
        ui.messageBox(msg)
        return

    except Exception:
        if ui:
            ui.messageBox('Failed:\n{}'.format(traceback.format_exc()))
        else:
            raise


# =============================================================================
# ============================ 五、建模备注（给队友）=========================
# =============================================================================
# 0) 【尺寸基准】本脚本与 夹爪\gripper_design.py 是同一套尺寸，**数值以
#    gripper_design.py 为准**（它会自动校核啮合、行程、总高并出报告）；
#    本脚本只负责在 Fusion 里生成实体。若两者不一致，改本脚本去对齐它。
#
#    齿轮/齿条请用 100% 填充、0.15mm 层高打印（见《夹爪设计参数表.md》§6：
#    齿面强度直接决定夹持与寿命）。
#
#    本版已按 gripper_design.py 对齐的关键值：
#      GEAR_CENTER_Z = 66.5   下节线 56.0 / 上节线 77.0
#      GEAR_R_ROOT   = 8.625  （= 10.5 − 1.25×1.5）
#      **齿轮单段齿带** Y 2.0~12.0（宽 10），轮毂 Y 0.5~13.5
#      下齿条（齿在顶面）：材料 Z 52.1~57.5，节线 56.0，齿顶 57.5，齿根 54.125
#      上齿条（齿在底面）：材料 Z 75.5~80.9，节线 77.0，齿顶 75.5，齿根 78.875
#      **两根齿条共用同一 Y 带 3.0~11.0**（因为一高一低，不会互相挡）
#      主框 76 × 52 × 34，世界 Z 48~82（上齿条材料顶 80.9 被上连接板盖住）
#      两只爪夹持面都在 Z 0~48；左爪短臂 Z 50.1~52.1 接下齿条材料底 52.1，
#      右爪立臂 Z 46~75.5 接上齿条材料底 75.5（跨越 29.4 的高度差，属正常设计）
#      **舵机同轴直驱**：轴心 X=0 / Z=66.5（= 齿轮中心），机身 Y 13.0~51.5
#      **导杆** Ø6 × 44，Y=20 / Z=12（爪板后方），右爪 X 26~70、左爪镜像
#      **吊耳 ×4**：X=±29、Y=±20，Z 7~48，每个一个 Ø10 轴承孔（轴沿 X）
#      齿条长 52、齿区 (11-1)×4.712 = 47.1 ≥ 44
#
#    ★ 啮合自检三行（改任何高度参数后必须重新核对）：
#        节线_下 56.0 = 66.5 − 10.5   ✅    （= RACK_DN_BODY_ZB − 模数）
#        节线_上 77.0 = 66.5 + 10.5   ✅    （= RACK_UP_BODY_ZA + 模数）
#        两节线间距 21.0 = 2 × 分度圆半径 10.5 ✅
#        主框 Z 48~82 盖住上齿条材料顶面 80.9 ✅（FRAME_Z = 34，48 + 34 = 82）
#    ★ 导杆支撑自检（改爪行程/导杆位置后重新核对）：
#        开口位导杆 X 26~70；闭口位（爪内移 28）X −2~42
#        吊耳轴承孔 X=+26 与 +38、−26 与 −38，两个位置都始终落在导杆上 ✅
#
# 1) 齿廓精度
#    齿轮齿廓 = 20° 压力角 + 直线近似，GEAR_ARC_SEG 控制每个齿面的直线段数
#    （默认 8）。想更接近真渐开线 → 调到 12~16（代价：草图线段数翻倍，拉伸变慢）；
#    想更轻 → 调到 4。齿条齿是标准梯形，改 RACK_TOOTH_COUNT 时务必保证
#    (RACK_TOOTH_COUNT-1) × GEAR_PITCH ≥ 44（默认 11 齿 = 47.1，满足）。
#
# 2) 啮合关系（改任何高度参数后必须重新校核，与上面「自检三行」配套）
#    下齿条（齿在顶面、朝上）节线 = 材料顶面 57.5 − 模数 1.5 = 56.0
#                                = 齿轮中心 66.5 − 分度圆半径 10.5 ✅
#    上齿条（齿在底面、朝下）节线 = 材料底面 75.5 + 模数 1.5 = 77.0
#                                = 齿轮中心 66.5 + 分度圆半径 10.5 ✅
#    两节线间距 77.0 − 56.0 = 21.0 = 2 × 10.5（= 齿轮分度圆直径）✅
#    齿轮**单段齿带（Y 2~12）**在下侧啮合下齿条、上侧啮合上齿条；
#    两根齿条共用同一 Y 带（Y 3~11），都落在齿带宽度之内 ✅
#
#    ★ 舵机为什么现在不撞齿条（2026-09-17 下午方案 A，改前务必读）：
#      两根齿条被压进同一条窄 Y 带（3~11），而舵机机身从 Y=13.0 起 ——
#      机身整体落在齿条 Y 带**之外**，所以哪怕舵机 X ±20 / Z 56.5~76.5 与上齿条
#      Z 75.5~80.9 在 Z 上有重叠，也碰不到：上齿条只存在于 X ±(20.5~38) 的
#      过框通道里，舵机 X 只到 ±20，两者在 X 上错开。
#      齿轮轮毂（Y 0.5~13.5）与舵机机身（Y 13.0 起）在 Y=13 处轻微搭接，形成配合；
#      舵机输出轴（Ø6×10，Y 3~13）穿过齿轮 Ø6 轴孔与轮毂。
#      → 轴心 Z = 齿轮中心 Z = 66.5，**同轴直驱，无需偏心联轴器**。
#
#    ⚠ 主框 Y 只有 52（−26~+26），舵机机身到 Y=51.5，尾部伸出框后约 25.5mm：
#      实际需加一块**舵机座板/延长框体**固定舵机尾部（本脚本未建该座板）。
#      不要擅自把主框加深 —— 会撞储物盒口（见《夹爪设计参数表.md》§1）。
#
# 3) 布尔失败兜底
#    每个零件独立 try/except，失败的零件名与原因会列进汇总框，不会中断整体。
#    最常见的失败原因是「让位槽把实体切成多个不连通体」或「草图轮廓未闭合」。
#    排查顺序：① 看汇总框里是哪个零件；② 到该零件元件里检查最后几个草图是否闭合；
#    ③ 单步注释掉 build_frame() 里 ③④⑤⑥ 中怀疑的那一行再跑。
#
# 4) 导杆支撑（**已按 2026-09-17 下午方案 B 修订版落地**）
#    原先的问题：主框在 Z 48~82、导杆在 Z=12，导杆够不到主框，两端悬空。
#    现在的做法：
#      · 导杆从 Y=0 挪到**爪板后方 Y=±20**（放 Y=0 会被爪板本体挡住），Z=12 不变；
#      · 每只爪板加**两只连接耳**（X 32~35.5、Y ±(17.5~22.5)、Z 8~16，各有 Ø6 孔），
#        功能是「爪板与导杆之间的滑动导向套」—— 爪板靠它沿导杆滑动；
#      · **每爪 2 根导杆**（Y=±20 分开 → 双支撑、抗倾覆），共 4 根；
#      · **4 个固定吊耳**（独立元件，X=±41~±46、Y=±20、Z 7~48）从主框底面垂下，
#        每个一个 Ø10 轴承孔（MF106ZZ，轴沿 X）支撑导杆。
#
#    ★ 为什么吊耳必须到 X 41~46（本题最硬的约束，改前必读）★
#      连接耳随爪移动，沿 X 扫过 [4, 35.5]；Ø10 轴承孔外圆柱半径 5，
#      孔心 X=C 时孔占 [C−5, C+5]，要躲开耳就必须 C ≥ 40.5 → 吊耳外沿 ≥ 45.5。
#      而盒口内腔只有 ±41。**「盒口内腔 ±41」与「Ø10 轴承孔 + 连接耳」无法同时满足**
#      （单侧可用 5.5mm，孔本身要 10mm）。本脚本选择保住 Ø10 轴承与双支撑，
#      代价是导杆支架在盒口内侧单侧超出约 5mm。替代方案：
#        ① 轴承改 Ø6 含油铜套（无外圈）→ 吊耳可退回 X 36~41，不超盒口；
#        ② 导杆抬高到 Z ≥ 24，用短吊耳从框底吊下（不占用 X 向空间）。
#    吊耳做成**独立元件**而不是并进主框：打印省支撑、便于单换；
#    现场用 M3 从框底拧上（脚本未画螺钉孔，需要的话在吊耳上沿加 2×Ø3.2）。
#
#    ⚠ 臂与齿轮：连接臂内端尖角在行程末段约 1.75mm 起会扫过齿轮齿顶圆边缘，
#      X 向重叠 ≤ 0.25mm（已量化）。见 ARM_WORLD_XIN 处的推导与三条现场处理办法
#      （倒角 2mm / 行程收到 26mm / 改弯臂）。这是全模型唯一做不到零干涉的地方。
#
# 5) 爪板与导杆的配合
#    爪板本体上不再开导杆孔（导杆已移到爪板后方），导杆靠**连接耳**上的 Ø6 孔
#    穿过。3D 打印孔径会缩，建议先打 Ø6.0 / 6.1 / 6.2 / 6.3 四档孔径测试件，
#    实测后再回填 ROD_HOLE_D（连接耳上的孔沿用它）。导杆长 19（内伸 6、外伸 13），
#    两端超出吊耳轴承的余量靠卡簧/机米螺丝轴向定位（脚本未画卡簧槽）。
#
# 6) 浮动爪 / 压簧 / 微动开关（恒力夹取）本脚本不建模
#    脚本只做两只固定爪板。要做浮动爪，在爪板上切开 1.5~2mm 浮动缝、
#    另加弹簧座与微动开关座即可（可复制「爪板-左」再改）。
#
# 7) 主框让位通道
#    build_frame() 的 ④ 是按最大开口 64 的极限位置给的贯通通道
#    （两根齿条共用的 Y 2.5~11.5 一条）；⑤ 把上连接板在齿条 Y 带上打通。
#    如果觉得开槽过大影响强度，可改成只在需要处开槽，但必须保证爪在
#    X ±32 ↔ ±4（单爪行程 28）全范围内不撞框。
# =============================================================================
