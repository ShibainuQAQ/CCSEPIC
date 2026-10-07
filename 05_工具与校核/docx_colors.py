# -*- coding: utf-8 -*-
r"""
从 .docx 里提取文字，并把"带颜色的文字"标出来（队友用颜色标注的文档 -> 可读文本）。
同时把 Word 批注（review comments）一并导出到文件末尾。

用法:
    python 05_工具与校核\docx_colors.py <输入.docx> [输出.txt]

输出标记:
    {C=FF0000}红字{/}        字体颜色
    {HL=yellow}黄底{/}       荧光笔高亮
没加标记的文字 = 默认黑色。

本机控制台是 GBK，所以结果一律写 UTF-8 文件再读；stdout 只打印 ASCII 统计。
"""
import sys
import re
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

W = '{http://schemas.openxmlformats.org/wordprocessingml/2006/main}'

OPEN = '{'
CLOSE = '}'


def run_text(r):
    t = ''.join(n.text or '' for n in r.iter(W + 't'))
    if not t:
        return ''
    tags = []
    rPr = r.find(W + 'rPr')
    if rPr is not None:
        c = rPr.find(W + 'color')
        if c is not None:
            v = c.get(W + 'val')
            if v and v.lower() not in ('auto', '000000'):
                tags.append('C=' + v)
        hl = rPr.find(W + 'highlight')
        if hl is not None:
            v = hl.get(W + 'val')
            if v and v != 'none':
                tags.append('HL=' + v)
    if not tags:
        return t
    head = OPEN + ','.join(tags) + CLOSE
    return head + t + '{/}'


def para_text(p):
    """按文档顺序拼一段文字，并把批注锚点标出来：〔#N起〕...〔#N止〕/【批注#N】"""
    out = []
    for el in p:
        if el.tag == W + 'r':
            ref = el.find(W + 'commentReference')
            if ref is not None:
                out.append('【批注#%s】' % ref.get(W + 'id'))
            t = run_text(el)
            if t:
                out.append(t)
        elif el.tag == W + 'commentRangeStart':
            out.append('〔#%s起〕' % el.get(W + 'id'))
        elif el.tag == W + 'commentRangeEnd':
            out.append('〔#%s止〕' % el.get(W + 'id'))
    return ''.join(out)


def walk(node, out, depth=0):
    pad = '  ' * depth
    for el in node:
        if el.tag == W + 'p':
            s = para_text(el).strip()
            if s:
                out.append(pad + s)
        elif el.tag == W + 'tbl':
            out.append(pad + '[TABLE]')
            for tr in el.findall(W + 'tr'):
                cells = []
                for tc in tr.findall(W + 'tc'):
                    sub = []
                    walk(tc, sub, depth + 1)
                    cells.append(' '.join(x.strip() for x in sub))
                out.append(pad + '  | ' + ' | '.join(cells))
            out.append(pad + '[/TABLE]')


def extract_comments(z):
    """Word 批注：word/comments.xml（正文里的批注锚点不解析，只导出批注内容）"""
    if 'word/comments.xml' not in z.namelist():
        return []
    root = ET.fromstring(z.read('word/comments.xml'))
    out = ['', '===== Word 批注（comments）=====']
    for c in root.findall(W + 'comment'):
        cid = c.get(W + 'id')
        author = c.get(W + 'author') or ''
        body = []
        walk(c, body, 1)
        out.append('[批注 id=%s 作者=%s] %s' % (cid, author, ' '.join(x.strip() for x in body)))
    return out


def main():
    if len(sys.argv) < 2:
        print('usage: docx_colors.py <in.docx> [out.txt]')
        return 2
    src = Path(sys.argv[1])
    out_path = Path(sys.argv[2]) if len(sys.argv) > 2 else src.with_suffix('.colored.txt')
    with zipfile.ZipFile(src) as z:
        xml = z.read('word/document.xml')
        comments = extract_comments(z)
    root = ET.fromstring(xml)
    body = root.find(W + 'body')
    out = []
    walk(body, out)
    text = '\n'.join(out + comments)
    out_path.write_text(text, encoding='utf-8')
    marks = len(re.findall(re.escape(OPEN + 'C='), text))
    hls = len(re.findall(re.escape(OPEN + 'HL='), text))
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    print('lines=%d colored_runs=%d highlight_runs=%d comments=%d'
          % (len(out), marks, hls, len([x for x in comments if x.startswith('[批注')])))
    print('out=%s' % out_path)
    return 0


if __name__ == '__main__':
    sys.exit(main())
