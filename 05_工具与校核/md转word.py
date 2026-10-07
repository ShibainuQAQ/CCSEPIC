# -*- coding: utf-8 -*-
"""
把 Markdown 转成 Word 能正确解析的 HTML，再由 Word 另存为 .docx。

用法：
    python md转word.py 校选方案说明书.md
会生成同名 .html，然后用同目录的 转docx.ps1 交给 Word 另存。

支持：标题 / 段落 / 粗体 / 行内代码 / 表格 / 代码块 / 引用 / 有序无序列表 / 分隔线
"""
import io
import os
import re
import sys
import html

CSS = """
body {
  font-family: 宋体, SimSun, serif;
  font-size: 12pt;
  line-height: 1.6;
  margin: 0;
}
h1 { font-family: 黑体, SimHei; font-size: 18pt; text-align: center; margin: 18pt 0 12pt; }
h2 { font-family: 黑体, SimHei; font-size: 15pt; margin: 16pt 0 8pt; }
h3 { font-family: 黑体, SimHei; font-size: 13pt; margin: 12pt 0 6pt; }
h4, h5, h6 { font-family: 黑体, SimHei; font-size: 12pt; margin: 10pt 0 6pt; }
p { margin: 0 0 8pt; }
table { border-collapse: collapse; width: 100%; margin: 8pt 0; }
th, td { border: 1px solid #000000; padding: 4pt 6pt; font-size: 10.5pt; vertical-align: top; }
th { background-color: #eeeeee; font-family: 黑体, SimHei; font-weight: bold; }
/* 新宋体(NSimSun) 的中文宽度恰为西文的两倍，能保住文本框图的字符对齐 */
pre {
  font-family: 新宋体, NSimSun, Consolas, monospace;
  font-size: 9pt;
  line-height: 1.25;
  background-color: #f7f7f7;
  border: 1px solid #cccccc;
  padding: 6pt;
  margin: 8pt 0;
}
code { font-family: Consolas, 新宋体, monospace; font-size: 10.5pt; }
blockquote {
  border-left: 3pt solid #999999;
  padding-left: 10pt;
  margin: 8pt 0;
  color: #333333;
}
hr { border: none; border-top: 1px solid #999999; margin: 12pt 0; }
ul, ol { margin: 0 0 8pt; padding-left: 22pt; }
li { margin-bottom: 3pt; }
"""


def inline(text):
    """处理行内标记。先转义再插标签。"""
    text = html.escape(text, quote=False)
    text = re.sub(r"`([^`]+)`", r"<code>\1</code>", text)
    text = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", text)
    text = re.sub(r"(?<!\*)\*([^*]+)\*(?!\*)", r"<em>\1</em>", text)
    return text


def split_row(line):
    return [c.strip() for c in line.strip().strip("|").split("|")]


TABLE_SEP = re.compile(r"^\s*\|[\s\-:|]+\|\s*$")
LIST_U = re.compile(r"^\s*[-*+]\s+(.*)$")
LIST_O = re.compile(r"^\s*\d+\.\s+(.*)$")
HEADING = re.compile(r"^(#{1,6})\s+(.*)$")
STARTS_BLOCK = re.compile(r"^(#{1,6}\s|\s*\||\s*>|```|\s*([-*+]|\d+\.)\s|\s*---)")


def convert(md):
    lines = md.split("\n")
    out = []
    i = 0
    while i < len(lines):
        line = lines[i]

        # 代码块
        if line.strip().startswith("```"):
            i += 1
            buf = []
            while i < len(lines) and not lines[i].strip().startswith("```"):
                buf.append(lines[i])
                i += 1
            i += 1  # 跳过结束的 ```
            out.append("<pre>" + html.escape("\n".join(buf)) + "</pre>")
            continue

        # 表格
        if line.strip().startswith("|") and i + 1 < len(lines) and TABLE_SEP.match(lines[i + 1]):
            header = split_row(line)
            i += 2
            rows = []
            while i < len(lines) and lines[i].strip().startswith("|"):
                rows.append(split_row(lines[i]))
                i += 1
            out.append("<table>")
            out.append("<tr>" + "".join("<th>%s</th>" % inline(c) for c in header) + "</tr>")
            for r in rows:
                out.append("<tr>" + "".join("<td>%s</td>" % inline(c) for c in r) + "</tr>")
            out.append("</table>")
            continue

        # 标题
        m = HEADING.match(line)
        if m:
            lvl = len(m.group(1))
            out.append("<h%d>%s</h%d>" % (lvl, inline(m.group(2)), lvl))
            i += 1
            continue

        # 分隔线
        if re.match(r"^\s*(-{3,}|\*{3,})\s*$", line):
            out.append("<hr>")
            i += 1
            continue

        # 引用
        if line.strip().startswith(">"):
            buf = []
            while i < len(lines) and lines[i].strip().startswith(">"):
                buf.append(inline(lines[i].strip()[1:].strip()))
                i += 1
            out.append("<blockquote>" + "<br>".join(buf) + "</blockquote>")
            continue

        # 无序列表
        if LIST_U.match(line):
            buf = []
            while i < len(lines):
                mm = LIST_U.match(lines[i])
                if not mm:
                    break
                buf.append(inline(mm.group(1)))
                i += 1
            out.append("<ul>" + "".join("<li>%s</li>" % b for b in buf) + "</ul>")
            continue

        # 有序列表
        if LIST_O.match(line):
            buf = []
            while i < len(lines):
                mm = LIST_O.match(lines[i])
                if not mm:
                    break
                buf.append(inline(mm.group(1)))
                i += 1
            out.append("<ol>" + "".join("<li>%s</li>" % b for b in buf) + "</ol>")
            continue

        # 空行
        if not line.strip():
            i += 1
            continue

        # 段落（合并连续的普通行）
        buf = [line.strip()]
        i += 1
        while i < len(lines) and lines[i].strip() and not STARTS_BLOCK.match(lines[i]):
            buf.append(lines[i].strip())
            i += 1
        out.append("<p>%s</p>" % inline(" ".join(buf)))

    return "\n".join(out)


def run_word(html_path, docx_path):
    """借本机已装的 Word，把 HTML 另存为 .docx（用 Word 自己的排版引擎）。

    两点说明：
    1. 中文路径在 PowerShell 里容易因编码判定不一而乱码，所以统一先复制到
       纯英文的临时目录再转换，路径全程只含 ASCII。
    2. 用 -Command 传内联脚本，不写 .ps1 文件，因此不需要 -ExecutionPolicy Bypass
       （执行策略约束的是脚本文件，内联命令不受其管辖）。
    """
    import shutil
    import subprocess
    import tempfile

    tmp = tempfile.mkdtemp(prefix="md2docx_")
    try:
        tmp_html = os.path.join(tmp, "in.html")
        tmp_docx = os.path.join(tmp, "out.docx")
        shutil.copyfile(html_path, tmp_html)

        # 单引号包裹路径，避免 PowerShell 把路径里的字符当变量解析
        ps = (
            "$ErrorActionPreference='Stop';"
            "$w=New-Object -ComObject Word.Application;"
            "$w.Visible=$false;$w.DisplayAlerts=0;"
            "try{"
            "$d=$w.Documents.Open('%s',$false,$false);"
            "$d.PageSetup.PageWidth=595.3;$d.PageSetup.PageHeight=841.9;"
            "$d.PageSetup.TopMargin=72.0;$d.PageSetup.BottomMargin=72.0;"
            "$d.PageSetup.LeftMargin=79.0;$d.PageSetup.RightMargin=79.0;"
            "$d.SaveAs('%s',16);"          # 16 = wdFormatDocumentDefault (.docx)
            "$n=$d.Tables.Count;$d.Close(0);"
            "Write-Output ('TABLES='+$n)"
            "}finally{$w.Quit()}"
            % (tmp_html, tmp_docx)
        )

        # 不经过 shell，整个脚本作为单个参数传给 PowerShell
        proc = subprocess.run(
            ["powershell.exe", "-NoProfile", "-Command", ps],
            capture_output=True,
        )
        out = proc.stdout.decode("utf-8", "replace").strip()
        err = proc.stderr.decode("utf-8", "replace").strip()

        if not os.path.exists(tmp_docx):
            return False, (err or out or "Word 未生成文件")

        shutil.move(tmp_docx, docx_path)
        return True, out
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def main():
    if len(sys.argv) < 2:
        print("用法: python md转word.py <文件.md>")
        sys.exit(1)

    src = sys.argv[1]
    if not os.path.isabs(src):
        src = os.path.join(os.getcwd(), src)

    with io.open(src, encoding="utf-8") as fh:
        md = fh.read()

    body = convert(md)
    page = (
        '<!DOCTYPE html>\n<html><head>\n'
        '<meta http-equiv="Content-Type" content="text/html; charset=utf-8">\n'
        '<meta charset="utf-8">\n'
        "<title>%s</title>\n<style>%s</style>\n</head>\n<body>\n%s\n</body></html>"
        % (html.escape(os.path.basename(src)), CSS, body)
    )

    base = os.path.splitext(src)[0]
    html_path = base + ".html"
    docx_path = base + ".docx"

    # 带 BOM 写出，确保 Word 能正确识别 UTF-8
    with io.open(html_path, "w", encoding="utf-8-sig") as fh:
        fh.write(page)

    ok, msg = run_word(html_path, docx_path)
    if ok:
        os.remove(html_path)  # 中间产物，转换成功即清理
        print("已生成: " + docx_path + "  (" + msg + ")")
    else:
        print("HTML 已生成，但转 Word 失败：" + msg)
        print("中间文件保留在: " + html_path)
        sys.exit(2)


if __name__ == "__main__":
    main()
