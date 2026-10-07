"""一次性脚本：抓放提速（用户 2026-10-04 要求"夹取速度再快一点" ✓）。

⚠️ 前提：**"Z 放下后等满 6 秒再夹取"这条不能动** ✗（用户明确要求 ✓）
   ⇒ 只优化"不影响那 6 秒"的地方：
     1. 步进 4000 → **5000Hz**（固件上限 ✓ = 62.5mm/s）
     2. ⭐ Z **抬起**单独用 zlift（1.5s ✓）—— 原来抬起也跟着等 5s ✗，每轮白等 ~7 秒
        放下仍用 zsettle（5.0s ⇒ 加补偿后 6.0s ✓）
     3. 通用停顿 0.4 → 0.25s
     4. 夹爪等待 0.8 → 0.6s
"""
import io

P = r"hw_bridge\tools\pick_place.py"

REPS = [
    # 抬起：改用 lift=True（三处 zup）
    ("d.z(a.zup); d.wait()", "d.z(a.zup, lift=True); d.wait()"),
    ("d.z(a.zup); d.wait(0.6)", "d.z(a.zup, lift=True); d.wait(0.6)"),
    # 默认值
    ('ap.add_argument("--speed", type=int, default=4000)',
     'ap.add_argument("--speed", type=int, default=5000)   # 固件上限 5000Hz = 62.5mm/s ✓'),
    ('ap.add_argument("--pause", type=float, default=0.4)',
     'ap.add_argument("--pause", type=float, default=0.25)'),
    ('ap.add_argument("--gsettle", type=float, default=0.8,',
     'ap.add_argument("--gsettle", type=float, default=0.6,'),
    # 新增 --zlift
    ("    a = ap.parse_args()",
     '    ap.add_argument("--zlift", type=float, default=1.5,\n'
     '                    help="Z **抬起**后等待秒数（抬起 1.5s 就够 ✓；放下仍用 --zsettle ✓）")\n'
     "    a = ap.parse_args()"),
    # Deck 调用带上 zlift
    ("d = Deck(port, a.pause, a.speed, a.zsettle, a.gsettle)",
     "d = Deck(port, a.pause, a.speed, a.zsettle, a.gsettle, a.zlift)"),
    # 打印里带上抬起等待
    ('print("    等待：Z %.1fs ｜ 夹爪 %.1fs ｜ 速度 %dHz" % (a.zsettle, a.gsettle, a.speed))',
     'print("    等待：Z放下 %.1fs（实到 %.1fs）｜ Z抬起 %.1fs ｜ 夹爪 %.1fs ｜ 速度 %dHz"\n'
     '          % (a.zsettle, a.zsettle + 1.0, a.zlift, a.gsettle, a.speed))'),
]

if __name__ == "__main__":
    s = io.open(P, encoding="utf-8").read()
    for old, new in REPS:
        n = s.count(old)
        if n == 0:
            print("  [SKIP] 没找到：%s" % old[:56])
            continue
        s = s.replace(old, new)
        print("  [OK x%d] %s" % (n, old[:56]))
    io.open(P, "w", encoding="utf-8", newline="").write(s)
    print("  已写回 %s" % P)
