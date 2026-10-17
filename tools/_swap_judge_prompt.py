# -*- coding: utf-8 -*-
"""一次性脚本：把 relationship_judge.py 里的内嵌 _JUDGE_SYSTEM 换为「候选库择优 + 降级兜底」。"""
from pathlib import Path

P = Path(r"G:\ai对话\11\src\companion\relationship_judge.py")
lines = P.read_text(encoding="utf-8").splitlines()

start = next(i for i, l in enumerate(lines) if l.startswith('_JUDGE_SYSTEM = """'))
end = next(i for i in range(start + 1, len(lines)) if lines[i].rstrip().endswith('"""'))
print(f"替换行区间 {start + 1}..{end + 1}（原提示词 {sum(len(l) for l in lines[start:end + 1])} 字）")

NEW = '''# 【2026-10-13 定轨】裁判提示词迁到 relationship_judge_prompts.py：
#   7 组候选在**同一模型**（生产自动快车道 zai-glm/glm-4-flash）、同一批 43 句语料
#   同场 A/B 实测择优，选中 F_patchshot —— 方向正确率 81.4%→88.4%、
#   边界句 6/10→10/10、sufficient 3/5→5/5、自一致性极差 0.050→0.000（6 句×3 次零抖动）。
#   调提示词请改那个文件，改完跑 tools/relationship_prompt_ab_probe.py 复测。
try:
    from .relationship_judge_prompts import BEST_JUDGE_SYSTEM as _JUDGE_SYSTEM
except Exception as _e:  # noqa: BLE001
    # 不静默：候选库缺失/语法错 → 打日志 + 降级内嵌精简版，绝不裸奔
    logger.warning("[关系裁判] 提示词候选库导入失败（%s: %s）→ 降级内嵌精简提示词",
                   type(_e).__name__, _e)
    _JUDGE_SYSTEM = """你是「关系裁判」，判断用户这句话对「角色↔用户」关系的影响，只输出 JSON。

规则：
1. 先看主语：说"我"（我累/我烦死了/我真没用）= 他难受 → valence 正、care 高、harsh 0；
   说"你"（你笨/你烦死了/你真没用）= 骂她 → harsh 高、valence 负。
2. 撒娇反语（带"才/哪有/呢/嘛/哈哈"的否认）= 亲昵正向；字面否定 = 拒绝负向。
3. 正向：表白 0.55~0.85；关心 0.45~0.75；感谢 0.35~0.55；夸赞 0.40~0.60；
   他袒露脆弱 0.20~0.45；分享 0.15~0.35；调侃 0.05~0.30；求确认 0.25~0.45；
   吃醋 0.30~0.50；道歉挽回 0.40~0.70。
4. 负向：骂她重（滚/闭嘴/废物/破程序）-0.70~-0.95；中（你真没用/你别黏人烦死了）-0.40~-0.60；
   轻（你走开/行了）-0.15~-0.35；拒绝感情 -0.50~-0.80。
5. 敷衍（嗯/哦/随便/在）→ valence -0.10~-0.30、perfunctory ≥0.7、sufficient false；
   但"我喜欢你""想你"这类短而明确的话 → sufficient true。
6. 中性闲聊 → valence ≈0，其余全 0。

字段：valence(-1~1) / care / perfunctory / repair / harsh / depth / effort (0~1) /
sufficient(bool) / confidence(0~1) / reason(≤40字)。

输出：{"valence":0.8,"care":0.9,"perfunctory":0,"repair":0,"harsh":0,"depth":0.9,"effort":0.8,"sufficient":true,"confidence":0.95,"reason":"用户表达喜欢"}"""'''

lines[start:end + 1] = NEW.splitlines()
P.write_text("\n".join(lines) + "\n", encoding="utf-8")
print("写入完成")
