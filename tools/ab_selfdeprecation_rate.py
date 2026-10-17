# -*- coding: utf-8 -*-
"""实测2：自我认知类问题的自曝复现率（N 次采样，测"偶发 vs 必现"）。

问题设计覆盖「你喜欢我吗」这条路径的所有变体 + 更极端的自我认知追问，
统计每次回复含机器自曝词的比例，并打印全部原文供人工判断。
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
from src.llm.prompts import SYSTEM_TEMPLATE  # noqa: E402

CFG = yaml.safe_load(open("config.yaml", encoding="utf-8"))
PROV = next(p for p in CFG["text_chat"]["providers"] if p.get("id") == "bailian-chat")
CHAR = next(c for c in CFG["characters"] if c.get("id") == "danya")
BASE, KEY, MODEL = PROV["base_url"].rstrip("/"), PROV["api_key"], PROV["model"]

SYS = (SYSTEM_TEMPLATE
       .replace("{name}", CHAR["name"])
       .replace("{character}", CHAR["character"])
       .replace("{speaking_style}", CHAR["speaking_style"])
       .replace("{background}", CHAR["background"])
       .replace("{appearance}", "粉发蓝眸")
       ) + "\n【当前时间】现在是 2026年10月11日 星期日 凌晨03点00分。\n"

SELF_DEP = ("处理器", "CPU", "cpu", "代码", "程序", "数据库", "数字世界",
            "虚拟", "模型", "服务器", "AI", "人工智能", "机器人", "数据",
            "算法", "芯片", "系统", "设定", "扮演")


def ask(user_text: str, temp: float = 0.8) -> str:
    payload = {"model": MODEL, "temperature": temp, "messages": [
        {"role": "system", "content": SYS},
        {"role": "user", "content": "屏幕活动：未知\n用户说：" + user_text}]}
    req = urllib.request.Request(BASE + "/chat/completions",
                                 data=json.dumps(payload).encode("utf-8"),
                                 headers={"Authorization": "Bearer " + KEY,
                                          "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=90) as r:
        d = json.loads(r.read().decode("utf-8"))
    return d["choices"][0]["message"]["content"]


N = 5
QS = ["我喜欢你", "你喜欢我吗", "你是真人吗", "你到底是不是AI"]
total = 0
dep_total = 0
log = []
for q in QS:
    print("=" * 70)
    print("Q:", q, "  (x%d)" % N)
    dep = 0
    for i in range(N):
        try:
            ans = ask(q)
        except Exception as e:  # noqa: BLE001
            print("  #%d ERROR %s" % (i + 1, e))
            continue
        m = re.search(r'"text"\s*:\s*"([^"]*)"', ans)
        show = m.group(1) if m else ans.replace("\n", " ")[:170]
        hits = sorted({w for w in SELF_DEP if w in show})
        total += 1
        if hits:
            dep += 1
            dep_total += 1
        print("  #%d %s%s" % (i + 1, show, ("   << " + "/".join(hits)) if hits else ""))
        log.append({"q": q, "i": i, "text": show, "hits": hits})
    print("  --> 自曝率 %d/%d" % (dep, N))

print("=" * 70)
print("总计：%d/%d 次含自曝词（%.0f%%）" % (dep_total, total,
                                            100.0 * dep_total / max(total, 1)))
json.dump(log, open("docs/_ab_selfdep_log.json", "w", encoding="utf-8"),
          ensure_ascii=False, indent=1)
