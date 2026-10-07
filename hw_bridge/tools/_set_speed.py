"""一次性脚本：把步进默认速度统一改成 4000Hz（用户 2026-10-04 要求 ✓）。

改哪些：
  · 固件 `board.c` 的上电默认步频 800 → 4000（= 50mm/s ✓）
  · `pick_place.py`  --speed 默认 600 → 4000，加速段 ACC 300 → 400
  · `vision_pick.py` --speed 默认 2000 → 4000
  · `step_jog.py`    --hz    默认 800  → 4000
  · `calibrate_2point.py` / `go_safe.py` 的 --speed 默认 → 4000
  · `manual_pick.py` 原本**不设速度**（吃固件默认 800 ✗）→ 启动时显式 SPD 4000 + ACC 400 ✓

⚠️ 4000Hz = 50mm/s，很快 ✗ ⇒ 加速段必须给足（ACC 400）✓，否则起步丢步 ✓。
"""
import io

EDITS = [
    (r"09_固件\src\board.c",
     "static uint32_t s_st_hz = 800u;          /* 默认目标步频 */",
     "static uint32_t s_st_hz = 4000u;         /* 默认目标步频（2026-10-04 用户要求 800→4000 = 50mm/s） */"),
    (r"hw_bridge\tools\pick_place.py",
     'ap.add_argument("--speed", type=int, default=600)',
     'ap.add_argument("--speed", type=int, default=4000)'),
    (r"hw_bridge\tools\pick_place.py",
     'd.send("ACC 300")',
     'd.send("ACC 400")'),
    (r"12_视觉\vision\vision_pick.py",
     'ap.add_argument("--speed", type=int, default=2000)',
     'ap.add_argument("--speed", type=int, default=4000)'),
    (r"hw_bridge\tools\step_jog.py",
     'ap.add_argument("--hz", type=int, default=800)',
     'ap.add_argument("--hz", type=int, default=4000)'),
    (r"12_视觉\vision\calibrate_2point.py",
     'ap.add_argument("--speed", type=int, default=600)',
     'ap.add_argument("--speed", type=int, default=4000)'),
    (r"hw_bridge\tools\go_safe.py",
     'ap.add_argument("--speed", type=int, default=2000)',
     'ap.add_argument("--speed", type=int, default=4000)'),
    (r"hw_bridge\tools\manual_pick.py",
     'lk.cmd("EN 1", wait=0.8)',
     'lk.cmd("EN 1", wait=0.8)\n'
     '    lk.cmd("SPD 4000")      # 2026-10-04 用户要求：手动台也跑 4000Hz（=50mm/s）\n'
     '    lk.cmd("ACC 400")       # 高速必须配足加速段，否则起步丢步 ✗'),
]

if __name__ == "__main__":
    for path, old, new in EDITS:
        try:
            s = io.open(path, encoding="utf-8").read()
        except Exception as exc:
            print("  [ERR ] %-38s %r" % (path, exc))
            continue
        n = s.count(old)
        if n != 1:
            print("  [SKIP] %-38s 匹配 %d 次（应为 1 ✗）" % (path, n))
            continue
        io.open(path, "w", encoding="utf-8", newline="").write(s.replace(old, new))
        print("  [OK  ] %s" % path)
