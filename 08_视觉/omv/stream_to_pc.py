# stream_to_pc.py -- run on CanMV/K210: capture frames in a loop and emit each as base64.
#
# Pair with PC-side 12_视觉/live_view.py, which decodes the frames and shows them in an
# OpenCV window -- used to focus the lens / aim the camera while watching the result.
#
# Pure ASCII on purpose.

import time

import image
import sensor
import ubinascii

FRAMES = 400          # 400 frames @ ~1s = 长时间取景（PC 侧按 q 可随时退出）
QUALITY = 60          # JPEG 质量：低一点 = 传输快 = 刷新快
CHUNK = 384           # 每行 base64 的原始字节数（与 shot_to_pc.py 一致）

sensor.reset()
sensor.set_pixformat(sensor.RGB565)
sensor.set_framesize(sensor.QVGA)     # 320x240
sensor.skip_frames(time=1500)

print("STREAM start")
for i in range(FRAMES):
    img = sensor.snapshot()
    img.save("/sd/_s.jpg", quality=QUALITY)
    data = open("/sd/_s.jpg", "rb").read()
    print("FRM %d %d" % (i, len(data)))
    for k in range(0, len(data), CHUNK):
        print("B64 " + ubinascii.b2a_base64(data[k:k + CHUNK]).decode().strip())
    print("FRM_END")
print("STREAM done")
