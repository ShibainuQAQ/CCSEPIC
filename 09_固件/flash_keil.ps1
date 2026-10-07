# flash_keil.ps1 —— 用 Keil uVision 命令行经 ST-Link 烧录（不用拨 BOOT0、不用串口 ISP）
#
# 原理：UV4.exe -f <工程> -o <日志> 就是 uVision 的 "Flash Download"。
#   工程 09_固件/keil/m1.uvprojx 的 OutputDirectory/OutputName 指向 ..\build\m1，
#   所以它烧的正是 build.py 编出来的 m1.axf —— **编译权留在 build.py，烧录借 Keil 的 ST-Link 通道**。
#
# ⚠️ 两个踩过的坑：
#   ① 必须用 Start-Process 调用 UV4.exe。直接 `& UV4.exe` 会因为它是 GUI 子系统程序
#      而**立刻返回**（PowerShell 不等它），拿不到退出码、日志也来不及生成。
#   ② Keil 烧完会把内核留在停止状态，`Application running ...` 并不表示代码真的在跑。
#      所以烧完通常还需要一次硬件复位（hw_bridge 的 serial_reset(mode='dtr')）。
#
# 用法：
#   powershell -File flash_keil.ps1              # 先编译再烧录
#   powershell -File flash_keil.ps1 -NoBuild     # 只烧现有的 build/m1.axf
param(
    [switch]$NoBuild,
    [string]$Keil = "C:\111\keil\UV4\UV4.exe"
)

$ErrorActionPreference = 'Stop'
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$proj = Join-Path $here 'keil\m1.uvprojx'
$log  = Join-Path $here 'build\flash.log'

if (-not $NoBuild) {
    & python (Join-Path $here 'build.py')
    if ($LASTEXITCODE -ne 0) { "构建失败，退出码 $LASTEXITCODE"; exit $LASTEXITCODE }
}
if (-not (Test-Path $proj)) { "找不到 Keil 工程：$proj"; exit 2 }
if (-not (Test-Path $Keil)) { "找不到 UV4.exe：$Keil"; exit 2 }

Remove-Item $log -ErrorAction SilentlyContinue
$p = Start-Process -FilePath $Keil -ArgumentList @('-f', $proj, '-o', $log) -Wait -PassThru
"UV4 退出码 = $($p.ExitCode)"
if (Test-Path $log) { Get-Content $log } else { "（没有生成烧录日志 —— 工程文件打不开？检查是否被写成了带 BOM 的 UTF-8）" }
if ($p.ExitCode -ne 0) { exit $p.ExitCode }
"提示：烧完若代码不跑，用 hw_bridge 的 serial_reset(mode='dtr') 复一次位。"
