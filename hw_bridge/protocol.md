# hw_bridge —— 硬件桥（串口 + 摄像头）用法与协议

> 建于 2026-09-16。目的：让 AI（DSH 会话里的模型）能直接**列串口 / 收发指令 / 拍照看图**，
> 不用每次让你手动复制粘贴串口输出。
> 桥程序：`hw_bridge\server.py`（单文件，零第三方框架依赖）。
> **整个文件夹是自包含的**：`server.py` / `protocol.md` / `vendor\`（pyserial）/ `captures\`（抓拍原图）都在里面，
> 换机器或搬家只改宿主配置里的 `args` 路径即可（代码里的路径都相对脚本自身算，2026-09-16 整理）。
>
> 🔌 **归属**：本目录属于"控制系统接入 + 摄像头"这条工作线（线路 A）——**由接入 agent 维护**；
> 整体设计推进那条线（线路 B）如果要动这里的代码或工具集，先在 `Claude接续日志.md`
> 的「🔌 分工与交接」一节留一句。两条线的接口契约（固件线协议、安全责任划分、应答时限）也写在那节。

---

## 1. 它是什么、为什么需要它

| 问题 | 说明 |
|---|---|
| 为什么不能让我直接用 pwsh 读串口/相机？ | DSH 的文件沙箱（workspace-write）会限制我的 shell：WMI/PnP 查询被拒（`拒绝访问`），`SerialPort::GetPortNames()` 返回空，每次调用还是新进程、**开不住串口** |
| 这个桥怎么绕过去 | 它由 DSH 的 MCP 客户端**直接 spawn**，不走沙箱 seam（沙箱只约束 bash/pwsh/fs 消费方），因此能正常打开设备 |
| 代价 | 多了一个常驻子进程；工具定义会占用少量 token（当前 9 个工具） |

**工具命名**：MCP 服务器名是 `hw`，所以工具在我这里显示为 `mcp__hw__<工具名>`。

---

## 2. 装配位置（改动前先看这里）

```
C:\Users\22431\.dsh\profiles\web\cordis.patch.yml     ← 宿主配置：注册这个桥（insert 一行）
   └─ 备份：cordis.patch.yml.bak-20260916
<项目>\hw_bridge\server.py                            ← 桥本体（9 个工具都在这）
<项目>\hw_bridge\protocol.md                          ← 本文件（用法 / 线协议 / 排错 / 回滚）
<项目>\hw_bridge\vendor\                              ← vendored pyserial 3.5（pip --target 装的）
<项目>\hw_bridge\captures\                            ← 相机原图落盘目录（不要提交进比赛材料）
```

宿主配置里那一段长这样（改地址/解释器就改这里）：

```yaml
- insert:
    - id: mcp-hw
      name: '@deepseek-ai/dsh-mcp-client'
      config:
        serverName: hw
        transport: stdio
        command: 'D:\python\python.exe'
        args: ['<项目>\hw_bridge\server.py']
        cwd: '<项目>'
        env: { PYTHONIOENCODING: 'utf-8' }
        toolCallTimeoutMs: 30000
```

> ⚠️ **改完 `server.py` 要生效**：MCP 子进程只在启动时加载一次代码，改完桥代码后**必须让子进程重开**。
> **已验证有效（2026-09-16 晚）**：`Stop-Process` 杀掉桥子进程即可 —— mcp-client 默认开了自动重连
> （`maxAttempts: 10`、首次退避 500 ms），约 1 秒后会**用当前磁盘上的 server.py 重新 spawn** 并重新注册全部工具。
> 这条比"重写 `cordis.patch.yml`"可靠得多：**重写配置无效**（配置内容没变时 patch 层不会重建插件实例，
> 2026-09-16 晚实测：子进程仍是改文件之前的旧代码，表现为新加的工具死活不出现）。
> 兜底手段仍是重启 `dsh web`（"一键重启"）。
> 杀掉子进程后如何确认：`Get-Process python*` 的 `StartTime` 应变成刚才的时间。

---

## 3. 相机怎么用

| 工具 | 用途 |
|---|---|
| `mcp__hw__cam_list` | 探测可用相机索引（默认试 0~3）。空列表 = 没插或被别的程序占用 |
| `mcp__hw__cam_shot` | 抓一帧：**原图 PNG 落盘 `captures\`**，同时把缩放后的画面回传给我看 |

典型对话：*"看一下托盘里现在有几个货"* → 我调 `cam_shot` → 直接看到画面。
参数：`index`（相机号）、`width/height`（请求分辨率）、`warmup`（丢弃帧数，默认 5，等自动曝光稳定）、
`max_edge`（回传最长边，默认 720，控制 token 与字节数）、`quality`、`save`。

⚠️ 每次抓拍都会**立即释放**相机（开源资料 3 的教训：USB 摄像头用完不 `release()`，下一次就打不开）。

---

## 4. 串口怎么用

| 工具 | 用途 |
|---|---|
| `mcp__hw__serial_ports` | 列串口（设备名 + 描述 + 硬件 ID）。空列表 = 没插 / 驱动没装 |
| `mcp__hw__serial_open` | 打开并**常驻**（后续调用复用同一条连接；板子的异步输出由后台线程持续收下） |
| `mcp__hw__serial_close` | 关闭常驻连接、释放端口（串口助手/Keil/烧录工具要独占该口时先调它） |
| `mcp__hw__serial_cmd` | 发一条命令 + 收集回复，可等指定前缀（如 `OK`）。超时**不报错**，便于诊断 |
| `mcp__hw__serial_read` | 排空板子主动吐的 `EVT`/`LOG` 遥测 |
| `mcp__hw__serial_status` | 端口/波特率/缓冲行数/收发字节/最后错误 |
| `mcp__hw__serial_reset` | 复位板子：脉冲 DTR（`mode=dtr`）或发 `RESET` 文本命令（`mode=command`） |

**为什么要"常驻"**：板子会主动输出日志，如果每条命令都重开一次串口，两次调用之间的输出就丢了。
后台读取线程把收到的行缓存起来（最多 2000 行），`serial_read` 随时取。

**DTR/RTS 陷阱（2026-09-21 首次记录，2026-09-22 用对照实验定案）**：不少 STM32 板把 DTR/RTS 经三极管接 NRST/BOOT0（自动 ISP 电路 —— 本项目的 CH340K 板上有 `Q1`/`Q2` 就是干这个的）。
→ **结论（实测，板子已知正常时逐项对照）：`serial_open` 传 `dtr: false` 或 `dtr: true` 都能正常收发 —— DTR 的静态电平对这块板没有影响。**
   `rts` 也建议传 `false`，但没有证据说它必须。
   ⚠️ 历史上这里先后写过两种互相矛盾的"规矩"（先 `dtr:false`、后 `dtr:true`），**都是误判**：当时"打开端口后一声不吭"的真因是
   **① Keil 烧完把内核停在调试态 ② CH340 掉驱动（端口僵死、句柄变僵尸但读操作不报错）**，与 DTR 无关。
   **排查顺序建议**：先确认串口**打得开**，再确认**内核在跑**（看有没有 boot banner），最后才怀疑线序/电平。

**⚠️ 复位（2026-09-22 实测，比 DTR 电平更容易踩）**
- **`serial_reset(mode='rts')` 对这块板完全没有效果**（脉冲后不产生任何输出、也没有 banner）。
- **要复位请用 `mode='dtr'`**（内部是 `dtr=False` 保持 → `dtr=True`，靠这个翻转产生复位）。
- **复位后立刻会有一大串启动输出（几十微秒内就发）** → 自己写抓包脚本时**绝对不要在复位后 `DiscardInBuffer()`**，否则 banner 被清掉，症状是"板子明明在跑却永远收到 0 字节"。（本项目 `09_固件\tools\uart_raw.ps1` 就踩过这个坑。）
- Keil（`UV4 -f`）烧完会把内核留在停止态，`Application running ...` 不代表代码真在跑 → **烧完通常要再来一次 `serial_reset(mode='dtr')`**。
  （但也实测到过例外：烧完直接 `serial_open` + `PING` 就有 banner 和回包 —— 所以**先试一下，别急着复位**。）

**8N1 vs 8E1（2026-09-21 实测）**：STM32 出厂 ROM bootloader 走**偶校验**。
- `0x7F` 同步字节在 8N1 下**仍会被接受**（它的停止位恰好等于所需的偶校验位）→ 可以用它当"链路通不通"的探针：`serial_cmd` + `hex:true` + `terminator:""` 发 `7F`，然后看 `serial_status` 的 `rx_bytes` 是否 +1。
- 但**其余命令字节在 8N1 下会被静默丢弃**（实测 `0x00 0xFF` = GET → 回 0 字节）。
- ✅ **桥已支持 parity（v0.2.0，2026-09-21）**：`serial_open` 传 `parity: "E"` 即 8E1，另支持 `O`/`M`/`S`（也接受 `even`/`odd`/…）。`serial_status` 里能看到当前校验位。

**⚠️⚠️ 8E1 的真凶：STM32 的 `CR1.M` 是"含校验位在内"的字长（2026-09-22 结案）**
- 固件侧若写 `M=0 + PCE=1`，自以为在发 8E1，实际发的是 **7 数据位 + 1 校验位**；PC 按 8E1 读就会**把校验位当第 8 个数据位** → 字符大体认得出来、但**高位时不时多一个 1**，帧同步却完全正常。
- **正确写法：`M=1 + PCE=1`**（F1 里 `USART_CR1_M` = bit12）。
- **一句话判据：把 PC 侧改成 `parity` 之外还改数据位（7E1）一读就全对** → 立刻确认是这个问题。
  本机 `09_固件\tools\parity_sweep.ps1` 会一次性扫「数据位 × 校验位」7 种组合。
- 注意 **`serial_open` 目前没有 `data_bits` 参数**（只有 `parity`）；要用 7E1 得靠上面的脚本或临时给桥加参数。

**二进制回复怎么看（v0.2.0 新增）**：接收端按 `\n` 切行，**不含换行的字节不会变成"行"**，所以二进制协议（bootloader、将来的 bin 帧）以前只能靠 `rx_bytes` 的数字猜。现在：
- `serial_cmd` / `serial_read` / `serial_status` 都会给出**未成行原始字节**的 **hex + ASCII 预览**；
- `serial_read` 传 `take_pending: true` 可**取走并清空**这批字节（`serial_cmd` 只预览不清空，避免把"半个 ASCII 行"误吃掉）。

### 4.2 串口 ISP 烧录（`mcu_flash`，v0.3.0 新增 · ✅ 2026-09-21 实测全通）

**不依赖 ST-Link、不需要任何额外软件**的烧录路：AI 自己"擦除 → 写入 → 回读校验"。

```
serial_open(port='COM6', baud=115200, dtr=false, rts=false, parity='E')   # 必须 8E1
serial_reset(mode='rts')        # 脉冲 RTS 复位 → BOOT0=1 时重新进 ROM bootloader
mcu_flash()                     # 不给 path = 只探测：同步 + 版本 + 芯片 ID
mcu_flash(path='xxx.bin', erase='pages', verify=true, go=false)   # 擦除+写入+回读校验
```

**实测成功的一次完整输出**（2026-09-21）：同步 OK → bootloader V2.2 → DEV_ID `0x0414` → 按页擦除 1 页 OK → 写入 1024/1024 字节 → **回读校验通过（4 块）**。

**协议要点（以 stm32flash 源码为准，比凭记忆靠谱）：**

| 命令 | 格式 | 关键坑 |
|---|---|---|
| 通用 | 第二字节 = 命令字节的**按位取反** | `Get`=`00 FF`、**`GetID`=`02 FD`**、`WriteMemory`=`31 CE`、`Erase`=`43 BC`、`ReadMemory`=`11 EE`、`Go`=`21 DE`。错一个字节回 `1F`(NACK) |
| Erase `0x43` | `页数-1`(**1 字节**) + `各页页号`(**每页 1 字节**) + `校验`(1 字节 = 页数-1 XOR 各页号) → **只在最后回一个 ACK** | ⚠️ 页数与页号都是**1 字节**，不是 2 字节！多发一个字节会被当成下一条命令的开头 → **后面整条链路全 NACK**（比少发字节难查得多） |
| Erase 全片 | `0x43` 之后发 `FF 00` | 耗时较长（超时给 40 s） |
| WriteMemory `0x31` | 地址 4 字节(高在前)+XOR 校验 → ACK；然后 `长度-1 + 数据 + 0xFF 补齐到 4 字节 + 校验` **一次性发完、只等一个 ACK** | ① **长度字节没有单独的 ACK**（读内存才有）② **校验包含长度字节** ③ 数据**补齐到 4 字节**，补的 `0xFF` 也参与校验 |
| ReadMemory `0x11` | 地址 5 字节 → ACK；`长度-1 + 其反码` → ACK；然后读 N 字节 | 这个**才有**独立的长度 ACK |

**`GetID` 的 DEV_ID**（本板实测 `79 01 04 14 79` = `0x0414`）：`0x0410` = **中容量**（F103x8/xB）、**`0x0414` = 高容量**（F103xC/xD/xE，RC 正是它）、`0x0430` = XL。可验明板子真伪（拿 C8 冒充 RC 的假片会回 `0x0410`）。

**收尾**：`go=true` 可让 bootloader 直接跳到应用运行（**不需要把 BOOT0 拨回 0** 就能立刻跑）；但**下次复位若 BOOT0 仍为 1，又会进 bootloader** —— 长期运行请把 BOOT0 拨回 0。

### 4.1 物理接线与上电（STM32F103RCT6 最小系统板，2026-09-21 补）

> ✅ **本项目这块板实测结论（2026-09-21）：板载有 CH340K**，插左侧丝印 `ISP下载` 的 Micro-USB 后
> `serial_ports` 直接返回 `COM6 | USB-SERIAL CH340K | VID:PID=1A86:7522` → **不需要外挂 CH340 模块、
> 一根杜邦线都不用接**。下面"外挂 CH340"那一节**只适用于板子没有板载芯片的情况**，留作备用（例如换板/备用件）。
> 该板载 CH340K 走的是 **USART1（PA9/PA10）**（已由 ROM bootloader 应答反证），**固件线协议必须用它 @115200**。

**第一步先判：板子上有没有板载 CH340？** 看 Micro-USB 口旁边有没有一颗小芯片印 `CH340`。

- **有** → 直接插板子自己的 Micro-USB，`serial_ports` 应直接出现 `USB-SERIAL CH340 (COMx)`，**不需要任何杜邦线**。
- **没有**（只有原生 USB = PA11/PA12）→ **无固件时不会出现 COM 口**（可能显示 `STM32 BOOTLOADER` 或未知设备），这是正常的、**不是驱动故障** → 必须外挂 CH340 USB-TTL。

**外挂 CH340 → USART1 接线（3 根，交叉）**

| CH340 模块 | 接板子 | 说明 |
|---|---|---|
| `TXD` | `PA10`（USART1_RX） | ⚠️ **交叉**：发 → 收 |
| `RXD` | `PA9`（USART1_TX） | ⚠️ **交叉**：收 → 发 |
| `GND` | `GND` | **必须共地**；不共地就是乱码或什么都没有 |
| `VCC` / `3V3` | **不接**（推荐） | 板子用自己的 Micro-USB 供电；两边都插同一台电脑时**不要再接 VCC**，免得两个电源打架 |

- **电平跳线选 3.3V**（模块上有 `3V3`/`5V` 跳线时）。若模块只能输出 5V：F103 的 **PA9/PA10 是 5V 容忍（FT）**，一般可用；但**别改用 PA2/PA3（USART2）—— 那两个不是 5V 容忍**。
- **上电顺序**：先给板子上电、再连信号线，避免向未上电的板子灌电流。
- TX/RX 接反**不影响枚举**（COM 口照样出现），只表现为 `serial_cmd` 无回复 → 查 §6。

**桥侧回环自检（不接板子，建议先做这一步）**：CH340 单独插电脑，用一根杜邦线把模块自己的 `TXD` ↔ `RXD` 短接（**VCC 不接、不接板子**）→ `serial_open` 后发 `PING`，应收到含 `PING` 的回显（`serial_cmd` **不滤回显**，见 server.py:378）。
**这一步通过 = 桥 + 驱动 + 适配器整条正常**，之后任何问题都必在板子 / 接线 / 固件侧 —— 这是最省时间的二分法。

---

## 5. 板子线协议（提案 —— 固件按这个实现，桥就能配合）

ASCII 行协议，**115200 8N1**，行尾 `\n`（`\r\n` 也兼容）：

| 方向 | 格式 | 例子 |
|---|---|---|
| 主机 → 板 | `命令 [参数...]` | `PING`、`STATUS`、`START`、`STOP`、`ESTOP`、`HOME X`、`JOG X 200` |
| 板 → 主机（应答） | `OK [payload]` / `ERR <code> [msg]` | `OK ready`、`ERR 12 not homed` |
| 板 → 主机（异步） | `EVT <name> [payload]` / `LOG <text>` | `EVT DONE slot3`、`LOG homing X` |

约定：
1. **每条命令 ≤100 ms 内必须应答**（这样 `serial_cmd expect_prefix="OK"` 才有意义）；
2. 异步信息一律以 `EVT` / `LOG` 开头，桥会把它们和应答一起按时间戳返回；
3. 固件**必须自己做软限位**，且未回原点时拒绝一切运动——桥不做安全判断；
4. 未实现/未知命令回 `ERR <code> unknown`，不要沉默。

> 这只是**提案**：固件还没写，协议可以改；改完同步更新本节和 §4 的例子即可。

---

## 6. 排错表

| 现象 | 可能原因 / 处理 |
|---|---|
| `serial_ports` 返回空 | 没插板子/适配器；CH340·CP210x·ST-Link 驱动没装；端口被占用（部分工具独占） |
| `serial_open` 报"端口被占用" | 串口助手/Keil 的串口窗口/另一个会话还开着；先关掉再开 |
| `serial_cmd` 未命中（超时、无回复） | ①波特率不对 ②TX/RX 没交叉 ③板子被 DTR 按在复位态（见 §4） ④固件没在跑 |
| 收到的行有 `` / 乱码 | 固件输出的不是 UTF-8 → 用 `serial_open` 的 `encoding: "gbk"` 再试 |
| `cam_list` 返回空 | 没插相机；被微信/相机应用/别的进程占用；笔记本内置相机被物理开关关闭 |
| `cam_shot` 报"打开了但读不到帧" | 相机刚被别的程序抓走，等 1~2 秒重试；或换一个 `index` |
| 工具根本没出现在我这儿 | MCP 行没被组合进 profile：检查 `cordis.patch.yml` 语法（用 `node -e "require('js-yaml')..."` 校验），或重启 `dsh web` |

---

## 7. 脱离 DSH 的自检命令（不依赖 MCP）

```powershell
python hw_bridge\server.py --check    # 打印工具清单（语法自检）
python hw_bridge\server.py --ports    # 列串口
python hw_bridge\server.py --probe    # 探测相机
python hw_bridge\server.py --shot 0   # 抓一张图
```

⚠️ 在**受限沙箱**里跑这些自检时，串口/相机/`vendor` 可能被拒或看不到——那是沙箱现象，
不代表桥坏了；真正的判据是 DSH 里的 `mcp__hw__*` 工具调用（子进程不受沙箱限制）。

---

## 8. 回滚

1. 把 `C:\Users\22431\.dsh\profiles\web\cordis.patch.yml` 还原为 `[]`（或 `Copy-Item` 用 `.bak-20260916` 覆盖）→ 工具消失；
2. 删掉 `<项目>\hw_bridge\`（整个文件夹，含 vendor 与 captures）；
3. 桥进程随插件 dispose 自动退出，不留常驻进程。

---

## 9. 当初考虑过但没用的方案

- **社区插件 `@infinitepersistence/dsh-serial-console`（MIT）**：现成的串口工具 + 网页终端，人工和 AI 共用一个口，看起来很好。
  没用它的原因：它的 peer 依赖声明是 `@deepseek-ai/dsh-tools >=0.0.1-rc.1 <0.1.0`、`@deepseek-ai/dsh-system-prompt >=0.0.1-rc.1 <0.1.0`，
  而本机宿主是 **`@deepseek-ai/dsh 0.1.5-rc.1`**，版本线对不上；往宿主里塞不确定的第三方插件风险高于自己写 200 行。
  它还需要**重启宿主**才能生效。若哪天它出了匹配 0.1.5 线的版本，值得重新评估（能白拿一个可审计的串口终端）。
- **`pip install pyserial` 到全局 site-packages**：改用 `--target` 装进 `vendor\`，好处是随项目走、不污染本机 Python。
