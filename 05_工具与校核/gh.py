# -*- coding: utf-8 -*-
"""GitHub 检索 / 目录树 / 下载小工具（无需 token，走 urllib）
用法：
  python gh.py search "parallel gripper stl"        # 仓库搜索
  python gh.py tree  owner/repo  [branch]           # 递归列文件
  python gh.py files owner/repo  [branch]  <正则>    # 只列匹配文件
  python gh.py get   <url>  <本地路径>               # 下载（含 raw / jsdelivr / 镜像）
"""
import json
import os
import re
import sys
import urllib.request
import urllib.error

sys.stdout.reconfigure(encoding='utf-8', errors='replace')

UA = {'User-Agent': 'Mozilla/5.0 (dsh-probe)', 'Accept': 'application/vnd.github+json'}


def _get(url, timeout=40):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def search(q, sort='stars', n=15):
    url = 'https://api.github.com/search/repositories?q=%s&sort=%s&per_page=%d' % (
        urllib.parse.quote(q), sort, n)
    d = json.loads(_get(url))
    print('total=%d  query=%s' % (d.get('total_count', 0), q))
    for it in d.get('items', []):
        lic = (it.get('license') or {}).get('spdx_id') or '-'
        print('%-58s %6d*  lic=%-14s upd=%s' % (
            it['full_name'], it['stargazers_count'], lic, it['updated_at'][:10]))
        print('    %s' % (it.get('description') or '')[:150])


def tree(repo, branch='', pat=None):
    cands = []
    try:
        info = json.loads(_get('https://api.github.com/repos/' + repo))
        if info.get('default_branch'):
            cands.append(info['default_branch'])
    except Exception as e:
        print('repo lookup failed (%s), 继续试 main/master' % e)
    if branch:
        cands.insert(0, branch)
    for b in ('main', 'master', 'HEAD'):
        if b not in cands:
            cands.append(b)
    d = None
    errors = []
    for b in cands:
        try:
            d = json.loads(_get('https://api.github.com/repos/%s/git/trees/%s?recursive=1' % (repo, b)))
            branch = b
            break
        except Exception as e:
            errors.append('%s -> %s' % (b, e))
    if d is None:
        print('列目录失败: %s' % '; '.join(errors))
        return
    print('repo=%s branch=%s truncated=%s' % (repo, branch, d.get('truncated')))
    rx = re.compile(pat, re.I) if pat else None
    n = 0
    for f in d.get('tree', []):
        if f['type'] != 'blob':
            continue
        p = f['path']
        if rx and not rx.search(p):
            continue
        n += 1
        print('%9d  %s' % (f.get('size', 0), p))
    print('-- %d file(s) --' % n)


def get(url, out):
    data = _get(url, timeout=120)
    os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
    with open(out, 'wb') as f:
        f.write(data)
    print('OK %8d bytes -> %s' % (len(data), out))


if __name__ == '__main__':
    cmd = sys.argv[1]
    if cmd == 'search':
        search(sys.argv[2])
    elif cmd == 'tree':
        tree(sys.argv[2], sys.argv[3] if len(sys.argv) > 3 else '')
    elif cmd == 'files':
        tree(sys.argv[2], sys.argv[3] if len(sys.argv) > 3 else '', sys.argv[4] if len(sys.argv) > 4 else None)
    elif cmd == 'get':
        get(sys.argv[2], sys.argv[3])
    else:
        print(__doc__)
