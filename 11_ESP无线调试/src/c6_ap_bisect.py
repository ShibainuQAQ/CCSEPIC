"""在 C6 上逐句二分：AP_IF 到底哪一句报 0x5001。

跑法（项目根目录）：
  set PYTHONPATH=...\\hw_bridge\\vendor_esp
  D:\\python\\python.exe -m mpremote connect COM8 run 11_ESP无线\\src\\c6_ap_bisect.py
"""
import time

import network

ap = network.WLAN(network.AP_IF)
print("0 MAC:", ":".join("%02X" % b for b in ap.config("mac")))
print("1 active:", ap.active())

try:
    ap.active(True)
    time.sleep(1.0)
    print("2 active(True) ok ->", ap.active())
except Exception as exc:
    print("2 ERR %r" % (exc,))
    raise SystemExit(1)

print("3 config:", ap.config())
print("4 ifconfig:", ap.ifconfig())

steps = [
    ("essid", {"essid": "SORT-C6"}),
    ("password", {"password": "sort12345"}),
    ("channel", {"channel": 1}),
    ("authmode", {"authmode": getattr(network, "AUTH_WPA2_PSK", 3)}),
]
for name, kw in steps:
    try:
        ap.config(**kw)
        print("5 config(%s) ok" % name)
    except Exception as exc:
        print("5 config(%s) ERR %r" % (name, exc))

try:
    ap.ifconfig(("192.168.4.1", "255.255.255.0", "192.168.4.1", "0.0.0.0"))
    print("6 static ip ok ->", ap.ifconfig())
except Exception as exc:
    print("6 static ip ERR %r" % (exc,))

print("7 final config:", ap.config())
print("8 final ifconfig:", ap.ifconfig())
