# -*- coding: utf-8 -*-
"""直接驱动淘晶驰串口屏（800×480 / 9600 8N1）—— 权威语义版，2026-10-01 重写。

为什么不用 hw_bridge 的 MCP 工具
--------------------------------
淘晶驰指令**必须以 `FF FF FF` 结尾**；桥的 `serial_cmd` 只能追**文本**结尾，发不出这三个字节。
本脚本自己处理 GBK 编码 + `FF FF FF`，一条命令一次 write。

⚠️ 旧版 screen.py 的三处错（已于 2026-10-01 修正）
--------------------------------------------------
1. 图片 ID 表是猜的（"绿00-06/蓝07-12/黄13-19"）→ 已换成**从队友工程里解出的权威表**
2. 控件名写成 `pic0` → 实际是 **`p0`**（图片控件默认名）
3. 语义写成"t0=序号/t1=名称"（单件刷新）→ 实际是 **6 列表格**：第 N 列 =
   序号 `n(N-1)`、名称 `t(N-1)`、图片 `p(N-1)`、计数 `n(6+N-1)`

用法
----
    python screen.py probe   --port COM5                       # 探控件在不在（不用猜）
    python screen.py reset   --port COM5                       # 序号写回 1~6、名称清空、计数归零
    python screen.py maptest --port COM5                       # 【验证 ID 对照】6 列各放一件，看图文对不对
    python screen.py show    --port COM5 --col 3 --id 16 --count 7
    python screen.py raw     --port COM5 --text 'page 0'       # 发任意一条原始指令
加 `--dry` 只打印不发送。

探测原理：淘晶驰对**不存在的控件**回错误码（`02 FF FF FF` = 无效控件 ID），对存在的控件**静默**。
"""

import argparse
import sys
import time
from pathlib import Path

VENDOR = Path(__file__).resolve().parent.parent / "hw_bridge" / "vendor"
sys.path.insert(0, str(VENDOR))
try:
    import serial  # type: ignore
except ImportError:
    sys.stdout.buffer.write(f"pyserial 不可用，找过：{VENDOR}\n".encode("utf-8"))
    raise SystemExit(1)

TERM = b"\xff\xff\xff"

# ===== 权威图片 ID 对照表（来源：队友工程 工创赛.HMI，2026-10-01 解出）=====
SHAPES = ["圆柱", "正方体", "正三棱柱", "正四棱锥", "正四面体", "正五棱锥", "正六棱柱"]
PIC_ID = {
    ("黄色", "圆柱"): 2,  ("黄色", "正方体"): 3,  ("黄色", "正六棱柱"): 4,
    ("黄色", "正三棱柱"): 5, ("黄色", "正四棱锥"): 6, ("黄色", "正四面体"): 7, ("黄色", "正五棱锥"): 8,
    ("蓝色", "圆柱"): 9,  ("蓝色", "正六棱柱"): 10, ("蓝色", "正三棱柱"): 11,
    ("蓝色", "正四棱锥"): 12, ("蓝色", "正四面体"): 13, ("蓝色", "正五棱锥"): 14,
    ("绿色", "圆柱"): 15, ("绿色", "正方体"): 16, ("绿色", "正六棱柱"): 17,
    ("绿色", "正三棱柱"): 18, ("绿色", "正四棱锥"): 19, ("绿色", "正四面体"): 20, ("绿色", "正五棱锥"): 21,
}
ID_NAME = {}
for (color, shape), rid in PIC_ID.items():
    ID_NAME[rid] = (color, shape)
ID_NAME[1] = ("—", "大赛logo")


def out(text: str = "") -> None:
    sys.stdout.buffer.write((text + "\n").encode("utf-8"))


def short(color: str, shape: str) -> str:
    """屏上队友用的写法：'黄色'→'黄'，中间一个短横"""
    return f"{color[0]}-{shape}"


class Screen:
    def __init__(self, port_name: str, baud: int, dry: bool = False):
        self.dry = dry
        self.port_name, self.baud = port_name, baud
        self.port = None

    def __enter__(self):
        if self.dry:
            return self
        self.port = serial.Serial(self.port_name, self.baud, timeout=0.25,
                                  bytesize=8, parity="N", stopbits=1)
        self.port.dtr = False
        self.port.rts = False
        time.sleep(0.3)
        self.port.reset_input_buffer()
        return self

    def __exit__(self, *a):
        if self.port:
            self.port.close()

    def send(self, text: str) -> bytes:
        """发一条屏指令（GBK + FF FF FF），返回屏回的字节（空 = 正常）

        ⚠️ 读取要"收到后继续等一小段"：淘晶驰的回复可能被拆成两次到达，
        只 read 一次会把 `02 FF FF FF` 读成 `00 00 00 FF` 这种碎片，
        表现为「明明存在的控件被判成不存在」。
        """
        if self.dry:
            out(f"    [dry] {text}")
            return b""
        self.port.reset_input_buffer()
        self.port.write(text.encode("gbk") + TERM)
        self.port.flush()
        buf = b""
        deadline = time.time() + 0.30
        while time.time() < deadline:
            chunk = self.port.read(64)
            if chunk:
                buf += chunk
                deadline = time.time() + 0.10      # 收到就再宽限 100ms（等可能的后半截）
        return buf


def cmd_probe(args) -> int:
    names = ([f"n{i}" for i in range(14)] + [f"t{i}" for i in range(8)] +
             [f"p{i}" for i in range(8)] + ["pic0", "t12", "n12"])
    with Screen(args.port, args.baud, args.dry) as s:
        out(f"探测 {args.port} @ {args.baud} 8N1 —— 有回复=控件不存在，静默=存在\n")
        out(f"{'控件':<8}{'结论':<12}回复")
        out("-" * 44)
        for name in names:
            r = s.send(f"vis {name},1")
            if r:
                out(f"{name:<8}{'❌ 不存在':<12}{' '.join('%02X' % b for b in r[:4])}")
            else:
                out(f"{name:<8}{'✅ 存在':<12}—")
    return 0


def cmd_reset(args) -> int:
    """把屏恢复成干净的初态：序号 1~6、名称空、计数 0"""
    with Screen(args.port, args.baud, args.dry) as s:
        s.send("page 0")
        for i in range(6):
            s.send(f"n{i}.val={i+1}")
            s.send(f't{i}.txt=""')
            s.send(f"n{6+i}.val=0")
        out("✅ 已复位：序号 1~6、名称清空、计数 0")
    return 0


def cmd_maptest(args) -> int:
    """验证图片 ID 对照：6 列各放一件（默认刻意打乱顺序），看屏上「图 vs 文字」是否对得上"""
    demo = [
        (3,  "黄色", "正方体"),    # 列1
        (14, "蓝色", "正五棱锥"),  # 列2
        (16, "绿色", "正方体"),    # 列3
        (5,  "黄色", "正三棱柱"),  # 列4
        (21, "绿色", "正五棱锥"),  # 列5
        (9,  "蓝色", "圆柱"),      # 列6
    ]
    out("=== 图片 ID 对照测试：屏上 6 列各显示一件，请核对「图片」与「名称」是否一致 ===")
    out(f"{'列':<4}{'序号':<6}{'名称':<12}{'图片ID':<8}")
    out("-" * 40)
    for i, (rid, color, shape) in enumerate(demo):
        out(f"{i+1:<4}{i+1:<6}{short(color, shape):<12}{rid:<8}")
    out("")
    with Screen(args.port, args.baud, args.dry) as s:
        s.send("page 0")
        for i, (rid, color, shape) in enumerate(demo):
            s.send(f"n{i}.val={i+1}")                 # 序号 1~6
            s.send(f't{i}.txt="{short(color, shape)}"')  # 名称（GBK）
            s.send(f"p{i}.pic={rid}")                 # 图片
            s.send(f"n{6+i}.val={(i+1)*11}")          # 计数 11/22/33/44/55/66
            out(f"  已写 列{i+1}: {short(color, shape)} / pic={rid} / 计数={(i+1)*11}")
    out("\n✅ 已发送。请核对屏上：第 N 列的文字是否就是第 N 列图片里的那件货；")
    out("   序号行应为 1~6，最下面那行应为 11 22 33 44 55 66。")
    return 0


def cmd_show(args) -> int:
    color, shape = ID_NAME.get(args.id, ("?", "?"))
    name = args.name or short(color, shape)
    with Screen(args.port, args.baud, args.dry) as s:
        s.send("page 0")
        s.send(f"n{args.col-1}.val={args.col}")
        s.send(f't{args.col-1}.txt="{name}"')
        s.send(f"p{args.col-1}.pic={args.id}")
        if args.count is not None:
            s.send(f"n{6+args.col-1}.val={args.count}")
        out(f"✅ 列{args.col} ← 名称「{name}」图片ID={args.id} 计数={args.count}")
    return 0


def cmd_raw(args) -> int:
    with Screen(args.port, args.baud, args.dry) as s:
        r = s.send(args.text)
        out(f"已发：{args.text}")
        out(f"屏回复：{' '.join('%02X' % b for b in r) if r else '（空 = 正常）'}")
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description="淘晶驰串口屏直驱（权威语义版）")
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--port", default="COM5")
    common.add_argument("--baud", type=int, default=9600)
    common.add_argument("--dry", action="store_true", help="只打印不发送")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("probe", parents=[common], help="探测控件是否存在").set_defaults(func=cmd_probe)
    sub.add_parser("reset", parents=[common], help="复位屏面：序号 1~6、名称空、计数 0").set_defaults(func=cmd_reset)
    sub.add_parser("maptest", parents=[common], help="验证图片 ID 对照（6 列打乱）").set_defaults(func=cmd_maptest)

    sh = sub.add_parser("show", parents=[common], help="单独写某一列")
    sh.add_argument("--col", type=int, required=True, help="列号 1~6")
    sh.add_argument("--id", type=int, required=True, help="图片 ID（2~21）")
    sh.add_argument("--name", default="", help="名称文本（不给则由 ID 推）")
    sh.add_argument("--count", type=int, default=None, help="该列计数")
    sh.set_defaults(func=cmd_show)

    rw = sub.add_parser("raw", parents=[common], help="发任意原始指令")
    rw.add_argument("--text", required=True)
    rw.set_defaults(func=cmd_raw)

    args = p.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
