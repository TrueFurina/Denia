# -*- coding: utf-8 -*-
"""三层冒烟：改完 config.yaml + local_omni_duplex.py 后的启动级验证。

  ① YAML 真解析（缩进错会在此暴露：mapping values are not allowed here）
  ② 被改模块语法/导入（py_compile + AST 检查门条件已改）
  ③ load_config() 真跑通，并断言 duplex_local_omni_ref_audio 已生效
"""
import os
import sys
import ast
import json

ROOT = r"G:\ai对话\11"
CFG = os.path.join(ROOT, "config.yaml")
SRC = os.path.join(ROOT, "src", "llm", "local_omni_duplex.py")
REF = r"G:\ai对话\11\assets\voices\daniya_ref.wav"

ok = True


def step(name, fn):
    global ok
    print()
    print("=" * 92)
    print(name)
    print("=" * 92)
    try:
        fn()
        print("  ⇒ PASS")
    except Exception as e:
        ok = False
        print("  ⇒ FAIL: %r" % (e,))


# ---------- ① YAML 真解析 ----------
def t1():
    import yaml
    with open(CFG, "rb") as f:
        raw = f.read()
    txt = raw.decode("utf-8")
    data = yaml.safe_load(txt)
    assert isinstance(data, dict), "顶层不是 dict"
    pro = data.get("proactive")
    assert isinstance(pro, dict), "proactive 段缺失/类型错"
    v = pro.get("duplex_local_omni_ref_audio")
    print("  yaml.safe_load OK, 顶层键 %d 个" % len(data))
    print("  proactive.duplex_local_omni_ref_audio = %r" % v)
    print("  proactive.duplex_native_audio         = %r" % pro.get("duplex_native_audio"))
    assert v == REF, "参考音未写入预期值"
    assert pro.get("duplex_native_audio") is True, "native_audio 不是 true"


# ---------- ② 源码语法 + 门条件 ----------
def t2():
    src = open(SRC, "rb").read().decode("utf-8")
    tree = ast.parse(src)
    print("  ast.parse OK（无语法错误）")
    # 找目标 if 节点
    found = []
    for node in ast.walk(tree):
        if isinstance(node, ast.If):
            seg = ast.get_source_segment(src, node.test)
            if seg and "self._ref_audio_path" in seg and "isfile" in seg:
                found.append((node.lineno, " ".join(seg.split())))
    print("  找到含参考音判断的 if：%d 处" % len(found))
    for ln, seg in found:
        print("    L%d: %s" % (ln, seg))
    assert found, "没找到目标 if"
    hit = [f for f in found if "_native_audio" in f[1]]
    assert hit, "门条件未包含 _native_audio（改动未生效）"
    # 语法编译
    compile(src, SRC, "exec")
    print("  compile OK")


# ---------- ③ load_config 真跑通 ----------
def t3():
    sys.path.insert(0, ROOT)
    from src.config import load_config  # type: ignore
    cfg = load_config(CFG)
    pro = cfg.ns.proactive
    v = getattr(pro, "duplex_local_omni_ref_audio", None)
    print("  load_config() OK")
    print("  cfg.ns.proactive.duplex_local_omni_ref_audio = %r" % v)
    assert v == REF, "load_config 读出的值与预期不符"


step("① YAML 真解析（缩进/结构）", t1)
step("② 被改模块语法 + 门条件落点", t2)
step("③ load_config() 端到端", t3)

print()
print("=" * 92)
print("总判定: %s" % ("全部 PASS" if ok else "存在 FAIL，需修复"))
print("=" * 92)
sys.exit(0 if ok else 1)
