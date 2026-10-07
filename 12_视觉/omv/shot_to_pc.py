"""跑在 CanMV/K210 板上：拍一张 -> 存 SD -> 用 base64 经串口吐回电脑。

为什么用 base64 走串口：板子的控制台就是那条 CH340 串口，直接发二进制不可靠；
base64 是纯文本最稳（30KB 的 JPEG 约 40KB base64，115200 下约 4 秒）。

PC 侧配套：`12_视觉\\shot.py`（粘贴本文件、收 base64、还原成 .jpg）。
注意：本文件保持纯 ASCII，避免板子 REPL 处理多字节字符时的意外。
"""
import time

import image
import sensor
import ubinascii

print("=== CAPS ===")
print("sensor module:", hasattr(sensor, "reset"), hasattr(sensor, "snapshot"))
sensor.reset()
sensor.set_pixformat(sensor.RGB565)
sensor.set_framesize(sensor.QVGA)
sensor.skip_frames(time=1500)

img = sensor.snapshot()
print("IMG", img.width(), "x", img.height())
for name in ("find_blobs", "find_circles", "find_rects", "find_lines",
             "get_statistics", "compress", "save", "to_bytes", "copy",
             "draw_rectangle", "histogram"):
    print("HAS", name, hasattr(img, name))

PATH = "/sd/_shot.jpg"
img.save(PATH, quality=75)
print("SAVED", PATH)

data = open(PATH, "rb").read()
print("JPG_LEN", len(data))
for i in range(0, len(data), 384):
    print("B64 " + ubinascii.b2a_base64(data[i:i + 384]).decode().strip())
print("JPG_END")
