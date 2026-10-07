"""下载 ESP32-C3 / C6 的 MicroPython 固件到本目录 firmware\\。

为什么用 Python urllib 而不是 curl/git：本机 curl/git 走 Windows schannel，会报
SEC_E_NO_CREDENTIALS(0x8009030E)；Python 自带 OpenSSL 正常（见接续日志 2026-09-17 记录）。

用法（项目根目录）：
  D:\\python\\python.exe 11_ESP无线\\get_fw.py            # 下载缺失的
  D:\\python\\python.exe 11_ESP无线\\get_fw.py --force    # 全部重下
"""
import argparse
import ssl
import sys
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
OUT = HERE / "firmware"

# 候选顺序：先试最新版，404 就退到上一版
TARGETS = [
    ("esp32c3", [
        "https://micropython.org/resources/firmware/ESP32_GENERIC_C3-20260824-v1.29.0.bin",
        "https://micropython.org/resources/firmware/ESP32_GENERIC_C3-20260406-v1.28.0.bin",
        "https://micropython.org/resources/firmware/ESP32_GENERIC_C3-20251209-v1.27.0.bin",
    ]),
    ("esp32c6", [
        "https://micropython.org/resources/firmware/ESP32_GENERIC_C6-20260824-v1.29.0.bin",
        "https://micropython.org/resources/firmware/ESP32_GENERIC_C6-20260406-v1.28.0.bin",
        "https://micropython.org/resources/firmware/ESP32_GENERIC_C6-20251209-v1.27.0.bin",
    ]),
]


def fetch(url: str, dst: Path) -> bool:
    ctx = ssl.create_default_context()
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    try:
        with urllib.request.urlopen(req, timeout=60, context=ctx) as r:
            data = r.read()
    except Exception as exc:
        print("    FAIL %s -> %r" % (url.rsplit("/", 1)[-1], exc))
        return False
    dst.write_bytes(data)
    print("    OK   %s (%d bytes)" % (dst.name, len(data)))
    return True


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true")
    a = ap.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    for chip, urls in TARGETS:
        have = sorted(OUT.glob("ESP32_GENERIC_%s-*.bin" % chip.upper().replace("ESP32", "")))
        if have and not a.force:
            print("%s: already have %s" % (chip, ", ".join(p.name for p in have)))
            continue
        print("%s: downloading" % chip)
        for url in urls:
            if fetch(url, OUT / url.rsplit("/", 1)[-1]):
                break
        else:
            print("    !! all candidates failed for %s" % chip)
    print("\nfirmware dir: %s" % OUT)
    for p in sorted(OUT.glob("*.bin")):
        print("  %8d  %s" % (p.stat().st_size, p.name))
    return 0


if __name__ == "__main__":
    sys.exit(main())
