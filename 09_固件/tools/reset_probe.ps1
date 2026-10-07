# reset_probe.ps1 —— 用"能不能唤醒 App"来反推哪种复位动作真的有效
#
# 背景：这块 CH340K 最小系统板的自动 ISP 电路到底把 DTR/RTS 接到 NRST 还是 BOOT0、
# 是电平有效还是边沿有效，文档说法互相矛盾。与其猜，不如把 6 种动作都试一遍，
# 谁能让 App 重新启动（收到字节）谁就是对的。
# 前提：BOOT0=0，复位后直接跑 App，App 每次启动都会打印 banner。
param(
    [string]$Port = "COM6",
    [int]$Baud = 115200,
    [int]$Ms = 1500,
    [int]$HoldMs = 150
)

$ErrorActionPreference = 'Stop'

$sp = New-Object System.IO.Ports.SerialPort $Port, $Baud, ([System.IO.Ports.Parity]::Even), 8, ([System.IO.Ports.StopBits]::One)
$sp.ReadTimeout = 100
$sp.WriteTimeout = 500
$sp.Open()

function Read-For([int]$ms) {
    $sp.DiscardInBuffer()
    $bytes = New-Object System.Collections.Generic.List[byte]
    $deadline = (Get-Date).AddMilliseconds($ms)
    $buf = New-Object byte[] 4096
    while ((Get-Date) -lt $deadline) {
        try {
            $n = $sp.Read($buf, 0, $buf.Length)
            if ($n -gt 0) {
                $chunk = New-Object byte[] $n
                [Array]::Copy($buf, $chunk, $n)
                $bytes.AddRange($chunk)
            }
        } catch [TimeoutException] { }
    }
    return $bytes.ToArray()
}

$actions = @(
    @{ Name = 'DTR 低->高'; Before = @{ D = $false; R = $null }; After = @{ D = $true;  R = $null } },
    @{ Name = 'DTR 高->低'; Before = @{ D = $true;  R = $null }; After = @{ D = $false; R = $null } },
    @{ Name = 'RTS 低->高'; Before = @{ D = $null; R = $false }; After = @{ D = $null; R = $true  } },
    @{ Name = 'RTS 高->低'; Before = @{ D = $null; R = $true  }; After = @{ D = $null; R = $false } },
    @{ Name = 'DTR+RTS 低->高'; Before = @{ D = $false; R = $false }; After = @{ D = $true;  R = $true  } },
    @{ Name = 'DTR+RTS 高->低'; Before = @{ D = $true;  R = $true  }; After = @{ D = $false; R = $false } }
)

try {
    Start-Sleep -Milliseconds 400
    [void](Read-For 300)

    foreach ($a in $actions) {
        # 摆到该动作的起始电平并稳定
        if ($null -ne $a.Before.D) { $sp.DtrEnable = $a.Before.D }
        if ($null -ne $a.Before.R) { $sp.RtsEnable = $a.Before.R }
        Start-Sleep -Milliseconds 250
        [void](Read-For 200)

        # 翻转
        if ($null -ne $a.After.D) { $sp.DtrEnable = $a.After.D }
        if ($null -ne $a.After.R) { $sp.RtsEnable = $a.After.R }
        Start-Sleep -Milliseconds $HoldMs

        $arr = Read-For $Ms
        $printable = ($arr | Where-Object { $_ -ge 32 -and $_ -lt 127 }).Count
        $head = if ($arr.Length -gt 0) { (($arr | Select-Object -First 12) | ForEach-Object { $_.ToString('X2') }) -join ' ' } else { '' }
        "{0,-14} 收到 {1,5} 字节  可打印 {2,5}   前12字节: {3}" -f $a.Name, $arr.Length, $printable, $head
    }
} finally {
    $sp.Close()
    $sp.Dispose()
}
