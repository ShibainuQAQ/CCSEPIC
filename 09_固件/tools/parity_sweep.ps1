# parity_sweep.ps1 —— 固定波特率，扫"校验位/数据位"组合，找能读出干净文本的那一组
#
# 为什么：PC 侧从 100k 扫到 200k 都没有一档能读出干净文本，说明问题不（只）是波特率，
# 而是**帧格式**对不上。最可能的两种情况：
#   ① 固件是 8 数据位 + 偶校验，但 PC 侧实际生效的是别的校验方式；
#   ② 数据位宽理解有分歧 —— 有些 STM32 系列在"使能校验"时会把校验位算进字长
#      （M=0 + PCE=1 → 实际只有 7 个数据位 + 1 个校验位），此时 PC 用 8 数据位
#      去读，就会**把校验位当成第 8 个数据位**，表现为"字符基本认识、
#      但高位时不时多一个 1" —— 这正是我们观察到的现象。
param(
    [string]$Port = "COM6",
    [int]$Baud = 115200,
    [int]$Ms = 1600
)

$ErrorActionPreference = 'Stop'

$cases = @(
    @{ Name = '8N1'; Data = 8; Parity = [System.IO.Ports.Parity]::None },
    @{ Name = '8E1'; Data = 8; Parity = [System.IO.Ports.Parity]::Even },
    @{ Name = '8O1'; Data = 8; Parity = [System.IO.Ports.Parity]::Odd },
    @{ Name = '8M1'; Data = 8; Parity = [System.IO.Ports.Parity]::Mark },
    @{ Name = '8S1'; Data = 8; Parity = [System.IO.Ports.Parity]::Space },
    @{ Name = '7E1'; Data = 7; Parity = [System.IO.Ports.Parity]::Even },
    @{ Name = '7O1'; Data = 7; Parity = [System.IO.Ports.Parity]::Odd }
)

foreach ($c in $cases) {
    $sp = $null
    try {
        $sp = New-Object System.IO.Ports.SerialPort $Port, $Baud, $c.Parity, $c.Data, ([System.IO.Ports.StopBits]::One)
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
        $pct = if ($arr.Length -gt 0) { [math]::Round(100.0 * $printable / $arr.Length, 1) } else { 0 }
        ""
        "===== $($c.Name)  $($arr.Length) 字节  可打印 $printable ($pct%) ====="
        $asc
    } catch {
        ""
        "===== $($c.Name) 打开/读取失败：$($_.Exception.Message) ====="
    } finally {
        if ($sp) { try { $sp.Close(); $sp.Dispose() } catch { } }
    }
}
