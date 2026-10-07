"""C6（装置侧）透传固件 —— AP + TCP server  <->  UART1（接 STM32）

角色：
  笔记本(USB) ── C3（STA + TCP 客户端）── WiFi ── C6（AP + TCP 服务端）── UART1 ── STM32 USART2

⚠️ 为什么用 UART1 而不是 UART0：
  UART0 = GPIO16/17 被板载 USB-转串口桥片（CH340K）占着当调试口用；
  拿它接 STM32 会和桥片的 TXD 顶牛（两个 3.3V 输出对驱）。所以 STM32 挂 UART1。

部署（PC → 板子）：
  mpremote connect COM8 cp src\\c6_bridge.py :main.py
  mpremote connect COM8 reset
调试输出走 UART0（也就是 COM8），可用 hw_bridge\\tools\\port_probe.py COM8 --listen 20 旁观。

自测（不需要 STM32）：把 UART1 的 TX 与 RX 用杜邦线短接（GPIO4 <-> GPIO5），
  则"TCP 发来的字节 → UART1 发出 → 立刻被自己收回 → 原样回传 TCP" ⇒ 整条桥路验证通过。
"""
import socket
import sys
import time

import machine
import network
import uselect

# ── 配置 ──────────────────────────────────────────────────────────────
SSID = "SORT-C6"
PASSWORD = ""                   # ⚠️ 空 = **开放热点**。联调期先开放：WPA2 握手是额外一个失败环节
                                #    （实测 C3 扫得到但握不上手），等链路稳了再决定要不要加密。
CHANNEL = 1                     # 现场 2.4G 挤：ch6 最挤，1/11 次之
TXPOWER_DBM = 2.0               # ⚠️ 抗"接收端饱和"用：两块板贴在同一个 USB 口上时，
                                #    20dBm 会让对方收到 -6dBm（扫得到却握不上手）；
                                #    压到 2dBm 后收端约 -12dBm，落在正常区间。
                                #    装置装好后两块板离得远（≥0.5m）可以调回 8~20。
AP_IP = "192.168.4.1"
TCP_PORT = 3333

UART_ID = 1
UART_TX = 4                     # → STM32 的 USART2_RX (PA3)
UART_RX = 5                     # ← STM32 的 USART2_TX (PA2)
UART_BAUD = 115200              # STM32 侧 USART2 按 8N1 配（USART1 那条才是 8E1）

VERBOSE = True
# ⚠️ 自测开关：TCP 收到的字节**同时回传一份**给客户端。
#    作用：还没接 STM32 时，也能验证"PC → C3 → C6 → 回传"整条链路。
#    **接上 STM32 之后请改回 False**（否则回传的数据会和 STM32 的应答混在一起）。
MIRROR_TO_CLIENT = False
# ─────────────────────────────────────────────────────────────────────


def log(*a):
    if VERBOSE:
        print("[C6]", *a)
        # ⚠️ 踩坑（2026-10-01）：ESP32-C6 这个构建的 `sys.stdout` 是 TextIOWrapper，
        #    **没有 flush()** → 直接调会 AttributeError 把 main.py 整个搞崩。所以必须包起来。
        try:
            sys.stdout.flush()
        except Exception:
            pass


def start_ap():
    ap = network.WLAN(network.AP_IF)
    ap.active(True)
    time.sleep(0.5)
    auth = getattr(network, "AUTH_WPA2_PSK", 3)  # 不同 MicroPython 版本常量位置略有差异
    if PASSWORD:
        ap.config(essid=SSID, password=PASSWORD, channel=CHANNEL, authmode=auth)
    else:
        ap.config(essid=SSID, channel=CHANNEL, authmode=getattr(network, "AUTH_OPEN", 0))
        log("no password -> OPEN ap")
    # ⚠️ 踩坑（2026-10-01 实测）：**顺序反了会崩** ——
    #    在 active(True) 之后、config(essid/password) 之前调 ifconfig(静态IP)，
    #    ESP32-C6 直接抛 `RuntimeError: Wifi Unknown Error 0x5001`，整个 main.py 挂掉、热点起不来。
    #    正确顺序 = 先 config 热点，再设静态 IP；而且这步失败也不该影响热点
    #    （AP 默认 IP 本来就是 192.168.4.1/24），所以整段包在 try 里。
    # ⚠️⚠️ 踩坑（2026-10-01 实测，**别把下面这行加回来**）：
    #    这句 `ap.ifconfig((静态IP...))` 在 ESP32-C6 上会抛
    #        RuntimeError: Wifi Unknown Error 0x5001
    #    而且**顺带把软 AP 的 DHCP 服务搞停** —— 症状极隐蔽：
    #        客户端**能关联上**（AP 的 stations 列表里看得到它的 MAC），
    #        但**永远拿不到 IP**；在客户端那侧表现为 "扫得到热点、status 一直
    #        CONNECTING/IDLE、isconnected() 永远 False"，看起来像"连不上"。
    #    AP 默认就是 192.168.4.1/24 + 自带 DHCP 服务，够用；要静态 IP **在客户端设**。
    # ap.ifconfig((AP_IP, "255.255.255.0", AP_IP, "0.0.0.0"))   # ← 千万别打开
    for _ in range(20):
        if ap.active():
            break
        time.sleep(0.2)
    try:
        ap.config(txpower=TXPOWER_DBM)
        log("txpower=%.1f dBm" % TXPOWER_DBM)
    except Exception as exc:
        log("txpower set failed: %r" % (exc,))
    # 兼容性：C3 是 11n 客户端，而 C6 是 11ax 芯片。显式把 AP 限到 11b/g/n，
    # 排除"AP 按 ax 专用能力广播、老客户端握不上手"这一类兼容问题。
    try:
        ap.config(protocol=7)
        log("protocol=11b/g/n(7)")
    except Exception as exc:
        log("protocol set failed: %r" % (exc,))
    log("AP up  ssid=%s ch=%d ip=%s" % (SSID, CHANNEL, ap.ifconfig()[0]))
    return ap


def serve_forever(ap):
    # ⚠️ 2026-10-01 实测记录（**缓冲参数先不要加**）：
    #    ESP32 的 UART 默认缓冲很小，而这段代码在 `cli.send()` 上会阻塞 ——
    #    往 WiFi 发一包期间，STM32 紧接着发来的"应答"确实有被冲掉的风险（`OK pong` 丢过一次）。
    #    但是：**加上 rxbuf=2048 / txbuf=1024 之后，"C6 → STM32"方向直接不通了**
    #    （实测：STM32 完全收不到字节；去掉这两个参数立刻恢复）。
    #    原因待查（怀疑这个 ESP32-C6 的 MicroPython 版本对自定义 txbuf 处理有问题）。
    #    所以先保持最朴素的写法；真要解决丢包应改成"非阻塞 socket + 自己排队"。
    # ⚠️⚠️ 2026-10-01 实测（**C6 上最邪门的一个坑**）：
    #    **开机时（main.py 里）创建的这个 UART 对象"能收不能发"** ——
    #      · RX 正常（能把 STM32 发来的话转给 TCP）✓
    #      · 但 `uart.write()` 出去的东西在 GPIO4 上一个字节都看不到 ✗
    #        （实测：开机信标没到、桥转发 `PING` 也没到；而同一时刻用示波器级别的判据
    #         ——STM32 的 `U2PD` + 读 IDR —— 证明 GPIO4→PA3 这根线是好的）
    #      · 在 REPL 里用**完全相同**的构造式新建一个 UART，**立刻就能发**
    #        （实测 STM32 连收 5 行 `C6-BOOT-test`）
    #    已排除：boot.py（出厂空模板）、rxbuf/txbuf 参数、接线、引脚配置。
    #    绕法：**开机后"建一次 → 拆掉 → 再建一次"**，让第二个对象像 REPL 里那样生效。
    machine.UART(UART_ID).deinit()   # 清掉可能残留的旧实例
    time.sleep_ms(150)
    uart = machine.UART(UART_ID, baudrate=UART_BAUD, tx=machine.Pin(UART_TX), rx=machine.Pin(UART_RX))
    time.sleep_ms(150)
    log("UART%d up tx=GPIO%d rx=GPIO%d baud=%d" % (UART_ID, UART_TX, UART_RX, UART_BAUD))
    # **开机信标**：一上电就往 UART1 发两行。
    # STM32 若在调试口打出 `EVT U2_LINE C6-BOOT` ⇒ 这条 TX 通路当场自证。
    uart.write(b"C6-BOOT\r\n")
    time.sleep_ms(50)
    uart.write(b"C6-BOOT2\r\n")

    srv = socket.socket()
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("0.0.0.0", TCP_PORT))
    srv.listen(4)      # 要留队列：新连接可能在上一个还没清掉时就到
    srv.settimeout(0)
    log("TCP listening on %s:%d" % (AP_IP, TCP_PORT))
    log("MIRROR_TO_CLIENT=%s (True 时回传会混进 UART 回环的证据，接 STM32 前必须 False)" % MIRROR_TO_CLIENT)

    # ⚠️⚠️ 2026-10-01 实测（"时好时坏"的真根因）：
    #    C3 一复位，它这边旧 socket 就死了，**但 C6 这一侧看不出死**（没有 FIN/RST）。
    #    原来的写法是"accept 一个客户端就死守它"，于是：
    #      · C6 把数据往那条**僵尸连接**上发（日志里能看到 `UART->TCP …` 却永远到不了 C3）
    #      · C3 重连后的**新连接被晾在 accept 队列里没人管** → 我们发的 `PING` 根本读不到
    #    表现就是"同一套代码，有时通有时不通"。
    #    正解：**新连接优先** —— 监听口和客户端口一起 poll，一有新连接就把旧的踢掉。
    cli = None
    addr = None
    poller = uselect.poll()
    poller.register(srv, uselect.POLLIN)

    rx_total = tx_total = 0
    tick = 0

    def drop_client():
        nonlocal cli
        if cli is not None:
            try:
                poller.unregister(cli)
            except Exception:
                pass
            try:
                cli.close()
            except Exception:
                pass
            cli = None

    while True:
        tick += 1
        if tick % 100 == 1:
            # 每 ~2 秒报一次"AP 上有没有客户端" —— 排查连接问题时这是第一手证据
            try:
                stations = ap.status("stations")
            except Exception as exc:
                stations = "?%r" % (exc,)
            log("stations=%s cli=%s" % (stations, "yes" if cli else "no"))

        try:
            events = poller.poll(20)
        except KeyboardInterrupt:
            continue

        for obj, _ev in events:
            if obj is srv:
                try:
                    new, new_addr = srv.accept()
                except OSError:
                    continue
                if cli is not None:
                    log("新连接 %s 到来 -> 踢掉旧连接 %s" % (new_addr, addr))
                drop_client()
                cli = new
                addr = new_addr
                cli.settimeout(0)          # 非阻塞：靠 poll 通知
                poller.register(cli, uselect.POLLIN)
                log("client connected from %s" % (addr,))
            elif obj is cli:
                try:
                    data = cli.recv(512)
                except OSError:
                    data = None
                if data:
                    uart.write(data)       # TCP → UART
                    rx_total += len(data)
                    log("TCP->UART %d B (total %d): %r" % (len(data), rx_total, data[:60]))
                    if MIRROR_TO_CLIENT:
                        try:
                            cli.send(b"<mirror>" + data)
                        except Exception:
                            pass
                elif data == b"":
                    log("client closed")
                    drop_client()

        # UART → TCP（不依赖有没有客户端：没有就丢弃，避免 RX 缓冲堵住）
        n = uart.any()
        if n:
            out = uart.read(n)
            if out and cli is not None:
                try:
                    cli.send(out)
                    tx_total += len(out)
                    log("UART->TCP %d B (total %d): %r" % (len(out), tx_total, out[:60]))
                except Exception as exc:
                    log("send failed: %r" % (exc,))
                    drop_client()


if __name__ == "__main__":
    _ap = start_ap()
    try:
        serve_forever(_ap)
    except KeyboardInterrupt:
        log("stopped by Ctrl-C")
