"""和 CanMV/K210 板子打交道的 PC 侧小库（线路 A 自备）。

为什么不用现成工具：
  - `mpremote` **进不去 CanMV 的 raw REPL**（实测 `could not enter raw repl`）；
  - 直接"粘贴大脚本"也不行 —— 实测 **约 1KB 以上的粘贴会被回显但不执行**
    （70 字节的小脚本正常，1.2KB 的只回显不跑，是这个固件粘贴缓冲的限制）。
所以走这条最稳的路：**把脚本 base64 分片写进板子文件系统，再 `exec` 读回来跑**。
每条命令都是短短一行，用最朴素的"敲命令行 + 回车"方式发（不用粘贴模式）。

用法示例：
    from board import Board
    b = Board("COM5"); b.open(); b.wait_prompt()
    print(b.cmd("print(1+1)"))
    b.push("12_视觉/omv/shot_to_pc.py", "/sd/_shot.py")
    print(b.cmd("exec(open('/sd/_shot.py').read())", wait=60))
    b.close()
"""
import base64
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "hw_bridge" / "vendor"))
import serial  # noqa: E402


class Board:
    def __init__(self, port="COM5", baud=115200, boot_wait=6.0):
        self.port = port
        self.baud = baud
        self.boot_wait = boot_wait
        self.ser = None

    # ---------- 底层 ----------
    def open(self):
        self.ser = serial.Serial(self.port, self.baud, timeout=0.2, write_timeout=5,
                                 dsrdtr=False, rtscts=False)
        try:
            self.ser.dtr = False
            self.ser.rts = False
        except Exception:
            pass
        return self

    def close(self):
        if self.ser:
            self.ser.close()
            self.ser = None

    def read(self, secs, quiet=0.6, stop=None):
        """收数据：最多 secs 秒；有数据后静默 quiet 秒就停；遇到 stop 字节串立即停。"""
        end = time.time() + secs
        buf = bytearray()
        last = time.time()
        while time.time() < end:
            n = self.ser.in_waiting
            if n:
                buf += self.ser.read(n)
                last = time.time()
                if stop and stop in buf:
                    break
            else:
                if buf and (time.time() - last) > quiet:
                    break
                time.sleep(0.02)
        return bytes(buf)

    def wait_prompt(self, tries=10, verbose=True):
        boot = self.read(self.boot_wait, 0.8)
        if verbose:
            print("[boot] %d B ... %s" % (len(boot),
                  boot.decode("utf-8", "replace").strip().splitlines()[-1:] ))
        for i in range(tries):
            self.ser.write(b"\x03")
            time.sleep(0.4)
            if b">>>" in self.read(0.8, 0.25):
                if verbose:
                    print("[ok] REPL 就绪（第 %d 次 Ctrl-C）" % (i + 1))
                return True
        return False

    # ---------- 上层 ----------
    def cmd(self, code, wait=4.0, quiet=0.5, stop=None, echo=False):
        """敲一行代码 + 回车执行（单行，不要带缩进块）。"""
        assert "\n" not in code.strip(), "cmd() 只接受单行代码"
        self.ser.write(code.encode("utf-8") + b"\r\n")
        out = self.read(wait, quiet, stop=stop)
        text = out.decode("utf-8", "replace")
        if not echo:
            # 去掉命令行本身的回显
            lines = [ln for ln in text.splitlines() if ln.strip() != code.strip()]
            text = "\n".join(lines)
        return text

    def push(self, local_path, remote_path="/sd/_x.py", chunk=96, verbose=True):
        """把本地文件 base64 分片写进板子文件系统。

        ⚠️ 2026-10-03 踩坑：**推送前必须清掉"上一次残留的输出"** ✗。
        典型场景：上一次跑的是连续吐帧的脚本（每帧几十行 B64），中断之后 PC 缓冲里
        还积着几千行 ✗ —— 于是推送时"找板子的回显"全被这些陈旧字节搅乱，
        表现就是**卡在推送不动**（用户只能 Ctrl-C ✗）。
        所以这里先 reset_input_buffer + 吸几口 + 连发 Ctrl-C 把板子打回干净提示符。"""
        data = Path(local_path).read_bytes()
        try:
            self.ser.reset_input_buffer()
        except Exception:
            pass
        for _ in range(3):
            self.ser.write(b"\x03")
            time.sleep(0.15)
        self.read(0.6, 0.2)          # 把残留吸掉

        self.cmd("import ubinascii")
        self.cmd("_f=open('%s','wb')" % remote_path, wait=3)
        total = 0
        for i in range(0, len(data), chunk):
            piece = base64.b64encode(data[i:i + chunk]).decode()
            self.cmd("_f.write(ubinascii.a2b_base64('%s'))" % piece, wait=3, quiet=0.35)
            total += len(data[i:i + chunk])
        self.cmd("_f.close()", wait=3)
        if verbose:
            print("[push] %s -> %s（%d 字节，%d 片）" % (local_path, remote_path, total,
                  (len(data) + chunk - 1) // chunk))
        return total

    def run(self, remote_path="/sd/_x.py", wait=60.0, quiet=6.0, stop=None, verbose=True):
        """在板子上执行已推送的脚本。"""
        if verbose:
            print("[run] %s" % remote_path)
        return self.cmd("exec(open('%s').read())" % remote_path, wait=wait, quiet=quiet, stop=stop)
