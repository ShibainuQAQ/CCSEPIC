"""C3（电脑侧）透传固件 —— 连 C6 的热点 + TCP 客户端  <->  USB 串口

角色：
  PC ──USB(COM7)── C3（STA + TCP 客户端）── WiFi ── C6（AP + TCP 服务端）── UART1 ── STM32

部署后对电脑的意义：**COM7 变成一根"无线串口线"** ——
  往 COM7 写什么，就原样送到 C6 的 UART1（也就是 STM32）；
  STM32 回什么，就原样从 COM7 冒出来。
  于是现有 `hw_bridge` 那套工具链（serial_cmd / LCD / PING …）**可以原样复用**，只是端口换成 COM7。

部署（PC → 板子）：
  mpremote connect COM7 cp src\\c3_bridge.py :main.py
  mpremote connect COM7 reset
  之后用 hw_bridge\\tools\\port_probe.py COM7 --send "PING" --crlf --listen 2 就能像普通串口一样用。

不想让它开机自动跑：mpremote connect COM7 rm :main.py
"""
import sys
import time

import network
import socket
import uselect

SSID = "SORT-C6"
PASSWORD = ""                   # 与 C6 侧保持一致：空 = 开放热点
HOST = "192.168.4.1"
PORT = 3333
STATIC_IP = "192.168.4.2"       # 固定下来，省掉 DHCP 的不确定性
VERBOSE = True


def log(*a):
    if VERBOSE:
        print("[C3]", *a)
        # ⚠️ 有些 MicroPython 构建的 sys.stdout 没有 flush()，不能无条件调
        try:
            sys.stdout.flush()
        except Exception:
            pass


def connect_wifi():
    sta = network.WLAN(network.STA_IF)
    sta.active(True)
    if not sta.isconnected():
        try:
            if PASSWORD:
                sta.connect(SSID, PASSWORD)
            else:
                sta.connect(SSID)
        except Exception as exc:
            log("connect() raised: %r" % (exc,))
        t0 = time.time()
        while not sta.isconnected():
            if time.time() - t0 > 20:
                log("wifi connect timeout, retry")
                sta.disconnect()
                time.sleep(1)
                if PASSWORD:
                    sta.connect(SSID, PASSWORD)
                else:
                    sta.connect(SSID)
                t0 = time.time()
            time.sleep(0.3)
    try:
        sta.ifconfig((STATIC_IP, "255.255.255.0", HOST, HOST))
    except Exception as exc:
        log("static ip failed (keep dhcp): %r" % (exc,))
    log("wifi ok ip=%s gw=%s" % (sta.ifconfig()[0], sta.ifconfig()[2]))
    return sta


def open_sock():
    s = socket.socket()
    s.settimeout(5)
    s.connect((HOST, PORT))
    s.settimeout(0)
    log("tcp connected %s:%d" % (HOST, PORT))
    return s


def usb_write(buf):
    try:
        sys.stdout.buffer.write(buf)
        sys.stdout.buffer.flush()
    except Exception:
        try:
            sys.stdout.write(buf.decode("latin-1"))
            sys.stdout.flush()
        except Exception:
            pass


def stdin_ready(poller):
    """只问"**stdin** 有没有数据"。⚠️ 别用 `if poller.poll(0):` —— 那会把 socket 的事件
    也算进来，于是在没有更多 USB 字节时继续 read(1) → **阻塞住整条桥**（实测踩到过：
    桥卡死、一个字节都发不出去）。"""
    try:
        for obj, ev in poller.poll(0):
            if obj is sys.stdin and (ev & uselect.POLLIN):
                return True
    except Exception:
        pass
    return False


def usb_read(poller):
    """USB 串口非阻塞读：取到 1 字节后**把当前确实可读的继续取走**，凑成一包再发。

    ⚠️ 踩过的坑：只 `read(1)` 就 send → "一个字节一个 TCP 包"（回传里 `<mirror>` 每字节一次）；
    但用 `poller.poll(0)` 当"还有没有"的判据又会阻塞。正确判据 = **stdin_ready()**。
    """
    out = bytearray()
    while len(out) < 512:
        try:
            b = sys.stdin.buffer.read(1)
        except Exception:
            try:
                ch = sys.stdin.read(1)
                b = ch.encode("latin-1") if ch else b""
            except Exception:
                b = b""
        if not b:
            break
        out += b
        if not stdin_ready(poller):
            break
    return bytes(out)


def main():
    connect_wifi()
    sock = None
    poller = uselect.poll()
    poller.register(sys.stdin, uselect.POLLIN)

    while True:
        if sock is None:
            try:
                sock = open_sock()
                poller.register(sock, uselect.POLLIN)
            except Exception as exc:
                log("tcp connect failed: %r (retry in 2s)" % (exc,))
                time.sleep(2)
                continue

        try:
            events = poller.poll(20)
        except KeyboardInterrupt:
            log("Ctrl-C ignored (bridge keeps running)")
            continue

        for obj, _ev in events:
            try:
                if obj is sys.stdin:
                    data = usb_read(poller)
                    if data:
                        # 诊断：把"从 USB 读到、准备发往 TCP"的内容打出来。
                        # 为什么需要：这条链路出问题时，"电脑没写进来"和"写进来了但没发出去"
                        # 看起来一模一样（C6 侧什么都看不到），有这行就能一刀切开。
                        log("usb->tcp %d B: %r" % (len(data), data[:80]))
                        sock.send(data)
                else:
                    data = sock.recv(512)
                    if data == b"":
                        raise OSError("peer closed")
                    log("tcp->usb %d B: %r" % (len(data), data[:80]))
                    usb_write(data)
            except OSError as exc:
                log("link lost: %r" % (exc,))
                try:
                    poller.unregister(sock)
                except Exception:
                    pass
                try:
                    sock.close()
                except Exception:
                    pass
                sock = None
                break


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        log("stopped by Ctrl-C")
