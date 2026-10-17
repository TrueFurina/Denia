# -*- coding: utf-8 -*-
p = r"G:/ai对话/.workbuddy/memory/2026-10-13.md"
note = (
    "\n"
    "## 19:26 回怼块去描述词 + 真人感清洗器（用户要求去掉【】动作描写）
"
    "- 问题：即便回怼块加了[别用括号写动作]硬规则，GLM-4-Flash 仍会在傲娇人设下冒出【傲娇地翻个白眼】/（轻哼）。
"
    "- 解法：两道防线的第二条——新增 tools/stage_direction_strip.py 的 strip_stage_directions，出管道前剥离【】/（）舞台提示；
"
    "  此清洗器一并修掉历史 bug[（歪头）泄漏进语音]。块里也加[真人感硬规则]作第一道。
"
    "- 重测：原始输出偶漏、清洗后 100% 清零且语气保留（～等），5 调侃全回怼无认怂、11 正常/边缘无过怼（[别理我]那句[哼]是护栏误报，实际是好的不认怂回应）。
"
    "- 分析更新：docs/_banter_analysis.txt（v2）。块=docs/_banter_block_prototype.txt；自测=tools/banter_selftest.py（含原始+清洗对照）。
"
    "- 待拍板：接入生产时把清洗器放进 src/companion 回复后处理 + 回怼块追加进 prompts.py SYSTEM_TEMPLATE。
"
)
with open(p, "a", encoding="utf-8") as f:
    f.write(note)
print("appended")
