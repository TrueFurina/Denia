# -*- coding: utf-8 -*-
"""①MEMORY.md 增 §2aj（8k 限带两层损失）②日志追加 ③清理暂存文件"""
import os, time

MEM = r'G:\ai对话\.workbuddy\memory\MEMORY.md'
LOG = r'G:\ai对话\.workbuddy\memory\2026-09-21.md'
ts = time.strftime('%Y-%m-%d %H:%M')

def read_norm(p):
    raw = open(p, 'rb').read()
    crlf = b'\r\n' in raw
    s = raw.decode('utf-8')
    if crlf:
        s = s.replace('\r\n', '\n')
    assert '\r' not in s
    return s, crlf

s, crlf = read_norm(MEM)
assert '### 2aj' not in s and '**2aj' not in s

SEC = (
    "- \u2605**2aj 8kHz \u9650\u5e26\u7684\u4ee3\u4ef7\uff1d\u4e24\u5c42\u72ec\u7acb\u635f\u5931\uff0809-21 \u5b9e\u6d4b\uff0c`tools/_band_cost.py`\uff09**\uff1a"
    "\u628a\u771f\u6e90\u4e0e\u514b\u9686**\u90fd\u9650\u5e26\u5230 8k**\u518d\u9010\u500d\u9891\u7a0b\u6bd4 \u21d2 "
    "\u2460 **\u5e26\u5bbd\u622a\u65ad\u53ea\u5f71\u54cd >8k**\uff1a8-12k \u5dee **10.5dB**\u30018k+ \u80fd\u91cf\u5360\u6bd4 \u771f\u6e90 **5.27%** vs \u514b\u9686 **0.22%**\uff08**\u5dee 24 \u500d**\uff09\u3001"
    "f99 **10168 vs 3571Hz**\uff1b\u2461 **\u58f0\u7801\u5668\u8fc7\u5e73\u6ed1\u5f71\u54cd 4-8k\uff08\u5e26\u5bbd\u4e4b\u5185\uff09**\uff1a4-6k \u4f4e **4.9dB**\u30016-8k \u4f4e **5.5dB**\uff08"
    "0-8k \u603b\u4f53 RMS \u504f\u5dee 3.3dB\uff09\u21d2 **\u201c\u4e0d\u50cf\u201d\u6709\u4e00\u534a\u8d23\u4efb\u5728\u5e26\u5bbd\u4e4b\u5916\u3002** "
    "\u8c31\u503e\u659c\uff1a\u771f\u6e90 **\u22123.42** / \u514b\u9686 **\u22125.61** / \u539f\u751f\u7b7e\u540d \u22125.99 dB/oct"
    "\uff08EQ1 \u4fee\u6b63\u5230 **\u22123.14**\uff0c\u540c\u65f6\u628a 0-8k RMS \u504f\u5dee **3.3\u21921.3dB**\u30018k+ \u5360\u6bd4 0.22%\u21923.21%\uff09\u3002"
    "\u000a  \U0001F511 **\u5224\u636e**\uff1a>8k \u90a3 \u221214.7dB **\u4e0d\u662f\u201c\u7f3a\u201d\uff0c\u662f\u201c\u88ab\u6a21\u578b\u5148\u9a8c\u66ff\u6362\u201d**"
    "\uff08\u539f\u751f\u7b7e\u540d\u97f3\u81ea\u8eab 9-12k = \u221284~\u2212108dB\uff0c\u4e0d\u53ef\u80fd\u6765\u81ea\u5b83\uff09\uff1bEQ \u53ea\u80fd\u6539\u5e45\u5ea6\u8c31\u5f62\u72b6\uff0c**\u6539\u4e0d\u4e86\u7eb9\u7406\u6765\u6e90\u4e0e\u76f8\u4f4d/\u77ac\u6001**\u3002"
    "\u000a  \u000a  \U0001F9F9 **\u540c\u65f6\u5b8c\u6210 MEMORY.md \u7b2c\u4e8c\u6b21\u7626\u8eab**\uff1a34490 \u2192 **19344 B**"
    "\uff08\u53bb\u6389 \u00a72af/\u00a72ag \u7684**\u6574\u5757\u91cd\u590d**\uff09\uff1b\u5168\u6587\u5f52\u6863 REFERENCE **\u00a724**\uff0c"
    "\u97f3\u8272/\u5e26\u5bbd/\u53cc\u5de5\u4e3b\u9898\u6574\u7406\u4e3a **\u00a725**\uff1b\u8be6\u7ec6\u63a8\u5bfc \u2192 `11/docs/8kHz\u5e26\u5bbd\u4ee3\u4ef7\u4e0e\u97f3\u8272\u5931\u771f\u5f52\u56e0.md`\u3002"
    "\u000a"
)
anchor = "## 3. \u672a\u4fee\u9879"
assert s.count(anchor) == 1
s2 = s.replace(anchor, SEC + anchor, 1)
assert '2aj' in s2
open(MEM, 'wb').write(s2.replace('\n', '\r\n').encode('utf-8'))
print('MEMORY.md -> %d bytes' % len(s2.replace('\n', '\r\n').encode('utf-8')))

# ---- 日志 ----
ls, lcrlf = read_norm(LOG)
ADD = (
    '\n\n## ' + ts + ' \u00b7 8kHz \u5e26\u5bbd\u4ee3\u4ef7\u5b9a\u91cf + MEMORY \u7626\u8eab\n\n'
    '### 1) MEMORY.md \u7626\u8eab\uff08\u5f3a\u5236\u4efb\u52a1\uff1a\u6ce8\u5165\u65f6\u88ab\u622a\u65ad\uff09\n'
    '- 34490 \u2192 **19344 B**\uff08\u221244%\uff09\u3002\u53d1\u73b0 **\u00a72af/\u00a72ag \u6574\u5757\u91cd\u590d\u4e24\u904d**\uff08\u4e0a\u4e00\u8f6e\u5199\u5165\u91cd\u590d\u8ffd\u52a0\uff09\u3002\n'
    '- \u9884\u5907\u4efd\uff1a`MEMORY.md.bak_slim_*` / `.bak_v2_*` / `.bak_v3_*`\uff1b`REFERENCE.md.bak_v25_*`\u3002\n'
    '- \u5f52\u6863\uff1a\u7626\u8eab\u524d\u5168\u6587\u2192 REFERENCE **\u00a724**\uff1b\u6309\u4e3b\u9898\u91cd\u6574\u4e3a **\u00a725**\uff08\u5e26\u5bbd/\u6d88\u878d/\u771f\u53cc\u5de5\uff09\u3002\n'
    '- \u5de5\u5177\uff1a`tools/_slim_memory.py`\u3001`_slim_memory2.py`\u3001`_slim_memory3.py`\n\n'
    '### 2) 8kHz \u9650\u5e26\u4ee3\u4ef7\uff08\u65b0\u5de5\u5177 `tools/_band_cost.py`\uff09\n'
    '- \u5173\u952e\u65b9\u6cd5\uff1a\u628a**\u771f\u6e90\u4e0e\u514b\u9686\u90fd\u91cd\u91c7\u6837\u5230 16k\uff08\u5f3a\u5236\u9650\u5e26\uff09\u518d\u9010\u500d\u9891\u7a0b\u6bd4** \u21d2 \u628a\u201c\u5e26\u5bbd\u622a\u65ad\u201d\u4e0e\u201c\u58f0\u7801\u5668\u8fc7\u5e73\u6ed1\u201d\u4e24\u5c42\u635f\u5931\u5206\u5f00\u3002\n'
    '- \u7ed3\u679c\u2460\uff08>8k\uff09\uff1a8-12k \u5dee **10.5dB**\uff1b8k+ \u5360\u6bd4 5.27% vs **0.22%**\uff08\u5dee 24\u00d7\uff09\uff1bf99 **10168 vs 3571Hz**\u3002\n'
    '- \u7ed3\u679c\u2461\uff08**4-8k\uff0c\u5728\u5e26\u5bbd\u4e4b\u5185**\uff09\uff1a4-6k \u4f4e **4.9dB**\u30016-8k \u4f4e **5.5dB** \u21d2 \u8fc7\u5e73\u6ed1\u662f\u72ec\u7acb\u4e8e\u5e26\u5bbd\u7684\u7b2c\u4e8c\u4e2a\u635f\u5931\u6e90\u3002\n'
    '- \u8c31\u503e\u659c\uff1a\u771f\u6e90 \u22123.42 / \u514b\u9686 \u22125.61 / \u539f\u751f\u7b7e\u540d \u22125.99 dB/oct\uff1bEQ1 \u2192 \u22123.14\u3002\n'
    '- \u4ea4\u4ed8\uff1a`docs/8kHz\u5e26\u5bbd\u4ee3\u4ef7\u4e0e\u97f3\u8272\u5931\u771f\u5f52\u56e0.md`\uff08\u9700\u6c42\uff1a\u7528\u6237\u95ee\u201c\u8fbe\u4e0d\u5230 8kHz \u4f1a\u5e26\u6765\u54ea\u4e9b\u5177\u4f53\u95ee\u9898\u201d\uff09\u3002\n'
)
ls2 = ls.rstrip('\n') + ADD
open(LOG, 'wb').write(ls2.replace('\n', '\r\n' if lcrlf else '\n').encode('utf-8'))
print('log -> %d bytes' % len(ls2.encode('utf-8')))

# ---- 清理暂存 ----
tmp = r'G:\ai对话\11\tools\_mem_new_index.md'
if os.path.exists(tmp):
    os.remove(tmp)
    print('removed staging file')
