"""连续抓取：把托盘里**所有指定颜色**的货物一件一件抓走 ✓。

做法（每轮独立 ✓，不依赖上一轮的坐标 ✓）：
   ① 拍照识别（`vision_pick.py --dry`）→ 还有没有该颜色的货？
   ② 有 ⇒ 正式抓一件：抓 → 搬回**安全位置** → 自由下落放下
   ③ 放在「安全位置 + 递增偏移」上 ✓ —— 免得多件**堆在同一处互相撞** ✗
   ④ 回到起点 ⇒ 下一轮坐标系不变 ✓（脚本每轮以"当前点"为原点 ✓，
      而流程结束时正好停在原点 ✓ ⇒ 偏移量一直有效 ✓✓）
   ⑤ 直到识别不到该颜色 ✓（或到 --max 上限 ✓，防跑飞 ✓）

⚠️ 安全：
   · 每轮都会检查 X ≤ 0（`pick_place.py` 内置拦截 ✓）—— 安全位就是 +X 极限 ✓
   · 偏移只往 **−X** 方向叠（`--step -70 0` ✓）⇒ 永远不会越过起点 ✓✓
   · 流程结束一律"Z 最高 + 原地停住" ✓

用法：
  D:\\python\\python.exe 12_视觉\\vision\\vision_pick_all.py --color green
  ... --step -70 0     # 每件放置点的偏移（默认往 −X 叠 70mm ✓）
  ... --max 6          # 最多抓几件（防跑飞 ✓）
"""
import argparse
import math
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
VP = HERE / "vision_pick.py"

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass


def run_vp(extra):
    """跑 vision_pick.py，返回 (退出码, 输出)。"""
    cmd = [sys.executable, str(VP)] + extra
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=900,
                       encoding="utf-8", errors="replace")
    return r.returncode, (r.stdout or "") + (r.stderr or "")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--color", required=True, help="yellow/green/blue ✓")
    ap.add_argument("--step", nargs=2, type=float, default=[-70.0, 0.0],
                    help="每件放置点相对上一件的偏移（默认 −X 70mm ✓）")
    ap.add_argument("--place0", nargs=2, type=float, default=[0.0, 0.0],
                    help="第 1 件的放置点（默认安全位置 0,0 ✓）")
    ap.add_argument("--max", type=int, default=6)
    ap.add_argument("--drop", action="store_true", default=True)
    ap.add_argument("--no-drop", dest="drop", action="store_false")
    a = ap.parse_args()

    print("=" * 64)
    print("  连续抓取：颜色 = %s ｜ 最多 %d 件" % (a.color, a.max))
    print("  放置点：第1件 (%.0f, %.0f)，之后每件再偏 (%+.0f, %+.0f)"
          % (a.place0[0], a.place0[1], a.step[0], a.step[1]))
    print("=" * 64)

    done = 0
    for k in range(a.max):
        # ① 先看看还有没有
        rc, out = run_vp(["--color", a.color, "--dry"])
        if ("没有 %s" % a.color) in out or "[FAIL]" in out:
            print("\n[%d] 托盘里已经没有 %s 了 ✓ 结束" % (k + 1, a.color))
            break
        # 打印这轮选中了哪件
        for line in out.splitlines():
            if "选中" in line:
                print("\n[%d] %s" % (k + 1, line.strip()))

        # ② 正式抓一件，放到递增偏移点 ✓
        px = a.place0[0] + k * a.step[0]
        py = a.place0[1] + k * a.step[1]
        extra = ["--color", a.color, "--place", "%.1f" % px, "%.1f" % py]
        if a.drop:
            extra.append("--drop")
        rc, out = run_vp(extra)
        for line in out.splitlines():
            if any(t in line for t in ("⏱", "完成", "中止", "ERR", "FAIL", "下落", "合计")):
                print("    " + line.strip())
        if rc != 0:
            print("\n✗ 第 %d 件失败（退出码 %d）—— 停止 ✓" % (k + 1, rc))
            print("   提示：串口被占用？请看上面的 FAIL 行 ✓")
            break
        done += 1
        print("    ✓ 第 %d 件完成（放到 %.0f, %.0f）" % (k + 1, px, py))

    print("\n" + "=" * 64)
    print("  共抓走 **%d** 件 %s ✓" % (done, a.color))
    print("=" * 64)
    return 0


if __name__ == "__main__":
    sys.exit(main())
