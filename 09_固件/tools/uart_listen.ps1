# uart_listen.ps1 —— 带时间戳的"全收"串口监听（诊断固件的 PROBE 自检）
#
# 与 uart_raw.ps1 的区别：
#   ① **从头到尾一个字节都不丢**：打开端口后立刻开始收，绝不清接收缓冲
#      （之前那版在复位后 DiscardInBuffer，正好把固件复位后立刻吐的 banner 丢掉）
#   ② 每段数据都带 "+xx ms" 时间戳 —— 固件的 9 条 PROBE 之间有明显间隔，
#      靠时间戳就能把它们一段段切开，从而知道"哪一条 BRR 是可读的"
#   ③ 固定间隔给出 ASCII 渲染，肉眼直接看哪一段是正常文字
#
# 固件侧对应代码：09_固件/src/main.c 的 PROBE 段（BRR = 55/60/65/69/75/80/85/89/95）
# 结论判读：哪一条 "PROBE brr=NN" 在 PC 上读得通，就说明真实 f_cpu ≈ 115200 × NN。
param(
    [string]$Port = "COM6",
    [int]$Baud = 115200,
    [int]$Ms = 25000,
    [switch]$NoReset,
    [int]$HoldMs = 150
)

$ErrorActionPreference = 'Stop'

$sp = New-Object System.IO.Ports.SerialPort $Port, $Baud, ([System.IO.Ports.Parity]::Even), 8, ([System.IO.Ports.StopBits]::One)
$sp.ReadTimeout = 100
$sp.WriteTimeout = 500
$sp.Open()
$sw = [Diagnostics.Stopwatch]::StartNew()
try {
    if (-not $NoReset) {
        # 复位脉冲：这里两个方向都翻一遍，避免搞错极性
        $sp.DtrEnable = $true; Start-Sleep -Milliseconds $HoldMs
        $sp.DtrEnable = $false; Start-Sleep -Milliseconds $HoldMs
        $sp.DtrEnable = $true
    }

    $all = New-Object System.Collections.Generic.List[byte]
    $buf = New-Object byte[] 4096
    $deadline = (Get-Date).AddMilliseconds($Ms)
    while ((Get-Date) -lt $deadline) {
        try {
            $n = $sp.Read($buf, 0, $buf.Length)
            if ($n -gt 0) {
                $chunk = New-Object byte[] $n
                [Array]::Copy($buf, $chunk, $n)
                $all.AddRange($chunk)
                $hex = ($chunk | ForEach-Object { $_.ToString('X2') }) -join ' '
                $asc = -join ($chunk | ForEach-Object { if ($_ -ge 32 -and $_ -lt 127) { [char]$_ } else { '.' } })
                "[+{0,6} ms] {1,4}B  {2}" -f $sw.ElapsedMilliseconds, $n, $hex
                "              | $asc"
            }
        } catch [TimeoutException] { }
    }

    $arr = $all.ToArray()
    $printable = ($arr | Where-Object { $_ -ge 32 -and $_ -lt 127 }).Count
    ""
    "===== 共 $($arr.Length) 字节，可打印 ASCII $printable ====="
    "---- 完整 ASCII ----"
    -join ($arr | ForEach-Object { if ($_ -ge 32 -and $_ -lt 127) { [char]$_ } else { '.' } })
} finally {
    $sp.Close()
    $sp.Dispose()
}
