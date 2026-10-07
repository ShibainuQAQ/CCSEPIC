#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""量 STL 的外形包围盒 / 数齿数（队内小工具，用于核对参考件尺寸）。

用途：拿到别人的 STL 后先量真实几何，别照着 README 猜
（README 只给整机 128×109×130.5，齿轮模数、齿条行程这类要自己量）。

用法：
  python "参考\\SO-ARM夹爪\\_measure.py" <文件.STL> [...]        # 量外形包围盒（可靠，放心用）
  python "参考\\SO-ARM夹爪\\_measure.py" --teeth <文件.STL> [...] # 顺带数齿（⚠️ 实验性，见下）

⚠️ **--teeth 的齿数/模数结果不可靠，别拿它当依据。**
2026-09-16 在参考件上试过四版算法（数齿顶平台 → 圆心网格搜索+中值穿越 → 按全齿高振幅反推
→ 自相关求节距），全都被"STL 顶点在长度方向稀疏 + 零件带轮毂/凸台把圆心带偏"打歪，得到的
模数从 0.6 到 3.0 乱跳。**要齿数/模数，请在 Fusion 360 里打开 STEP 实量（或数实物）。**
保留这段代码只为把"别再用这种方法"记录在案。

原理（--teeth，仅供参考）：
  * 圆盘件（三个跨度里有两个接近）= 齿轮：齿宽是**最短**那根轴；在齿形平面里网格搜圆心
    （目标 = 各角度最大半径的包络离散度最小），包络振幅 ≈ 全齿高 → m ≈ 全齿高/2.25。
  * 长条件（一个跨度远大于其余）= 齿条：长度方向取极值包络，用自相关求节距 → m = p/π。

"""

from __future__ import annotations

import math
import struct
import sys
from pathlib import Path


def triangles_binary(data: bytes):
    count = struct.unpack("<I", data[80:84])[0]
    if count == 0 or 84 + count * 50 > len(data):
        raise ValueError("不像二进制 STL（三角面数 %d，文件 %d 字节）" % (count, len(data)))
    for index in range(count):
        base = 84 + index * 50 + 12
        for corner in range(3):
            yield struct.unpack("<3f", data[base + corner * 12: base + corner * 12 + 12])


def triangles_ascii(text: str):
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("vertex"):
            parts = line.split()
            yield (float(parts[1]), float(parts[2]), float(parts[3]))


def vertices(path: Path):
    data = path.read_bytes()
    head = data[:512].lstrip()
    if head[:5].lower() == b"solid" and b"facet" in data[:4096].lower():
        return list(triangles_ascii(data.decode("utf-8", "replace"))), "ascii"
    return list(triangles_binary(data)), "binary"


def spans(points):
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    zs = [p[2] for p in points]
    return (min(xs), max(xs)), (min(ys), max(ys)), (min(zs), max(zs))


def bbox(path: Path):
    points, kind = vertices(path)
    if not points:
        print("%-58s 没有读到顶点" % path.name)
        return None
    (x0, x1), (y0, y1), (z0, z1) = spans(points)
    print(
        "%-58s %s  X %7.2f..%7.2f (跨度 %6.2f)  Y %7.2f..%7.2f (%6.2f)  Z %7.2f..%7.2f (%6.2f)"
        % (path.name, kind, x0, x1, x1 - x0, y0, y1, y1 - y0, z0, z1, z1 - z0)
    )
    return points


def runs(values: list[float], gap: float) -> list[list[float]]:
    """把排好序的数值聚成一簇一簇（相邻间隔 > gap 就断开）。"""
    clusters: list[list[float]] = []
    for value in values:
        if clusters and value - clusters[-1][-1] <= gap:
            clusters[-1].append(value)
        else:
            clusters.append([value])
    return clusters


def count_crossings(profile: list[float], threshold: float) -> int:
    """数"向上穿越阈值"的次数 —— 对平台型波形比找局部极大稳。"""
    count = 0
    for index in range(1, len(profile)):
        if profile[index - 1] < threshold <= profile[index]:
            count += 1
    if profile and profile[0] >= threshold:  # 首点就在阈值上，补一次环绕
        count += 1
    return count


def best_pitch(profile: list[float], step: float, pitch_range=(1.5, 9.0)):
    """自相关求周期：返回 (节距 mm, 归一化峰强, 滞后箱数) 或 None。

    比"数平台/数穿越"稳 —— 采样点在长度方向本来就稀疏，靠波形计数容易翻车。
    """
    n = len(profile)
    low = int(round(pitch_range[0] / step))
    high = int(round(pitch_range[1] / step))
    if n < max(32, high * 2):
        return None
    mean = sum(profile) / n
    centered = [v - mean for v in profile]
    energy = sum(v * v for v in centered)
    if energy <= 0:
        return None
    best = None
    for lag in range(max(2, low), min(high, n // 2) + 1):
        total = 0.0
        for i in range(n - lag):
            total += centered[i] * centered[i + lag]
        score = total / energy
        if best is None or score > best[0]:
            best = (score, lag)
    if best is None:
        return None
    return best[1] * step, best[0], best[1]


def teeth_round(points, axis: int) -> None:
    """圆盘件（齿轮）：轴 axis 是**齿宽**方向（最短那根），另两轴构成齿形平面。

    做法：① 网格搜圆心（目标 = "各角度最大半径"包络的离散度最小，绕开轮毂/凸台
    把 bbox 中心带偏）；② 该包络的振幅 = 全齿高 → m = 全齿高/2.25；
    ③ 中值穿越数 = 齿数 z；④ 用 Ø顶 = m(z+2) 交叉验证。
    """
    others = [a for a in (0, 1, 2) if a != axis]
    a0, a1 = others
    pa = [(p[a0], p[a1]) for p in points]
    lo0, hi0 = min(v[0] for v in pa), max(v[0] for v in pa)
    lo1, hi1 = min(v[1] for v in pa), max(v[1] for v in pa)

    def envelope(ca: float, cb: float):
        bins: dict[float, float] = {}
        for va, vb in pa:
            radius = math.hypot(va - ca, vb - cb)
            angle = round(math.degrees(math.atan2(vb - cb, va - ca)) % 360.0)
            if radius > bins.get(angle, -1.0):
                bins[angle] = radius
        if not bins:
            return None
        keys = sorted(bins)
        return keys, [bins[k] for k in keys]

    best = None
    steps = 21
    for i in range(steps):
        ca = lo0 + (hi0 - lo0) * i / (steps - 1)
        for j in range(steps):
            cb = lo1 + (hi1 - lo1) * j / (steps - 1)
            got = envelope(ca, cb)
            if got is None:
                continue
            profile = got[1]
            spread = _percentile(profile, 0.96) - _percentile(profile, 0.04)
            if best is None or spread < best[0]:
                best = (spread, ca, cb)
    if best is None:
        print("    （顶点太少，数不了齿）")
        return
    _, ca, cb = best
    _, profile = envelope(ca, cb)
    r_tip = _percentile(profile, 0.96)
    r_root = _percentile(profile, 0.04)
    amplitude = r_tip - r_root
    crossings = count_crossings(profile, (r_tip + r_root) / 2.0)
    print("    圆心 ≈ (%.2f, %.2f)；齿顶 R%.2f / 齿根 R%.2f → 全齿高 %.2f mm；穿越数 %d"
          % (ca, cb, r_tip, r_root, amplitude, crossings))
    if amplitude <= 0.05:
        print("    （齿高≈0，可能不是齿轮）")
        return
    module = amplitude / 2.25
    print("    ① 由全齿高：m = %.3f mm" % module)
    if crossings >= 5:
        print("    ② 由穿越数 z≈%d 与 Ø顶 %.2f：m = Ø顶/(z+2) = %.3f mm"
              % (crossings, 2 * r_tip, 2 * r_tip / (crossings + 2)))
    pick = round(2 * r_tip / module - 2)
    print("    ③ 取 m=%.2f 时 z = Ø顶/m − 2 = %.1f（应接近整数）" % (module, 2 * r_tip / module - 2))


def _extremal_profile(points, length_axis: int, value_axis: int, direction: int, step: float = 0.25):
    bins: dict[float, float] = {}
    sign = 1.0 if direction > 0 else -1.0
    for p in points:
        key = round(p[length_axis] / step) * step
        value = sign * p[value_axis]
        if value > bins.get(key, -1e9):
            bins[key] = value
    keys = sorted(bins)
    return keys, [sign * bins[k] for k in keys]


def _percentile(values: list[float], q: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, int(round(q * (len(ordered) - 1)))))
    return ordered[index]


def teeth_long(points, axis: int) -> None:
    """长条件（齿条）：轴 axis 是长度方向。

    齿条可能"齿朝上"也可能"齿朝下"，采样点又稀疏，所以把「两个候选面 × 正反两个方向」
    四种包络都算一遍，每种用**自相关**求周期，取峰最强的那一组 → 节距 p → 模数 m = p/π。
    """
    others = [a for a in (0, 1, 2) if a != axis]
    results = []
    for value_axis in others:
        for direction in (1, -1):
            keys, profile = _extremal_profile(points, axis, value_axis, direction)
            if len(keys) < 32:
                continue
            got = best_pitch(profile, 0.25)
            if got is None:
                continue
            pitch, score, lag = got
            amplitude = _percentile(profile, 0.98) - _percentile(profile, 0.02)
            results.append((score, pitch, amplitude, value_axis, direction, keys, profile))
    if not results:
        print("    （顶点太少，数不了齿）")
        return
    results.sort(key=lambda r: r[0], reverse=True)
    for score, pitch, amplitude, value_axis, direction, keys, profile in results:
        axis_name = "XYZ"[value_axis]
        face = "齿朝 +%s" % axis_name if direction > 0 else "齿朝 −%s" % axis_name
        print("    %s：自相关峰强 %.2f（越接近 1 越可信）、振幅 %.2f mm" % (face, score, amplitude))
    score, pitch, amplitude, value_axis, direction, keys, profile = results[0]
    module = pitch / math.pi
    span = keys[-1] - keys[0]
    print("    → 取最强的一组：节距 p = %.3f mm → 模数 m = %.3f mm；齿区 %.2f mm → 齿数 ≈ %.1f"
          % (pitch, module, span, span / pitch))


def measure(path: Path, want_teeth: bool) -> None:
    points = bbox(path)
    if not points or not want_teeth:
        return
    (x0, x1), (y0, y1), (z0, z1) = spans(points)
    sizes = [x1 - x0, y1 - y0, z1 - z0]
    longest = max(range(3), key=lambda a: sizes[a])
    rest = sorted(sizes, reverse=True)[1]
    if rest / max(sizes[longest], 1e-9) > 0.6:
        # 圆盘件：齿宽方向是**最短**那根轴（齿轮 8mm 厚、Ø20 —— 最长的是直径，不是齿宽）
        teeth_round(points, min(range(3), key=lambda a: sizes[a]))
    else:
        teeth_long(points, longest)


def main(argv: list[str]) -> int:
    want_teeth = "--teeth" in argv
    files = [a for a in argv if a != "--teeth"]
    if not files:
        print(__doc__)
        return 0
    for raw in files:
        path = Path(raw)
        if not path.is_file():
            print("!! 找不到 %s" % path)
            continue
        try:
            measure(path, want_teeth)
        except Exception as error:
            print("!! %s 解析失败：%s" % (path.name, error))
    return 0


if __name__ == "__main__":
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    sys.exit(main(sys.argv[1:]))
