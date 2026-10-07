# -*- coding: utf-8 -*-
"""hmi_tool.py —— 解析淘晶驰 USART HMI 的工程文件（.HMI）

用法：
    python hmi_tool.py "C:\\Users\\...\\Desktop\\工创赛.HMI"            # 打印控件表 + 资源清单
    python hmi_tool.py "工创赛.HMI" --out _解析结果                     # 额外导出每张资源的图 + 拼图

能拿到什么（都是工程的原始数值，不是猜测）：
  ① 屏分辨率（page0 控件的 w/h）
  ② 每个控件的 **名称 / 类型 / x,y / w,h / 初值 / 图片ID**（序号 n0~n5、名称 t0~t5、图片 p0~p5、计数 n6~n11 …）
  ③ **资源 ID → 图片**（资源在工程里就叫 `<ID>.i` / `<ID>.is`，表项里带 [偏移, 长度]）

背景：2026-10-01 为了搞清「屏里到底哪个图片 ID 是哪件货」而逆出来的。工程文件是自有格式：
    记录 = [4字节标记][名字][填充到固定位置][值]，数值是小端；资源表项 = [u32 偏移][u32 长度][u32 其它]，
    偏移指向的图片前有 27 字节头。本工具只用这几条规律，改工程版本后如失效，先核对这三点。
"""
import os, re, sys, io, struct, argparse

ATTRS = {"type", "id", "objname", "vscope", "drag", "sendkey", "aph", "movex", "movey", "x", "y", "w", "h",
         "endx", "endy", "effect", "first", "time", "lockobj", "groupid0", "groupid1", "up", "down", "left",
         "right", "sta", "style", "key", "borderc", "borderw", "font", "bco", "picc", "pic", "pco", "xcen",
         "ycen", "val", "lenth", "format", "isbr", "spax", "spay", "txt", "dis", "en", "pw", "rad", "min",
         "max", "clear", "spd", "gain", "high", "low", "dir", "mode", "count", "open", "point", "pen",
         "curve", "vscope0", "sel", "delay"}

TYPE_DESC = {"att-28": "页面(page)", "att-39": "数字/文本(n/t)", "att-22": "图片(p)",
             "att-8": "图块/曲线(tc)", "att-24": "按钮(bt)", "att-26": "文本(t)"}


def load(path):
    return open(path, "rb").read()


def sniff(b, off):
    """图片在偏移处（或偏移+27）"""
    for delta, kind in ((0, ""), (27, "@+27")):
        p = off + delta
        if 0 <= p < len(b) - 4:
            if b[p:p+3] == b"\xff\xd8\xff":
                return "jpg" + kind
            if b[p:p+4] == b"\x89PNG":
                return "png" + kind
    return None


def parse_records(seg):
    cand, i, n = [], 0, len(seg)
    while i < n - 6:
        if seg[i+1] == 0 and seg[i+2] == 0 and seg[i+3] == 0 and seg[i] <= 0x20:
            m = re.match(rb"[\x21-\x7e]{1,16}", seg[i+4:i+24])
            if m:
                nm = m.group().decode("latin1")
                if nm in ATTRS or nm.startswith("att-") or nm.startswith("codes"):
                    cand.append((i, nm, i + 4 + len(m.group())))
                    i += 4
                    continue
        i += 1
    out = []
    for k, (mp, nm, vend) in enumerate(cand):
        nxt = cand[k+1][0] if k+1 < len(cand) else n
        out.append((nm, seg[vend:nxt].lstrip(b"\x00")))
    return out


def le(v):
    return None if not v else int.from_bytes(v[:4], "little")


def s_gbk(v):
    return v.split(b"\x00")[0].decode("gbk", "replace")


def dump_controls(b):
    """返回 (控件列表, 行文本列表)。控件列表项 = dict(att/objname/x/y/w/h/pic/val/txt)"""
    anchors = sorted(set(m.start() for m in re.finditer(rb"page0", b)))
    for a in anchors:
        start = a - 24
        seg = b[start:start + 24000]          # 一个 page 约 20KB；取太长会把文件里下一份副本也读进来
        ctrls, cur = [], None
        for nm, val in parse_records(seg):
            if nm.startswith("att-"):
                if nm == "att-28" and any(c["att"] == "att-28" for c in ctrls):
                    break                      # 又遇到一个页面控件 = 进入下一份副本，停
                if cur:
                    ctrls.append(cur)
                cur = {"att": nm, "attrs": {}}
                continue
            if cur is None:
                cur = {"att": "page", "attrs": {"objname": b"page0"}}
            cur["attrs"][nm] = val
        if cur:
            ctrls.append(cur)
        names = [s_gbk(c["attrs"].get("objname", b"")) for c in ctrls]
        if "n0" in names and len(ctrls) >= 10:
            out = []
            for c in ctrls[:60]:
                A = c["attrs"]
                out.append(dict(
                    att=c["att"], type=TYPE_DESC.get(c["att"], c["att"]),
                    objname=s_gbk(A.get("objname", b"")),
                    x=le(A.get("x")), y=le(A.get("y")), w=le(A.get("w")), h=le(A.get("h")),
                    pic=le(A.get("pic")), picc=le(A.get("picc")), val=le(A.get("val")),
                    font=le(A.get("font")), sta=le(A.get("sta")), txt=s_gbk(A.get("txt", b"")),
                ))
            return out, start
    return [], None


def dump_resources(b):
    """返回 {资源名: (偏移, 长度, 类型)}，只保留偏移处确实是图片的表项"""
    pat = re.compile(rb"(?:^|[\x00-\x20])((?:[0-9]{1,2})\.(?:i|is|zi|pa|s))")
    res = {}
    for m in pat.finditer(b):
        name = m.group(1).decode()
        name_start = m.end() - len(name)
        fe = name_start + 16
        if fe + 12 > len(b):
            continue
        a, s, _c = struct.unpack("<3I", b[fe:fe + 12])
        kind = sniff(b, a) if 0 < a < len(b) and 0 < s < 6_000_000 else None
        if kind and name.endswith((".i", ".is")):
            if name not in res or s > res[name][1]:
                res[name] = (a, s, kind)
    return res


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("hmi")
    ap.add_argument("--out", default=None, help="导出目录（每张资源一张图 + 控件表.txt）")
    args = ap.parse_args()

    b = load(args.hmi)
    print(f"工程文件: {args.hmi}   {len(b)} 字节")

    ctrls, start = dump_controls(b)
    print(f"\n=== 页面控件（page0 @{start}，共 {len(ctrls)} 个）===")
    print(f"{'类型':<12}{'名称':<8}{'x':>5}{'y':>5}{'w':>5}{'h':>5}{'pic':>6}{'val':>6}  文本")
    for c in ctrls:
        f = lambda v: "" if v in (None, 65535) else v
        print(f"{c['type']:<12}{c['objname']:<8}{f(c['x']):>5}{f(c['y']):>5}{f(c['w']):>5}{f(c['h']):>5}"
              f"{f(c['pic']):>6}{f(c['val']):>6}  {c['txt']}")

    res = dump_resources(b)
    ids = sorted(int(n.split(".")[0]) for n in res)
    print(f"\n=== 资源（{len(res)} 条，ID {min(ids) if ids else '-'}~{max(ids) if ids else '-'}）===")
    for n in sorted(res, key=lambda s: (int(s.split('.')[0]), s)):
        a, s, kind = res[n]
        print(f"  {n:<8} off={a:<10} size={s:<9} {kind}")

    if args.out:
        os.makedirs(args.out, exist_ok=True)
        for n, (a, s, kind) in res.items():
            blob = b[a:a + s]
            for magic in (b"\xff\xd8\xff", b"\x89PNG"):
                k = blob.find(magic)
                if 0 <= k < 128:
                    blob = blob[k:]
                    break
            ext = "jpg" if "jpg" in kind else "png"
            fn = os.path.join(args.out, f"res_{n.replace('.', '_')}.{ext}")
            open(fn, "wb").write(blob)
        with open(os.path.join(args.out, "控件表.txt"), "w", encoding="utf-8") as g:
            for c in ctrls:
                g.write(f"{c['att']}\t{c['objname']}\tx={c['x']}\ty={c['y']}\tw={c['w']}\th={c['h']}\t"
                        f"pic={c['pic']}\tval={c['val']}\ttxt={c['txt']}\n")
        print(f"\n已导出到 {args.out}")


if __name__ == "__main__":
    main()
