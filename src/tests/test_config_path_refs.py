# -*- coding: utf-8 -*-
"""「配置引用体检」判级测试：路径指错必红、可选功能只提示、标识符不误判。

为什么需要它：2026-10-19 修 ASR 时发现 `model_path` 指向不存在的目录，
而"非空值会把自动下载堵死"——这是**按类**的问题（配置看着都填了，
从没被验证过）。扫描器 `tools/_qc_path_refs.py` 就是把这类一次扫干净，
本测试守住它的判级，防止以后被改松（漏报）或改严（天天假红）。

运行：.venv/Scripts/python.exe src/tests/test_config_path_refs.py
"""
import importlib.util
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, ROOT)

spec = importlib.util.spec_from_file_location(
    "path_refs", os.path.join(ROOT, "tools", "_qc_path_refs.py"))
PR = importlib.util.module_from_spec(spec)
spec.loader.exec_module(PR)

FAIL = 0


def check(cond, msg):
    global FAIL
    print(("  OK  " if cond else "  FAIL ") + msg)
    if not cond:
        FAIL += 1


REAL_FILE = os.path.join(ROOT, "config.yaml")
MISSING = os.path.join(tempfile.gettempdir(), "daniya_ surely_missing_dir_xyz")

print("1) 路径字段指向不存在的目录 + 功能开着 → FAIL")
data = {"voice": {"asr": {"model_path": MISSING}}}
problems, notes, n = PR.scan(data)
check(any("路径不存在" in p for p in problems), f"有红：{problems}")
check(n >= 1, f"计数了 {n} 处")

print("\n2) 同一个路径，但挂在 auto_start: false 的子树 → 只提示不判红")
data = {"proactive": {"local_omni_engine": {
    "auto_start": False, "model_dir": MISSING,
    "comni_resources_dir": MISSING}}}
problems, notes, n = PR.scan(data)
check(not problems, f"不判红（实际 {problems}）")
check(any("路径不存在" in x for x in notes), f"有提示：{[x for x in notes if '路径' in x]}")

print("\n3) enabled: false 同样只提示")
data = {"channels": {"wechat": {"enabled": False,
                                "gewechat": {"base_url": "http://127.0.0.1:2531"}}}}
problems, notes, n = PR.scan(data)
check(not problems, f"不判红（实际 {problems}）")

print("\n4) 路径真的存在 → PASS")
data = {"characters": [{"name": "测试", "voice": {"engine": "pyttsx3",
                                                "reference_audio": "assets/voices/daniya_ref_0813_24k.wav"}}]}
problems, notes, n = PR.scan(data)
check(not problems, f"无红（实际 {problems}）")
check(any("[A ok]" in x for x in notes), "记录了 ok")

print("\n5) 角色没绑 engine → FAIL（静音后端，她能收不能说）")
data = {"characters": [{"name": "测试", "voice": {"engine": ""}}]}
problems, notes, n = PR.scan(data)
check(any("未绑引擎" in p for p in problems), f"有红：{problems}")

print("\n6) engine 不认识 → FAIL")
data = {"characters": [{"name": "测试", "voice": {"engine": "不存在的引擎"}}]}
problems, notes, n = PR.scan(data)
check(any("引擎不认识" in p for p in problems), f"有红：{problems}")

print("\n7) 端口没听：默认只提示，--strict-ports 才判红")
data = {"channels": {"wechat": {"enabled": True,
                                "gewechat": {"base_url": "http://127.0.0.1:2531"}}}}
p1, n1, _ = PR.scan(data, strict_ports=False)
p2, n2, _ = PR.scan(data, strict_ports=True)
check(not p1, f"默认不红（实际 {p1}）")
check(any("服务没起" in x for x in n1), "默认仍给提示")
check(any("服务没起" in x for x in p2), f"严格态判红（实际 {p2}）")

print("\n7b) PATH_KEYS 里的键，值是相对目录名（没有分隔符）也要查")
data = {"voice": {"asr": {"model_path": "no_such_model_dir_name"}}}
problems, notes, n = PR.scan(data)
check(any("路径不存在" in x for x in problems),
      f"抓到了无分隔符的相对路径（实际 {problems}）")

print("\n8) 非路径字段不能被误判成路径")
data = {"voice_profiles": [{"name": "档", "voice_id": "cosyvoice-v3.5-plus",
                            "model": "cosyvoice-v3.5-plus",
                            "api_url": "https://dashscope.aliyuncs.com"}]}
problems, notes, n = PR.scan(data)
check(not problems, f"无红（实际 {problems}）")
check("cosyvoice-v3.5-plus" not in " ".join(problems), "标识符没被当成路径查")

print("\n9) 空值 / ${ENV} 引用不算缺失")
data = {"voice": {"asr": {"model_path": ""}},
        "sticker": {"api_key": "${ZHIPU_API_KEY}"}}
problems, notes, n = PR.scan(data)
check(not problems, f"无红（实际 {problems}）")

print("\n9b) 未知键 + 无路径分隔符的值 → 不查（避免把任意字符串当路径）")
data = {"something": {"random_key": "just-a-name"}}
problems, notes, n = PR.scan(data)
check(not problems, f"无红（实际 {problems}）")
check(n == 0, f"没有计入校验数（实际 {n}）")

print("\n10) 真实模板扫一遍（进仓的 config.release.yaml 必须干净）")
import yaml  # noqa: E402

P = os.path.join(ROOT, "config.release.yaml")
if os.path.exists(P):
    d = yaml.safe_load(open(P, encoding="utf-8"))
    problems, notes, n = PR.scan(d)
    check(not problems, f"模板零红（实际 {problems}）")
    check(n >= 4, f"确实扫了 {n} 处（不是空转）")

print()
if FAIL:
    print(f"共 {FAIL} 处不符")
    sys.exit(1)
print("全部通过：配置引用体检守住（指错必红 / 可选只提示 / 端口默认不红）")
