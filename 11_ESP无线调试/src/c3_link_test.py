"""一次性链路测试（跑在 C3 上）：连 C6 的 AP → 连 TCP 端口 → 发几行 → 打印回显。

用途：不部署 main.py 也能单独验证"C3 ↔ C6"这一段。C6 侧应已刷 `c6_bridge.py`（main.py）。

跑法（项目根目录）：
  set PYTHONPATH=...\\hw_bridge\\vendor_esp
  D:\\python\\python.exe -m mpremote connect COM7 run 11_ESP无线\\src\\c3_link_test.py

⚠️ 踩坑：如果 STA 之前连过别的热点、而那个热点已经消失，`connect()` 会直接抛
   `OSError: Wifi Internal State Error` —— 解决方法是先 `active(False)` 再 `active(True)`
   把射频彻底复位，然后重试。
"""
import time

import network
import socket

SSID = "SORT-C6"
PASSWORD = ""                   # 与 C6 侧保持一致：空 = 开放热点
HOST = "192.168.4.1"
PORT = 3333
TXPOWER_DBM = 2.0               # 与 C6 侧对称：压发射功率，避免贴太近把对方接收端打饱和

STATUS_NAMES = dict(
    (getattr(network, _n), _n)
    for _n in dir(network)
    if _n.startswith("STAT_") and isinstance(getattr(network, _n), int)
)


def status_str():
    s = sta.status()
    return "%s(%s)" % (s, STATUS_NAMES.get(s, "?"))


def reset_radio():
    sta = network.WLAN(network.STA_IF)
    try:
        sta.active(False)
        time.sleep(0.6)
    except Exception:
        pass
    sta.active(True)
    time.sleep(1.0)
    try:
        sta.config(txpower=TXPOWER_DBM)
    except Exception as exc:
        print("  txpower set failed: %r" % (exc,))
    return sta


sta = reset_radio()
# 诊断：看这块 C3 开机时是不是"还惦记着上一个热点"（旧的 ESP32C6_LAN 已被擦掉）
try:
    print("bound ssid=%r status=%s connected=%s" % (sta.config("ssid"), sta.status(), sta.isconnected()))
except Exception as exc:
    print("bound ssid query failed: %r" % (exc,))

print("scan ...")
found = None
for n in sta.scan():
    try:
        nm = n[0].decode()
    except Exception:
        nm = str(n[0])
    if nm == SSID:
        found = n
        print("  found %s ch=%d rssi=%d auth=%s" % (nm, n[2], n[3], n[4]))
if not found:
    print("  !! %s not visible in scan" % SSID)

ok = False
for attempt in range(3):
    try:
        sta.disconnect()
    except Exception:
        pass
    time.sleep(0.4)
    print("connect attempt %d ..." % (attempt + 1))
    try:
        if PASSWORD:
            sta.connect(SSID, PASSWORD)
        else:
            sta.connect(SSID)
    except Exception as exc:
        print("  connect() raised: %r" % (exc,))
        sta = reset_radio()
        continue
    t0 = time.time()
    while not sta.isconnected() and time.time() - t0 < 15:
        time.sleep(0.3)
    if sta.isconnected():
        ok = True
        break
    print("  failed, status=%s" % status_str())

print("wifi isconnected:", ok, sta.ifconfig() if ok else "")

# ⚠️ 不依赖 isconnected()：实测 AP 的 DHCP 一旦不工作，C3 会"关联上了但永远拿不到 IP"，
#    此时 isconnected() 恒 False。所以这里**兜底强制配静态 IP**，然后照样试 TCP。
if not ok:
    print("no DHCP -> force static ip")
    try:
        sta.ifconfig(("192.168.4.2", "255.255.255.0", "192.168.4.1", "192.168.4.1"))
        print("static ip:", sta.ifconfig())
    except Exception as exc:
        print("static ip failed: %r" % (exc,))
    time.sleep(1)

s = socket.socket()
s.settimeout(5)
s.connect((HOST, PORT))
print("tcp: connected to %s:%d" % (HOST, PORT))

for msg in (b"HELLO-FROM-C3\n", b"SECOND-LINE\n"):
    s.send(msg)
    print("TX:", msg)
    time.sleep(0.6)
    try:
        r = s.recv(256)
        print("RX:", r)
    except Exception as exc:
        print("RX: <%r>" % (exc,))

s.close()
print("done")
