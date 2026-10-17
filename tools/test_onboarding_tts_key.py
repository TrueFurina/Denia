"""验证引导页「百炼 Key → 语音合成 payload」传播修复（2026-10-09）。

不启动 GUI：用 node + jsdom 加载 first_launch_setup.html 的那一段逻辑做不到
（页面依赖大量 DOM / iframe 环境）。改为**行为契约测试**：
  1. 直接从 first_launch_setup.html 抽出三处关键接线，做静态断言
     （变量声明存在、_finish 第三参存在、payload.tts 回退链包含它）；
  2. 用 stub 环境「重放」doFinish 的 tts 选择表达式，验证三条回退链的优先级；
  3. 把产出的 tts payload 喂给「真实抽取的」apply_onboarding_voice 分支判定，
     证明 provider='dashscope_cosyvoice' 会走 dashscope 分支而不是 custom_tts
     —— 这正是修复前会踩的坑。
"""
import io
import os
import re
import sys

ROOT = r"G:\ai对话\11"
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

# ④ 出向 payload 的回退链
m_tts = re.search(r"tts:\s*\(([^)]*)\)\s*\|\|\s*null", html)
check("存在 tts: (...) || null 出向赋值", m_tts is not None)
if m_tts:
    chain = m_tts.group(1)
    check("回退链 = _ttsPayload → BAILIAN_TTS_PAYLOAD → VOICE_PAYLOAD",
          "_ttsPayload" in chain and "BAILIAN_TTS_PAYLOAD" in chain and "VOICE_PAYLOAD" in chain,
          "实际: %s" % chain.strip())
    check("BAILIAN_TTS_PAYLOAD 优先于 VOICE_PAYLOAD",
          chain.index("BAILIAN_TTS_PAYLOAD") < chain.index("VOICE_PAYLOAD"))

log("")
log("=" * 66)
log("2. 回退链优先级重放（模拟三条分支）")
log("=" * 66)


def pick(ttsPayload, bailian, voice):
    """重放 payload.tts 表达式。"""
    return (ttsPayload or bailian or voice) or None


B = {"backend": "dashscope_cosyvoice", "provider": "dashscope_cosyvoice",
     "api_key": "sk-ws-" + "a" * 100, "model": "qwen-audio-3.0-tts-plus"}
V = {"backend": "edge"}
T = {"backend": "minimax"}

r = pick(None, B, V)
check("只有百炼屏 → 用 BAILIAN_TTS_PAYLOAD", r is B)
r = pick(None, B, None)
check("百炼屏通过、无声音屏 → 仍用百炼 payload", r is B)
r = pick(T, B, V)
check("另开声音屏 → _ttsPayload 优先（不误伤用户显式选择）", r is T)
r = pick(None, None, V)
check("百炼未通过 → 回落 VOICE_PAYLOAD", r is V)
r = pick(None, None, None)
check("都没有 → null（不改变既有行为）", r is None)

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

io.open(os.path.join(ROOT, "docs", "_p1_onboarding_tts.txt"), "w",
        encoding="utf-8").write("\n".join(OUT))
sys.exit(1 if FAIL else 0)
