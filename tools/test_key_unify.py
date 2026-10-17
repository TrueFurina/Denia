"""P1 key_unify 功能验证 —— 真实调用 webapp 里的收口函数，不靠人眼看代码。

验证矩阵：
  1. 开关关闭（默认）→ 一把都不改（保证"不改变既有行为"）
  2. 开关打开 + 合法百炼 Key → 统一全部百炼 Key
  3. 开关打开 + 空 Key → 不动（防灾难：空 key 会把 28 处清空）
  4. 开关打开 + 非百炼 Key（智谱短 key / sk-api-）→ 不动
  5. 开关打开 → sk-api-（MiniMax）必须幸存
  6. 开关打开 → 智谱短 key 必须幸存
  7. 幂等：连跑两次结果一致
"""
import sys, os, json, copy
sys.path.insert(0, r"G:\ai对话\11")

# 直接加载 webapp 里的三个纯函数（不 import 整个 webapp，避免拉起 GUI 依赖）
import importlib.util

SRC = r"G:\ai对话\11\src\ui\webapp.py"
OUT = []


def log(*a):
    OUT.append(" ".join(str(x) for x in a))


# --- 从 webapp.py 抽出这几个纯函数（它们不依赖 self，可独立执行）---
import ast, textwrap

with open(SRC, encoding="utf-8") as f:
    tree = ast.parse(f.read())

WANT = {"_key_unify_enabled", "_looks_like_bailian_key", "_collect_bailian_keys",
        "_unify_bailian_key", "_diffuse_bailian_key"}

src_lines = open(SRC, encoding="utf-8").read().splitlines(keepends=True)
chunks = []
for node in tree.body:
    if isinstance(node, ast.FunctionDef) and node.name in WANT:
        chunks.append("".join(src_lines[node.lineno - 1: node.end_lineno]))
    if isinstance(node, ast.Assign):
        pass

ns = {}
# logger 桩
class _L:
    def info(self, *a, **k): pass
    def warning(self, *a, **k): pass
ns["logger"] = _L()

code = "\n\n".join(chunks)
exec(compile(code, "extracted", "exec"), ns)

_key_unify_enabled = ns["_key_unify_enabled"]
_looks_like_bailian_key = ns["_looks_like_bailian_key"]
_collect_bailian_keys = ns["_collect_bailian_keys"]
_unify_bailian_key = ns["_unify_bailian_key"]

log("=== 抽取函数成功，共 %d 个 def ===" % len(chunks))
log("")

# --- 构造测试 config：模拟当前真实情况（3 把不同百炼 Key + 干扰项）---
# 真实长度：前缀"sk-ws-H.PRERXHP."=16 字符 + 中段 + 后缀 10 字符
K_115 = "sk-ws-H.PRERXHP." + "x" * 89 + "KcsuGYw8xA"
K_117 = "sk-ws-H.PLPHEYH." + "y" * 91 + "tfFgiU4wYI"
K_116 = "sk-ws-H.PLLDLHH." + "z" * 90 + "2quLFxNHfo"
K_MINIMAX = "sk-api-opYB53AqM" + "m" * 100 + "zQiEZzChjM"
K_ZHIPU = "ae1bd969f017416a" + "w" * 23 + "Boatpll5c9"

assert len(K_115) == 115, len(K_115)
assert len(K_117) == 117, len(K_117)
assert len(K_116) == 116, len(K_116)
assert len(K_MINIMAX) == 126, len(K_MINIMAX)
assert len(K_ZHIPU) == 49, len(K_ZHIPU)


def make_cfg():
    return {
        "key_unify": {"enabled": False},
        "llm": {"openai": {"api_key": K_115}},
        "cascade": {"api_key": K_115},
        "agent": {"llm": {"api_key": K_115}},
        "cloud_omni": {"api_key": K_115},
        "text_chat": {"providers": [
            {"id": "bailian-chat", "api_key": K_117},
            {"id": "bailian-deepseek-v3", "api_key": K_115},
            {"id": "zai-glm", "api_key": K_ZHIPU},
            {"id": "siliconflow", "api_key": ""},
        ]},
        "vision_models": {"models": [{"api_key": K_115} for _ in range(20)]},
        "voice_profiles": [{"id": "tmu1ik64h", "api_key": K_115}],
        "voice": {"clone": {"cloud": {"api_key": K_116}}},
        "characters": [
            {"id": "danya", "voice": {"api_key": K_MINIMAX}},
            {"id": "danya_test", "voice": {"api_key": K_MINIMAX}},
        ],
    }


def summarize(cfg):
    """统计各 key 值的出现次数。"""
    acc = []

    def w(x):
        if isinstance(x, dict):
            for k, v in x.items():
                if isinstance(v, str) and v.startswith(("sk-", "ae1")):
                    acc.append(v)
                else:
                    w(v)
        elif isinstance(x, list):
            for v in x:
                w(v)
    w(cfg)
    d = {}
    for s in acc:
        d[s[:16] + ".." + s[-4:]] = d.get(s[:16] + ".." + s[-4:], 0) + 1
    return d


PASS = 0
FAIL = 0


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        log("  [OK]   %s %s" % (name, detail))
    else:
        FAIL += 1
        log("  [FAIL] %s %s" % (name, detail))


# ---- 用例 1：开关关闭 → 一把都不改 ----
log("--- 用例1：开关关闭（默认）→ 必须一把都不改 ---")
c = make_cfg()
before = copy.deepcopy(c)
n = _unify_bailian_key(c, K_115, source="test")
check("替换数为 0", n == 0, "n=%d" % n)
check("配置逐字段未变", c == before)
log("  分布: " + json.dumps(summarize(c), ensure_ascii=False))
log("")

# ---- 用例 2：开关打开 → 统一为 K_115 ----
log("--- 用例2：开关打开 + 合法百炼 Key → 统一 ---")
c = make_cfg()
c["key_unify"]["enabled"] = True
n = _unify_bailian_key(c, K_115, source="test")
check("替换数 > 0", n > 0, "n=%d" % n)
dist = summarize(c)
check("只剩 3 种 key（K_115 + MiniMax + 智谱）", len(dist) == 3, json.dumps(dist, ensure_ascii=False))
check("K_117 已消失", not any(k.startswith("sk-ws-H.PLPHEYH") for k in dist))
check("K_116 已消失", not any(k.startswith("sk-ws-H.PLLDLHH") for k in dist))
check("MiniMax(sk-api-) 幸存", any(k.startswith("sk-api-opYB53AqM") for k in dist))
check("智谱短 key 幸存", any(k.startswith("ae1bd969f017416a") for k in dist))
# 精确核对：20 个 vision + 28 处 + text_chat[1] 等都应变 K_115
vm_keys = [m["api_key"] for m in c["vision_models"]["models"]]
check("20 个 vision Key 全为 K_115", all(k == K_115 for k in vm_keys), "样例=%s" % vm_keys[0][:12])
check("text_chat[0] 变 K_115", c["text_chat"]["providers"][0]["api_key"] == K_115)
check("voice.clone.cloud 变 K_115", c["voice"]["clone"]["cloud"]["api_key"] == K_115)
check("char danya 未被碰", c["characters"][0]["voice"]["api_key"] == K_MINIMAX)
check("空 key 仍是空", c["text_chat"]["providers"][3]["api_key"] == "")
log("  分布: " + json.dumps(dist, ensure_ascii=False))
log("")

# ---- 用例 3：空 Key → 不动 ----
log("--- 用例3：开关打开但 new_key 为空 → 必须不动（防灾难） ---")
c = make_cfg()
c["key_unify"]["enabled"] = True
before = copy.deepcopy(c)
n = _unify_bailian_key(c, "", source="test")
check("替换数为 0", n == 0, "n=%d" % n)
check("28 处 K_115 未被清空", c == before)
log("")

# ---- 用例 4：非百炼 Key → 不动 ----
log("--- 用例4：开关打开但 new_key 是智谱短 key → 必须不动 ---")
c = make_cfg()
c["key_unify"]["enabled"] = True
before = copy.deepcopy(c)
n = _unify_bailian_key(c, K_ZHIPU, source="test")
check("替换数为 0", n == 0, "n=%d" % n)
check("配置未变", c == before)
log("")

log("--- 用例5：开关打开但 new_key 是 sk-api-（MiniMax）→ 必须不动 ---")
c = make_cfg()
c["key_unify"]["enabled"] = True
before = copy.deepcopy(c)
n = _unify_bailian_key(c, K_MINIMAX, source="test")
check("替换数为 0", n == 0, "n=%d" % n)
check("配置未变", c == before)
log("")

# ---- 用例 6：幂等 ----
log("--- 用例6：幂等（连跑两次） ---")
c = make_cfg()
c["key_unify"]["enabled"] = True
_unify_bailian_key(c, K_115, source="t1")
snap1 = copy.deepcopy(c)
n2 = _unify_bailian_key(c, K_115, source="t2")
check("第二次替换数为 0", n2 == 0, "n=%d" % n2)
check("结果与第一次一致", c == snap1)
log("")

# ---- 用例 7：_looks_like_bailian_key 边界 ----
log("--- 用例7：_looks_like_bailian_key 边界 ---")
_L = lambda p: p + "a" * (50 - len(p))  # 补足到 50 位
cases = [
    (_L("sk-ws-"), True),
    (_L("sk-"), True),
    ("sk-api-abc", False),
    ("ae1bd9", False),
    ("", False), (None, False),
    ("  " + _L("sk-ws-x") + "  ", True),
    ("SK-WS-X", False),
    # ⭐灾难防护：只有前缀的残缺值必须拒绝（2026-10-08 自测抓到）
    ("sk-", False),
    ("sk-api", False),
    ("sk-ws-", False),
    ("sk-" + "a" * 10, False),      # 太短
    ("sk-" + "a" * 36, False),      # 39 位，仍太短
    ("sk-" + "a" * 37, True),       # 40 位，刚好通过
]
for v, want in cases:
    got = _looks_like_bailian_key(v)
    disp = (v[:34] + "…len=%d" % len(v)) if isinstance(v, str) and len(v) > 34 else repr(v)
    check("looks_like(%s) == %s" % (disp, want), got == want, "got=%s" % got)
log("")

# ---- 用例 8：_key_unify_enabled 边界 ----
log("--- 用例8：_key_unify_enabled 边界 ---")
for v, want in [({}, False), ({"key_unify": {}}, False),
                ({"key_unify": {"enabled": True}}, True),
                ({"key_unify": {"enabled": False}}, False),
                ({"key_unify": "bad"}, False), (None, False)]:
    got = _key_unify_enabled(v)
    check("enabled(%r) == %s" % (v, want), got == want, "got=%s" % got)

log("")
log("=" * 60)
log("总计: PASS=%d  FAIL=%d" % (PASS, FAIL))
log("=" * 60)

open(r"G:\ai对话\11\docs\_p1test.txt", "w", encoding="utf-8").write("\n".join(OUT))
