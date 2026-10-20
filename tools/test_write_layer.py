"""P3 写入收口层自测 —— 真实 import src.models.writer，跑行为矩阵。

关键验证点：
  1. commit_model_write 永不抛异常（喂畸形输入）
  2. 默认零行为：key_unify 关闭时只抹空白，不改 Key
  3. 幂等：连跑两次结果一致
  4. Key 收敛：众数胜出；平分秋色时不动
  5. 绝不误伤：sk-api- / 智谱短 key 幸存
  6. report 里能看见 warn（Key 分裂应被报出）
  7. suggest_for_changed 精准匹配
"""
import os
import sys, copy, json

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

PASS = 0
FAIL = 0
OUT = []


def log(*a):
    OUT.append(" ".join(str(x) for x in a))


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        log("  [OK]   %s %s" % (name, detail))
    else:
        FAIL += 1
        log("  [FAIL] %s %s" % (name, detail))


from src.models.writer import (
    commit_model_write, normalize_whitespace, unify_bailian_keys, suggest_for_changed,
)

K_A = "sk-ws-H.PRERXHP." + "a" * 89 + "AAAAAAAAAA"      # 115
K_B = "sk-ws-H.PLPHEYH." + "b" * 91 + "BBBBBBBBBB"      # 117
K_C = "sk-ws-H.PLLDLHH." + "c" * 90 + "CCCCCCCCCC"      # 116
K_MAX = "sk-api-opYB53AqM" + "m" * 100 + "MMMMMMMMMM"   # 126
K_ZP = "ae1bd969f017416a" + "z" * 23 + "ZZZZZZZZZZ"     # 49


def make_cfg(enabled=False, mode="multi"):
    c = {"key_unify": {"enabled": enabled},
         "llm": {"openai": {"api_key": K_A}},
         "cloud_omni": {"api_key": K_A},
         "vision_models": {"models": [{"api_key": K_A} for _ in range(3)]},
         "text_chat": {"providers": [{"id": "bailian-chat", "api_key": K_B}]},
         "voice": {"clone": {"cloud": {"api_key": K_C}}},
         "characters": [{"id": "danya", "voice": {"api_key": K_MAX}}]}
    if mode == "tie":
        # 真正 2 : 2 平分 → 必须不动（清掉其它会干扰计数的字段）
        c = {"key_unify": {"enabled": True},
             "s1": {"api_key": K_B}, "s2": {"api_key": K_B},
             "s3": {"api_key": K_C}, "s4": {"api_key": K_C}}
    return c


log("=== P3 写入收口层自测 ===")
log("")

# ---- 1. 默认零行为（key_unify 关闭）----
log("--- 1. 默认零行为（开关关闭）→ Key 一把不动 ---")
c = make_cfg(enabled=False)
snap = copy.deepcopy(c)
r = commit_model_write(c, changed="llm.openai", source="t")
check("key_unified.applied == False", r["key_unified"]["applied"] is False)
check("配置的 Key 分布未变", c == snap)
check("返回结构完整", set(["normalized", "key_unified", "report", "ok", "source", "changed"]) <= set(r.keys()))
log("")

# ---- 2. 抹空白 ----
log("--- 2. 抹首尾空白 ---")
c = make_cfg(enabled=False)
c["llm"]["openai"]["api_key"] = "  " + K_A + "  "
c["text_chat"]["providers"][0]["base_url"] = "  https://x.com/v1  "
n = normalize_whitespace(c)
check("抹掉 2 处空白", n == 2, "n=%d" % n)
check("api_key 已 strip", c["llm"]["openai"]["api_key"] == K_A)
check("base_url 已 strip", c["text_chat"]["providers"][0]["base_url"] == "https://x.com/v1")
log("")

# ---- 3. Key 收敛（众数胜出）----
log("--- 3. 开关打开 → 众数 Key(A) 胜出，统一全树 ---")
c = make_cfg(enabled=True)
r = commit_model_write(c, changed="", source="t")
check("applied == True", r["key_unified"]["applied"] is True)
check("key_len == 115", r["key_unified"]["key_len"] == 115, "len=%d" % r["key_unified"]["key_len"])
vals = {
    c["text_chat"]["providers"][0]["api_key"],
    c["voice"]["clone"]["cloud"]["api_key"],
}
check("B 与 C 都变成 A", vals == {K_A}, "vals=%s" % [v[:12] for v in vals])
check("MiniMax 幸存", c["characters"][0]["voice"]["api_key"] == K_MAX)
check("vision 3 个仍为 A", all(m["api_key"] == K_A for m in c["vision_models"]["models"]))
log("")

# ---- 4. 平分时不动 ----
log("--- 4. 候选平分（2:2）→ 必须不动 ---")
c = make_cfg(enabled=True, mode="tie")
snap = copy.deepcopy(c)
r = commit_model_write(c, changed="", source="t")
check("applied == False（平分不敢猜）", r["key_unified"]["applied"] is False, json.dumps(r["key_unified"]))
check("配置未变", c == snap)
check("candidates 报出 2 种", len(r["key_unified"]["candidates"]) >= 2,
      str(r["key_unified"]["candidates"]))
log("")

# ---- 5. 幂等 ----
log("--- 5. 幂等（连跑两次） ---")
c = make_cfg(enabled=True)
commit_model_write(c, changed="", source="1")
s1 = copy.deepcopy(c)
r2 = commit_model_write(c, changed="", source="2")
check("第二次 replaced == 0", r2["key_unified"]["replaced"] == 0,
      "replaced=%d" % r2["key_unified"]["replaced"])
check("结果一致", c == s1)
log("")

# ---- 6. 畸形输入不抛异常 ----
log("--- 6. 畸形输入 → 必须不抛异常 ---")
bad_cases = [
    ({}, "空 dict"),
    ({"key_unify": None}, "key_unify=None"),
    ({"key_unify": {"enabled": True}, "llm": "字符串不是 dict"}, "类型错乱"),
    ({"key_unify": {"enabled": True}, "text_chat": {"providers": [None, 1, "x"]}}, "列表含异类"),
    ({"key_unify": {"enabled": True}, "a": {"b": ["  ", None, 3.14]}}, "深层畸形"),
]
for cfg, desc in bad_cases:
    try:
        r = commit_model_write(cfg, changed="", source="bad")
        check("不抛异常: %s" % desc, True)
        check("  返回 ok=True", r.get("ok") is True)
    except Exception as e:
        check("不抛异常: %s" % desc, False, "抛出 %r" % e)
log("")

# ---- 7. report 能看见 warn（Key 分裂）----
log("--- 7. report 含 Key 分裂 warn（未开开关时也应报出） ---")
c = make_cfg(enabled=False)
r = commit_model_write(c, changed="", source="t")
warns = [x for x in r["report"] if x.get("level") == "warn"]
check("有 warn 级条目", len(warns) >= 1, "warn=%d 条" % len(warns))
key_warns = [x for x in warns if "百炼" in str(x.get("msg", ""))]
check("含「百炼 Key 分裂」告警", len(key_warns) >= 1,
      key_warns[0]["msg"][:60] if key_warns else "无")
check("ok == False（因为有 warn）", r["ok"] is False)
log("")

# ---- 8. 开开关统一后 warn 消失 ----
log("--- 8. 统一之后，「Key 分裂」warn 应消失 ---")
c = make_cfg(enabled=True)
r = commit_model_write(c, changed="", source="t")
key_warns2 = [x for x in r["report"] if "百炼" in str(x.get("msg", ""))]
check("Key 分裂告警已消失", len(key_warns2) == 0,
      key_warns2[0]["msg"][:60] if key_warns2 else "已消失")
log("")

# ---- 9. suggest_for_changed ----
log("--- 9. suggest_for_changed 精准匹配 ---")
c = make_cfg(enabled=False)
hits = suggest_for_changed(c, changed="voice.clone.cloud")
check("传 voice.clone.cloud 命中 voice.* 条目", len(hits) >= 1, "%d 条" % len(hits))
hits2 = suggest_for_changed(c, changed="")
check("传空 → 返回全部 diagnose", len(hits2) >= len(hits))
log("")

# ---- 10. 单值场景（只有一把 Key）----
log("--- 10. 只有一把 Key → applied=False 但 key_len 正确 ---")
c = {"key_unify": {"enabled": True}, "llm": {"openai": {"api_key": K_A}}}
r = commit_model_write(c, changed="", source="t")
check("applied == False（本来就统一）", r["key_unified"]["applied"] is False)
check("key_len == 115", r["key_unified"]["key_len"] == 115, "len=%d" % r["key_unified"]["key_len"])
log("")

# ---- 11. 只读契约（copy=True）----
log("--- 11. copy=True 只读契约：就地模式改、只读模式绝不改 ---")
c = make_cfg(enabled=True)
c["llm"]["openai"]["api_key"] = "  " + K_A + "  "     # 制造 1 处脏空白
snap = copy.deepcopy(c)
n_ro = normalize_whitespace(c, copy=True)
check("只读调用后原对象逐字段未变", c == snap)
check("只读调用仍能报出脏空白处数", n_ro >= 1, "n=%d" % n_ro)
r_unify = unify_bailian_keys(c, copy=True)
check("只读 unify 后原对象逐字段未变", c == snap)
check("只读 unify 报出候选", len(r_unify.get("candidates", [])) >= 1,
      str(r_unify.get("candidates")))
# 就地模式必须真的会改，否则 copy 开关形同虚设
n_rw = normalize_whitespace(c)
check("就地模式确实 strip", n_rw >= 1 and c["llm"]["openai"]["api_key"] == K_A,
      "n=%d" % n_rw)
check("就地模式改动可见", c != snap)
log("")

log("=" * 60)
log("总计: PASS=%d  FAIL=%d" % (PASS, FAIL))
log("=" * 60)

# 【2026-10-21 修·两处】
# ① 原来把结果写到 `G:\ai对话\11\docs\_p3test.txt` —— 旧项目目录，本机不存在，
#    直接 FileNotFoundError。结果已经打在日志里，不再写盘。
# ② 原来**没有设置退出码**：即使 FAIL 也返回 0，是典型的「装饰闸门」。
sys.exit(0 if FAIL == 0 else 1)
