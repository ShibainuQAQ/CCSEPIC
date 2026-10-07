"""在 ESP32-C3 上跑：连上 C6 开的热点，试探它有没有在监听 TCP，并尝试发一条 HTTP 请求。

目的：**不刷 C6 固件**，先用它现有的固件验证"无线链路 + 能把信息送达 C6 并拿到回应"。

跑法（项目根目录）：
  set PYTHONPATH=...\\hw_bridge\\vendor_esp
  D:\\python\\python.exe -m mpremote connect COM7 run 11_ESP无线\\src\\probe_c6_ap.py [SSID]
"""
import socket
import sys
import time

import network

TARGET = sys.argv[1] if len(sys.argv) > 1 else "ESP32C6_LAN"
AUTH = {0: "OPEN", 1: "WEP", 2: "WPA-PSK", 3: "WPA2-PSK", 4: "WPA/WPA2-PSK", 5: "WPA2-ENT", 6: "WPA3"}

w = network.WLAN(network.STA_IF)
w.active(True)
time.sleep(0.5)

print("== scan ==")
hit = None
for n in w.scan():
    try:
        name = n[0].decode()
    except Exception:
        name = str(n[0])
    if name.upper().startswith(TARGET.upper()[:8]):
        print("  %s ch=%d rssi=%d auth=%s hidden=%s" % (name, n[2], n[3], AUTH.get(n[4], n[4]), n[5]))
        hit = name
print("target:", hit or TARGET)

print("== connect ==")
w.connect(TARGET)
for _ in range(24):
    if w.isconnected():
        break
    time.sleep(0.5)
print("connected:", w.isconnected())
if not w.isconnected():
    print("status:", w.status())
    raise SystemExit("cannot join %s (可能需要密码)" % TARGET)

ip, mask, gw, dns = w.ifconfig()
print("ip=%s mask=%s gw=%s dns=%s" % (ip, mask, gw, dns))

print("== port sweep on gateway ==")
for port in (80, 8080, 3333, 5000, 9000):
    s = socket.socket()
    s.settimeout(2)
    try:
        s.connect((gw, port))
        print("  %5d OPEN" % port)
        if port in (80, 8080):
            s.send(b"GET / HTTP/1.0\r\nHost: %s\r\n\r\n" % gw.encode())
            time.sleep(0.6)
            try:
                data = s.recv(512)
            except Exception as exc:
                data = b"<%r>" % exc
            print("        reply %d B: %r" % (len(data), data[:220]))
        else:
            s.send(b"HELLO-FROM-C3\n")
            time.sleep(0.4)
            try:
                data = s.recv(256)
            except Exception as exc:
                data = b"<%r>" % exc
            print("        reply %d B: %r" % (len(data), data[:220]))
    except Exception as exc:
        print("  %5d  --  %s" % (port, exc))
    finally:
        s.close()

print("== done ==")
