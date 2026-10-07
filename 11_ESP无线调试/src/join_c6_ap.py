"""在 ESP32-C3 上跑：用常见出厂密码尝试加入 C6 的热点；连上就把它的 TCP 服务扫一遍。

用法（项目根目录）：
  set PYTHONPATH=...\\hw_bridge\\vendor_esp
  D:\\python\\python.exe -m mpremote connect COM7 run 11_ESP无线\\src\\join_c6_ap.py [SSID]
"""
import socket
import sys
import time

import network

TARGET = sys.argv[1] if len(sys.argv) > 1 else "ESP32C6_LAN"
CANDIDATES = ["12345678", "1234567890", "88888888", "123456789", "esp32c6", "ESP32C6", "esp32c6lan", "password"]

w = network.WLAN(network.STA_IF)
w.active(True)
time.sleep(0.5)

ok = None
for pw in CANDIDATES:
    w.disconnect()
    time.sleep(0.3)
    print("try password: %r" % pw)
    try:
        w.connect(TARGET, pw)
    except Exception as exc:
        print("   connect() raised %r" % exc)
        continue
    t0 = time.time()
    while time.time() - t0 < 6:
        if w.isconnected():
            break
        time.sleep(0.4)
    if w.isconnected():
        ok = pw
        print("   CONNECTED with %r" % pw)
        break
    print("   nope (status=%s)" % w.status())

if not ok:
    print("== no candidate worked ==")
    raise SystemExit(1)

ip, mask, gw, dns = w.ifconfig()
print("ip=%s gw=%s" % (ip, gw))

for port in (80, 8080, 3333, 5000, 9000):
    s = socket.socket()
    s.settimeout(2)
    try:
        s.connect((gw, port))
        print("  %5d OPEN" % port)
        s.send(b"GET / HTTP/1.0\r\nHost: %s\r\n\r\n" % gw.encode())
        time.sleep(0.6)
        try:
            data = s.recv(512)
        except Exception as exc:
            data = b"<%r>" % exc
        print("        reply %d B: %r" % (len(data), data[:240]))
    except Exception as exc:
        print("  %5d  --  %s" % (port, exc))
    finally:
        s.close()
print("== done ==")
