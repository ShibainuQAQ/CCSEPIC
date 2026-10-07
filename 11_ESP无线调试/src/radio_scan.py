"""在 ESP32-C3 上跑：扫一遍 2.4GHz 频段，确认无线射频可用。

用途：
  1) 证明"电脑 → ESP（COM 口）→ 板子上跑代码"这条链路通了；
  2) 看清周围有哪些 AP、哪些信道最挤 —— 以后给装置选 AP 信道用得上；
  3) 打印本机 MAC，方便以后做白名单/固定 IP。

跑法（项目根目录）：
  set PYTHONPATH=...\\hw_bridge\\vendor_esp
  D:\\python\\python.exe -m mpremote connect COM7 run 11_ESP无线\\src\\radio_scan.py
"""
import time

import network

w = network.WLAN(network.STA_IF)
w.active(True)
time.sleep(0.8)

print("STA active:", w.active())
print("MAC:", ":".join("%02X" % b for b in w.config("mac")))

nets = w.scan()
print("scan: %d AP(s)" % len(nets))

by_ch = {}
for n in nets:
    ssid, _bssid, ch, rssi = n[0], n[1], n[2], n[3]
    try:
        name = ssid.decode()
    except Exception:
        name = repr(ssid)
    by_ch[ch] = by_ch.get(ch, 0) + 1
    print("  ch=%2d rssi=%4d dBm  %s" % (ch, rssi, name if name else "(hidden)"))

print("channel load:", " ".join("ch%d=%d" % (c, by_ch[c]) for c in sorted(by_ch)))
