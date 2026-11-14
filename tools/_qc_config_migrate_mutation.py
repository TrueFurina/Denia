# -*- coding: utf-8 -*-
"""TTS 配置迁移门（src/tests/test_config_migrate.py）的变异刀。

每把刀 = 把**生产代码**改坏一处 → 本门必须变红，而且必须红在**指定的那条判据**上。

判据口径（沿用第 38~43 轮）：本门不是 unittest，自己打 `结果：N 通过 / M 失败`；
「红了」= 非零退出 + 有汇总行（不是半路崩掉），「命中」= 判据名出现在 `  ✗ ` 行里。
日志里的 Traceback 一律不算崩溃。

**第 43 轮探针查出的 5 处零判据**（本套件的存在理由）：
  ① 子串判定从未被逼住 —— 唯一的样本 `cosyvoice-v2` 本身就在 STALE_TTS_MODELS 里；
  ② `copy.deepcopy` 从未被逼住 —— 样本字段 `api_key_probe` 不是 secret_box 认的名字；
  ③ webapp 接线只有源码文本断言 —— 把调用塞进 `if False:` 照样全绿（与 2026-10-19
     "函数写了没接线"同型，只是藏在分支后面）；
  ④ `backup` 参数失效时门直接崩（无汇总行），说不清是哪条判据在守；
  ⑤ 写盘失败改抛异常时门也崩 —— "失败不抛"从来没被断言过。

运行：  python tools/_qc_config_migrate_mutation.py
"""
import hashlib
import os
import re
import subprocess
import sys

PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PY = sys.executable
GATE = [os.path.join("src", "tests", "test_config_migrate.py")]

CM = "src/config_migrate.py"
WEB = "src/ui/webapp.py"

_PROFILE_COND = ('            if (v in STALE_TTS_MODELS or m in STALE_TTS_MODELS\n'
                 '                    or "cosyvoice" in v or "qwen-audio" in v):')

MUTS = [
    # ---- 模型迁移（两处都要改）----
    ("cloud_half_off", CM,
     '        ct = raw.get("cloud_tts") or {}\n'
     '        b = ct.get("dashscope_cosy")\n'
     '        if isinstance(b, dict) and str(b.get("model_name") or "").strip() in STALE_TTS_MODELS:\n'
     '            b["model_name"] = TTS_MIGRATE_TO\n'
     '            out.append("cloud_tts.dashscope_cosy.model_name")',
     '        ct = raw.get("cloud_tts") or {}\n'
     '        b = ct.get("dashscope_cosy")',
     "cloud_tts…model_name 已迁移",
     "全局云端 TTS 段不再迁移 —— 角色内联音色改了、全局段还传失效模型，试听照样 400", False),

    ("entry_one_half", CM,
     "    return migrate_stale_tts_model(raw) + migrate_stale_voice_profiles(raw)",
     "    return migrate_stale_tts_model(raw)",
     "入口里调了音色档迁移",
     "唯一入口只跑模型那一半 —— 这正是 2026-10-19 那次「函数写了没接线」的复现路径", False),

    ("stale_list_empty", CM,
     'STALE_TTS_MODELS: tuple[str, ...] = (\n'
     '    "qwen-audio-3.0-tts-plus",',
     'STALE_TTS_MODELS: tuple[str, ...] = (\n'
     '    "_x",',
     "voice.clone…model_name 已迁移",
     "失效模型清单被清空 —— 迁移函数还在跑，但一个值都认不出来（静默空转）", False),

    # ---- 音色档迁移 ----
    ("voice_field_typo", CM,
     '                prof["voice_id"] = TTS_MIGRATE_VOICE',
     '                prof["voice"] = TTS_MIGRATE_VOICE',
     "不再往音色档里塞没人读的 voice 字段",
     "把音色写进 `voice` 而不是 `voice_id`（2026-10-20 真机 bug）—— 模型换了音色没换，请求照样 418", False),

    ("profile_model_kept", CM,
     '                prof["model"] = TTS_MIGRATE_TO',
     "                _unused_m = TTS_MIGRATE_TO",
     "失效档的 model/voice_id 都已迁移",
     "音色档只换音色不换模型 —— 音色对了但模型仍是已下线的，请求 400", False),

    ("substring_off", CM,
     _PROFILE_COND,
     "            if (v in STALE_TTS_MODELS or m in STALE_TTS_MODELS):",
     "voice_id 是 cosyvoice 克隆 id",
     "子串判定拆掉 —— 克隆音色 id（cosyvoice-v3.5-plus-bailian-…）不在清单里，整档漏迁", False),

    ("model_in_list_off", CM,
     _PROFILE_COND,
     '            if (v in STALE_TTS_MODELS\n'
     '                    or "cosyvoice" in v or "qwen-audio" in v):',
     "三个点全部命中",
     "音色档不再看 `model` 字段 —— 只有音色脏、模型也脏的档被漏掉", False),

    ("engine_filter_off", CM,
     '            if not isinstance(prof, dict) or prof.get("engine") != "dashscope_cosy":\n'
     "                continue",
     "            if not isinstance(prof, dict):\n"
     "                continue",
     "pyttsx3 档不迁移",
     "引擎过滤拆掉 —— 本地 pyttsx3 音色档被改成云端模型名，本地合成直接坏掉", False),

    # ---- 原子写盘 ----
    ("inplace_rewrite", CM,
     "                os.replace(tmp, path)",
     '                with open(path, "w", encoding="utf-8") as _o:\n'
     "                    _o.write(open(tmp, encoding=\"utf-8\").read())\n"
     "                os.unlink(tmp)",
     "目标文件被整体替换",
     "改成原地截断重写 —— 截断后、写完前崩溃就是一个半截 YAML，之后所有保存永久失败（P0）", False),

    ("no_fsync", CM,
     "            f.flush()\n            os.fsync(f.fileno())",
     "            f.flush()",
     "实现里 fsync 过",
     "不再 fsync —— 掉电后 os.replace 换上去的文件可能是空的", False),

    ("no_deepcopy", CM,
     "        payload: Any = copy.deepcopy(data)",
     "        payload: Any = data",
     "调用方 dict 里的 api_key 仍是明文",
     "写盘不再用副本 —— seal_tree 就地加密，运行中的 raw 配置里的 api_key 变成 dpapi 密文", False),

    ("seal_off", CM,
     "        if seal:\n            try:\n                from . import secret_box",
     "        if False:\n            try:\n                from . import secret_box",
     "落盘的是密文",
     "写盘前不加密 —— 用户的 api_key 以明文躺在 config.yaml 里（迁移路径绕过了 Config.save）", False),

    ("bak_overwrite", CM,
     "            if not os.path.exists(bak):",
     "            if True:",
     "已有 .bak 不被覆盖",
     "已有 .bak 也覆盖 —— 反复启动把「最初那份用户原稿」冲成上一次迁移后的样子", False),

    ("backup_inert", CM,
     "        if backup and os.path.isfile(path):",
     "        if False:",
     "产生了 .bak",
     "backup 参数彻底失效 —— 自动迁移改用户配置却不留后路，yaml.dump 把注释永久丢掉", False),

    ("fail_raises", CM,
     "        return False",
     "        raise",
     "目录不存在时抛了",
     "写盘失败改抛异常 —— 配置目录只读时软件直接打不开（迁移失败不该让软件打不开）", False),

    # ---- webapp 接线（运行时行为，不是源码文本）----
    ("wiring_if_false", WEB,
     "            _mig = migrate_stale_tts_config(_raw_mig)\n"
     "            if _mig:",
     "            _mig = []\n"
     "            if False:\n"
     "                _mig = migrate_stale_tts_config(_raw_mig)\n"
     "            if _mig:",
     "CharacterAPI 启动后盘上的模型名已迁移",
     "接线调用塞进 `if False:` —— 源码里那行字符串还在，文本断言全绿，迁移却永不执行", False),

    ("wiring_no_backup", WEB,
     "                if dump_config_atomic(self._config_path, _raw_mig, backup=True):",
     "                if dump_config_atomic(self._config_path, _raw_mig):",
     "迁移写盘带 backup=True",
     "接线写盘不带 backup —— 启动时自动改用户配置却不留 .bak", False),

    ("wiring_bare_write", WEB,
     "                if dump_config_atomic(self._config_path, _raw_mig, backup=True):",
     '                if (io.open(self._config_path, "w", encoding="utf-8").write("x") or True):',
     "webapp 的迁移写盘走 dump_config_atomic",
     "接线退回裸 io.open 直写 —— 非原子、不 fsync，与 Config.save() 审计出的 P0 同型", False),
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
