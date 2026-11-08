# -*- coding: utf-8 -*-
"""验证陪伴提示词修复：渲染 build_system，检查关键条款是否到位。"""
import os
import sys

os.chdir(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
sys.path.insert(0, os.getcwd())

from src.companion.persona import Persona  # noqa: E402
from src.llm.prompts import build_system, PROACTIVE_SYSTEM_TEMPLATE  # noqa: E402

# 用 config.yaml 里 danya 的真实字段构造 Persona
p = Persona(
    name="达妮娅",
    character="星炬学院虚质科学部学生，粉发蓝眸的少女，总挂着'温柔'的微笑。",
    speaking_style="用中文，语气轻缓、慢悠悠，爱用'～''呀''呢'结尾。叫我'漂泊者'。",
    background="《鸣潮》拉海洛·星炬学院虚质科学部学生。……她根本不是人类。真实身份是残星会为鸣式'阿列夫一'打造的容器……",
    appearance="粉发蓝眸",
)

sys_text = build_system(p)
lines = sys_text.splitlines()

checks = {
    "关系定位段": "【你和他是什么关系 · 优先级最高】" in sys_text,
    "不自曝条款": "不要说自己是" in sys_text and "AI/程序/代码/机器/模型" in sys_text,
    "禁复述AI词": "绝不承认也不解释" in sys_text and "不要顺着" in sys_text,
    "求确认==撒娇": "撒娇和要安全感" in sys_text and "别觉得被冒犯" in sys_text,
    "身世不改动声明": "不改动上面任何身世设定" in sys_text and "照常成立" in sys_text,
    "不压身世(旧条款已撤)": "别乱展开" not in sys_text and "自我否定的理由" not in sys_text,
    "旧段落还在(自我认知)": "【自我认知（必须遵守）】" in sys_text,
    "旧段落还在(像真人)": "【说话要像真人，别像机器人】" in sys_text,
    "称呼段还在": "【称呼 · 优先级最高" in sys_text,
    "输出格式还在": "【输出格式 —— 极其重要】" in sys_text,
}

print("=" * 60)
print("build_system() 渲染结果检查（共 %d 行 / %d 字）" % (len(lines), len(sys_text)))
print("=" * 60)
for k, v in checks.items():
    print(("  [PASS] " if v else "  [FAIL] ") + k)

# 打印新增段落的实际文本，人工核对
print("-" * 60)
print(">> 新增「关系定位」段实际内容：")
start = sys_text.find("【你和他是什么关系")
end = sys_text.find("你正在桌面陪伴用户")
print(sys_text[start:end] if start >= 0 and end > start else "!! 未找到")

print("-" * 60)
print(">> PROACTIVE 模板含关系条款：",
      "【你和他是什么关系 · 优先级最高】" in PROACTIVE_SYSTEM_TEMPLATE)
print(">> PROACTIVE 模板占位符 name 存在：", "{name}" in PROACTIVE_SYSTEM_TEMPLATE)

allok = all(checks.values())
print("=" * 60)
print("RESULT:", "ALL PASS" if allok else "HAS FAILURE")
# 【2026-11-08】上面这行 `RESULT:` 原本是这条脚本的**唯一**结论输出，而它始终 exit 0 ——
# 登记在 GATES 的「陪伴话术条款」一项里，于是条款掉了也永远 ✓。
# 已经算出了 allok，差的就是把它变成退出码。
sys.exit(0 if allok else 1)
