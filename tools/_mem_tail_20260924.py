# -*- coding: utf-8 -*-
"""2026-09-24 15:00 收尾：缓存键隐患 + 更正 CRLF 约定。"""
import io

DAY = r"G:\ai对话\.workbuddy\memory\2026-09-24.md"
IDX = r"G:\ai对话\.workbuddy\memory\MEMORY.md"

NOTE = """

## 15:00 收尾两条（§2da-3）

1. **TTS 缓存键不含 pitch/rate（隐患）**
   `tts.py:770 _tts_cache_key` = `engine|fallback_voice|text`；而 `DashScopeCosyBackend.fallback_voice`
   返回 `self._voice_id`（tts_backends.py:1062）⇒ **键含 voice_id，换音色会失效（注释属实）**，
   但**不含 pitch_scale / rate**。⇒ 若调整 pitch（如 1.0→0.88）而文本相同，会命中旧音高的缓存 PCM，
   表现为"改了没生效 / 声音时对时不对"。**改音高参数后必须清缓存或重启**。
2. **更正**：`config.yaml` 实为 **LF**（基线 tag `p0-baseline-20260924` 的 blob 实测 CRLF=0），
   此前记忆里"config.yaml＝CRLF"**是错的**。已验证的换行约定：
   - LF：`config.yaml`、`src/companion/proactive.py`
   - 未复验：`local_omni_duplex.py` / `webapp.py` —— 改前仍需实测，勿凭记忆。
"""

IDX_OLD = "换行：config.yaml/local_omni_duplex.py/webapp.py/记忆＝CRLF；**proactive.py＝LF**。"
IDX_NEW = ("换行**必须实测勿凭记忆**：`config.yaml`=LF、`proactive.py`=LF（已验）；"
           "`local_omni_duplex.py`/`webapp.py` 未复验。")


def main():
    d = io.open(DAY, "r", encoding="utf-8").read()
    if "§2da-3" not in d:
        with io.open(DAY, "a", encoding="utf-8", newline="") as f:
            f.write(NOTE)
        print("日志已追加")
    else:
        print("日志已有，跳过")

    m = io.open(IDX, "r", encoding="utf-8").read()
    if IDX_OLD in m:
        m = m.replace(IDX_OLD, IDX_NEW)
        print("已更正 CRLF 约定")
    else:
        print("未找到旧的 CRLF 约定行（可能已改），跳过")
    # 缓存键隐患加入索引
    if "缓存键不含 pitch" not in m:
        anchor = "- ★**2da ASR 打结"
        i = m.find(anchor)
        if i < 0:
            i = m.find("### B 组")
        if i < 0:
            i = 0
        line = ("- ⚠**TTS 缓存键＝engine|voice_id|text，不含 pitch/rate**⇒ 改音高后须清缓存/重启，"
                "否则命中旧音高 PCM（时对时不对）\n")
        m = m[:i] + line + m[i:]
        print("索引已插入缓存键条目")
    io.open(IDX, "w", encoding="utf-8", newline="").write(m)
    print("MEMORY 大小", len(m))


if __name__ == "__main__":
    main()
