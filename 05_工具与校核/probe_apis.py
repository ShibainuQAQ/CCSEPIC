# -*- coding: utf-8 -*-
"""探测能不能从 Printables / Thingiverse 直接取到可下载的模型文件。"""
import json
import re
import sys
import urllib.request
import urllib.error

sys.stdout.reconfigure(encoding='utf-8', errors='replace')
UA = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
                    '(KHTML, like Gecko) Chrome/124.0 Safari/537.36',
      'Accept': 'text/html,application/json,*/*'}
GQL = 'https://api.printables.com/graphql/'


def post(url, payload, headers=None):
    h = dict(UA)
    h['Content-Type'] = 'application/json'
    if headers:
        h.update(headers)
    req = urllib.request.Request(url, data=json.dumps(payload).encode(), headers=h)
    with urllib.request.urlopen(req, timeout=40) as r:
        return r.read().decode('utf-8', 'replace')


def get(url, headers=None):
    h = dict(UA)
    if headers:
        h.update(headers)
    req = urllib.request.Request(url, headers=h)
    with urllib.request.urlopen(req, timeout=40) as r:
        return r.read().decode('utf-8', 'replace')


print('=== 1. Printables GraphQL 免登录搜索 ===')
q = {'query': 'query{ searchPrints2(query:"parallel gripper", limit:5, offset:0)'
              '{ items{ id name slug likesCount downloadCount } } }'}
try:
    t = post(GQL, q)
    print(t[:1200])
except Exception as e:
    print('FAIL:', type(e).__name__, e)
    try:
        print('body:', e.read().decode('utf-8', 'replace')[:400])
    except Exception:
        pass

print()
print('=== 2. Thingiverse thing 页面里的 cdn 资源直链 ===')
for tid in ['thing:4868526', 'thing:3521975']:
    url = 'https://www.thingiverse.com/' + tid
    try:
        html = get(url)
        assets = sorted(set(re.findall(r'https://cdn\.thingiverse\.com/assets/[^"\\\s]+', html)))
        print('%s  html=%d bytes  assets=%d' % (tid, len(html), len(assets)))
        for a in assets[:10]:
            print('    ', a)
    except Exception as e:
        print('%s FAIL: %s %s' % (tid, type(e).__name__, e))
