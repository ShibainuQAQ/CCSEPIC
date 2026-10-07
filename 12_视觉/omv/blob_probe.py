"""跑在 CanMV/K210 板上：找色块 + 打形状特征 + 把标注后的图吐回电脑。

目的：验证"颜色 + 形状"能不能把 7 种货物分开（尤其是 **正方体 vs 正四棱锥** 这一对）。
先把阈值放宽一点，看能不能框到东西；框不到再按实际颜色调。

PC 侧：`12_视觉\\shot.py --script 12_视觉\\omv\\blob_probe.py`
（保持纯 ASCII，避免板子 REPL 的多字节字符意外）
"""
import time

import image
import sensor
import ubinascii

sensor.reset()
sensor.set_pixformat(sensor.RGB565)
sensor.set_framesize(sensor.QVGA)      # 320x240
sensor.skip_frames(time=2000)

img = sensor.snapshot()
st = img.get_statistics()
print("STAT l=%d a=%d b=%d" % (st.l_mean(), st.a_mean(), st.b_mean()))

TH = {
    "yellow": [(30, 100, -25, 30, 15, 95)],
    "blue":   [(5, 75, -50, 25, -95, -5)],
    "green":  [(5, 90, -80, -5, -35, 75)],
}

total = 0
for name in TH:
    bs = img.find_blobs(TH[name], area_threshold=120, pixels_threshold=120, merge=True)
    print("COLOR %s blobs=%d" % (name, len(bs)))
    for b in bs:
        total += 1
        try:
            img.draw_rectangle(b.rect(), color=(255, 0, 0))
            img.draw_cross(b.cx(), b.cy(), color=(0, 255, 0))
        except Exception as exc:
            print("  DRAW_ERR", repr(exc))
        print("  B %s cx=%d cy=%d w=%d h=%d px=%d dens=%.3f"
              % (name, b.cx(), b.cy(), b.w(), b.h(), b.pixels(), b.density()))
        for f in ("roundness", "elongation", "rotation", "compactness"):
            if hasattr(b, f):
                try:
                    print("     %s=%.3f" % (f, getattr(b, f)()))
                except Exception as exc:
                    print("     %s ERR %r" % (f, exc))
print("TOTAL", total)

img.save("/sd/_blob.jpg", quality=75)
data = open("/sd/_blob.jpg", "rb").read()
print("JPG_LEN", len(data))
for i in range(0, len(data), 384):
    print("B64 " + ubinascii.b2a_base64(data[i:i + 384]).decode().strip())
print("JPG_END")
