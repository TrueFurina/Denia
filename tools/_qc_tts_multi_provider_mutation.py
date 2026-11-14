# -*- coding: utf-8 -*-
"""TTS 多供应商门（tools/test_tts_multi_provider.py）的变异刀。

每把刀 = 把**生产代码**改坏一处 → 本门必须变红，而且必须红在**指定的那条判据**上。

判据口径（沿用第 38~41 轮）：本门不是 unittest，自己打 `结果：N 通过 / M 失败`；
「红了」= 非零退出 + 有汇总行（不是半路崩掉），「命中」= 判据名出现在 `  ✗ ` 行里。
日志里的 Traceback 一律不算崩溃。

运行：  python tools/_qc_tts_multi_provider_mutation.py
"""
import hashlib
import os
import re
import subprocess
import sys

PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PY = sys.executable
GATE = [os.path.join("tools", "test_tts_multi_provider.py")]

CT = "src/models/cloud_tts.py"
TB = "src/voice/tts_backends.py"

MUTS = [
    # ---- 归一 / 合并（cloud_tts.py）----
    ("alias_normalize_off", CT,
     "        prov = PROVIDER_ALIASES.get(prov, prov)",
     "        prov = prov",
     "aliyun 别名归一",
     "别名归一拆掉 —— 同一家服务商按 aliyun / bailian 存成两个桶，用户看到两份配置", False),

    ("global_key_fallback_off", CT,
     '        if not str(out.get("api_key") or "").strip() and _fallback_key:\n'
     '            out["api_key"] = _fallback_key',
     "        if False:\n"
     '            out["api_key"] = _fallback_key',
     "cloud_tts 全局 Key 回填",
     "全局 Key 兜底拆掉 —— 用户在「一键配置」里填过 Key，角色卡仍说他没配（2026-10-17 修过）", False),

    ("merge_clears_key", CT,
     '        "api_key": api_key or _prev.get("api_key") or "",',
     '        "api_key": api_key or "",',
     "只回传部分字段时已存 Key 不被擦空",
     "merge 空值不再保留旧值 —— 引导页只回传 model 时把已配 Key 静默擦空（F3 回归）", False),

    ("merge_prev_wins", CT,
     '        "api_key": api_key or _prev.get("api_key") or "",',
     '        "api_key": _prev.get("api_key") or api_key or "",',
     "显式新 Key 覆盖旧 Key",
     "「空=不改」写成「永远不改」 —— 用户在设置页换了 Key，存的还是旧的", False),

    ("merge_no_upgrade", CT,
     '    elif "api_key" in cc or "provider" in cc:',
     "    elif False:",
     "旧单层升级后仍含原阿里云 Key",
     "旧单层结构不再提升为分桶 —— 配第二家服务商时把第一家的 Key 整段丢掉", False),

    ("merge_alias_off", CT,
     "    provider = (PROVIDER_ALIASES.get(provider, provider) or \"\").strip()",
     "    provider = (provider or \"\").strip()",
     "别名 aliyun 写入 dashscope_cosy 桶",
     "merge 不归一别名 —— 传 aliyun 就存出一个 aliyun 桶，和 dashscope_cosy 各存一份", False),

    ("fish_placeholder_off", CT,
     "            if not voices:",
     "            if False:",
     "fish_audio 无 Key → 占位提示",
     "fish_audio 无 Key 不再给占位提示 —— 下拉框空白，用户以为功能坏了", False),

    # ---- FishAudioBackend（tts_backends.py）----
    ("payload_format_wrong", TB,
     '            "reference_id": self._reference_id,\n'
     '            "format": "mp3",',
     '            "reference_id": self._reference_id,\n'
     '            "format": "wav",',
     "请求体带 reference_id / format=mp3",
     "合成请求的 format 写错 —— 服务端回 wav 二进制，前端按 mp3 播（或直接失败）", False),

    ("http_error_swallowed", TB,
     "            resp = await client.post(url, json=payload, headers=headers)\n"
     "            if resp.status_code >= 400:\n"
     "                try:\n"
     '                    detail = resp.json().get("message") or resp.json().get("error") or resp.text[:300]',
     "            resp = await client.post(url, json=payload, headers=headers)\n"
     "            if False:\n"
     "                try:\n"
     '                    detail = resp.json().get("message") or resp.json().get("error") or resp.text[:300]',
     "HTTP 401 → 抛 RuntimeError",
     "HTTP 错误不再报错 —— Key 失效时把错误 JSON 当音频写进文件，用户只听到一片安静", False),

    ("json_branch_off", TB,
     '            ctype = resp.headers.get("content-type", "") or ""\n'
     '            if "application/json" in ctype:',
     '            ctype = resp.headers.get("content-type", "") or ""\n'
     "            if False:",
     "JSON 返回 audio_url → 二次下载并落盘",
     "JSON(audio_url) 分支拆掉 —— 异步任务形态的返回被当二进制写盘，落一个 JSON 文件当 mp3", False),

    ("empty_audio_ok", TB,
     "            if not audio_bytes:\n"
     '                raise RuntimeError("Fish Audio 返回空音频")',
     "            if False:\n"
     '                raise RuntimeError("Fish Audio 返回空音频")',
     "空音频 → 抛错",
     "空音频不再报错 —— 写出 0 字节文件，下游播放器静默失败、日志里什么都没有", False),

    ("ref_id_guard_off", TB,
     "        if not self._reference_id:",
     "        if False:",
     "有 key 但缺 reference_id → 抛错",
     "缺 reference_id 不再报错 —— 拿空音色 id 去打云端，返回 400 且报错看不懂", False),

    ("factory_drops_fish", TB,
     '        "fish_audio": FishAudioBackend,',
     '        "fish_audio_x": FishAudioBackend,',
     "create_backend('fish_audio')",
     "工厂不认 fish_audio —— 用户选了 Fish Audio，落到兜底引擎（音色全不对）", False),

    # ---- 静默跳过本身 ----
    ("synthesize_raises", TB,
     "    async def synthesize(self, text: str, output_path: str) -> None:\n"
     "        if not self._api_key:\n"
     '            raise RuntimeError("Fish Audio 未配置 API Key（fish.audio 注册获取，用户自费）")',
     "    async def synthesize(self, text: str, output_path: str) -> None:\n"
     '        raise RuntimeError("MUT：合成直接炸")\n'
     "        if not self._api_key:\n"
     '            raise RuntimeError("Fish Audio 未配置 API Key（fish.audio 注册获取，用户自费）")',
     "FishAudioBackend 落盘成功",
     "合成整段抛错 —— 原版被「跳过」吞掉并打出 27/0 全绿；现在异常本身记 FAIL（条数总账兜底）", False),
]


def _sha(p):
    with open(p, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def _run():
    return subprocess.run([PY] + GATE, cwd=PROJ, capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=900)


def _out(r):
    return (r.stdout or "") + (r.stderr or "")


def _summary(r):
    for ln in (r.stdout or "").splitlines():
        if ln.startswith("结果："):
            return ln.strip()
    return "(无汇总行)"


def _judged(r):
    """非零退出 + 本门自己打了汇总行（而不是半路崩掉）。"""
    return r.returncode != 0 and bool(re.search(r"^结果：\d+ 通过 / \d+ 失败", _out(r), re.M))


def _hit(r, name):
    return any(ln.strip().startswith("✗") and name in ln
               for ln in (r.stdout or "").splitlines())


def main():
    base = _run()
    if not (base.returncode == 0
            and re.search(r"^结果：\d+ 通过 / 0 失败", _out(base), re.M)):
        # 带 FAIL 前缀：空变异体检要能区分「套件自己报了失败」和「套件崩了」。
        print("FAIL 基线不是绿的 —— 后面的红不能证明任何事：")
        print(_out(base)[-1500:])
        return 2
    print("基线：绿 | %s" % _summary(base))

    rows = []
    for key, rel, old, new, expect, why, all_hits in MUTS:
        p = os.path.join(PROJ, *rel.split("/"))
        orig = open(p, encoding="utf-8").read()
        pairs = list(old) if isinstance(old, tuple) else [(old, new)]
        counts = [orig.count(o) for o, _ in pairs]
        if any(c == 0 for c in counts) or (not all_hits and any(c != 1 for c in counts)):
            print("  %-24s 锚点命中 %s（应恰好 1 次），锚点已腐烂" % (key, counts))
            rows.append((key, "ANCHOR", why))
            continue
        h0 = _sha(p)
        try:
            text = orig
            for _o, _n in pairs:
                text = text.replace(_o, _n)
            open(p, "w", encoding="utf-8", newline="").write(text)
            r = _run()
        finally:
            open(p, "w", encoding="utf-8", newline="").write(orig)
            if _sha(p) != h0:
                raise SystemExit("还原失败（字节不一致）：%s" % rel)
        judged = _judged(r)
        hit = _hit(r, expect)
        tag = "红·命中" if (judged and hit) else ("红·跑偏" if judged else "没红")
        print("  %-24s %-8s %s" % (key, tag, _summary(r)))
        if judged and not hit:
            print("      ↳ 红了但没红在「%s」上：%s"
                  % (expect, [ln[:70] for ln in (r.stdout or "").splitlines()
                              if ln.strip().startswith("✗")][:3]))
        rows.append((key, tag, why))

    print("\n" + "=" * 72)
    ok = sum(1 for _, t, _ in rows if t == "红·命中")
    print("刀 %d 把：红且命中 %d、红但跑偏 %d、没红 %d、锚点腐烂 %d"
          % (len(rows), ok,
             sum(1 for _, t, _ in rows if t == "红·跑偏"),
             sum(1 for _, t, _ in rows if t == "没红"),
             sum(1 for _, t, _ in rows if t == "ANCHOR")))
    if ok != len(rows):
        print("\n没验住的：")
        for key, t, why in rows:
            if t != "红·命中":
                print("  [%s] %s —— %s" % (t, key, why))
        return 1
    print("全部 %d 把刀都红在指定判据上。" % ok)
    return 0


if __name__ == "__main__":
    sys.exit(main())
