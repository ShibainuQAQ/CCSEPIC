# servo_jog.ps1 —— 交互式点动：你自己一条一条走，走到机械限位就停手
#
# 为什么需要「你自己按」：标定行程端点必须**在撞到限位的那一刻停下来**。
# 由 AI 来发命令的话，命令发出去到你看清结果之间总有延迟，很容易一路顶到限位堵转。
# 这个脚本让你自己掌握节奏。
#
# 用法（在 PowerShell 窗口里）：
#     cd C:\Users\22431\Desktop\工创赛智能分拣项目\09_固件\tools
#     powershell -ExecutionPolicy Bypass -File .\servo_jog.ps1 -Cmd ZSET
#     powershell -ExecutionPolicy Bypass -File .\servo_jog.ps1 -Cmd GSET
#     powershell -ExecutionPolicy Bypass -File .\servo_jog.ps1 -Cmd ZPCT -Step 2
#
# 步长会按命令自动选：ZPCT/GPCT（百分比）默认 5%，ZSET/GSET（脉宽µs）默认 50µs。
#
# 交互指令：
#     回车         → 按当前步长往 + 走一步
#     减号 -       → 按当前步长往 - 走一步
#     数字         → 直接跳到那个值（如 1700）
#     s 数字       → 改步长（如 s 20）
#     q            → 退出（退出前请把舵机停在一个安全的中间值）
#
# 注意：中文串里**不能出现 ASCII 直引号**，会把字符串提前截断导致 ParserError。
param(
    [string]$Port = "COM6",
    [int]$Baud = 115200,
    [ValidateSet("ZPCT", "GPCT", "ZSET", "GSET")]
    [string]$Cmd = "ZPCT",
    [int]$Step = 0,
    [int]$Start = 0
)

$ErrorActionPreference = 'Stop'

$isPct = $Cmd.EndsWith("PCT")
if ($isPct) { $lo = 0;   $hi = 100;  $def = 50 }
else        { $lo = 500; $hi = 2500; $def = 1500 }

if ($Step -le 0) { $Step = if ($isPct) { 5 } else { 50 } }
if ($Start -le 0) { $Start = $def }
$cur = [Math]::Max($lo, [Math]::Min($hi, $Start))

$sp = New-Object System.IO.Ports.SerialPort $Port, $Baud, ([System.IO.Ports.Parity]::Even), 8, ([System.IO.Ports.StopBits]::One)
$sp.ReadTimeout = 400
$sp.WriteTimeout = 500
$sp.NewLine = "`n"
$sp.Open()

function Send-Val([int]$v) {
    $script:cur = [Math]::Max($lo, [Math]::Min($hi, $v))
    $sp.Write("$Cmd $script:cur`n")
    Start-Sleep -Milliseconds 250
    $r = ""
    try { $r = $sp.ReadLine().Trim() } catch { $r = "(no reply)" }
    "{0,6}   {1}" -f $script:cur, $r
}

try {
    Start-Sleep -Milliseconds 500
    $sp.DiscardInBuffer()

    ""
    "通道 = $Cmd   范围 = $lo ~ $hi   步长 = $Step"
    "走到机构顶住/嗡嗡响之前就停手，记下那个数。输入 q 退出。"
    ""
    Send-Val $cur

    while ($true) {
        $raw = Read-Host "[$cur] 回车=+$Step  减号=-$Step  数字=跳转  s数字=改步长  q=退出"
        if ($null -eq $raw) { ""; "（输入结束，安全退出）"; break }
        $line = $raw.Trim()

        if ($line -eq 'q') { break }
        elseif ($line -eq '') { Send-Val ($cur + $Step) }
        elseif ($line -eq '-') { Send-Val ($cur - $Step) }
        elseif ($line -match '^[sS]\s*(\d+)$') { $Step = [int]$Matches[1]; "步长改为 $Step" }
        elseif ($line -match '^\d+$') { Send-Val ([int]$line) }
        else { "看不懂：$line  （回车=加 / 减号=减 / 数字=跳转 / s数字=改步长 / q=退出）" }
    }

    ""
    "退出时停在：$cur"
    "把这个数、以及机构顶住时的两个数，一起发给我，我写进固件常量。"
} finally {
    $sp.Close()
    $sp.Dispose()
}