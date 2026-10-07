# uart_raw.ps1 —— 原始串口抓包（不经过 hw_bridge 的行切分逻辑）
#
# 为什么需要它：hw_bridge 的 serial_read 会按 '\n' 切行，且 pending 预览只给前 64 字节；
# 排查"固件输出乱码"需要完整未处理的字节流。本脚本直接用 .NET SerialPort：
#   ① 复位（DTR 翻转 = 自动 ISP 电路的复位脉冲）
#   ② 可发任意字节：-Send <hex>，或用 -Go 发 AN3155 的 Go 跳进 App
#      （BOOT0=1 时复位只进 ROM bootloader，必须用 Go 才跑应用）
#   ③ 原样打印收到的每个字节（hex + ASCII）
#
# 用法：
#   powershell -File uart_raw.ps1 -Baud 115200 -Ms 2500 -Go
#   powershell -File uart_raw.ps1 -Send 7F -Ms 500          # 只探测 bootloader 是否在
#   powershell -File uart_raw.ps1 -NoReset -Ms 1500          # 纯听
param(
    [string]$Port = "COM6",
    [int]$Baud = 115200,
    [int]$Ms = 2000,
    [switch]$Go,
    [string]$Send = "",
    [switch]$NoReset,
    [int]$HoldMs = 120,
    [int]$SettleMs = 400
)

$ErrorActionPreference = 'Stop'

$sp = New-Object System.IO.Ports.SerialPort $Port, $Baud, ([System.IO.Ports.Parity]::Even), 8, ([System.IO.Ports.StopBits]::One)
$sp.ReadTimeout = 100
$sp.WriteTimeout = 500
$sp.Open()
try {
    Start-Sleep -Milliseconds $SettleMs       # 端口刚打开要等一下，CH340K 才稳定
    $sp.DiscardInBuffer()

    if (-not $NoReset) {
        # ⚠️ 复位后**绝对不能**再 DiscardInBuffer：固件一复位就在几十微秒内吐 banner，
        #    之前那版脚本在复位后清空接收缓冲，正好把 banner 整个丢掉 —— 症状是
        #    "板子明明在跑，却永远收到 0 字节"，白白误导了一轮排查。
        $sp.DtrEnable = $false
        Start-Sleep -Milliseconds $HoldMs
        $sp.DtrEnable = $true                  # 翻转 = 复位脉冲（自动 ISP 电路边沿有效）
    }

    $payload = @()
    if ($Send -ne "") {
        $payload = $Send -split '[ ,]' | Where-Object { $_ -ne "" } | ForEach-Object { [Convert]::ToByte($_, 16) }
    } elseif ($Go) {
        # AN3155：0x7F 同步(→0x79 ACK)；0x21 Go + 补码 0xDE + 地址 0x08000000(高位在前) + 校验和 0x08
        $payload = @(0x7F, 0x21, 0xDE, 0x08, 0x00, 0x00, 0x00, 0x08)
    }
    if ($payload.Count -gt 0) {
        if ($Go) { Start-Sleep -Milliseconds $SettleMs }   # 等 ROM bootloader 起来
        [byte[]]$bytesToSend = $payload
        $sp.Write($bytesToSend, 0, $bytesToSend.Length)
    }

    $bytes = New-Object System.Collections.Generic.List[byte]
    $deadline = (Get-Date).AddMilliseconds($Ms)
    $buf = New-Object byte[] 4096
    while ((Get-Date) -lt $deadline) {
        try {
            $n = $sp.Read($buf, 0, $buf.Length)
            if ($n -gt 0) {
                # PS 5.1 下 $buf[0..($n-1)] 会退化成 Object[]，AddRange 不认 → 显式拷成 byte[]
                $chunk = New-Object byte[] $n
                [Array]::Copy($buf, $chunk, $n)
                $bytes.AddRange($chunk)
            }
        } catch [TimeoutException] { }
    }

    $arr = $bytes.ToArray()
    $printable = ($arr | Where-Object { $_ -ge 32 -and $_ -lt 127 }).Count
    "端口=$Port 波特率=$Baud 8E1 复位=$(-not $NoReset) 发送=$($payload.Count)字节 收到=$($arr.Length)字节 可打印=$printable"
    if ($arr.Length -gt 0) {
        "---- hex ----"
        ($arr | ForEach-Object { $_.ToString('X2') }) -join ' '
        "---- ascii ----"
        -join ($arr | ForEach-Object { if ($_ -ge 32 -and $_ -lt 127) { [char]$_ } else { '.' } })
    }
} finally {
    $sp.Close()
    $sp.Dispose()
}
