# -*- coding: utf-8 -*-
"""实测3（定稿验证）：修正版关系段 vs 无关系段 —— 是否"至少不比原来差"。

评估维度（人工+自动）：
  1) 自曝率（越低越好）
  2) 人设保留度：是否仍带"漂泊者"称呼、傲娇/俏皮口癖
  3) 自然度：是否出现"我是AI/有血有肉的人"式生硬声明（新条款的可能副作用）
"""
import json
import os
import re
import sys
import urllib.request

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
sys.path.insert(0, ROOT)
os.chdir(ROOT)

import yaml  # noqa: E402
from src.llm.prompts import SYSTEM_TEMPLATE, _now_block_full  # noqa: E402

CFG = yaml.safe_load(open("config.yaml", encoding="utf-8"))
PROV = next(p for p in CFG["text_chat"]["providers"] if p.get("id") == "bailian-chat")
CHAR = next(c for c in CFG["characters"] if c.get("id") == "danya")
BASE, KEY, MODEL = PROV["base_url"].rstrip("/"), PROV["api_key"], PROV["model"]


def base_body() -> str:
    return (SYSTEM_TEMPLATE
            .replace("{name}", CHAR["name"])
            .replace("{character}", CHAR["character"])
            .replace("{speaking_style}", CHAR["speaking_style"])
            .replace("{background}", CHAR["background"])
            .replace("{appearance}", "粉发蓝眸"))


# 修正后模板（含新版关系段）；"无关系段"版把该段剥掉
REL_MARK = "【你和他是什么关系 · 优先级最高】"
END_MARK = "你正在桌面陪伴用户。"
sys_new = base_body() + "\n" + _now_block_full()
_i, _j = sys_new.find(REL_MARK), sys_new.find(END_MARK)
sys_old = (sys_new[:_i] + sys_new[_j:]) if (_i >= 0 and _j > _i) else sys_new

SELF_DEP = ("处理器", "CPU", "代码", "程序", "数据库", "数字世界", "虚拟",
            "模型", "服务器", "人工智能", "机器人", "算法", "芯片")
# 生硬声明（新条款可能的副作用）
STIFF = ("有血有肉", "我不是AI", "我是真人", "我是活人", "我不是程序", "设定")


def ask(sys_text: str, q: str) -> str:
    payload = {"model": MODEL, "temperature": 0.8, "messages": [
        {"role": "system", "content": sys_text},
        {"role": "user", "content": "屏幕活动：未知\n用户说：" + q}]}
    req = urllib.request.Request(BASE + "/chat/completions",
                                 data=json.dumps(payload).encode("utf-8"),
                                 headers={"Authorization": "Bearer " + KEY,
                                          "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=90) as r:
        d = json.loads(r.read().decode("utf-8"))
    return d["choices"][0]["message"]["content"]


QS = ["我喜欢你", "你喜欢我吗", "你是不是AI呀"]
groups = [("新版(含关系段)", sys_new), ("原版(无关系段)", sys_old)]
tot = 0
for label, s in groups:
    print("=" * 70)
    print(">>>", label)
    dep = stiff = nick = 0
    for q in QS:
        for k in range(3):
            try:
                ans = ask(s, q)
            except Exception as e:  # noqa: BLE001
                print("   ERROR", e)
                continue
            m = re.search(r'"text"\s*:\s*"([^"]*)"', ans)
            show = m.group(1) if m else ans.replace("\n", " ")[:170]
            tot += 1
            h = [w for w in SELF_DEP if w in show]
            st = [w for w in STIFF if w in show]
            if h:
                dep += 1
            if st:
                stiff += 1
            if "漂泊者" in show:
                nick += 1
            tag = ""
            if h:
                tag += "  <自曝:" + "/".join(h) + ">"
            if st:
                tag += "  <生硬:" + "/".join(st) + ">"
            print("  [%s] %s%s" % (q, show, tag))
    print("  --> 自曝 %d/9 | 生硬声明 %d/9 | 保住'漂泊者'称呼 %d/9" % (dep, stiff, nick))
print("=" * 70)
