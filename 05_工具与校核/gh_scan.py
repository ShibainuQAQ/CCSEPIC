# -*- coding: utf-8 -*-
"""批量看仓库基本信息 + 默认分支 + CAD 文件清单"""
import json
import re
import sys
import urllib.request

sys.stdout.reconfigure(encoding='utf-8', errors='replace')
UA = {'User-Agent': 'dsh-probe', 'Accept': 'application/vnd.github+json'}
REPOS = [
    'Source-Robotics/SSG-48-adaptive-electric-gripper',
    'Source-Robotics/MSG-compliant-AI-stepper-gripper',
    'Toyota/yubi-hw',
    'FyrbyAdditive/SO-ARM101-STS3250',
    'roboninecom/SO-ARM100-101-Parallel-Gripper',
]
CAD = re.compile(r'\.(stl|step|stp|3mf|f3d|obj|ipt|sldprt)$', re.I)


def get(url):
    with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=40) as r:
        return json.load(r)


for repo in REPOS:
    print('=' * 70)
    print(repo)
    try:
        info = get('https://api.github.com/repos/' + repo)
    except Exception as e:
        print('  repo lookup FAIL:', e)
        continue
    br = info.get('default_branch')
    print('  branch=%s  stars=%s  size=%sKB  lic=%s  archived=%s' % (
        br, info.get('stargazers_count'), info.get('size'),
        (info.get('license') or {}).get('spdx_id'), info.get('archived')))
    print('  desc=%s' % (info.get('description') or '')[:110])
    try:
        t = get('https://api.github.com/repos/%s/git/trees/%s?recursive=1' % (repo, br))
    except Exception as e:
        print('  tree FAIL:', e)
        continue
    files = [f for f in t.get('tree', []) if f['type'] == 'blob']
    cad = [f for f in files if CAD.search(f['path'])]
    print('  files=%d  cad=%d  truncated=%s' % (len(files), len(cad), t.get('truncated')))
    for f in cad[:25]:
        print('     %8d  %s' % (f.get('size', 0), f['path']))
