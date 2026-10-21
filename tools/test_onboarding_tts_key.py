"""验证引导页「百炼 Key → 语音合成 payload」传播修复（2026-10-09）。

不启动 GUI：用 node + jsdom 加载 first_launch_setup.html 的那一段逻辑做不到
（页面依赖大量 DOM / iframe 环境）。改为**行为契约测试**：
  1. 直接从 first_launch_setup.html 抽出三处关键接线，做静态断言
     （变量声明存在、_finish 第三参存在、payload.voice/tts 恒为 null）；
  2. 锁住旧回退链不再被接回 payload（该机制已随能力槽重构退场）；
  3. 把产出的 tts payload 喂给「真实抽取的」apply_onboarding_voice 分支判定，
     证明 provider='dashscope_cosyvoice' 会走 dashscope 分支而不是 custom_tts
     —— 这正是修复前会踩的坑。

【2026-10-21 更新】第 2 段原来是「回退链优先级重放」，守的是
`_ttsPayload → BAILIAN_TTS_PAYLOAD → VOICE_PAYLOAD` 三级回退。能力槽重构后
`set_capability_slot` 成为唯一写入口并立即落盘，收尾再传一遍会覆盖用户刚设好的
配置，于是 payload.tts 改成恒 null —— 那套回退链整条退场。判据跟着改成守新契约。
第 3 段不受影响：apply_onboarding_voice 仍被设置页（保存音色档）使用，是活代码。
"""
import io
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HTML = os.path.join(ROOT, "src", "ui", "web", "first_launch_setup.html")
SRC = os.path.join(ROOT, "src", "ui", "webapp.py")
OUT = []


def log(*a):
    OUT.append(" ".join(str(x) for x in a))


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


html = io.open(HTML, encoding="utf-8").read()
src = io.open(SRC, encoding="utf-8").read()

log("=" * 66)
log("1. 静态接线断言：first_launch_setup.html")
log("=" * 66)

# ① 变量声明
check("声明了 BAILIAN_TTS_PAYLOAD",
      re.search(r"var\s+BAILIAN_TTS_PAYLOAD\s*=\s*null", html) is not None)

# ② _finish 接受第三参并在成功时缓存
m_finish = re.search(r"const _finish = \(ok, msg, ttsFixed\)\s*=>\s*\{(.*?)\n\s*\};", html, re.S)
check("_finish 签名带第三参 ttsFixed", m_finish is not None)
if m_finish:
    body = re.sub(r"\s+", " ", m_finish.group(1))
    check("_finish 成功分支缓存 BAILIAN_TTS_PAYLOAD",
          "if(ttsFixed) BAILIAN_TTS_PAYLOAD = ttsFixed" in body)
    check("_finish 失败分支清空 PROVIDER_PAYLOAD",
          "PROVIDER_PAYLOAD = null" in body)

# ③ 实测通过时构造真实 tts payload
check("构造 _bailianTts 且 backend=provider='dashscope_cosyvoice'",
      re.search(r"_bailianTts\s*=\s*\{[^}]*backend:'dashscope_cosyvoice'[^}]*"
                r"provider:'dashscope_cosyvoice'", html) is not None)
check("_finish(...) 第三个实参传了 _bailianTts",
      re.search(r"_finish\(true,\s*'已逐项实测：'\+_parts\.join\('；'\),\s*_bailianTts\)", html) is not None)

# ④ 出向 payload 的 voice / tts 字段
# 【2026-10-21 修·判据过时】原判据守的是 `tts: (_ttsPayload || BAILIAN_TTS_PAYLOAD ||
# VOICE_PAYLOAD) || null` 三级回退链（2026-10-09 为修「百炼 Key 选了却不生效」建的）。
# 能力槽重构后这套机制整条退场：`set_capability_slot` 成为引擎/语音/Key 的唯一写入口
# 并**立即落盘**，收尾时再传一遍就会覆盖用户刚设好的配置。于是 payload 改成恒 null。
# 判据跟着改，但守的东西更关键：**别被顺手改回去**。一旦有人把 TTS 塞回 payload，
# 就会静默覆盖能力槽里的选择，而且测试还是绿的（因为原来那条判据已经不成立了）。
m_null = re.search(r"voice:\s*null\s*,\s*//[^\n]*\n\s*tts:\s*null\s*,", html)
check("payload.voice / payload.tts 均为 null（能力槽已落盘，收尾不覆盖）", m_null is not None)
check("null 处留了注释说明原因（否则下一个人会当成漏填补上）",
      m_null is not None and len(m_null.group(0).split("//")[1].strip()) >= 6)

log("")
log("=" * 66)
log("2. 旧回退链已退场：payload 组装块不许再引用那两个变量")
log("=" * 66)

# 退场后 BAILIAN_TTS_PAYLOAD / VOICE_PAYLOAD 仍被写入（_finish 缓存、语音屏赋值），
# 但**没有任何读取点**。这是「写了没接线」的形态①，只是这次是前端、而且是成批的。
# 这里不把它们判红（删前端要走真机验证），只锁住"别再接到 payload 上"。
# 锚点必须是 `mode: MODE,` —— 文件里有两个 `const payload = {`（另一个是保存 LLM 配置
# 的函数），只写 `const payload = {` 会让非贪婪匹配一路吞到后文的 `};`（实测吞了 82KB，
# 于是"块里含 BAILIAN_TTS_PAYLOAD"恒为真 —— 判据自己变成了假绿）。
m_payload_blk = re.search(r"const payload = \{\s*\n\s*mode: MODE,(.*?)\n    \};", html, re.S)
check("找到 doFinish 的 payload 组装块", m_payload_blk is not None)
if m_payload_blk:
    blk = m_payload_blk.group(1)
    check("payload 组装块不引用 BAILIAN_TTS_PAYLOAD", "BAILIAN_TTS_PAYLOAD" not in blk)
    check("payload 组装块不引用 VOICE_PAYLOAD", "VOICE_PAYLOAD" not in blk)
    check("payload 组装块不引用 _ttsPayload（该变量已不存在）", "_ttsPayload" not in html)

log("")
log("=" * 66)
log("3. 后端分支判定：payload.tts.provider 决定走哪条路")
log("=" * 66)

# 抽 apply_onboarding_voice 的 engine_map 判定行，确认只认 dashscope_cosyvoice
m_map = re.search(r'"mimo_tts":\s*"mimo_tts",\s*\n\s*"dashscope_cosyvoice":\s*"dashscope_cosyvoice",'
                  r'\s*\n\s*"custom_tts":\s*"openai_compatible"', src)
check("save_first_run_config 的 engine_map 认 'dashscope_cosyvoice'", m_map is not None)

# 关键：apply_onboarding_voice 里 backend 白名单必须含 dashscope_cosyvoice
m_wl = re.search(r'backend not in \("mute",\s*"edge",\s*"local_qwen_tts",\s*"mimo_tts",\s*\n\s*'
                 r'"dashscope_cosyvoice",\s*"custom_tts",', src)
check("apply_onboarding_voice 白名单含 'dashscope_cosyvoice'", m_wl is not None)

# 修复前若写 provider:'dashscope' 会掉进 custom_tts 分支（该分支要求 endpoint+model）
m_custom = re.search(r'elif backend == "custom_tts":\s*\n\s*endpoint = \(d\.get\("endpoint"\)', src)
check("custom_tts 分支要求 endpoint（写错 provider 必失败）", m_custom is not None)

# dashscope 分支只要求 api_key
m_ds = re.search(r'elif backend == "dashscope_cosyvoice":\s*\n\s*api_key = \(d\.get\("api_key"\)'
                 r' or ""\)\.strip\(\)\s*\n\s*if not api_key:\s*\n\s*return \{"success": False,'
                 r' "error": "请先填阿里云百炼 API Key"\}', src)
check("dashscope_cosyvoice 分支只要求 api_key（有 Key 即成功）", m_ds is not None)

log("")
log("=" * 66)
log("4. 后半场：voice.clone.cloud 会被写成 dict（含 provider/api_key/model_name）")
log("=" * 66)
# 该分支写的结构（dashscope 分支）
m_write = re.search(r'v\["clone"\]\["cloud"\] = \{"provider": "dashscope_cosyvoice",\s*\n\s*'
                    r'"api_key": api_key, "model_name": model\}', src)
check("dashscope 分支写 voice.clone.cloud = {provider, api_key, model_name}", m_write is not None)

log("")
log("=" * 60)
log("总计: PASS=%d  FAIL=%d" % (PASS, FAIL))
log("=" * 60)

# 【2026-10-21 修】原实现把结果写进 docs/_p1_onboarding_tts.txt —— 往真仓库目录写产物，
# 跑一次就多一个未跟踪文件，进闸门后每轮都污染 git status。改成只打印。
print("\n".join(OUT))
sys.exit(1 if FAIL else 0)
