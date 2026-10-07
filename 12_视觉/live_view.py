"""实时取景 —— 把 K210 连续拍到的画面显示在电脑窗口里（调焦距/角度/摆货时用）。

为什么需要：之前"拍一张 → 发给你看"一轮要一分钟 ✗，根本没法调焦。
这个工具让板子连续吐帧、电脑连续显示，**边拧镜头边看效果** ✓。

用法（项目根目录）：
  D:\\python\\python.exe 12_视觉\\live_view.py --port COM5
  （K210 = CH340(1A86:7523)，用 hw_bridge\\tools\\port_probe.py --list 查）

窗口里：
  · `q` 或 `Esc` 退出
  · `s` 存下当前帧到 12_视觉\\shots\\live-<时间>.jpg（用来给我看 / 存档）

速度：QVGA + quality 60，115200 串口 ⇒ 大约 1~2 帧/秒（调焦够用 ✓）。
想更快：把板载脚本的 QUALITY 调低（40）或 CHUNK 调大。
"""
import argparse
import base64
import re
import sys
import time
from pathlib import Path

import cv2
import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from board import Board  # noqa: E402

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", default="COM5", help="K210 = CH340(1A86:7523)")
    ap.add_argument("--script", default=str(HERE / "omv" / "stream_to_pc.py"))
    ap.add_argument("--remote", default="/sd/_stream.py")
    a = ap.parse_args()

    b = Board(a.port, 115200).open()
    try:
        if not b.wait_prompt():
            print("[FAIL] 拿不到 K210 提示符 —— 板子插着吗？端口对吗？")
            return 1
        b.push(a.script, a.remote)
        # 用底层串口直接跑流（不能用 cmd()：它要等"安静"，而流是一直在说话的）
        b.ser.write(("exec(open('%s').read())\r\n" % a.remote).encode())
        print("[i] 已启动板载流。窗口里按 q 退出，按 s 存一帧。")

        buf = bytearray()
        parts = []
        shown = 0
        t_last = time.time()
        while True:
            chunk = b.ser.read(4096)
            if chunk:
                buf += chunk
            while b"\n" in buf:
                line, _, rest = buf.partition(b"\n")
                buf = bytearray(rest)
                s = line.decode("utf-8", "replace").strip()
                if s.startswith("B64 "):
                    parts.append(s[4:])
                elif s.startswith("FRM_END"):
                    if parts:
                        raw = base64.b64decode("".join(parts))
                        parts = []
                        img = cv2.imdecode(np.frombuffer(raw, np.uint8), cv2.IMREAD_COLOR)
                        if img is not None:
                            shown += 1
                            t_last = time.time()
                            cv2.imshow("K210 live  [q]=quit  [s]=save", img)
                            k = cv2.waitKey(1) & 0xFF
                            if k in (ord("q"), 27):
                                return 0
                            if k == ord("s"):
                                out = HERE / "shots" / time.strftime("live-%Y%m%d-%H%M%S.jpg")
                                out.parent.mkdir(parents=True, exist_ok=True)
                                cv2.imwrite(str(out), img)
                                print("  [s] 已存 %s" % out)
                            if shown % 10 == 0:
                                print("  ...已显示 %d 帧" % shown)
                elif s and not s.startswith((">>>", "...", "exec(")):
                    print("  " + s)
            # 卡住保护：20 秒没有新帧就提示（多半是板子重启了 / 端口不对）
            if time.time() - t_last > 20:
                print("[!] 20 秒没有新帧 —— 板子还在跑吗？")
                t_last = time.time()
    finally:
        try:
            cv2.destroyAllWindows()
        except Exception:
            pass
        b.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
