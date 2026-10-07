"""探 WLAN 能力：看这个 MicroPython 构建支持哪些 config 键（尤其是 txpower）。

背景：C3 扫得到 C6 的热点（-6 dBm，太强）但握不上手，怀疑是**接收端饱和**。
      如果能调发射功率，就可以把 C6 的 AP 功率降下来，这在"两块板贴在同一个机箱里"的最终形态下也是必需的。

跑法（项目根目录）：
  set PYTHONPATH=...\\hw_bridge\\vendor_esp
  D:\\python\\python.exe -m mpremote connect COM7 run 11_ESP无线\\src\\wlan_caps.py
"""
import network

sta = network.WLAN(network.STA_IF)
sta.active(True)
ap = network.WLAN(network.AP_IF)

for name, obj in (("STA", sta), ("AP", ap)):
    for key in ("txpower", "protocol", "channel", "mac", "ssid", "pm", "hostname", "authmode", "password"):
        try:
            print("%s.config(%-9r) = %r" % (name, key, obj.config(key)))
        except Exception as exc:
            print("%s.config(%-9r) ERR %s" % (name, key, exc))

print("module attrs:", ", ".join(a for a in dir(network) if a.startswith(("AUTH", "STAT", "PHY", "MODE"))))
