#!/usr/bin/env python3
"""M1 固件构建脚本 —— 用 Keil MDK 自带的 armclang 工具链，不需要 make。

为什么自己写构建脚本：
  ① 本机没有装 make；
  ② 命令行构建让 AI 能自己编译、自己看报错、自己迭代（不用人开 GUI 点按钮）；
  ③ 产物 .bin 可以直接喂给 hw_bridge 的 mcu_flash 烧录 —— 形成"编译→烧录→自测"闭环。

用法：
    python build.py            编译 + 链接，产出 build/m1.elf 与 build/m1.bin
    python build.py clean      删除 build/

工具链位置（2026-09-21 实测确认存在且无许可限制）：
    C:\\111\\keil\\ARM\\ARMCLANG\\bin\\{armclang,armlink,fromelf}.exe
头文件来自器件包：
    Keil.STM32F1xx_DFP 2.4.1  →  Device/Include/stm32f10x.h
    ARM::CMSIS 5.9.0          →  CMSIS/Core/Include/core_cm3.h
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
BUILD = HERE / "build"

KEIL_BIN = Path(r"C:\111\keil\ARM\ARMCLANG\bin")
PACKS = Path(os.environ.get("LOCALAPPDATA", "")) / "Arm" / "Packs"
DFP_DEVICE = PACKS / "Keil" / "STM32F1xx_DFP" / "2.4.1" / "Device"
CMSIS_CORE = PACKS / "ARM" / "CMSIS" / "5.9.0" / "CMSIS" / "Core" / "Include"

TARGET = "m1"
SOURCES = ["src/startup.c", "src/board.c", "src/main.c"]

CFLAGS = [
    "--target=arm-arm-none-eabi",
    "-mcpu=cortex-m3",
    "-c",
    "-O1",
    "-g",
    "-Wall",
    # ⚠️ 2026-10-04：**板子从 RCT6 换成了 C8T6**（原来的 RCT6 因接线短路烧了 ✗）。
    #    RCT6 = 高容量 → -DSTM32F10X_HD（256KB flash + 48KB RAM，有 UART4/5）
    #    C8T6 = 中容量 → -DSTM32F10X_MD（64KB flash + 20KB RAM，**没有 UART4/5、没有 PC0~PC12**）
    #    要换回 RCT6：把这行改回 HD，并把 m1.scf / startup.c 的栈顶 / 引脚表一起改回（见下）。
    "-DSTM32F10X_MD",
    "-I", str(DFP_DEVICE / "Include"),
    "-I", str(CMSIS_CORE),
    "-I", str(HERE / "src"),
]

LDFLAGS = [
    "--cpu=Cortex-M3",
    "--scatter", str(HERE / "m1.scf"),
    "--entry=Reset_Handler",
    "--info", "sizes",
]


def run(cmd: list) -> None:
    """跑一条命令。刻意**不捕获输出**（子进程用管道在本机沙箱下不可靠），
    让编译器直接写到控制台，我们再根据返回码判断成败。"""
    print("\n> " + " ".join(str(part) for part in cmd), flush=True)
    result = subprocess.run([str(part) for part in cmd], check=False)
    if result.returncode != 0:
        print("\n!! 构建失败（退出码 %d）" % result.returncode, flush=True)
        sys.exit(result.returncode)


def main() -> int:
    # 控制台默认是 GBK，中文会乱码、emoji 直接抛 UnicodeEncodeError（曾因此让
    # 构建"看起来失败"而其实 .bin 已经生成）。统一按 UTF-8 输出。
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

    if len(sys.argv) > 1 and sys.argv[1] == "clean":
        shutil.rmtree(BUILD, ignore_errors=True)
        print("已清理 %s" % BUILD)
        return 0

    for tool in ("armclang.exe", "armlink.exe", "fromelf.exe"):
        if not (KEIL_BIN / tool).is_file():
            print("找不到编译器：%s" % (KEIL_BIN / tool))
            return 2
    for path in (DFP_DEVICE, CMSIS_CORE):
        if not path.is_dir():
            print("找不到器件包头文件目录：%s" % path)
            return 2

    BUILD.mkdir(parents=True, exist_ok=True)

    objects = []
    for source in SOURCES:
        obj = BUILD / (Path(source).stem + ".o")
        run([KEIL_BIN / "armclang.exe"] + CFLAGS + [str(HERE / source), "-o", str(obj)])
        objects.append(obj)

    # 产物后缀刻意用 .axf（而不是 .elf）：Keil 的 Flash Download（UV4 -f）会去
    # <OutputDirectory>\<OutputName>.axf 找可执行文件。用 .axf 就能让 Keil 直接
    # 烧我们 build.py 编出来的东西 —— 编译权在自己手里，烧录借用 Keil+ST-Link。
    elf = BUILD / (TARGET + ".axf")
    run([KEIL_BIN / "armlink.exe"] + LDFLAGS + [str(o) for o in objects] + ["-o", str(elf)])

    binf = BUILD / (TARGET + ".bin")
    run([KEIL_BIN / "fromelf.exe", "--bin", "--output", str(binf), str(elf)])

    size = binf.stat().st_size
    print("\n===== 构建成功 =====")
    print("  AXF : %s（Keil 烧录用这个）" % elf)
    print("  BIN : %s（%d 字节，%.1f KB）" % (binf, size, size / 1024.0))
    print("  ST-Link 烧录: pwsh -File flash_keil.ps1")
    print("  串口 ISP 烧录: mcu_flash(path=r'%s', erase='pages', verify=True, go=True)" % binf)
    return 0


if __name__ == "__main__":
    sys.exit(main())
