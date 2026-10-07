"""实时取景窗口（Tkinter + Pillow）—— 调 K210 焦距/角度时用。

为什么不用 OpenCV：本机装的 OpenCV 是**无界面版**（headless），`cv2.imshow` 直接报
"The function is not implemented" ✗。Tkinter 是 Python 标准库、Pillow 又有，所以用它们做窗口，
不依赖 OpenCV 的 GUI 支持 ✓。

用法（项目根目录）：
  D:\\python\\python.exe 12_视觉\\live_view_tk.py --port COM5
  （K210 = CH340(1A86:7523)；用 hw_bridge\\tools\\port_probe.py --list 查当前号）

窗口里：
  · `q` / `Esc` → 退出
  · `s`         → 存下当前帧到 12_视觉\\shots\\live-<时间>.jpg（给我看 / 存档）
  · `r`         → 板载流跑完（400 帧）后重新开始

⚠️ 这个工具会**独占 K210 的串口**；同时别用 CanMV IDE 连它（会抢端口 ✗）。
"""
import argparse
import base64
import sys
import time
import tkinter as tk
from pathlib import Path

from PIL import Image, ImageTk

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from board import Board  # noqa: E402

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass


class LiveView:
    def __init__(self, root, board, script, remote):
        self.root = root
        self.b = board
        self.script = script
        self.remote = remote
        self.buf = bytearray()
        self.parts = []
        self.frames = 0
        self.t0 = time.time()
        self.last_frame = None
        self.t_last = time.time()
        self.bad = 0

        root.title("K210 实时取景   [q]=退出  [s]=存当前帧  [r]=重开流")
        self.img_label = tk.Label(root, bg="#202020")
        self.img_label.pack()
        self.info = tk.Label(root, text="启动中…", font=("Consolas", 11), anchor="w")
        self.info.pack(fill="x")
        root.bind("q", lambda e: self.quit())
        root.bind("<Escape>", lambda e: self.quit())
        root.bind("s", lambda e: self.save())
        root.bind("r", lambda e: self.start_stream())

        self.start_stream()
        root.after(10, self.pump)

    def start_stream(self):
        self.buf = bytearray()
        self.parts = []
        self.bad = 0
        try:
            self.b.ser.reset_input_buffer()
            self.b.ser.write(("exec(open('%s').read())\r\n" % self.remote).encode())
            print("[i] 已请求板子重开流", flush=True)
        except Exception as exc:
            print("[!] 写串口失败：%r（端口被占用？）" % (exc,), flush=True)

    def save(self):
        if self.last_frame is None:
            return
        out = HERE / "shots" / time.strftime("live-%Y%m%d-%H%M%S.jpg")
        out.parent.mkdir(parents=True, exist_ok=True)
        self.last_frame.save(str(out), quality=90)
        print("  [s] 已存 %s" % out)

    def quit(self):
        try:
            self.root.destroy()
        except Exception:
            pass

    def pump(self):
        # 非阻塞读：只取"现在已经在缓冲区里"的字节
        if self.bad < 5:
            try:
                n = self.b.ser.in_waiting
                if n:
                    self.buf += self.b.ser.read(n)
                self.bad = 0
            except Exception as exc:
                # ⚠️ 端口被别人抢走时（CanMV IDE / 串口助手），这里会**连续抛异常** ——
                #    原来每次都 print，结果刷了几千行日志 ✗。现在只报前 5 次，然后停下并提示。
                self.bad += 1
                if self.bad <= 5:
                    print("  serial-err %r" % (exc,), flush=True)
                if self.bad == 5:
                    self.info.configure(text="⚠️ 串口打不开/被别的程序占用（CanMV IDE？串口助手？）"
                                             " —— 关掉它，然后按 r 重试")

        while b"\n" in self.buf:
            line, _, rest = self.buf.partition(b"\n")
            self.buf = bytearray(rest)
            s = line.decode("utf-8", "replace").strip()
            if s.startswith("B64 "):
                self.parts.append(s[4:])
            elif s.startswith("FRM_END"):
                if self.parts:
                    raw = base64.b64decode("".join(self.parts))
                    self.parts = []
                    try:
                        img = Image.open(__import__("io").BytesIO(raw))
                        img.load()
                        self.last_frame = img
                        self.tk_img = ImageTk.PhotoImage(img)
                        self.img_label.configure(image=self.tk_img)
                        self.frames += 1
                        self.t_last = time.time()
                        dt = time.time() - self.t0
                        self.info.configure(
                            text="帧 %d ｜ %.1f 帧/秒 ｜ 尺寸 %dx%d ｜ JPEG %d B ｜ 距上次帧 %.1fs"
                                 % (self.frames, self.frames / max(dt, 0.001),
                                    img.width, img.height, len(raw), time.time() - self.t_last))
                    except Exception as exc:
                        print("  decode-err %r" % (exc,))
            elif s.startswith("STREAM done"):
                self.info.configure(text="板载流跑完了（400 帧）—— 按 r 重开")
            elif s and not s.startswith((">>>", "...", "exec(", "B64")):
                print("  " + s)

        if time.time() - self.t_last > 20:
            self.info.configure(text="⚠️ 20 秒没有新帧 —— 板子在跑吗？端口对吗？按 r 重试")
            self.t_last = time.time()

        self.root.after(10, self.pump)


def find_k210_port():
    """自动找 K210 的板载 CH340（VID:PID = 1A86:7523）。"""
    sys.path.insert(0, str(HERE.parent / "hw_bridge" / "vendor"))
    try:
        import serial.tools.list_ports as lp
    except Exception:
        return None
    for p in lp.comports():
        if "1A86:7523" in (p.hwid or "").upper():
            return p.device
    return None


def list_ports_text():
    sys.path.insert(0, str(HERE.parent / "hw_bridge" / "vendor"))
    try:
        import serial.tools.list_ports as lp
    except Exception:
        return "    （列不出来，缺 pyserial）"
    out = []
    for p in lp.comports():
        out.append("    %-6s | %s | %s" % (p.device, p.description, p.hwid))
    return "\n".join(out) if out else "    （一个串口都没有 —— USB 没插好/板子没供电）"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", default=None,
                    help="不给就自动找 CH340(1A86:7523) = K210；给错也会自动回退")
    ap.add_argument("--script", default=str(HERE / "omv" / "stream_to_pc.py"))
    ap.add_argument("--remote", default="/sd/_stream.py")
    a = ap.parse_args()

    port = a.port
    if not port:
        port = find_k210_port()
        if port:
            print("[i] 自动识别到 K210（CH340 1A86:7523） → %s" % port, flush=True)
        else:
            print("[FAIL] 没找到 K210 的串口。当前串口：", flush=True)
            print(list_ports_text(), flush=True)
            print("    K210 = CH340(1A86:7523)；STM32 = CH340K(1A86:7522)", flush=True)
            return 1

    try:
        b = Board(port, 115200).open()
    except Exception as exc:
        dev = find_k210_port()
        if dev and dev != port:
            print("[i] %s 打不开（%r）→ 改用自动识别的 %s" % (port, exc, dev), flush=True)
            b = Board(dev, 115200).open()
        else:
            print("[FAIL] 打不开 %s：%r" % (port, exc), flush=True)
            print("    当前串口：", flush=True)
            print(list_ports_text(), flush=True)
            return 1
    if not b.wait_prompt():
        print("[FAIL] 拿不到 K210 提示符 —— 板子插着吗？端口对吗？（--port）")
        return 1
    b.ser.timeout = 0                     # 非阻塞：交给 Tk 事件循环轮询
    b.push(a.script, a.remote)

    root = tk.Tk()
    LiveView(root, b, a.script, a.remote)
    try:
        root.mainloop()
    finally:
        b.close()
    print("[i] 已退出")
    return 0


if __name__ == "__main__":
    sys.exit(main())
