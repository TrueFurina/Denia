# -*- coding: utf-8 -*-
"""凭据扫描器：明文必须红，密文/空值/环境变量引用必须放过。

2026-10-18 两类误报都是实测撞到的：
  · `dpapi:AQAAANCM...` 密文被当成明文 → 每次打包报一次假警（sticker.api_key）
  · `${ZHIPU_API_KEY}` 引用被当成非空凭据 → 发布模板被迫永远写空 key，
    用户只能把 key 手抄进配置，那才正是要防的

运行：.venv/Scripts/python.exe src/tests/test_package_secrets_scan.py
"""
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tools"))

import check_package_secrets as C  # noqa: E402

FAIL = 0


def check(cond, msg):
    global FAIL
    print(("  ✅ " if cond else "  ❌ ") + msg)
    if not cond:
        FAIL += 1


def write(text: str) -> str:
    fd, p = tempfile.mkstemp(suffix=".yaml")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(text)
    return p


print("① 必须抓住的：明文 key / 密码 / cookie")
p = write("""\
llm:
  openai:
    api_key: 'sk-live-1234567890'
    model: glm-4-flash
channels:
  qq:
    password: 'hunter2'
    cookie: 'uin=o123456; skey=@abc'
""")
hits = C.scan_config(p)
check(len(hits) == 3, f"抓到 {len(hits)} 处（应为 3）：{hits}")
check({h[1] for h in hits} == {"api_key", "password", "cookie"},
      f"键名正确：{[h[1] for h in hits]}")
os.unlink(p)

print("② 必须放过的：空值 / dpapi 密文 / ${{ENV}} 引用")
p = write("""\
a:
  api_key: ""
  secret: ''
c:
  api_key: "dpapi:AQAAANCMnd8BFdERjHoAwE/Cl+sBAAAAiyDXhNfcMUeB=="
  cookie: dpapi:AAAA
b:
  api_key: ${ZHIPU_API_KEY}
  secret: ${MY_SECRET}
""")
hits = C.scan_config(p)
check(hits == [], f"零误报：{hits}")
os.unlink(p)

print("③ 行尾注释不能被当成值的一部分（# 后是说明，不是密钥）")
p = write("llm:\n  api_key: ''   # 填你自己的\n")
check(C.scan_config(p) == [], "注释不是值")
p = write("x:\n  api_key: sk-real  # 备注\n")
check(len(C.scan_config(p)) == 1, "真 key 仍被抓住（哪怕带注释）")
os.unlink(p)

print("④ 引号包裹的引用同样是引用")
p = write("x:\n  api_key: '${MODELSCOPE_API_KEY}'\n")
check(C.scan_config(p) == [], "带引号的 ${VAR} 也算引用")
os.unlink(p)

print("⑤ 不是 ${...} 整串的（内嵌引用/拼写）仍按非空处理")
p = write("x:\n  api_key: 'Bearer ${MODELSCOPE_API_KEY}'\n")
check(len(C.scan_config(p)) == 1, "内嵌引用不豁免（保守方向：宁可误报）")
os.unlink(p)

print("⑥ 真实模板与实配文件的回归")
for f in C.TEMPLATE_FILES:
    if not os.path.exists(f):
        continue
    hits = C.scan_config(f)
    check(hits == [], f"{f} 模板零凭据")
for f in C.INSTANCE_FILES:
    if not os.path.exists(f):
        continue
    hits = C.scan_config(f)
    print(f"  · {f}（实配，不入包；--strict 下才阻断）：{len(hits)} 处")

print("⑦ EMPTY_VALUES / SENSITIVE_KEY_RE 的既有语义没被我改坏")
import re  # noqa: E402
check(not C.SENSITIVE_KEY_RE.match("max_new_tokens"), "tokenizer 类不进敏感名单")
check(not C.SENSITIVE_KEY_RE.match("tokenizer_dir"), "tokenizer_dir 不进敏感名单")
check(C.SENSITIVE_KEY_RE.match("api_key"), "api_key 在敏感名单")
check(C.ENV_REF_RE.fullmatch("${ZHIPU_API_KEY_2}"), "ENV_REF_RE 认下划线/数字")

print()
if FAIL:
    print(f"✗ {FAIL} 项失败")
    raise SystemExit(1)
print("✅ 全部通过")
