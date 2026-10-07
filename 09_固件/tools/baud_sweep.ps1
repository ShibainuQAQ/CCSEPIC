# baud_sweep.ps1 —— PC 侧扫波特率，反推板子真实主频
#
# 思路：固件正在以固定的 BRR=69 每秒重复输出一行已知字符串（心跳）。
# 逐个 PC 波特率去听，**哪一档能读出干净的 ASCII，真实波特率就是它**，
# 于是 f_cpu ≈ 69 × 该波特率（因为 BRR = f_cpu / baud）。
#
# 为什么不在板子侧扫：板子侧的 BRR 扫描要靠"复位才讲一次话"，而这块板的复位通路
# 一直不可靠；PC 侧扫描只需要板子持续说话，任何时候都能测。
#
# 用法：
#   powershell -File baud_sweep.ps1                       # 默认 100k~200k 粗扫
#   powershell -File baud_sweep.ps1 -From 140000 -To 160000 -Step 500
param(
    [string]$Port = "COM6",
    [int]$From = 100000,
    [int]$To = 200000,
    [int]$Step = 2500,
    [int]$Ms = 1300
)

$ErrorActionPreference = 'Stop'
$results = @()

for ($baud = $From; $baud -le $To; $baud += $Step) {
    $sp = $null
    try {
        $sp = New-Object System.IO.Ports.SerialPort $Port, $baud, ([System.IO.Ports.Parity]::Even), 8, ([System.IO.Ports.StopBits]::One)
        $sp.ReadTimeout = 60
        $sp.WriteTimeout = 300
        $sp.Open()
        Start-Sleep -Milliseconds 120

        $bytes = New-Object System.Collections.Generic.List[byte]
        $buf = New-Object byte[] 4096
        $deadline = (Get-Date).AddMilliseconds($Ms)
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
        $arr = $bytes.ToArray()
        $printable = ($arr | Where-Object { $_ -ge 32 -and $_ -lt 127 }).Count
        $asc = -join ($arr | ForEach-Object { if ($_ -ge 32 -and $_ -lt 127) { [char]$_ } else { '.' } })
        $results += [pscustomobject]@{ Baud = $baud; Bytes = $arr.Length; Printable = $printable; Ascii = $asc }
    } catch {
        $results += [pscustomobject]@{ Baud = $baud; Bytes = -1; Printable = -1; Ascii = "打开失败: $($_.Exception.Message)" }
    } finally {
        if ($sp) { try { $sp.Close(); $sp.Dispose() } catch { } }
    }
}

"===== 按可打印 ASCII 数量排序（越多说明波特率越接近） ====="
$results | Sort-Object Printable -Descending | Select-Object -First 10 | ForEach-Object {
    "{0,7} baud  {1,5} 字节  可打印 {2,5}" -f $_.Baud, $_.Bytes, $_.Printable
}
""
"===== 前 3 名的实际内容 ====="
$results | Sort-Object Printable -Descending | Select-Object -First 3 | ForEach-Object {
    "--- $($_.Baud) baud ---"
    $_.Ascii
}
