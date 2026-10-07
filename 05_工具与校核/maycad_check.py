#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""MayCAD 场景校核器 —— 读 .scene，出【下料表 + 结构尺寸 + 红线校核】。

为什么写它：每改一版框架就发一份 .scene 让人工抠 XML，又慢又容易看漏。这个工具把
「读场景 → 重建每根型材的三维位置 → 对照 II-2 规则和我们定的尺寸链」自动化。
以后每发一版，跑 `python maycad_check.py 1.scene` 就出报告。

用法：
  python maycad_check.py <文件.scene> [<另一个.scene> ...]

格式理解（MayCAD 11.x / Maytec，已用 2026-09-17 两份真实场景验证）：
  * 每个 <object> 是一根型材；<height> = 杆长（厘米），<width>/<length> = 截面（4.0 = 40mm，方形）；
  * <rotation> = 16 个浮点，行优先 4x4；第 4 列是平移（厘米）；局部杆长轴 = 局部 +Y，
    即全局轴向 = 旋转矩阵第 1 列（rot[1], rot[5], rot[9]）；截面居中于轴线；
  * 全局 Y 轴朝上。杆件包围盒 = 轴线段沿杆长方向延伸 + 垂直方向各撑开 20mm。

⚠️ 下面的"需求常量"是按 2026-09-17 定的设计（下挂式龙门）写死的；改了设计就改这里。
"""

from __future__ import annotations

import sys
import xml.etree.ElementTree as ET
from collections import defaultdict
from pathlib import Path

# ---------------- 需求常量（改设计就改这里） ----------------
FOOT_MAX = 350.0          # 规则：整体长×宽 ≤ 350×350
HEIGHT_MAX = 450.0        # 规则：整体高 ≤ 450
WORK_SURFACE = 45.0       # 工作台面离地 = 底框 40 + 底板 5
POST_TOP_NEED = 254.0     # 立柱顶面离地（计算值；允许 254~260）
YBEAM_BOTTOM_ABOVE = 155.0  # Y 梁底面到工作台面的净空（夹爪能抬出盒口的硬指标）
RAIL_TRAVEL_NEED = 306.0  # 深向所需行程（托盘前沿货 → 后排盒放置点）
WIDTH_TRAVEL_NEED = 225.0 # 宽向所需行程（左列盒 → 右列盒的货）
CARRIAGE = 45.0           # 滑块/滑座沿导轨占掉的长度（MGN12H ≈ 45）
BOX_OUTER_MAX = 88.0      # 4040 立柱下单盒外宽上限（3 盒 + 2×2mm 缝 ≤ 内净宽 270）
# ------------------------------------------------------------

AXES = "XYZ"


def parse_scene(path: Path) -> list[dict]:
    text = path.read_text(encoding="utf-8", errors="replace")
    try:
        root = ET.fromstring(text)
    except ET.ParseError as error:
        raise RuntimeError("XML 解析失败：%s（这不是 MayCAD 的 .scene？）" % error)
    members = []
    for obj in root.iter("object"):
        if (obj.findtext("type") or "").strip() != "Profile":
            continue
        rot_raw = (obj.findtext("rotation") or "").split(",")
        if len(rot_raw) != 16:
            continue
        try:
            rot = [float(x) for x in rot_raw]
            length = float(obj.findtext("height")) * 10.0
        except (TypeError, ValueError):
            continue
        members.append({
            "id": obj.findtext("id") or "?",
            "profile": obj.findtext("profile") or "?",
            "length": length,
            "rot": rot,
        })
    if not members:
        raise RuntimeError("没解析到任何型材（object/Profile）")
    return members


def geometry(member: dict):
    """返回 (起点, 终点, 轴向单位向量, 包围盒[(lo,hi)×3])。单位 mm。"""
    rot = member["rot"]
    start = [rot[3] * 10.0, rot[7] * 10.0, rot[11] * 10.0]
    axis = [rot[1], rot[5], rot[9]]            # 局部 +Y（杆长方向）的像
    length = member["length"]
    end = [start[i] + axis[i] * length for i in range(3)]
    bbox = []
    for i in range(3):
        lo, hi = min(start[i], end[i]), max(start[i], end[i])
        if abs(axis[i]) < 0.5:                  # 该全局轴与杆长垂直 → 截面撑开 ±20
            lo -= 20.0
            hi += 20.0
        bbox.append((lo, hi))
    return start, end, axis, bbox


def direction(axis) -> str:
    i = max(range(3), key=lambda k: abs(axis[k]))
    if abs(axis[i]) < 0.99:
        return "斜向(非轴向)"
    return {0: "沿X(宽)", 1: "竖直(高)", 2: "沿Z(深)"}[i]


def fmt_point(p) -> str:
    return "(%6.1f, %6.1f, %6.1f)" % (p[0], p[1], p[2])


def report(path: Path) -> None:
    members = parse_scene(path)
    geoms = [(m, *geometry(m)) for m in members]

    print("=" * 78)
    print("文件：%s" % path.name)
    print("=" * 78)

    # ---- 下料表（按 型材+长度 归并） ----
    bom = defaultdict(int)
    for m in members:
        bom[(m["profile"], round(m["length"]))] += 1
    print("\n【下料表】")
    total_len = 0.0
    for (profile, length), count in sorted(bom.items(), key=lambda kv: -kv[0][1]):
        print("  %-18s %5.0f mm × %d 根" % (profile, length, count))
        total_len += length * count
    print("  合计用料 %.2f m（含锯缝建议多买 5%%）" % (total_len / 1000.0))

    # ---- 逐件明细 ----
    print("\n【逐件明细】（起点=该杆参考面中心，单位 mm）")
    for m, start, end, axis, bbox in geoms:
        print("  id=%-3s %-8s 长 %5.0f  起点 %s  终点 %s" % (
            m["id"], direction(axis), m["length"], fmt_point(start), fmt_point(end)))

    # ---- 外廓 ----
    env = [
        (min(g[4][i][0] for g in geoms), max(g[4][i][1] for g in geoms))
        for i in range(3)
    ]
    span = [env[i][1] - env[i][0] for i in range(3)]
    print("\n【整机外廓】")
    print("  宽(X) %.0f × 高(Y) %.0f × 深(Z) %.0f mm" % (span[0], span[1], span[2]))
    print("  X: %.0f..%.0f   Y: %.0f..%.0f   Z: %.0f..%.0f" % (
        env[0][0], env[0][1], env[1][0], env[1][1], env[2][0], env[2][1]))

    # ---- 分类 ----
    posts = [(m, s, e, a, b) for m, s, e, a, b in geoms if abs(a[1]) > 0.99]
    beams_z = [(m, s, e, a, b) for m, s, e, a, b in geoms if abs(a[2]) > 0.99]  # 沿深向
    beams_x = [(m, s, e, a, b) for m, s, e, a, b in geoms if abs(a[0]) > 0.99]  # 沿宽向

    # ---- 校核清单 ----
    print("\n【红线校核】")
    ok = lambda good, msg: print("  %s %s" % ("✅" if good else "❌", msg))
    warn = lambda msg: print("  ⚠️  %s" % msg)

    ok(span[0] <= FOOT_MAX + 0.5 and span[2] <= FOOT_MAX + 0.5,
       "长×宽 %.0f×%.0f ≤ 350×350" % (span[0], span[2]))
    ok(span[1] <= HEIGHT_MAX + 0.5, "总高 %.0f ≤ 450（余量 %.0f）" % (span[1], HEIGHT_MAX - span[1]))

    if posts:
        post_top = max(b[1][1] for *_x, b in posts)
        ok(post_top >= POST_TOP_NEED - 1,
           "立柱顶面离地 %.0f（要求 ≥%.0f，下挂式方案）" % (post_top, POST_TOP_NEED))
        inner_x = span[0] - 80.0
        warn("内净宽 ≈ %.0f（4040 立柱吃 80）→ 单盒外宽须 ≤ %d（3 盒+缝）" % (inner_x, int((inner_x - 4) / 3)))
    else:
        warn("没找到竖直立柱？")

    # ---- 龙门识别与两轴行程（不预设轴朝向，也不被框架件干扰）----
    # 导轨 = 架在框架顶面之上的水平件（Y 底 ≥ 256 → 260~300 那层）
    # Y 梁 = 与导轨垂直、位于工作区之上、且"不贴边"的那根水平件
    # 行程 = 该件长度 − 滑块
    horizontal = [g for g in geoms if abs(g[3][0]) > 0.99 or abs(g[3][2]) > 0.99]
    rails = [g for g in horizontal if g[4][1][0] >= 256.0]
    rail_axis = beam_axis = None
    if rails:
        n_z = sum(1 for g in rails if abs(g[3][2]) > 0.99)
        rail_axis = 2 if n_z * 2 >= len(rails) else 0
        rail_len = max(g[0]["length"] for g in rails)
        print("  导轨：%d 根、沿%s、长 %.0f → 该轴行程 %.0f"
              % (len(rails), "深" if rail_axis == 2 else "宽", rail_len, rail_len - CARRIAGE))
    else:
        warn("没找到架在框架顶面之上（260~300 层）的导轨")

    beam = None
    if rail_axis is not None:
        beam_axis = 0 if rail_axis == 2 else 2
        lo, hi = env[rail_axis]

        def not_at_edge(g) -> bool:
            c = (g[4][rail_axis][0] + g[4][rail_axis][1]) / 2.0
            return (c - lo) > 60.0 and (hi - c) > 60.0

        cands = [g for g in horizontal
                 if abs(g[3][beam_axis]) > 0.99
                 and g[4][1][0] > WORK_SURFACE + 100
                 and not_at_edge(g)]
        beam = max(cands, key=lambda g: g[0]["length"]) if cands else None
    if beam is not None:
        print("  Y 梁：id=%s、沿%s、长 %.0f → 该轴行程 %.0f、底面离地 %.0f"
              % (beam[0]["id"], "深" if beam_axis == 2 else "宽", beam[0]["length"],
                 beam[0]["length"] - CARRIAGE, beam[4][1][0]))
    else:
        warn("没找到 Y 梁（与导轨垂直、位于工作区之上、不贴边的那根）")

    travel_by_axis: dict[int, tuple] = {}
    if rail_axis is not None:
        travel_by_axis[rail_axis] = ("导轨", rail_len - CARRIAGE)
    if beam is not None:
        travel_by_axis[beam_axis] = ("Y 梁", beam[0]["length"] - CARRIAGE)
    for label, ax, need in (("深向（托盘↔盒阵）", 2, RAIL_TRAVEL_NEED),
                            ("宽向（左列↔右列）", 0, WIDTH_TRAVEL_NEED)):
        src, travel = travel_by_axis.get(ax, (None, None))
        if travel is None:
            warn("%s 行程：没有对应构件（导轨/Y 梁）" % label)
            continue
        ok(travel >= need, "%s 行程 %.0f（来自%s）需 ≥%.0f" % (label, travel, src, need))

    if beam is not None:
        ok(beam[4][1][0] - WORK_SURFACE >= YBEAM_BOTTOM_ABOVE - 20,
           "Y 梁底面距台面 %.0f（需 ≥%.0f 左右）" % (beam[4][1][0] - WORK_SURFACE, YBEAM_BOTTOM_ABOVE))

    print("\n提示：盒阵/托盘/夹爪的干涉与行程覆盖，需把它们也建进来后由我按坐标核。")


def main(argv: list[str]) -> int:
    if not argv:
        print(__doc__)
        return 0
    for raw in argv:
        path = Path(raw)
        if not path.is_file():
            print("!! 找不到 %s" % raw)
            continue
        try:
            report(path)
        except Exception as error:
            print("!! %s 校核失败：%s" % (path.name, error))
    return 0


if __name__ == "__main__":
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    sys.exit(main(sys.argv[1:]))
