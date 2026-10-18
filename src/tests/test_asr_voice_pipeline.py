# -*- coding: utf-8 -*-
"""ASR「她现在听得见吗」：语言代码正确性 + model_path 不堵死自动下载。

2026-10-19 实测两个 bug：
① `Model(lang="zh-cn")` → vosk 报 `lang zh-cn does not exist`，
   所以 asr.py 里那条"model_path 留空就自动下载中文模型"的兜底**从来没通过**；
② 配置把 model_path 写成一个不存在的目录 → `Model(路径)` 必抛异常，
   自动下载那条路又被非空值堵死。两口一堵，她就是"听不见"，
   而日志里只有一条 ASR_INIT_FAIL，没有任何"配置指错了"的提示。

本测试用打桩抓住①，用真实文件系统抓住②。
运行：.venv/Scripts/python.exe src/tests/test_asr_voice_pipeline.py
"""
import os
import sys
import types

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, ROOT)

FAIL = 0


def check(cond, msg):
    global FAIL
    print(("  OK  " if cond else "  FAIL ") + msg)
    if not cond:
        FAIL += 1


print("1) model_path 留空时，向 vosk 请求的语言代码必须是 'cn'")
import src.voice.asr as asr_mod  # noqa: E402

seen = {}


class FakeVosk(types.ModuleType):
    """假的 vosk 模块：既要有 Model，也要有 SetLogLevel（asr.py 两个都 import）。"""

    class Model:
        def __init__(self, path=None, lang=None):
            seen["path"] = path
            seen["lang"] = lang

    @staticmethod
    def SetLogLevel(level):
        seen["loglevel"] = level


_orig = sys.modules.get("vosk")
sys.modules["vosk"] = FakeVosk("vosk")
try:
    a = asr_mod.ASR.__new__(asr_mod.ASR)
    a._model_path = ""
    a._load_model()
    check(seen.get("lang") == "cn",
          f"lang={seen.get('lang')!r}（必须是 'cn'；'zh-cn' 会让 vosk 直接抛 does not exist）")
    check(seen.get("path") is None, "没有把路径也传进去")
finally:
    if _orig is not None:
        sys.modules["vosk"] = _orig
    else:
        sys.modules.pop("vosk", None)

print("\n2) model_path 非空时，把路径原样交给 vosk（不改写、不兜底）")
seen.clear()
sys.modules["vosk"] = FakeVosk("vosk")
try:
    a = asr_mod.ASR.__new__(asr_mod.ASR)
    a._model_path = "D:/some/where/model"
    a._load_model()
    check(seen.get("path") == "D:/some/where/model",
          f"path={seen.get('path')!r}")
    check(seen.get("lang") is None, "显式路径时不再请求 lang")
finally:
    if _orig is not None:
        sys.modules["vosk"] = _orig
    else:
        sys.modules.pop("vosk", None)

print("\n3) 配置里的 model_path 不能指向不存在的目录（那会把自动下载堵死）")
import yaml  # noqa: E402

for P in ("config.yaml", "config.release.yaml"):
    if not os.path.exists(P):
        continue
    d = yaml.safe_load(open(P, encoding="utf-8"))
    mp = ((d.get("voice") or {}).get("asr") or {}).get("model_path") or ""
    if mp:
        check(os.path.isdir(mp),
              f"{P} 的 model_path={mp!r} 指向的目录存在")
        if not os.path.isdir(mp):
            print(f"       → 该目录不存在：留空才能走自动下载（ voir asr.py:_load_model）")
    else:
        check(True, f"{P} 的 model_path 留空（自动下载路径畅通）")

print("\n4) 真加载一次（走项目的 _load_model，不打网络时用本地缓存）")
try:
    from vosk import Model, SetLogLevel
    SetLogLevel(-1)
    a = asr_mod.ASR.__new__(asr_mod.ASR)
    a._model_path = ""
    m = a._load_model()
    check(m is not None, "模型可加载（本地缓存已就绪）")
    from vosk import KaldiRecognizer
    r = KaldiRecognizer(m, 16000)
    check(r is not None, "识别器可构造")
except Exception as e:  # noqa: BLE001
    print(f"  --  本机暂无缓存且/或未联网：{type(e).__name__}: {e}")
    print("      （这条不算失败：doctor 会按 WARN 提示首次需联网下载）")

print("\n5) 已知坑的回归：zh-cn 这个错误代码不许再回来")
src_text = open(os.path.join(ROOT, "src", "voice", "asr.py"),
                encoding="utf-8").read()
check('lang="zh-cn"' not in src_text and "lang='zh-cn'" not in src_text,
      "asr.py 里没有 zh-cn")
check('lang="cn"' in src_text, "asr.py 用的是 lang=\"cn\"")

print()
if FAIL:
    print(f"共 {FAIL} 处不符")
    sys.exit(1)
print("全部通过：她能听见（语言代码对 / 自动下载路径没被堵死）")
