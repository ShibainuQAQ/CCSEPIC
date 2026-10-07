#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""一次性把"开源夹爪打印包"搭出来（队内构建脚本，可重复跑）。"""
import shutil
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / '05_工具与校核'))
import stl_tool as st  # noqa: E402
import grip_pad_gen as gp  # noqa: E402

SRC = ROOT / '06_参考与开源' / '参考' / 'SO-ARM夹爪'
PKG = ROOT / '06_参考与开源' / '开源夹爪打印包_20260917'
KIT = SRC / 'models' / 'Follower gripper (165x165 bed size).STL'
SSG = ROOT / '_download' / 'SSG48'

NAMES = {  # (面数, 长, 宽, 高) -> 文件名
    (1804, 128, 60, 24): 'RB9.01.062.010_主框.stl',
    (2902, 101, 34, 52): 'RB9.01.060.074_相机支架.stl',
    (754, 80, 46, 16): 'RB9.01.062.020_夹爪_x2.stl',
    (1650, 17, 48, 37): 'RB9.01.060.080_安装支架.stl',
    (1114, 20, 20, 8): 'RB9.01.062.040_齿轮.stl',
    (824, 38, 38, 2): 'RB9.01.060.090_相机垫片.stl',
    (440, 61, 9, 5): 'RB9.01.062.030_齿条_x2.stl',
}


def main():
    pf = PKG / '01_打印文件'
    single = pf / '单件'
    for d in (PKG, pf, single, PKG / '07_其他开源方案'):
        d.mkdir(parents=True, exist_ok=True)

    # 1) 整版 STL（原厂已摆好、已贴床）
    kit_dst = pf / '整版_SO-ARM夹爪_154x154_全部件.stl'
    shutil.copy2(KIT, kit_dst)
    t = st.read_stl(KIT)
    lab = st.components(t)
    print('整版：%d 件 / %d 面' % (lab.max() + 1, len(t)))

    # 2) 拆单件 + 按真实件名重命名
    rows = []
    for i in range(lab.max() + 1):
        sub = t[lab == i]
        lo, hi = st.bounds(sub)
        size = hi - lo
        key = (len(sub),) + tuple(int(round(v)) for v in size)
        name = NAMES.get(key, '未命名_%dx%dx%d_%d面.stl' % (
            round(size[0]), round(size[1]), round(size[2]), len(sub)))
        if name in rows:
            name = name.replace('.stl', '_b.stl')
        rows.append(name)
        st.write_stl(single / name, sub - lo)     # 平移到原点，Z=0 贴床
    print('单件：' + ', '.join(rows))

    # 3) 补打固定销 ×2（整版里没有）
    nail = SRC / 'models' / 'parts' / 'RB9.01.062.100 Nail.STL'
    st.cmd_plate(str(pf / '补打_固定销x2.stl'), [(str(nail), 2)],
                 bed=(60.0, 60.0), gap=4.0, margin=5.0)

    # 4) 爪垫（锯齿 + 平面）
    gp.write_stl(pf / '爪垫_锯齿_70x14x2.stl', gp.build(70, 14, 2.0, 5, 0.8))
    gp.write_stl(pf / '爪垫_平面_70x14x2.stl', gp.box(0, 70, 0, 14, 0, 2.0))
    print('爪垫：锯齿 + 平面 各 1 件（切片时复制成 2 份）')

    # 5) 摆盘图
    st.cmd_render(str(kit_dst), str(pf / '摆盘_俯视.png'), 'top')
    st.cmd_render(str(kit_dst), str(pf / '摆盘_正视.png'), 'front')
    st.cmd_render(str(pf / '爪垫_锯齿_70x14x2.stl'), str(pf / '爪垫_锯齿_预览.png'), 'top')

    # 6) 其他开源方案（SSG-48 文档，先只带文档+许可，STL 太大按需再拉）
    if SSG.exists():
        dst = PKG / '07_其他开源方案' / 'SSG48自适应夹爪'
        for rel in ['README.md', 'BOM/BOM.md', 'LICENSE', 'SAFETY_WARNING_AND_DISCLAIMER.md',
                    'Assembly manual/Building_cables.md']:
            s = SSG / rel
            if s.exists():
                d = dst / Path(rel).name
                d.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(s, d)
        print('SSG-48 文档已放入 07_其他开源方案')

    total = sum(f.stat().st_size for f in PKG.rglob('*') if f.is_file())
    print('包大小：%d 个文件 / %.2f MB' % (
        len([f for f in PKG.rglob('*') if f.is_file()]), total / 1048576.0))

    # 7) 回头校验：每个 STL 都能重新读出来，且贴床（Z 从 0 起）
    print('---- 校验 ----')
    bad = 0
    for f in sorted(pf.rglob('*.stl')):
        try:
            tt = st.read_stl(f)
            lo, hi = st.bounds(tt)
            size = hi - lo
            flag = '' if abs(lo[2]) < 0.05 else '  ⚠ Z 不贴床'
            if abs(lo[2]) >= 0.05:
                bad += 1
            print('%-46s 面=%-6d %.1f × %.1f × %.1f%s' % (
                f.relative_to(pf).as_posix(), len(tt), size[0], size[1], size[2], flag))
        except Exception as e:
            bad += 1
            print('!! %s : %s' % (f.name, e))
    print('校验失败 %d 个' % bad)
    return 0


if __name__ == '__main__':
    sys.exit(main())
