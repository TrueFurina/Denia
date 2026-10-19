"""`src/secret_box.py` DPAPI 密钥加密的测试（C9-1）。

覆盖四层：
1. 底层往返（真实 DPAPI，Windows 必须可用——本项目就是 Windows-only）；
2. seal/unseal 树遍历（嵌套 dict + list、幂等、失败路径保留密文）；
3. Config.save 端到端：**磁盘文件里不得出现明文 Key**；
4. load_config 端到端：读回内存是明文，调用方无感；换机器解不开时给降级提示。

运行：.venv/Scripts/python.exe src/tests/test_secret_box.py
"""
from __future__ import annotations

import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from src import secret_box  # noqa: E402

_fails: list[str] = []


def check(cond: bool, msg: str) -> None:
    print(("  ✓ " if cond else "  ✗ ") + msg)
    if not cond:
        _fails.append(msg)


KEY = "sk-test-0123456789abcdef"
PLAIN = "sk-plain-secret-value"

print("== 1. 底层往返 ==")
sealed = secret_box.encrypt_secret(KEY)
check(sealed.startswith("dpapi:"), f"密文带 dpapi: 前缀（得到 {sealed[:12]}…）")
check(KEY not in sealed, "密文中不含明文子串")
back = secret_box.decrypt_secret(sealed)
check(back == KEY, "encrypt→decrypt 往返还原明文")
check(secret_box.decrypt_secret(PLAIN) == PLAIN, "无前缀明文原样返回（兼容）")
check(not secret_box.is_sealed(PLAIN) and secret_box.is_sealed(sealed),
      "is_sealed 明文 False / 密文 True")

print("== 2. seal_tree / unseal_tree ==")
raw = {
    "api_key": PLAIN,
    "empty_key": "",
    "name": "not-a-key",
    "llm": {"openai": {"api_key": PLAIN}},
    "roles": [{"api_key": PLAIN}, {"api_key": ""}],
    "sealed": {"api_key": sealed},
}
n = secret_box.seal_tree(raw)
check(n == 3, f"加密 3 处明文（实际 {n}）")
check(raw["api_key"].startswith("dpapi:"), "顶层 api_key 已加密")
check(raw["llm"]["openai"]["api_key"].startswith("dpapi:"), "嵌套 dict 已加密")
check(raw["roles"][0]["api_key"].startswith("dpapi:"), "list 内 api_key 已加密")
check(raw["roles"][0]["api_key"] != PLAIN, "list 内不是明文残留")
check(raw["empty_key"] == "" and raw["name"] == "not-a-key", "空串与非密钥字段不动")

print("== 2b. cookie 字段覆盖（C9 缺口关闭：qzone.cookie） ==")
raw2 = {
    "qzone": {"enabled": True, "uin": "12345", "cookie": "p_skey=SECRET; uin=o12345;"},
    "channels": [{"cookie": "sid=abc"}],
    "not_cookie_field": "plain text",
}
n3 = secret_box.seal_tree(raw2)
check(n3 == 2, f"cookie 加密 2 处（实际 {n3}）")
check(raw2["qzone"]["cookie"].startswith("dpapi:"), "qzone.cookie 已加密")
check(raw2["channels"][0]["cookie"].startswith("dpapi:"), "list 内 cookie 已加密")
check(raw2["qzone"]["uin"] == "12345" and raw2["not_cookie_field"] == "plain text",
      "uin 与非 cookie 字段不动")
ok2, failed2 = secret_box.unseal_tree(raw2)
check(ok2 == 2 and failed2 == [], f"cookie 解密 2 处 0 失败（实际 {ok2}/{failed2}）")
check(raw2["qzone"]["cookie"].startswith("p_skey="), "qzone.cookie 往返还原")
check(raw["sealed"]["api_key"] == sealed, "已加密的不二次加密")
n2 = secret_box.seal_tree(raw)
check(n2 == 0, f"幂等：再 seal 加密 0 处（实际 {n2}）")
ok, failed = secret_box.unseal_tree(raw)
# 4 = 本轮 seal 的 3 处 + 预置的 sealed 字段 1 处（它也在树里，同样要解）
check(ok == 4 and failed == [], f"解密 4 处 0 失败（实际 {ok}/{failed}）")
check(raw["api_key"] == PLAIN
      and raw["llm"]["openai"]["api_key"] == PLAIN
      and raw["roles"][0]["api_key"] == PLAIN, "三层全部还原明文")

print("== 3. Config.save 端到端：磁盘不得有明文 ==")
import yaml  # noqa: E402

tmpdir = tempfile.mkdtemp(prefix="secretbox_test_")
cfg_path = os.path.join(tmpdir, "config.yaml")
with open(cfg_path, "w", encoding="utf-8") as f:
    yaml.dump({"sticker": {"api_key": PLAIN, "text_model": "glm-4-flash"},
               "llm": {"openai": {"api_key": PLAIN}}}, f)

from src.config import load_config  # noqa: E402

cfg = load_config(cfg_path)
cfg.save()
with open(cfg_path, "r", encoding="utf-8") as f:
    on_disk = f.read()
check(PLAIN not in on_disk, "写盘后文件里没有明文 Key")
check(on_disk.count("dpapi:") == 2, "两处 Key 都落成 dpapi: 密文")
check("glm-4-flash" in on_disk, "非密钥字段照常写盘")

print("== 4. load_config 端到端：读回明文 / 换机降级 ==")
cfg2 = load_config(cfg_path)
check(cfg2.ns.sticker.api_key == PLAIN, "点号访问拿到的是明文（调用方无感）")
check(cfg2.ns.llm.openai.api_key == PLAIN, "嵌套层级同样还原")

# 模拟换机器：把真实密文替换成一段本机解不开的假密文
# （真 DPAPI 密文在本机一定能解开，所以只能伪造不可解的 base64）
with open(cfg_path, "r", encoding="utf-8") as f:
    content = f.read()
fake = "dpapi:" + "QUJDREVG" * 40  # 解出来不是合法 DPAPI blob → Unprotect 失败
content = content.replace("dpapi:", fake, 1)
bad_path = os.path.join(tmpdir, "config_bad.yaml")
with open(bad_path, "w", encoding="utf-8") as f:
    f.write(content)
cfg3 = load_config(bad_path)
degr = "; ".join(cfg3.runtime_status.degradations)
check("重新填写" in degr, f"解不开的密文给出降级提示（实际：{degr[:80]}…）")
check(PLAIN not in open(bad_path, encoding="utf-8").read(), "坏密文场景磁盘仍无明文")

shutil_ret = 0
import shutil  # noqa: E402
shutil.rmtree(tmpdir, ignore_errors=True)

print()
if _fails:
    print(f"FAIL：{len(_fails)} 项")
    for m in _fails:
        print("  - " + m)
    sys.exit(1)
print("全部通过")
