# 线路 C → 线路 A 交接文档（STM32 固件 + 硬件联调）

> **交接时间**：2026-09-22
> **从**：线路 C（临时测试会话，用户指派"负责测试的分支"）
> **到**：线路 A（控制系统接入 + 摄像头，`hw_bridge` 的所有者）
> **原因**：线路 C 会话上下文过长、界面变卡，用户要求移交。
>
> **这份文档是自包含的** —— 不需要读线路 C 的对话记录就能接手。
> 项目级背景仍以 `..\Claude接续日志.md` 为准（线路 C 的完整结案记录在文末「🎯 / 🔧 / Z 轴行程标定结论」三节）。

---

## 0. 一句话现状

**M1 握手固件已验收通过；M2 舵机侧全通、Z 轴行程已标定、**入盒红外单路验证通过**（10 对已到货，剩 5 路未接）；**中断路径已打通（M3 前置解除）**；M3/M4 未开工。当前 fw = **0.2.2**。**

---

## 1. 最短上手路径（5 分钟）

```powershell
# ① 编译 + 经 ST-Link 烧录（不用拨 BOOT0）
powershell -File C:\Users\22431\Desktop\工创赛智能分拣项目\09_固件\flash_keil.ps1
```

```
# ② 串口验证（用 hw_bridge 的 MCP 工具）
serial_open(port='COM6', baud=115200, parity='E', dtr=false, rts=false)
serial_cmd('PING')      → OK pong
serial_cmd('VER')       → OK fw=0.2.0 proto=1
serial_cmd('STATUS')    → OK state=IDLE_UNHOMED homed=0 estop=0 box=000000 pos=0,0,0,0
serial_close()          # 用完记得关，串口是"一个进程一条连接"
```

**硬件事实**

| 项 | 值 |
|---|---|
| 主控 | **STM32F103RCT6** 最小系统板（DEV_ID `0x0414` = 高密度，真片） |
| 板载 USB-TTL | **CH340K**（SOP-8，VID:PID `1A86:7522`）接 **USART1 (PA9/PA10)** → 枚举为 **COM6** |
| 跳线 | **BOOT0 = 0**（串口 ISP 已不是必需路径；保持 0 让复位直接跑 App） |
| 烧录器 | ST-Link 接板上 `SWD接口`；**3.3V 不要接**（避免与板载供电打架） |
| Keil | `C:\111\keil\`，命令行 `C:\111\keil\UV4\UV4.exe` |
| 舵机 | **DS3225 270°** ×2（Z 轴 → PB0；夹爪 → PB1），**必须独立 5~6V 供电并与主控共地** |

---

## 2. 产物清单（都在 `09_固件\`）

| 路径 | 说明 | 改动注意 |
|---|---|---|
| `src\startup.c` | 向量表（段名 `RESET`，被 `m1.scf` 钉在 0x08000000）+ Reset_Handler + 手写 RW 拷贝/ZI 清零 | ⚠️ **向量表下标 = 16 + IRQn**；SysTick 在 index **15** |
| `src\board.c` / `board.h` | 时钟、USART1、SysTick、**TIM3 舵机 PWM**、**PC0~PC5 入盒扫描** | 开关都在文件头 |
| `src\main.c` | 命令解析、状态机、错误码、主循环（含入盒消抖与 `BOX_CHANGED` 事件） | `FW_VERSION` 在这里 |
| `src\boot_marker.h` | RAM 启动脚印地址表（诊断用，见 §5） | |
| `m1.scf` | 分散加载（flash 0x08000000 / RAM 0x20000000，栈顶 0x2000C000） | |
| `build.py` | 命令行构建（Keil 自带 `armclang/armlink/fromelf`，本机无 make）→ `build\m1.axf` + `m1.bin` | 产物后缀必须是 `.axf`（Keil 烧录只认它） |
| `keil\m1.uvprojx` + `m1.uvoptx` | Keil 工程（器件 STM32F103RC / ST-Link / `STM32F10x_512.FLM`） | ⚠️⚠️ **绝对不能带 UTF-8 BOM**（见 §4.2） |
| `flash_keil.ps1` | 一键"编译 → 烧录" | 用 `Start-Process -Wait` 调 UV4 |
| `tools\*.ps1` | 诊断/标定脚本（见 §3） | 含中文的 .ps1 **必须带 BOM** |
| `固件设计-协议与引脚.md` | **协议 v1.0 + 引脚表 + 里程碑 + 安全状态机**（权威） | 已更新到 fw 0.2.0 |

---

## 3. 诊断/标定工具（`09_固件\tools\`，全部已过语法检查）

| 脚本 | 用途 |
|---|---|
| `servo_jog.ps1` | **交互式点动**：回车=+步长、`-`=减、数字=跳转、`s N`=改步长、`q`=退出。标定行程端点用（自己掌握节奏，走到顶住就停手） |
| `servo_cal.ps1` | 按序列扫掠：`-Cmd ZSET -Values 1500,1100,1500` 或 `-Seq "50,10,90,50"` |
| `parity_sweep.ps1` | 一次扫「数据位 × 校验位」7 种组合 —— **当初定 8E1 真凶就是靠它** |
| `uart_listen.ps1` / `uart_raw.ps1` | 原始抓包（不经过桥的行切分逻辑，`uart_listen` 带时间戳） |
| `baud_sweep.ps1` | PC 侧扫波特率，反推板子真实主频 |

> **为什么这些脚本不走桥**：桥的 `serial_read` 按 `\n` 切行，二进制/乱码流会看不出结构。

---

## 4. 必读的 7 个坑（全是花时间换来的，别重走）

### 4.1 ⭐ 8E1 的真凶：`USART_CR1.M` 是"**含校验位在内**"的字长

| 配置 | 实际发出 |
|---|---|
| `M=0 + PCE=0` | 8 数据位 |
| `M=0 + PCE=1` | **7 数据位 + 1 校验位** ← 原来就是这个，还自以为在发 8E1 |
| `M=1 + PCE=1` | 8 数据位 + 1 校验位 ✅ |

PC 按 8E1 读 7+1，就把**校验位当成第 8 个数据位**。

**症状极具欺骗性**：帧同步完全正常（`\r\n` 原样、字符个数一个不差）、字符大体认得出来、只是**高位时不时多个 1** → 看着像干扰，其实是格式错。
**一句话判据**：把 PC 侧改成 **7E1** 一读就全对。（`parity_sweep.ps1` 就是干这个的。）
⚠️ 桥的 `serial_open` **只有 `parity`，没有 `data_bits`** —— 建议顺手补上，这类问题以后再遇到能一键定位。

### 4.2 Keil 工程文件**不能带 UTF-8 BOM**

带 BOM 时 `UV4 -f` **静默失败**：退出码 15、**连 `-o` 日志都不生成**，极难联想到 BOM。
对照：正确文件头 3 字节 `60 63 78`（`<?x`）；被 PowerShell 的 `Set-Content -Encoding utf8` 写过会变 `EF BB BF`。
（Windows PowerShell 5.1 的 `-Encoding utf8` 是**带 BOM** 的；要用 `[IO.File]::WriteAllText($p,$t,(New-Object Text.UTF8Encoding($false)))`。）

### 4.3 调 UV4 必须 `Start-Process -Wait`

`& UV4.exe ...` 对 GUI 子系统程序**立刻返回**（拿不到退出码，日志也来不及生成）。
Keil 烧完还会把内核留在停止态，`Application running ...` **不代表代码真在跑**。

### 4.4 打开串口会让板子复位 —— 所以"安全位"必须选**任何机构都安全**的位置

这块 CH340K 板**每次 `serial_open` 都会复位 MCU**。
我们把安全位写成"Z 抬到最高 = 2500µs"，结果**每开一次串口就把机构顶一次机械限位**，越顶越死。
→ **正解：安全位取中位 1500µs**（中位在任何机构上都安全），等行程方向拍板后再改。
> **教训：安全位要选"在任何机构上都安全"的位置，而不是"语义上正确"的位置。**

### 4.5 复位：只有 `dtr` 有效，`rts` 完全无效

- `serial_reset(mode='rts')` 对这块板**没有任何效果**（脉冲后不产生任何输出、也没有 banner）。
- `serial_reset(mode='dtr')` 才能复位。
- **DTR 的静态电平无所谓**：`serial_open(dtr:false)` 和 `dtr:true` 实测**都能正常收发**（旧文档里"DTR/RTS 会把 MCU 按在复位态"是误判）。

### 4.6 CH340 会掉驱动 → **僵句柄**

USB 拔插后 COM6 可能变成"注册表里还在、但打不开"（`Device COM6 is not currently available` / `OSError(22) Incorrect function`），
而且**桥的句柄会变成僵尸：读操作不报错，但永远读不到任何东西**。
→ **只能物理拔下等 5 秒再插回**。**判断"板子到底有没有在跑"之前，先确认串口打得开** —— 这一点曾让我们误判"固件不跑"好几轮。

### 4.7 写含中文的 `.ps1` 有三个坑

1. 文件**必须带 UTF-8 BOM**，否则 PS 5.1 按 ANSI 读 → `ParserError`。
2. **中文串里绝对不能出现 ASCII 直引号 `"`** → 会把字符串提前截断。
3. **别用 `[void](...)` 包住需要回显的调用** → 会把输出整行吞掉。
→ **交付脚本前先跑** `[System.Management.Automation.Language.Parser]::ParseFile()` **过语法，再喂输入做一次冒烟测试。**

---

## 5. 诊断手法（建议保留）

**RAM 启动脚印 + 寄存器快照**：外设寄存器不一定能读（bootloader 会 NACK 部分地址），但 **RAM 一直可读**。
所以固件把"跑到哪一步"和关键寄存器值写进 RAM，再"复位回 bootloader → 用 Read Memory 读出来"：

- 脚印：`BOOT_MARKER @ 0x2000B000`
- 寄存器快照：`0x2000B010`（`RCC_CFGR / RCC_CR / USART1->BRR / USART1->CR1`）
- 读法：`serial_cmd(hex="7F11EE2000B0009003FC", terminator="")`

另外固件里还有两个**默认关闭**的诊断开关，排查串口问题时很有用：
- `board.c: PROBE_BOOT=1` → 用 9 个不同 BRR 各发一句同样的话，**PC 上读得通的那一行 → f_cpu = 115200 × 那个 BRR**（实测只有 `brr=69` 干净）
- `main.c: HEARTBEAT=1` → 每秒重复输出一行已知字符串，**彻底摆脱对复位的依赖**

---

## 6. 未完成 / 下一步（按优先级）

| # | 事项 | 阻塞点 | 备注 |
|---|---|---|---|
| 1 | **入盒红外 6 路** | ❌ **还没采买** | 接 `PC0~PC5`，⚠️ **这几脚非 5V 容忍** → 模块必须 3.3V 供电，或把输入改到 FT 脚（`PC6/PC7/PC10/PC11/PC12`+1）。固件侧 `BOX?` 与 `BOX_CHANGED` 事件已就绪、含 1ms 二次采样消抖 |
| 2 | **夹爪行程标定** | 夹爪舵机未接 PB1 | 接上后 `servo_jog.ps1 -Cmd GSET`，找"完全张开 / 刚好夹住 40mm"两个值 |
| 3 | **Z 轴方向拍板** | 托盘/平台未装，看着连杆判不出来 | 装好后若反了发 `ZINV 1`（运行时生效，不用重烧） |
| 4 | **微动开关** | 未确认是否要装 | 现在 `EVT GRIP_DONE loaded=` **恒报 0**（"未确认"），没有假装知道夹住没夹住 |
| 5 | **M3 步进引擎** | ⚠️ 需用户告知自备驱动器接口（共阴/共阳、EN 极性、信号 3.3V 还是 5V） | TIM4 @20kHz 软件时基 + 回零 + 软限位 + `JOG/MOVE/HOME` |
| 6 | ~~**M3 前置隐患**：`systick_init()` 一开 TICKINT，`uart_puts` 就卡在等 TXE~~ **✅ 已查清并解决（2026-09-22，线路 A）** | ~~原因仍未查清~~ → **真凶是向量表下标错位** | **原来记的"SysTick 一开 TICKINT 就卡"是误判。** 真凶 `startup.c` 第 89-93 行它自己就记着：USART1 的向量被放到 IRQn 21、真正的 IRQn 37 位置是 0 → **"开中断 + RX 线上一个噪声起始位"就跳到地址 0 跑飞**；卡死恰好落在"开中断之后的第一次 `uart_puts`"，于是被错记成 SysTick。<br>**实验 1**（只开 SysTick）与**实验 2**（SysTick + `UART_RX_IRQ=1` 都开）**双双通过**：banner 完整、`PING → OK pong`。<br>**处置**：去掉 `__disable_irq()`、`UART_RX_IRQ` 改为 **1**（常态开中断），新增 **`MS?`** 命令自证 tick（实测 3.78 秒内 MCU 计时比 PC 计时只差 **3ms**，误差 0.08%）。**M3 的定时器中断路径已就绪。** |
| 7 | **M4 联调** | 依赖 M1~M3 | 拍照 → 判类 → `MOVE` → `ZDOWN` → `GRIP` → 抬起 → `MOVE` 到盒 → `RELEASE` → `BOX?` |

---

## 7. ⚠️ 线路 C 改动过线路 A 的文件（请知悉 / 自行复核）

| 文件 | 改了什么 | 生效方式 |
|---|---|---|
| `hw_bridge\server.py` | `_preview(data, limit=64)` → **`limit=4096`**（64 字节根本不够看一次自检的 9 行输出） | **必须 `Stop-Process` 杀掉那个 python 子进程**，mcp-client 约 1 秒后自动重连加载新代码 |
| `hw_bridge\protocol.md` | §4 订正 DTR 建议；新增 **8E1 / `CR1.M` 字长陷阱**、**复位（rts 无效、dtr 才有效）**、**复位后别 `DiscardInBuffer`** | 文档，直接生效 |

> 改桥的代码按线路 C 的规矩**应先问用户**；当时已在 `Claude接续日志.md` 里写了说明。**这两处请线路 A 复核是否保留。**

---

## 8. 当前板子状态（接手时应该就是这样）

- `BOOT0 = 0`
- flash 内 = fw **0.2.0**（`build/m1.bin`，约 10.7 KB）
- 串口 COM6 @ **115200 8E1**，**`serial_close()` 已关**（避免占住）
- Z 轴舵机停在 **中位 1500µs**（安全位）
- 夹爪通道 PB1 也在输出 1500µs，但**没接舵机**
- ST-Link 插在 `SWD接口` 上，3.3V 未接

**验收自测（贴着跑一遍就知道板子好不好）**

```
PING            → OK pong
VER             → OK fw=0.2.0 proto=1
STATUS          → OK state=IDLE_UNHOMED ... box=000000 pos=0,0,0,0
HELP            → OK commands=PING,VER,HELP,STATUS,GETPOS,BOX?,SAFE,ESTOP,CLEAR,RESET,ZUP,ZDOWN,ZSET,ZPCT,ZINV,GRIP,RELEASE,GSET,GPCT
TIM3?           → OK psc=7 arr=19999 ccr3=1500 ccr4=1500 cr1=129 ccer=4352 cnt=<变化> bcrl=... mapr=0 zinv=0
ZPCT 0          → GETPOS 显示 z=600
ZPCT 100        → GETPOS 显示 z=2400
ESTOP           → OK estop + EVT ESTOP reason=cmd；此后 ZUP → ERR 14
CLEAR           → OK cleared（并回安全位）
FOOBAR 1 2      → ERR 1 unknown FOOBAR
```

---

## 9. 交接后的边界

- **本会话（线路 C）就此停止改动 `09_固件\` 与 `hw_bridge\`。**
- 线路 A 接手后，如需与线路 B 协调，仍按 `Claude接续日志.md`「🔌 分工与交接」的规矩：**改对方占用的文件前先在日志里写一句**。
- 建议线路 A 接手后做的第一件事：**把 §4.1 的 `data_bits` 参数补进 `serial_open`** —— 这次那 40 分钟弯路的直接产物，成本极低、收益很高。
