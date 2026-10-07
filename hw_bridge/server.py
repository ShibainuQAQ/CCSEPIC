#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""hw_bridge —— 把 USB 摄像头与串口（STM32 主控板）暴露给 DSH 模型的最小 MCP 服务器。

为什么需要它：DSH 的文件沙箱（workspace-write）只给 bash/pwsh 消费方加限制，
本进程由 DSH 的 MCP 客户端直接 spawn，不走沙箱，因此能可靠地打开摄像头与串口设备。

设计约束（改代码前务必遵守）：
  * stdout 只能输出 JSON-RPC（每行一条），任何诊断信息一律走 stderr；
  * 只依赖标准库 + 本机已装的 opencv-python，外加上 vendored 的 pyserial；
  * 不做设备语义（jog/home 之类）：那是固件的事，这里只提供通用能力。

用法：
  python server.py            # MCP stdio 模式（由 DSH 启动）
  python server.py --check    # 只打印工具清单，用于语法/协议自检
  python server.py --probe    # 探测可用摄像头索引
  python server.py --shot 0   # 抓一张图落盘，用于脱离 DSH 的排错
  python server.py --ports    # 列出串口，用于脱离 DSH 的排错
"""

from __future__ import annotations

import base64
import json
import sys
import threading
import time
from collections import deque
from pathlib import Path

SERVER_NAME = "hw-bridge"
SERVER_VERSION = "0.3.0"

# 我们认识的 MCP 协议版本；初始化时优先回显客户端请求的版本。
SUPPORTED_PROTOCOLS = ("2025-06-18", "2025-03-26", "2024-11-05")
LATEST_PROTOCOL = SUPPORTED_PROTOCOLS[0]

# 桥自己的文件夹：server.py / protocol.md / vendor / captures 全在一起，搬到哪都能跑
HERE = Path(__file__).resolve().parent
CAPTURE_DIR = HERE / "captures"
VENDOR_DIR = HERE / "vendor"

# vendored 依赖（pyserial）就放在旁边，省得改宿主配置里的 PYTHONPATH
if VENDOR_DIR.is_dir() and str(VENDOR_DIR) not in sys.path:
    sys.path.insert(0, str(VENDOR_DIR))


def log(message: str) -> None:
    """诊断信息只走 stderr —— stdout 是协议通道。"""
    sys.stderr.write("[hw_bridge] %s\n" % message)
    sys.stderr.flush()


# --------------------------------------------------------------------------
# 相机
# --------------------------------------------------------------------------

def _cv2():
    import cv2  # 延迟导入：--check 不依赖摄像头与 opencv
    return cv2


def probe_cameras(max_index: int = 3) -> list[dict]:
    """逐个索引试开，返回可用相机（开一次读一帧再释放）。"""
    cv2 = _cv2()
    found: list[dict] = []
    for index in range(max(0, max_index) + 1):
        cap = cv2.VideoCapture(index, cv2.CAP_DSHOW)
        if not cap.isOpened():
            cap.release()
            continue
        try:
            ok, frame = cap.read()
            if not ok or frame is None:
                continue
            height, width = frame.shape[:2]
            found.append({"index": index, "width": width, "height": height})
        finally:
            cap.release()
    return found


def grab_frame(index: int, width: int, height: int, warmup: int):
    """开相机→丢掉前几帧（自动曝光未稳定）→取一帧→立刻释放。

    Windows 上优先用 CAP_DSHOW：MSMF 后端首次打开常有数秒延迟。
    """
    cv2 = _cv2()
    problems: list[str] = []
    for backend, label in ((cv2.CAP_DSHOW, "DSHOW"), (cv2.CAP_ANY, "ANY")):
        cap = cv2.VideoCapture(index, backend)
        if not cap.isOpened():
            cap.release()
            problems.append("索引 %d 用 %s 后端打不开" % (index, label))
            continue
        try:
            if width > 0:
                cap.set(cv2.CAP_PROP_FRAME_WIDTH, width)
            if height > 0:
                cap.set(cv2.CAP_PROP_FRAME_HEIGHT, height)
            frame = None
            for _ in range(max(1, warmup)):
                ok, candidate = cap.read()
                if ok and candidate is not None:
                    frame = candidate
            if frame is None:
                problems.append("索引 %d 用 %s 后端打开了但读不到帧（可能被其他程序占用）" % (index, label))
                continue
            return frame
        finally:
            cap.release()
    raise RuntimeError("；".join(problems) or "取帧失败")


def encode_jpeg(frame, max_edge: int, quality: int) -> tuple[bytes, tuple[int, int]]:
    """按最长边缩放后编码 JPEG（控制回传给模型的字节数）。"""
    cv2 = _cv2()
    height, width = frame.shape[:2]
    scale = min(1.0, float(max_edge) / float(max(width, height))) if max_edge > 0 else 1.0
    if scale < 1.0:
        frame = cv2.resize(
            frame,
            (max(1, int(width * scale)), max(1, int(height * scale))),
            interpolation=cv2.INTER_AREA,
        )
    ok, buffer = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), int(quality)])
    if not ok:
        raise RuntimeError("JPEG 编码失败")
    out_h, out_w = frame.shape[:2]
    return buffer.tobytes(), (out_w, out_h)


def save_png(frame, directory: Path, stem: str | None = None) -> Path:
    """落盘 PNG。用 imencode + 二进制写文件，绕开 cv2.imwrite 不支持中文路径的问题。"""
    cv2 = _cv2()
    directory.mkdir(parents=True, exist_ok=True)
    name = stem or time.strftime("shot-%Y%m%d-%H%M%S")
    path = directory / ("%s.png" % name)
    ok, buffer = cv2.imencode(".png", frame)
    if not ok:
        raise RuntimeError("PNG 编码失败")
    with open(path, "wb") as handle:
        handle.write(buffer.tobytes())
    return path


def mean_brightness(frame) -> float:
    try:
        return round(float(frame.mean()), 1)
    except Exception:
        return -1.0


# --------------------------------------------------------------------------
# 串口
# --------------------------------------------------------------------------

class SerialLink:
    """常驻串口连接：后台线程不停收行，主线程按需发命令。

    为什么要后台线程：板子会主动吐 EVT/LOG，不持续读会塞满驱动缓冲甚至丢数据；
    而 MCP 是"一次调用一次响应"，没法在调用之间保持读取。
    """

    def __init__(self) -> None:
        self._handle = None
        self._reader: threading.Thread | None = None
        self._stop = threading.Event()
        self._lines: deque = deque(maxlen=2000)
        self._lock = threading.Lock()
        self._pending = bytearray()
        self._encoding = "utf-8"
        self._rx_bytes = 0
        self._tx_bytes = 0
        self._opened_at = 0.0
        self._last_error = ""
        self._port = ""
        self._baud = 0
        self._parity = "N"
        self._binary = False

    # -- 内部 ---------------------------------------------------------------
    PARITY_CHOICES = {
        "n": "PARITY_NONE", "none": "PARITY_NONE",
        "e": "PARITY_EVEN", "even": "PARITY_EVEN",
        "o": "PARITY_ODD", "odd": "PARITY_ODD",
        "m": "PARITY_MARK", "mark": "PARITY_MARK",
        "s": "PARITY_SPACE", "space": "PARITY_SPACE",
    }

    @staticmethod
    def _preview(data: bytes, limit: int = 4096) -> dict:
        """把原始字节渲染成 hex + ASCII 预览（二进制协议靠它读值）。

        limit 原来是 64 —— 排查"串口乱码"时太短了：固件一次自检会连发 9 行共
        500+ 字节，只看前 64 字节根本判断不出哪一行是可读的。改成 4096。"""
        chunk = bytes(data[:limit])
        tail = " …" if len(data) > limit else ""
        return {
            "count": len(data),
            "hex": " ".join("%02X" % byte for byte in chunk) + tail,
            "ascii": "".join(chr(byte) if 32 <= byte < 127 else "." for byte in chunk) + tail,
        }

    @staticmethod
    def _serial_module():
        try:
            import serial  # type: ignore
            return serial
        except Exception as error:
            raise RuntimeError(
                "pyserial 不可用（%s）。请装到 %s（python -m pip install --target ... pyserial）"
                % (error, VENDOR_DIR)
            )

    def _reader_loop(self) -> None:
        while not self._stop.is_set():
            handle = self._handle
            if handle is None:
                break
            try:
                chunk = handle.read(256)
            except Exception as error:
                self._last_error = "读取失败：%s" % error
                break
            if not chunk:
                time.sleep(0.01)
                continue
            self._rx_bytes += len(chunk)
            with self._lock:
                self._pending.extend(chunk)
                if not self._binary:
                    while True:
                        index = self._pending.find(b"\n")
                        if index < 0:
                            break
                        raw = bytes(self._pending[:index])
                        del self._pending[: index + 1]
                        text = raw.rstrip(b"\r").decode(self._encoding, "replace")
                        self._lines.append({"t": round(time.time() - self._opened_at, 3), "text": text})

    # -- 对外 ---------------------------------------------------------------
    @property
    def is_open(self) -> bool:
        return self._handle is not None

    def open(self, port: str, baud: int = 115200, encoding: str = "utf-8",
             dtr=None, rts=None, parity=None) -> dict:
        serial = self._serial_module()
        parity_key = "N"
        parity_attr = "PARITY_NONE"
        if parity is not None:
            parity_key = str(parity).strip().lower()
            parity_attr = self.PARITY_CHOICES.get(parity_key, "")
            if not parity_attr:
                raise RuntimeError(
                    "parity 只支持 N/E/O/M/S 或 none/even/odd/mark/space，收到 %r" % (parity,)
                )
        self.close()
        try:
            handle = serial.Serial()
            handle.port = port
            handle.baudrate = int(baud)
            handle.parity = getattr(serial, parity_attr)
            handle.timeout = 0.05
            handle.write_timeout = 1.0
            handle.open()
            if dtr is not None:
                handle.dtr = bool(dtr)
            if rts is not None:
                handle.rts = bool(rts)
        except Exception as error:
            self._last_error = str(error)
            raise RuntimeError(
                "打开 %s 失败：%s —— 端口被别的程序占用？先用 serial_ports 看可用端口" % (port, error)
            )
        self._handle = handle
        self._port = port
        self._baud = int(baud)
        self._encoding = encoding or "utf-8"
        self._opened_at = time.time()
        self._rx_bytes = 0
        self._tx_bytes = 0
        self._parity = parity_key.upper()
        self._stop.clear()
        with self._lock:
            self._lines.clear()
            self._pending.clear()
        self._reader = threading.Thread(target=self._reader_loop, name="hw-serial-reader", daemon=True)
        self._reader.start()
        log("serial opened: %s @ %d" % (port, self._baud))
        return self.status()

    def close(self) -> dict:
        self._stop.set()
        reader, self._reader = self._reader, None
        if reader is not None and reader.is_alive():
            reader.join(timeout=1.0)
        handle, self._handle = self._handle, None
        if handle is not None:
            try:
                handle.close()
            except Exception as error:
                self._last_error = str(error)
            log("serial closed: %s" % self._port)
        return self.status()

    def write(self, payload: bytes) -> int:
        handle = self._handle
        if handle is None:
            raise RuntimeError("串口未打开，先 serial_open")
        try:
            count = handle.write(payload)
            handle.flush()
        except Exception as error:
            self._last_error = str(error)
            raise RuntimeError("写入失败：%s —— 板子被拔掉了？可重新 serial_open" % error)
        self._tx_bytes += len(payload)
        return count if isinstance(count, int) else len(payload)

    def pending_bytes(self) -> bytes:
        """尚未成行的原始字节（二进制协议如 STM32 bootloader 的回复靠它）。"""
        with self._lock:
            return bytes(self._pending)

    def take_pending(self) -> bytes:
        """取出并清空尚未成行的原始字节。"""
        with self._lock:
            data = bytes(self._pending)
            del self._pending[:]
            return data

    def set_binary(self, enabled: bool) -> None:
        """二进制模式开关：开启后收到的字节不再按 \\n 切行。

        为什么必须要有它：ISP/自定义二进制协议的数据里**可能恰好出现 0x0A**，
        一旦被切行逻辑吃掉，数据就悄悄错了 —— 这种 bug 最难查。
        """
        with self._lock:
            self._binary = bool(enabled)
            self._pending.clear()
            self._lines.clear()

    def read_exact(self, count: int, timeout_ms: int = 1000) -> bytes:
        """二进制模式专用：阻塞等到**恰好** count 个字节再返回。"""
        if count <= 0:
            return b""
        deadline = time.time() + max(0, timeout_ms) / 1000.0
        while True:
            with self._lock:
                if len(self._pending) >= count:
                    data = bytes(self._pending[:count])
                    del self._pending[:count]
                    return data
            if time.time() >= deadline:
                with self._lock:
                    got = len(self._pending)
                raise RuntimeError("等 %d 字节超时（实际只收到 %d 个）" % (count, got))
            time.sleep(0.002)

    def drain(self, max_lines: int = 200) -> list[dict]:
        with self._lock:
            out: list[dict] = []
            while self._lines and len(out) < max_lines:
                out.append(self._lines.popleft())
            return out

    def collect(self, expect_prefix: str | None, timeout_ms: int) -> tuple[list[dict], str | None, int]:
        """收行直到匹配 expect_prefix 或超时。返回 (行, 命中的行, 耗时ms)。"""
        started = time.time()
        deadline = started + max(0, timeout_ms) / 1000.0
        collected: list[dict] = []
        matched: str | None = None
        while True:
            for item in self.drain(500):
                collected.append(item)
                if matched is None and expect_prefix and item["text"].startswith(expect_prefix):
                    matched = item["text"]
            if matched is not None:
                break
            if time.time() >= deadline:
                break
            time.sleep(0.02)
        return collected, matched, int((time.time() - started) * 1000)

    def status(self) -> dict:
        return {
            "open": self.is_open,
            "port": self._port,
            "baud": self._baud,
            "encoding": self._encoding,
            "parity": self._parity,
            "buffered_lines": len(self._lines),
            "pending": self._preview(self.pending_bytes()),
            "rx_bytes": self._rx_bytes,
            "tx_bytes": self._tx_bytes,
            "uptime_s": round(time.time() - self._opened_at, 1) if self.is_open else 0,
            "last_error": self._last_error,
        }

    def reset(self, mode: str = "dtr", hold_ms: int = 100) -> dict:
        handle = self._handle
        if handle is None:
            raise RuntimeError("串口未打开，先 serial_open")
        if mode == "command":
            self.write(b"RESET\n")
            return {"mode": mode, "note": "已发送 RESET 文本命令"}
        if mode == "rts":
            # 本项目这块 CH340K 板实测：RTS 有效会把 MCU 按在复位态（自动 ISP 电路），
            # 所以"脉冲 RTS"= 复位一次 → BOOT0=1 时会重新进入 ROM bootloader。
            try:
                handle.rts = True
                time.sleep(max(10, hold_ms) / 1000.0)
                handle.rts = False
            except Exception as error:
                self._last_error = str(error)
                raise RuntimeError("RTS 复位失败：%s" % error)
            self._rx_bytes = 0
            self._tx_bytes = 0
            with self._lock:
                self._pending.clear()
                self._lines.clear()
            return {"mode": "rts", "hold_ms": hold_ms, "note": "已脉冲 RTS；若 BOOT0=1，板子应重新进入 bootloader"}
        if mode != "dtr":
            raise RuntimeError("mode 只支持 dtr / rts / command，收到 %r" % mode)
        try:
            handle.dtr = False
            time.sleep(max(10, hold_ms) / 1000.0)
            handle.dtr = True
        except Exception as error:
            self._last_error = str(error)
            raise RuntimeError("DTR 复位失败：%s（部分 USB-TTL 适配器 DTR 未接 NRST）" % error)
        return {"mode": "dtr", "hold_ms": hold_ms, "note": "已脉冲 DTR；若板子没复位，说明 DTR 未接 NRST 或极性相反"}


LINK = SerialLink()


# --------------------------------------------------------------------------
# STM32 串口 ISP（AN3155）—— 不依赖 ST-Link 的烧录路径
# --------------------------------------------------------------------------


def _xor(data: bytes) -> int:
    value = 0
    for byte in data:
        value ^= byte
    return value


class Stm32Isp:
    """STM32 出厂 ROM bootloader（AN3155）客户端。

    前置条件：串口用 `parity: "E"`（8E1）打开，且板子在 bootloader 模式
    （BOOT0=1 后复位）。⚠️ 每个命令的第二字节是**命令字节的按位取反**，
    发错会收到 NACK(0x1F) —— 别误判成"板子不支持"。
    """

    ACK = 0x79
    NACK = 0x1F

    def __init__(self, link: "SerialLink", trace: list | None = None) -> None:
        self.link = link
        self.trace: list = trace if trace is not None else []

    def _recv(self, count: int, timeout_ms: int = 1500, what: str = "") -> bytes:
        try:
            return self.link.read_exact(count, timeout_ms)
        except RuntimeError as error:
            raise RuntimeError(("%s：%s" % (what, error)) if what else str(error)) from None

    def _expect_ack(self, what: str, timeout_ms: int = 1500) -> None:
        byte = self._recv(1, timeout_ms, what)
        if byte == bytes([self.ACK]):
            return
        if byte == bytes([self.NACK]):
            raise RuntimeError("%s：设备回 NACK（0x1F）—— 参数或校验字节不对" % what)
        raise RuntimeError("%s：收到意外字节 0x%02X" % (what, byte[0]))

    def _command(self, code: int) -> None:
        self.link.write(bytes([code, (~code) & 0xFF]))
        self._expect_ack("命令 0x%02X" % code)

    def _address(self, address: int, what: str) -> None:
        raw = int(address).to_bytes(4, "big")
        self.link.write(raw + bytes([_xor(raw)]))
        self._expect_ack(what)

    # -- 协议 -------------------------------------------------------------
    def sync(self) -> None:
        self.link.take_pending()
        time.sleep(0.05)
        self.link.take_pending()
        self.link.write(b"\x7f")
        byte = self._recv(1, 1500, "同步 0x7F")
        if byte == bytes([self.ACK]):
            self.trace.append("同步 OK（收到 0x79）")
        elif byte == bytes([self.NACK]):
            # 设备已在命令模式：0x7F 被当成未知命令。
            # 这是**正常情况**（同一上电周期内第二次调用），不该报错。
            self.trace.append("设备已在命令模式（0x7F 回 NACK），跳过同步")
        else:
            raise RuntimeError("同步 0x7F：收到意外字节 0x%02X" % byte[0])

    def get(self) -> tuple[int, list[int]]:
        self._command(0x00)
        count = self._recv(1, 1500, "GET 长度")[0]
        payload = self._recv(count + 1, 1500, "GET 数据")
        self._expect_ack("GET 收尾")
        self.trace.append(
            "bootloader 版本 0x%02X，支持 %d 条命令：%s"
            % (payload[0], count, " ".join("%02X" % c for c in payload[1:]))
        )
        return payload[0], list(payload[1:])

    def get_id(self) -> int:
        self._command(0x02)
        count = self._recv(1, 1500, "GetID 长度")[0]
        payload = self._recv(count + 1, 1500, "GetID 数据")
        self._expect_ack("GetID 收尾")
        pid = int.from_bytes(payload[:2], "big")
        self.trace.append("芯片 DEV_ID = 0x%04X" % pid)
        return pid

    def erase_mass(self) -> None:
        """全片擦除：`0x43` 之后发 `FF 00`（0xFF=魔数、0x00=其反码）。耗时较长，超时给足。"""
        self._command(0x43)
        self.link.write(bytes([0xFF, 0x00]))
        self._expect_ack("全片擦除", timeout_ms=40000)
        self.trace.append("全片擦除 OK")

    def erase_pages(self, pages: list[int]) -> None:
        """按页擦除（命令 0x43）——**格式以 stm32flash 源码为准**（2026-09-21 核对）：

            0x43, 0xBC          → ACK
            页数-1              1 字节          ← 注意：1 字节，不是 2 字节
            各页页号            每页 1 字节      ← 注意：1 字节/页，不是 2 字节
            校验                1 字节 = (页数-1) XOR 所有页号
                                → 只在最后回一个 ACK

        ⚠️ 血泪教训：我先前猜成"2 字节页数 + 2 字节页号"，结果**多发了一个 0x00**，
        那个多余字节被设备当成下一条命令的开头 → 后续整条链路全部 NACK。
        **协议里多一个字节比少一个字节更难查** —— 因为它不报错，只是"后面全错"。
        """
        count = len(pages)
        if not 1 <= count <= 256:
            raise RuntimeError("0x43 单次擦除 1~256 页，收到 %d" % count)
        if any(not 0 <= int(page) <= 255 for page in pages):
            raise RuntimeError("0x43 的页号是 1 字节（0~255）")
        self._command(0x43)
        payload = bytearray([count - 1])
        checksum = (count - 1) & 0xFF
        for page in pages:
            byte = int(page) & 0xFF
            payload.append(byte)
            checksum ^= byte
        payload.append(checksum)
        self.link.write(bytes(payload))
        self._expect_ack("擦除完成（%d 页）" % count, timeout_ms=30000)
        self.trace.append("按页擦除 %d 页 OK（页 0~%d）" % (count, pages[-1]))

    def write(self, address: int, data: bytes) -> None:
        """写内存（0x31）——格式以 stm32flash 源码为准：

            0x31, 0xCE               → ACK
            地址 4 字节(高在前) + XOR 校验  → ACK
            长度-1 + 数据 + 0xFF 补齐 + 校验 → **一次性发完，只等一个 ACK**

        ⚠️ 两个易错点：① **长度字节没有单独的 ACK**（读内存才有）；
        ② **校验包含长度字节**，且数据要**补齐到 4 字节**（补 0xFF，补的字节也参与校验）。
        """
        if not 1 <= len(data) <= 256:
            raise RuntimeError("一次写 1~256 字节，收到 %d" % len(data))
        if address & 0x3:
            raise RuntimeError("写地址必须 4 字节对齐，收到 0x%08X" % address)
        self._command(0x31)
        raw = int(address).to_bytes(4, "big")
        self.link.write(raw + bytes([_xor(raw)]))
        self._expect_ack("写地址")

        aligned = (len(data) + 3) & ~3
        checksum = (aligned - 1) & 0xFF
        payload = bytearray([aligned - 1])
        for byte in data:
            payload.append(byte)
            checksum ^= byte
        for _ in range(aligned - len(data)):
            payload.append(0xFF)
            checksum ^= 0xFF
        payload.append(checksum)
        self.link.write(bytes(payload))
        self._expect_ack("写数据", timeout_ms=5000)

    def read(self, address: int, count: int) -> bytes:
        if not 1 <= count <= 256:
            raise RuntimeError("一次读 1~256 字节，收到 %d" % count)
        self._command(0x11)
        self._address(address, "读地址")
        n = count - 1
        self.link.write(bytes([n, (~n) & 0xFF]))
        self._expect_ack("读长度")
        return self._recv(count, 5000, "读数据")

    def go(self, address: int) -> None:
        self._command(0x21)
        self._address(address, "Go 地址")
        self.trace.append("已跳转到 0x%08X（Go）" % address)


FLASH_BASE = 0x08000000
FLASH_LIMIT = 0x08040000  # F103RC = 256KB
FLASH_PAGE = 2048         # 高容量 F103 的页大小


def tool_mcu_flash(arguments: dict) -> list[dict]:
    if not LINK.is_open:
        raise RuntimeError(
            "串口没打开。先 serial_open(port='COM6', baud=115200, dtr=False, rts=False, parity='E')"
        )
    parity = str(LINK.status().get("parity", "")).upper()
    if parity != "E":
        raise RuntimeError(
            "当前校验位是 %s，STM32 出厂 bootloader 需要 8E1 —— 请用 parity:'E' 重新 serial_open" % parity
        )

    address = int(arguments.get("address") or FLASH_BASE)
    erase = str(arguments.get("erase") or "mass").lower()
    verify = bool(arguments.get("verify", True))
    jump = bool(arguments.get("go", True))
    path = arguments.get("path")
    lines: list[str] = []

    LINK.set_binary(True)
    isp = Stm32Isp(LINK)
    try:
        isp.sync()
        isp.get()
        pid = isp.get_id()

        if not path:
            lines.append("（未给 path → 只探测，不烧录）")
            return [{"type": "text", "text": "\n".join(isp.trace + lines)}]

        image = Path(str(path)).read_bytes()
        if not image:
            raise RuntimeError("镜像是空的：%s" % path)
        if not (FLASH_BASE <= address < FLASH_LIMIT):
            raise RuntimeError("地址 0x%08X 不在 flash 范围 0x%08X~0x%08X" % (address, FLASH_BASE, FLASH_LIMIT))
        if address + len(image) > FLASH_LIMIT:
            raise RuntimeError("镜像 %d 字节放不下（%dKB flash）" % (len(image), (FLASH_LIMIT - FLASH_BASE) // 1024))

        padded = image + b"\xff" * ((-len(image)) % 256)
        lines.append("镜像 %d 字节 → 补齐到 %d 字节，写入 0x%08X" % (len(image), len(padded), address))

        if erase == "none":
            lines.append("跳过擦除（erase=none）")
        else:
            # ⚠️ 不用 0x43 + 0xFF 0x00 的"全片擦除"写法：实测 bootloader V2.2 会 ACK
            # 该命令后一直等页号、不再应答（2026-09-21 踩过）。显式页号最稳。
            pages = list(range((len(padded) + FLASH_PAGE - 1) // FLASH_PAGE))
            lines.append("擦除 %d 页（每页 %d 字节，命令 0x43 + 显式页号）" % (len(pages), FLASH_PAGE))
            isp.erase_pages(pages)

        total = len(padded)
        for offset in range(0, total, 256):
            isp.write(address + offset, padded[offset:offset + 256])
            done = offset + 256
            if done % 4096 == 0 or done == total:
                lines.append("  写入 %d / %d 字节" % (done, total))
        lines.append("写入完成（%d 字节）" % total)

        if verify:
            blocks = total // 256
            bad = 0
            for index, offset in enumerate(range(0, total, 256)):
                back = isp.read(address + offset, 256)
                if back != padded[offset:offset + 256]:
                    bad += 1
            if bad:
                raise RuntimeError("回读校验失败：%d / %d 块不一致" % (bad, blocks))
            lines.append("回读校验通过（%d 块）" % blocks)

        if jump:
            isp.go(address)
            # 跳转后固件通常立刻发一条上电横幅（如 `LOG boot fw=...`）。此刻串口还在
            # **二进制模式**，这些字节只是躺在 pending 里；不主动取出来，就会被收尾的
            # set_binary(False) 一起清掉 —— 那等于白丢了一条"固件真的跑起来了"的证据。
            time.sleep(0.4)
            greeting = LINK.take_pending()
            if greeting:
                preview = SerialLink._preview(greeting)
                lines.append("跳转后固件立即吐出 %d 字节：" % preview["count"])
                lines.append("  ASCII：%s" % preview["ascii"])
                lines.append("  hex  ：%s" % preview["hex"])
            else:
                lines.append("⚠️ 跳转后 400ms 内固件没有任何输出（可能没跑起来，或先不输出）")
        lines.append("✅ 完成。⚠️ 若 BOOT0 仍是 1，请拨回 0 再复位，否则板子不进应用。")
    except Exception as error:
        lines.append("❌ 失败：%s" % error)
        lines.append("提示：若设备卡在中途状态（例如在等页号），可用 serial_reset(mode='rts') 复位重进 bootloader。")
    finally:
        LINK.set_binary(False)
    return [{"type": "text", "text": "\n".join(isp.trace + lines)}]


def tool_serial_ports(arguments: dict) -> list[dict]:
    SerialLink._serial_module()
    from serial.tools import list_ports  # type: ignore

    ports = list(list_ports.comports())
    if not ports:
        text = "未发现任何串口。请检查：USB-TTL/板子是否插好、驱动（CH340/CP210x/ST-Link）是否装上。"
    else:
        lines = ["发现 %d 个串口：" % len(ports)]
        for item in ports:
            lines.append("  %s | %s | %s" % (item.device, item.description, item.hwid))
        text = "\n".join(lines)
    return [{"type": "text", "text": text}]


def tool_serial_open(arguments: dict) -> list[dict]:
    port = arguments.get("port")
    if not port:
        raise RuntimeError("必须给 port（先用 serial_ports 查）")
    state = LINK.open(
        str(port),
        int(arguments.get("baud", 115200) or 115200),
        str(arguments.get("encoding", "utf-8") or "utf-8"),
        arguments.get("dtr"),
        arguments.get("rts"),
        arguments.get("parity"),
    )
    return [{"type": "text", "text": "串口已打开：\n" + json.dumps(state, ensure_ascii=False, indent=1)}]


def tool_serial_close(arguments: dict) -> list[dict]:
    return [{"type": "text", "text": "串口已关闭：\n" + json.dumps(LINK.close(), ensure_ascii=False, indent=1)}]


def tool_serial_cmd(arguments: dict) -> list[dict]:
    command = arguments.get("command")
    if command is None:
        raise RuntimeError("必须给 command")
    as_hex = bool(arguments.get("hex", False))
    terminator = arguments.get("terminator", "\n")
    if as_hex:
        payload = bytes.fromhex(str(command).replace(" ", ""))
    else:
        payload = str(command).encode("utf-8")
    if terminator:
        payload += str(terminator).encode("utf-8")
    timeout_ms = int(arguments.get("timeout_ms", 1000) or 0)
    expect = arguments.get("expect_prefix")

    LINK.write(payload)
    collected, matched, elapsed = LINK.collect(expect, timeout_ms)
    lines = ["发送：%s（%d 字节，hex=%s）" % (command, len(payload), as_hex)]
    lines.append("期望前缀：%s" % (expect if expect else "（未指定，只等超时）"))
    lines.append("结果：%s，耗时 %d ms，收到 %d 行" % ("命中" if matched else "未命中（超时）", elapsed, len(collected)))
    if collected:
        lines.append("回复：")
        lines.extend("  %8.3fs  %s" % (item["t"], item["text"]) for item in collected)
    raw = LINK.pending_bytes()
    if raw:
        preview = SerialLink._preview(raw)
        lines.append(
            "未成行原始字节 %d 个（自打开端口起累计，hex）：%s" % (preview["count"], preview["hex"])
        )
        lines.append("  同批 ASCII：%s" % preview["ascii"])
    elif not collected:
        lines.append("回复：（无）——连原始字节都没有：板子没应答。检查波特率、接线 TX/RX 是否交叉、固件是否在跑")
    return [{"type": "text", "text": "\n".join(lines)}]


def tool_serial_read(arguments: dict) -> list[dict]:
    timeout_ms = int(arguments.get("timeout_ms", 0) or 0)
    max_lines = int(arguments.get("max_lines", 200) or 200)
    take = bool(arguments.get("take_pending", False))
    collected, _, elapsed = LINK.collect(None, timeout_ms)
    collected = collected[:max_lines]
    if not collected:
        text = "没有新的串口输出（等了 %d ms）。" % elapsed
    else:
        text = "读到 %d 行：\n" % len(collected) + "\n".join(
            "  %8.3fs  %s" % (item["t"], item["text"]) for item in collected
        )
    raw = LINK.take_pending() if take else LINK.pending_bytes()
    if raw:
        preview = SerialLink._preview(raw)
        text += "\n未成行原始字节 %d 个（hex）：%s\n  同批 ASCII：%s%s" % (
            preview["count"],
            preview["hex"],
            preview["ascii"],
            "（已清空）" if take else "（未清空；要清空用 take_pending=true）",
        )
    return [{"type": "text", "text": text}]


def tool_serial_status(arguments: dict) -> list[dict]:
    return [{"type": "text", "text": json.dumps(LINK.status(), ensure_ascii=False, indent=1)}]


def tool_serial_reset(arguments: dict) -> list[dict]:
    result = LINK.reset(str(arguments.get("mode", "dtr") or "dtr"), int(arguments.get("hold_ms", 100) or 100))
    return [{"type": "text", "text": json.dumps(result, ensure_ascii=False, indent=1)}]


# --------------------------------------------------------------------------
# 工具定义
# --------------------------------------------------------------------------

TOOLS = [
    {
        "name": "cam_list",
        "description": "枚举可用 USB 摄像头索引与分辨率。硬件排查第一步；返回空列表表示没有可用相机。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "max_index": {
                    "type": "integer",
                    "description": "探测的最大索引（默认 3，即试 0~3）",
                }
            },
        },
    },
    {
        "name": "cam_shot",
        "description": "抓拍一帧：原图 PNG 落盘到 captures/，并把缩放后的画面回传给模型查看。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "index": {"type": "integer", "description": "相机索引，默认 0"},
                "width": {"type": "integer", "description": "请求宽度，默认 1280"},
                "height": {"type": "integer", "description": "请求高度，默认 720"},
                "warmup": {"type": "integer", "description": "取帧前丢弃的帧数，默认 5"},
                "max_edge": {"type": "integer", "description": "回传画面最长边，默认 720"},
                "quality": {"type": "integer", "description": "回传 JPEG 质量 1~100，默认 80"},
                "save": {"type": "boolean", "description": "是否把原图落盘，默认 true"},
            },
        },
    },
    {
        "name": "serial_ports",
        "description": "枚举本机串口（含描述与硬件 ID）。连板子前第一步；空列表=没插或驱动没装。",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "serial_open",
        "description": "打开并常驻一个串口连接（后续调用复用同一条，板子的异步输出会被后台线程持续收下）。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "port": {"type": "string", "description": "如 COM3"},
                "baud": {"type": "integer", "description": "波特率，默认 115200"},
                "encoding": {"type": "string", "description": "解码方式，默认 utf-8（固件若输出 GBK 可改 gbk）"},
                "dtr": {"type": "boolean", "description": "可选：开端口后设置 DTR。某些板子 DTR 接 NRST，保持默认可能把板子按在复位态"},
                "rts": {"type": "boolean", "description": "可选：同理设置 RTS。⚠️ STM32 板（自动 ISP 电路）只压 DTR 不够 —— RTS 也会把 MCU 按在复位态，建议 dtr/rts 一起传 false"},
                "parity": {"type": "string", "description": "校验位：N/E/O/M/S（或 none/even/odd/mark/space），默认 N。STM32 出厂 ROM bootloader 需要 E（8E1）"},
            },
            "required": ["port"],
        },
    },
    {
        "name": "serial_close",
        "description": "关闭常驻串口连接并释放端口（要让串口助手/Keil/烧录工具独占该口时先调它）。",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "serial_cmd",
        "description": "发一条命令并收集回复行，可等指定前缀出现。超时不报错（便于诊断），返回发送内容/回复行/是否命中/耗时；若收到不含换行符的原始字节（二进制协议），会额外以 hex + ASCII 显示。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "command": {"type": "string", "description": "命令文本；hex=true 时填十六进制串"},
                "expect_prefix": {"type": "string", "description": "等到某行以此前缀开头就算命中，如 OK"},
                "timeout_ms": {"type": "integer", "description": "等待上限，默认 1000"},
                "terminator": {"type": "string", "description": "行尾，默认 \\n；传空串表示不加"},
                "hex": {"type": "boolean", "description": "command 按十六进制解析，默认 false"},
            },
            "required": ["command"],
        },
    },
    {
        "name": "serial_read",
        "description": "排空并返回板子的异步输出（EVT/LOG 遥测），可用于长时间观察。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "timeout_ms": {"type": "integer", "description": "最多等多久，默认 0（只取已缓冲的）"},
                "max_lines": {"type": "integer", "description": "最多返回行数，默认 200"},
                "take_pending": {"type": "boolean", "description": "是否顺便清空「未成行原始字节」缓存，默认 false（只预览不清空；二进制协议读完想清空就传 true）"},
            },
        },
    },
    {
        "name": "serial_status",
        "description": "查看串口连接状态：端口/波特率/校验位/缓冲行数/未成行原始字节(hex)/收发字节/最后错误。",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "serial_reset",
        "description": "复位板子：脉冲 DTR（mode=dtr）或发送 RESET 文本命令（mode=command）。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "mode": {"type": "string", "description": "dtr 或 command，默认 dtr"},
                "hold_ms": {"type": "integer", "description": "DTR 拉低时长，默认 100ms"},
            },
        },
    },
    {
        "name": "mcu_flash",
        "description": "STM32 串口 ISP 烧录（AN3155）：把 .bin 写进 flash，可回读校验、可烧完跳转运行。需要串口已用 parity:'E' 打开、且板子在 bootloader 模式（BOOT0=1 后复位）。不给 path 则只探测（同步+版本+芯片ID）。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": ".bin 文件路径；不给则只探测不烧录"},
                "address": {"type": "integer", "description": "起始地址，默认 0x08000000"},
                "erase": {"type": "string", "description": "pages（默认，按页擦除）或 none（跳过擦除）。不用全片擦除写法：实测 bootloader V2.2 会卡住"},
                "verify": {"type": "boolean", "description": "是否回读校验，默认 true"},
                "go": {"type": "boolean", "description": "烧完是否 Go 跳转运行，默认 true"},
            },
        },
    },
]


def tool_cam_list(arguments: dict) -> list[dict]:
    max_index = int(arguments.get("max_index", 3) or 0)
    cameras = probe_cameras(max_index)
    if not cameras:
        text = "未发现可用摄像头（已探测索引 0~%d）。请检查：摄像头是否插好、是否被其他程序（微信/相机应用/另一个进程）占用。" % max_index
    else:
        lines = ["发现 %d 个可用摄像头：" % len(cameras)]
        for item in cameras:
            lines.append("  索引 %d：%dx%d" % (item["index"], item["width"], item["height"]))
        text = "\n".join(lines)
    return [{"type": "text", "text": text}]


def tool_cam_shot(arguments: dict) -> list[dict]:
    index = int(arguments.get("index", 0) or 0)
    width = int(arguments.get("width", 1280) or 0)
    height = int(arguments.get("height", 720) or 0)
    warmup = int(arguments.get("warmup", 5) or 1)
    max_edge = int(arguments.get("max_edge", 720) or 720)
    quality = int(arguments.get("quality", 80) or 80)
    should_save = bool(arguments.get("save", True))

    frame = grab_frame(index, width, height, warmup)
    full_h, full_w = frame.shape[:2]

    saved_note = "未落盘（save=false）"
    if should_save:
        path = save_png(frame, CAPTURE_DIR)
        saved_note = "原图：%s" % path

    payload, (out_w, out_h) = encode_jpeg(frame, max_edge, quality)
    text = "\n".join(
        [
            "抓拍成功（相机索引 %d）",
            "原始分辨率：%dx%d，平均亮度：%.1f",
            "回传画面：%dx%d，JPEG %d 字节",
            saved_note,
            "时间：%s" % time.strftime("%Y-%m-%d %H:%M:%S"),
        ]
    ) % (index, full_w, full_h, mean_brightness(frame), out_w, out_h, len(payload))
    return [
        {"type": "text", "text": text},
        {"type": "image", "data": base64.b64encode(payload).decode("ascii"), "mimeType": "image/jpeg"},
    ]


DISPATCH = {
    "cam_list": tool_cam_list,
    "cam_shot": tool_cam_shot,
    "serial_ports": tool_serial_ports,
    "serial_open": tool_serial_open,
    "serial_close": tool_serial_close,
    "serial_cmd": tool_serial_cmd,
    "serial_read": tool_serial_read,
    "serial_status": tool_serial_status,
    "serial_reset": tool_serial_reset,
    "mcu_flash": tool_mcu_flash,
}


# --------------------------------------------------------------------------
# JSON-RPC / MCP
# --------------------------------------------------------------------------

def send(message: dict) -> None:
    """写一条 JSON-RPC。

    注意：中文 Windows 上 sys.stdout 的文本层是 GBK，直接 write 会把中文
    编码成 GBK 字节，而 MCP 客户端按 UTF-8 解码 → 工具描述乱码。
    所以这里绕过文本层，直接向二进制缓冲写 UTF-8。
    """
    payload = json.dumps(message, ensure_ascii=False) + "\n"
    sys.stdout.buffer.write(payload.encode("utf-8"))
    sys.stdout.buffer.flush()


def send_result(request_id, result) -> None:
    send({"jsonrpc": "2.0", "id": request_id, "result": result})


def send_error(request_id, code: int, message: str) -> None:
    send({"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}})


def handle_initialize(request_id, params: dict) -> None:
    requested = (params or {}).get("protocolVersion")
    protocol = requested if requested in SUPPORTED_PROTOCOLS else LATEST_PROTOCOL
    send_result(
        request_id,
        {
            "protocolVersion": protocol,
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
        },
    )
    log("initialize ok (client=%s, negotiated=%s)" % (requested, protocol))


def handle_tools_call(request_id, params: dict) -> None:
    name = (params or {}).get("name") or ""
    arguments = (params or {}).get("arguments") or {}
    handler = DISPATCH.get(name)
    if handler is None:
        send_result(
            request_id,
            {
                "content": [{"type": "text", "text": "未知工具：%s" % name}],
                "isError": True,
            },
        )
        return
    try:
        content = handler(arguments)
        send_result(request_id, {"content": content, "isError": False})
    except Exception as error:  # 工具失败要变成可见的 isError 文本，而不是协议错误
        log("tool %s failed: %r" % (name, error))
        send_result(
            request_id,
            {
                "content": [{"type": "text", "text": "%s 执行失败：%s" % (name, error)}],
                "isError": True,
            },
        )


def handle(message: dict) -> None:
    method = message.get("method")
    request_id = message.get("id")
    params = message.get("params")

    if request_id is None:  # 通知：不需要回复
        if method == "notifications/initialized":
            log("client initialized")
        return

    if method == "initialize":
        handle_initialize(request_id, params)
    elif method == "tools/list":
        send_result(request_id, {"tools": TOOLS})
    elif method == "tools/call":
        handle_tools_call(request_id, params)
    elif method == "ping":
        send_result(request_id, {})
    else:
        send_error(request_id, -32601, "Method not found: %s" % method)


def serve() -> int:
    log("started (pid=%d, dir=%s)" % (__import__("os").getpid(), HERE))
    stream = sys.stdin.buffer
    while True:
        raw = stream.readline()
        if not raw:
            log("stdin closed, exiting")
            return 0
        line = raw.decode("utf-8", "replace").strip()
        if not line:
            continue
        try:
            message = json.loads(line)
        except Exception as error:
            log("bad json: %r (%s)" % (line[:200], error))
            continue
        try:
            handle(message)
        except Exception as error:  # 单条消息出错不能拖垮整个服务器
            log("handler crashed: %r" % (error,))
            if message.get("id") is not None:
                send_error(message["id"], -32603, "internal error: %s" % error)


def main(argv: list[str]) -> int:
    # 中文 Windows 下文本层的默认编码是 GBK；CLI 模式与日志统一按 UTF-8 输出。
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

    if "--check" in argv:
        print(json.dumps(TOOLS, ensure_ascii=False, indent=2))
        return 0
    if "--probe" in argv:
        try:
            print(json.dumps(probe_cameras(3), ensure_ascii=False))
            return 0
        except Exception as error:
            print("probe failed: %s" % error)
            return 1
    if "--ports" in argv:
        try:
            print(json.dumps(tool_serial_ports({}), ensure_ascii=False))
            return 0
        except Exception as error:
            print("ports failed: %s" % error)
            return 1
    if "--shot" in argv:
        position = argv.index("--shot")
        index = int(argv[position + 1]) if len(argv) > position + 1 else 0
        try:
            frame = grab_frame(index, 1280, 720, 5)
            path = save_png(frame, CAPTURE_DIR, stem="cli-shot")
            print("saved: %s" % path)
            return 0
        except Exception as error:
            print("shot failed: %s" % error)
            return 1
    return serve()


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv[1:]))
    except KeyboardInterrupt:
        sys.exit(0)
