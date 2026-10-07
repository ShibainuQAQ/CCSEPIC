"""生成「随机抓取顺序」的屏指令十六进制载荷（**不碰串口**，所以能在沙箱里跑）。

背景
----
淘晶驰的指令必须以 `FF FF FF` 结尾，而 hw_bridge 的 `serial_cmd` 在文本模式下
只能追加**文本**结尾。所以这里把每条指令编译成**十六进制**，交给桥的 hex 模式：

    serial_cmd(hex="<本脚本输出的十六进制>", terminator="")

⚠️ 关键技巧：**一整件的所有指令可以拼成一条载荷**（屏只是消费字节流），
   所以 8 件只要 8 次 MCP 调用，不用几十次。

用法
----
    python seq_payloads.py probe t0 t1 t2 t3 t4 pic0 p0      # 探测控件是否存在
    python seq_payloads.py demo --count 8 --seed 7           # 随机抓取顺序
"""

import argparse
import random
import sys

TERM = b"\xff\xff\xff"

# 图片 ID 对照 —— 必须与 图片ID对照表.md 一致
ITEMS = {
    0: ("绿色", "圆柱"), 1: ("绿色", "正三棱柱"), 2: ("绿色", "正五棱锥"),
    3: ("绿色", "正六棱柱"), 4: ("绿色", "正四棱锥"), 5: ("绿色", "正四面体"),
    6: ("绿色", "正方体"),
    7: ("蓝色", "圆柱"), 8: ("蓝色", "正三棱柱"), 9: ("蓝色", "正五棱锥"),
    10: ("蓝色", "正六棱柱"), 11: ("蓝色", "正四棱锥"), 12: ("蓝色", "正四面体"),
    13: ("黄色", "圆柱"), 14: ("黄色", "正三棱柱"), 15: ("黄色", "正五棱锥"),
    16: ("黄色", "正六棱柱"), 17: ("黄色", "正四棱锥"), 18: ("黄色", "正四面体"),
    19: ("黄色", "正方体"),
}


def out(text: str = "") -> None:
    sys.stdout.buffer.write((text + "\n").encode("utf-8"))


def compile_cmd(text: str) -> bytes:
    """一条屏指令 → GBK 字节 + FF FF FF。"""
    return text.encode("gbk") + TERM


def hexof(commands) -> str:
    """多条指令拼成一条载荷，返回十六进制（大写、无空格 —— 桥会自己去掉空格）。"""
    blob = b"".join(compile_cmd(c) for c in commands)
    return blob.hex().upper()


def cmd_probe(args) -> int:
    out("# 探测控件是否存在：发 vis <名字>,1，**回错误码=不存在 / 静默=存在**")
    out("# 用法：对每个名字调一次 serial_cmd(hex=..., terminator=\"\")，然后 serial_read")
    out("")
    for name in args.names:
        out(f"{name:<6} {hexof([f'vis {name},1'])}")
    return 0


def cmd_demo(args) -> int:
    count = max(1, min(int(args.count), len(ITEMS)))
    rng = random.Random(args.seed)
    picks = rng.sample(sorted(ITEMS), count)

    out(f"# 随机抓取顺序：{count} 件（种子={args.seed if args.seed is not None else '随机'}）")
    out("# 每行 = 一整件的载荷，直接 serial_cmd(hex=该串, terminator=\"\")")
    out("")
    for index, item_id in enumerate(picks, start=1):
        color, shape = ITEMS[item_id]
        commands = [
            "page 0",
            f't0.txt="{index}"',            # 序号
            f't1.txt="{color}{shape}"',     # 名称（GBK）
            f"{args.pic_control}.pic={item_id}",  # 图片（控件名实测是 p0，不是 pic0）
        ]
        if args.total_control:
            commands.append(f'{args.total_control}.txt="{index}"')
        out(f"# 第 {index} 件：{color}{shape}（图片 ID {item_id}）")
        out(hexof(commands))
        out("")
    return 0


def cmd_mark(_args) -> int:
    """给每个可能的控件写一个**不同的标记** —— 拍一张屏的照片，就能反推出"谁是谁"。

    为什么要它：控制名存在 ≠ 知道它代表哪个格子。给 t0..t7 各写一个不同字母、
    给 p0..p5 各写一张**颜色不同**的图，一眼就能把"控件名 ↔ 表格位置"对上。
    """
    commands = ["page 0"]
    for index in range(8):
        commands.append(f't{index}.txt="{chr(ord("A") + index)}"')  # t0→A, t1→B, …

    # 特意挑**颜色不同**的图片，方便一眼区分（否则全是绿的，看图也分不清）
    marker_pics = [0, 6, 7, 13, 12, 19]  # 绿圆柱/绿正方体/蓝圆柱/黄圆柱/蓝四面体/黄正方体
    for index, pic in enumerate(marker_pics):
        commands.append(f"p{index}.pic={pic}")

    out("# 控件标记载荷：t0~t7 写 A~H，p0~p5 写 6 张**颜色不同**的图")
    out("# 用法：serial_cmd(hex=下面这串, terminator=\"\")，然后拍一张屏的照片")
    out("")
    out(hexof(commands))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="生成屏指令的十六进制载荷")
    sub = parser.add_subparsers(dest="cmd", required=True)

    probe = sub.add_parser("probe", help="生成 vis 探测载荷")
    probe.add_argument("names", nargs="+", help="要探测的控件名，如 t0 t1 pic0")
    probe.set_defaults(func=cmd_probe)

    demo = sub.add_parser("demo", help="生成随机抓取顺序的载荷")
    demo.add_argument("--count", default=8)
    demo.add_argument("--seed", type=int, default=None)
    demo.add_argument("--pic-control", default="p0",
                      help="图片控件名（实测本屏是 p0；不是 pic0）")
    demo.add_argument("--total-control", default="", help="成功总数控件名（不给就不写）")
    demo.set_defaults(func=cmd_demo)

    sub.add_parser("mark", help="给每个控件写不同标记，拍张照就能反推控件对照").set_defaults(func=cmd_mark)

    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
