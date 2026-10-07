"""把一段要发给淘晶驰串口屏的文本，转成 **GBK** 的十六进制字节串。

为什么需要它
------------
屏的中文字库是 **GBK** 编码，而 `hw_bridge` 的 `serial_cmd` 在文本模式下按
**UTF-8** 发送 —— 直接发中文会变成乱码。所以**中文一律走桥的 hex 模式**：

    serial_cmd(hex="<本脚本输出的十六进制>", terminator="")

本脚本负责把「文本 + 结尾的 FF FF FF」一起算成十六进制。
（淘晶驰每条指令都必须以 FF FF FF 结尾，否则屏不执行。）

用法
----
    python lcd_hex.py 'page 0'                     # 直接给文本（不含引号最省事）
    python lcd_hex.py --file _lcd_text.txt         # ⭐ 从文件读（**带双引号时用这个**）
    python lcd_hex.py --no-terminator --file x.txt # 不补 FF FF FF

⚠️ **为什么要有 `--file`**：Windows PowerShell 5.1 在把参数传给原生程序时会
   把参数里的 `"` 吃掉 —— 而淘晶驰的文本赋值指令 `t1.txt="红色方块"` **必须带双引号**。
   实测 `lcd_hex.py 't1.txt="AB"'` 传给 Python 的会变成 `t1.txt=AB`（引号丢了）。
   所以带引号的指令一律**写进文件再用 `--file` 读**，编码固定 UTF-8。

自检
----
    python lcd_hex.py 'AB'   # → 41 42 FF FF FF
"""

import sys

TERMINATOR = b"\xff\xff\xff"


def to_hex(text: str, terminator: bool = True) -> str:
    """文本 → GBK 十六进制（大写、空格分隔）。"""
    data = text.encode("gbk") + (TERMINATOR if terminator else b"")
    return " ".join("%02X" % byte for byte in data)


def main() -> int:
    argv = sys.argv[1:]
    terminator = "--no-terminator" not in argv
    argv = [a for a in argv if a != "--no-terminator"]

    if not argv:
        # 没给参数时打印用法（用 UTF-8 写 stdout，免得中文在 GBK 控制台上炸掉）
        sys.stdout.buffer.write((__doc__ or "").encode("utf-8"))
        return 2

    if argv[0] == "--file":
        if len(argv) < 2:
            sys.stdout.buffer.write("--file 需要跟一个路径\n".encode("utf-8"))
            return 2
        with open(argv[1], "r", encoding="utf-8") as handle:
            text = handle.read().rstrip("\r\n")
    else:
        text = argv[0]

    # 直接把结果写成 UTF-8 字节，绕开中文 Windows 控制台的 GBK 默认编码问题
    sys.stdout.buffer.write((to_hex(text, terminator) + "\n").encode("utf-8"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
