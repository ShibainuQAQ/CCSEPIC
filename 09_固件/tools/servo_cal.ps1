# servo_cal.ps1 —— 舵机行程标定 / 动作验证工具
#
# 为什么需要它：舵机**没有位置反馈**，两个标定位（Z 的上/下、夹爪的开/合）
# 只能用"发一个值 → 看它转到哪"的方式现场试出来。这个脚本负责按节奏发命令，
# 人只负责看舵机、并在合适的位置喊"停"。
#
# 两种用法：
#   ① 慢速扫掠（第一次上电验证用，最常用）：
#        powershell -File servo_cal.ps1 -Cmd ZPCT -Seq "50,10,90,50"
#      会在 50%→10%→90%→50% 之间**每 5% 一步**慢慢走，方便看清方向与行程。
#   ② 定点逐个走（找端点用）：
#        powershell -File servo_cal.ps1 -Cmd ZPCT -Values 0,5,10,15,20 -GapMs 1200
#
# -Cmd 可选：ZPCT/GPCT（百分比 0~100）、ZSET/GSET（脉宽 500~2500）
#
# ⚠️ 第一次上电请让舵机**空转（不装机构）**：万一脉宽范围不对，它会一路顶到
#    机械限位堵转 —— 又发热又拉垮电源。确认方向与范围都对了再装机。
param(
    [string]$Port = "COM6",
    [int]$Baud = 115200,
    [string]$Cmd = "ZPCT",
    [string]$Seq = "",
    [int[]]$Values = @(),
    [int]$StepPct = 5,
    [int]$GapMs = 400,
    [int]$SettleMs = 600
)

$ErrorActionPreference = 'Stop'

# 把 "50,10,90,50" 展开成每 StepPct 一步的完整序列
$plan = New-Object System.Collections.Generic.List[int]
if ($Seq -ne "") {
    $wp = $Seq -split '[,\s]+' | Where-Object { $_ -ne "" } | ForEach-Object { [int]$_ }
    for ($i = 0; $i -lt $wp.Count - 1; $i++) {
        $a = $wp[$i]; $b = $wp[$i + 1]
        $dir = if ($b -ge $a) { 1 } else { -1 }
        for ($v = $a; ; $v += $dir * $StepPct) {
            if (($dir -gt 0 -and $v -gt $b) -or ($dir -lt 0 -and $v -lt $b)) { break }
            $plan.Add($v)
        }
    }
    $plan.Add($wp[-1])
} elseif ($Values.Count -gt 0) {
    foreach ($v in $Values) { $plan.Add($v) }
} else {
    "必须给 -Seq 或 -Values"; exit 2
}

$sp = New-Object System.IO.Ports.SerialPort $Port, $Baud, ([System.IO.Ports.Parity]::Even), 8, ([System.IO.Ports.StopBits]::One)
$sp.ReadTimeout = 200
$sp.WriteTimeout = 500
$sp.NewLine = "`n"
$sp.Open()
try {
    Start-Sleep -Milliseconds $SettleMs
    $sp.DiscardInBuffer()

    "序列（$Cmd）：" + (($plan | ForEach-Object { $_ }) -join ' → ')
    ""
    foreach ($v in $plan) {
        $sp.Write("$Cmd $v`n")
        Start-Sleep -Milliseconds $GapMs
        try {
            $reply = $sp.ReadLine()
            "{0,6}  {1}" -f $v, $reply.Trim()
        } catch {
            "{0,6}  （没收到应答）" -f $v
        }
    }
    ""
    "扫掠结束。当前应在最后一个值上：$($plan[-1])"
} finally {
    $sp.Close()
    $sp.Dispose()
}
