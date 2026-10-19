"""视觉引导抓取：拍照 → 识别 → 换算成龙门坐标 → 调用抓放流程。

这条链就是「校赛方案」的完整形态 ✓：
    K210 拍照 ──> PC 识别（颜色+形状）──> 像素→毫米换算 ──> pick_place 抓放
（K210 只当相机 ✓；识别和换算都在 PC 上 ✓ —— 好调、好改、不占 K210 ✓）

⚠️ 标定数据（2026-10-04 用户手动示教的两点 ✓，坐标系原点 = **安全位置** ✓）：
    像素 (244.7, 185.4)  ↔  龙门 (−195, −65) mm     ← 右下绿块
    像素 (100.1,  13.7)  ↔  龙门 (−100, +65) mm     ← 左上绿块
   ⇒ 由此得到：比例 ≈0.717 mm/px、画面相对龙门旋转 ≈180°（含 4° 小倾斜 ✓）
     ⚠️ 若相机被碰动过，必须重新示教这两点 ✗

安全规则（用户 2026-10-04 明确要求 ✓）：
  · **每次抓放开始前，夹爪必须停在"安全位置"** ✓（脚本以当前点为原点 ✓）
  · **安全位置就是 +X 的机械极限** ✗ ⇒ 目标的 X 必须 ≤ 0 ✓
  · 流程结束：Z 抬到最高 + **原地停住** ✓（不做任何多余动作 ✗）

用法：
  D:\\python\\python.exe 08_视觉\\vision\\vision_pick.py --color yellow
  ... --color green --shape 六棱柱   # ⭐ 颜色 + 形状一起筛（形状名见 pc_detect.shape_of ✓）
  ... --color any       # 选"X 最靠近 0"的那件（移动距离最短、最安全 ✓）
  ... --place 0 0       # 放到哪（默认安全位置 ✓）
  ... --dry             # 只看结果不动机器 ✓
"""
import argparse
import math
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
sys.path.insert(0, str(HERE))
# ⚠️ 2026-10-08：目录已由 `08_视觉` 改名为 **`08_视觉`** —— 这里原来还写着旧名，
#    于是 `shot.py` 根本找不到（拍照直接 FAIL ✗）。改名后必须同步这一行 ✓。
sys.path.insert(0, str(ROOT / "08_视觉"))

import pc_detect  # noqa: E402

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

SHOT = ROOT / "08_视觉" / "shot.py"

# ⭐⭐ 标定数据（**存成"比例 + 旋转 + 一个锚点"** ✓ —— 2026-10-08 重构）
#
# 为什么不再存"两点" ✗：两点式只能整体替换，**换 (0,0) / 换原点时必须重标一遍** ✗。
# 而相似变换的自由度就是「比例 s、旋转 θ、平移 t」三个 ✓ ——
# 平移只由**一个锚点**（某件货的像素中心 ↔ 它当时的龙门 mm）决定 ✓✓。
# ⇒ 相机没动过的话，换原点**只改下面 `anchor_px / anchor_mm` 两行**就行 ✓，
#   比例和旋转（s、θ）不用重标 ✓。
#
# 溯源（2026-10-04 用户手动示教的两点，由此解出 s、θ ✓）：
#   像素 (244.7, 185.4) ↔ 龙门 (−195, −65)；像素 (100.1, 13.7) ↔ 龙门 (−100, +65)
CAL = {
    "scale_mm_per_px": 0.71725,      # = |Δmm| / |Δpx| = 161.01 / 224.48 ✓
    "rot_deg": -176.05,              # 画面相对龙门的旋转（含约 4° 的小倾斜 ✓）
    # ⭐ 锚点：**改这两行就是"喂新的 (0,0) 点"** ✓
    #    2026-10-08 用户重新喂点（手动台对准黄色圆柱中心 → `mx`）：
    #      黄圆柱中心 = 龙门 (−130.0, +10.0) mm  ↔  照片像素 (144.8, 108.4) ✓
    #      （实测复核：按此锚点算出的黄圆柱 = (−130.0, +10.4)，与用户 `mx` 喂的
    #        (−130.0, +10.0) 只差 **0.4mm** ✓✓ —— 比例/旋转沿用两点标定是对的 ✓）
    #    ⚠️⚠️ **相机装在龙门上**（跟夹爪一起走 ✓，`calibrate_2point.py` 就是靠这个原理 ✓）
    #      ⇒ 本映射是"**相对当前夹爪位置**"的、**与夹爪停在哪无关** ✓✓。
    #      所以"把 (0,0) 挪几毫米"（用户按 `l`/`j` 移动夹爪）**不需要改这两行** ✗ ——
    #      只要跑之前夹爪停在新 (0,0) 上，脚本自己 ZERO 就把原点定在那儿了 ✓。
    #      这两行只在**相机被动过**（重新标定比例/旋转后）或**发现整体有固定偏移**时才改 ✓。
    "anchor_px": (144.8, 108.4),
    "anchor_mm": (-130.0, 10.0),
}


def find_k210():
    """自动认 K210 相机的串口 ✓。

    ⚠️ 为什么必须自动认：**USB 口号是会变的** ✗ —— 2026-10-08 实测这台机器上
        STM32 从 COM14 变成 **COM11**、K210 从 COM5 变成 **COM15**（同一天内），
        脚本里写死的口号当场就失效（拍照 FAIL ✓，而不是报"口不对"✗）。
    判据：115200 打开 → 发 `\\r\\n` + Ctrl-C → 看有没有 **CanMV 横幅**或 **`>>>` 提示符** ✓。
        （STM32 那条口是 **8E1**、也不回这些，所以不会被认成相机 ✓）
    """
    try:
        sys.path.insert(0, str(ROOT / "hw_bridge" / "vendor"))
        import serial                                     # noqa: E402
        from serial.tools import list_ports as lp          # noqa: E402
    except Exception as e:                                 # noqa: BLE001
        print("[i] 认相机口失败（import serial：%s）" % e)
        return None

    found = []
    for p in lp.comports():
        dev = p.device
        buf = b""
        try:
            s = serial.Serial(dev, 115200, timeout=0.4, dsrdtr=False, rtscts=False)
        except Exception:                                  # noqa: BLE001
            continue
        try:
            try:
                s.dtr = False
                s.rts = False
            except Exception:                              # noqa: BLE001
                pass
            time.sleep(0.3)
            s.reset_input_buffer()
            s.write(b"\r\n\x03")
            time.sleep(0.8)
            buf = s.read(4096) or b""
        except Exception:                                  # noqa: BLE001
            buf = b""
        finally:
            s.close()
        if b"CanMV" in buf or b">>>" in buf:
            found.append(dev)
            print("[i] 认到相机口 %s（%s）" % (dev, p.description))
    return found[0] if found else None


PICK = ROOT / "hw_bridge" / "tools" / "pick_place.py"


def px_to_mm(px):
    """像素 → 龙门毫米：mm = anchor_mm + s·R(θ)·(px − anchor_px) ✓"""
    s = CAL["scale_mm_per_px"]
    th = math.radians(CAL["rot_deg"])
    ax, ay = CAL["anchor_px"]
    mx, my = CAL["anchor_mm"]
    du, dv = px[0] - ax, px[1] - ay
    c, si = math.cos(th), math.sin(th)
    return (mx + s * (c * du - si * dv), my + s * (si * du + c * dv))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--color", default="any", help="yellow/green/blue/any ✓")
    # ⭐ 2026-10-08：加形状筛选 ✓ —— 原来只能按颜色选 ✗，
    #    于是"抓绿色**六棱柱**"这种指定件根本没法表达（同一颜色有别的形状时会抓错 ✗✗，
    #    而"一盒混入不同形状 = 整个盒清零"是最危险的规则 ✗）。
    #    形状名来自 pc_detect.shape_of：circle(圆) / hexagon(六棱柱) / square-ish(…) /
    #    triangle(…) / pentagon(五边形) … ⇒ 这里做**子串**匹配 ✓（如 `--shape 六棱柱`、`--shape hex`、`--shape 圆`）
    ap.add_argument("--shape", default=None,
                    help="形状子串（如 六棱柱 / hex / 圆 / circle）；不填 = 不限 ✓")
    ap.add_argument("--place", nargs=2, type=float, default=[0.0, 0.0])
    ap.add_argument("--speed", type=int, default=5000)   # 固件上限 5000Hz ✓
    ap.add_argument("--zsettle", type=float, default=6.0,
                    help="Z **放下**等待秒数。⭐ 用户 2026-10-04 要求 **6.0s** ✓")
    ap.add_argument("--zlift", type=float, default=5.0,
                    help="Z **抬起**等待秒数。⭐ 用户要求 5.0s ✓（与放下同档 ✓）")
    ap.add_argument("--gsettle", type=float, default=0.35,
                    help="夹爪等待秒数。⚠️ 舵机转速硬件固定 ✗（0.17s/60° ⇒ 81° 约 0.23s）"
                         "⇒ 0.35s 已够 ✓（原来 0.8s 是白等 ✗）")
    ap.add_argument("--zup", type=int, default=2370)
    # ⭐ 2026-10-04：Z 最低点 630 → **611**（用户手动调到舵机实测最低 ✓）
    #    原因：矮货（蓝色三角锥）用 630 **夹不起来** ✗
    ap.add_argument("--zdn", type=int, default=611)
    ap.add_argument("--gopen", type=int, default=1850)
    ap.add_argument("--gclose", type=int, default=950)
    ap.add_argument("--shot-file", default=None, help="用现成照片，不重新拍")
    ap.add_argument("--k210", default="auto",
                    help="K210 相机串口；**auto = 自动认** ✓（口号会变，别写死 ✗）")
    ap.add_argument("--drop", action="store_true",
                    help="⭐ 到放置点后不下 Z、夹爪开到最大让货自由下落 ✓（用户要求 ✓）")
    ap.add_argument("--dry", action="store_true", help="只算不动机器 ✓")
    a = ap.parse_args()

    print("[i] 标定：%.5f mm/px，旋转 %.2f°，锚点 像素%s ↔ 龙门%s"
          % (CAL["scale_mm_per_px"], CAL["rot_deg"], CAL["anchor_px"], CAL["anchor_mm"]))

    img_path = Path(a.shot_file) if a.shot_file else (HERE / "_vp.jpg")
    if not a.shot_file:
        k210 = a.k210
        if k210 == "auto":
            k210 = find_k210()
        if not k210:
            print("[FAIL] 没自动认到 K210 相机口 —— 板子插了吗？也可以手动指定：--k210 COM15")
            return 1
        r = subprocess.run([sys.executable, str(SHOT), "--port", k210, "--out", str(img_path)],
                           capture_output=True, text=True, timeout=180,
                           encoding="utf-8", errors="replace")   # ⚠️ 不加这句在 GBK 控制台会 decode 崩 ✗
        if not img_path.exists():
            print("[FAIL] 拍照失败（口 %s）：\n" % k210 + r.stdout[-300:] + r.stderr[-300:])
            return 1
    img = pc_detect.imread_unicode(img_path)
    if img is None:
        print("[FAIL] 读不到照片")
        return 1

    items = pc_detect.detect(img, 250)
    cand = []
    for it in items:
        mm = px_to_mm(it["px"])
        cand.append((it["color"], it["shape"], it["px"], mm))
    print("\n识别到 %d 件（已换算成龙门坐标 ✓）：" % len(cand))
    print("  颜色    形状                     像素           龙门 mm        可用")
    usable = []
    for c, sh, px, mm in cand:
        ok = mm[0] <= 0.0                     # ⛔ X 必须 ≤ 0（安全位置是 +X 极限 ✗）
        print("  %-6s %-24s (%5.1f,%6.1f)  (%7.1f,%6.1f)  %s"
              % (c, sh, px[0], px[1], mm[0], mm[1], "✓" if ok else "✗ 超 +X 极限"))
        if ok:
            usable.append((c, sh, mm))

    if not usable:
        print("\n[FAIL] 没有可抓的货物（都超 +X 极限 ✗）")
        return 1

    def _match(t):
        """颜色 + 形状两个条件都满足才算候选 ✓（不填的维度 = 不限 ✓）。"""
        c, sh, _mm = t
        if a.color != "any" and c != a.color:
            return False
        if a.shape and a.shape not in sh:
            return False
        return True

    if a.color == "any" and not a.shape:
        # 选 X 最靠近 0 的（移动最短、离极限最远 ✓）
        target = max(usable, key=lambda t: t[2][0])
    else:
        pool = [t for t in usable if _match(t)]
        if not pool:
            print("\n[FAIL] 没有符合条件的货（颜色=%s 形状=%s）✓" % (a.color, a.shape or "不限"))
            return 1
        target = max(pool, key=lambda t: t[2][0])

    c, sh, (tx, ty) = target
    px, py = a.place
    print("\n⭐ 选中：%s %s   龙门 (%.1f, %.1f) mm → 放到 (%.1f, %.1f)"
          % (c, sh, tx, ty, px, py))

    if a.dry:
        print("(--dry：不动机器 ✓)")
        return 0

    cmd = [sys.executable, str(PICK),
           "--item", "%.1f" % tx, "%.1f" % ty,
           "--place", "%.1f" % px, "%.1f" % py,
           "--zup", str(a.zup), "--zdn", str(a.zdn),
           "--gopen", str(a.gopen), "--gclose", str(a.gclose),
           "--speed", str(a.speed), "--zsettle", str(a.zsettle),
           "--zlift", str(a.zlift), "--gsettle", str(a.gsettle), "--yes"]
    if a.drop:
        cmd.append("--drop")
    print("\n执行：" + " ".join(cmd[1:]) + "\n")
    r = subprocess.run(cmd, text=True, timeout=600, encoding="utf-8", errors="replace")
    return r.returncode


if __name__ == "__main__":
    sys.exit(main())
