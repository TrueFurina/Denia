# -*- coding: utf-8 -*-
"""「配置引用体检」的变异验证：改松/改严都必须被测试抓住。

跑法：.venv/Scripts/python.exe -u tools/_qc_pathrefs_mutation.py

坑（2026-10-19 踩了三次）：多行锚点用 J() 逐行拼，别在字符串里写 \n
（heredoc 会把 \n 换成字面 /n）；中文不进 bytes 字面量，一律 E()/J()；
变异必须保持**语法合法**，否则测试是被 SyntaxError 崩红的、没有区分力。
"""
import hashlib
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(ROOT)

P = "tools/_qc_path_refs.py"
TEST = "src/tests/test_config_path_refs.py"
PY = ".venv/Scripts/python.exe"


# 【2026-11-08 修·刀自己会空转】原来只判 `r.returncode != 0`，于是判定文件**崩了**
# 也被算成"抓住"。实测：把 `src/tests/test_config_path_refs.py` 整个换成一句 `raise RuntimeError("自己崩了")`，
# 本刀仍然逐条打印「已抓住：<无输出>」、最后打印「N 刀全红」并 exit 0 ——
# **一个连第一行都跑不完的判定，被认证成"全抓"**。
# 崩了和识别是两件事：前者说明判定没跑，后者才说明判据在工作。
# 起点也必须先绿：恒红的判定能让**每一把**刀都变红，那种"全红"什么都不证明。
def _judged(r) -> bool:
    """判定文件是「判出了问题」而不是「崩了」或「什么都没说」。

    【2026-11-08 第二版·第一版太严】第一版还要求「输出里没有 Traceback」，
    实测把**先印 FAIL、随后收尾时崩掉**的判定判成了"没抓住"：
      · `_qc_asr_mutation` ①②：印了 `FAIL lang='zh-cn'`，随后 vosk 桩抛
        `AttributeError: 'Model' object has no attribute '_handle'`
      · `_qc_config_bootstrap_mutation` B：印了 `FAIL 状态 created`，随后读临时
        `config.yaml` 抛 `FileNotFoundError`
    两次都是**仪器太窄**（本轮第三次）。真正的判别是**有没有点名**：
    退化桩只印一个 `exit 1`，一个字都没有 —— 那才叫"什么都没说"。
    """
    out = (r.stdout or "") + (r.stderr or "")
    return r.returncode != 0 and ("FAIL" in out or "✗" in out)


def _run_test():
    return subprocess.run([PY, "-u", TEST], capture_output=True, text=True,
                          encoding="utf-8", errors="replace")


def _baseline_green() -> bool:
    r = _run_test()
    out = (r.stdout or "") + (r.stderr or "")
    return r.returncode == 0 and "Traceback" not in out



def E(s: str) -> bytes:
    return s.encode("utf-8")


def J(*lines: str) -> bytes:
    return chr(10).join(lines).encode("utf-8")


MUTS = [
    ("A 路径不存在也不判红（回到「漏报」）",
     J("                    (_notes if optional else _problems).append(msg)"),
     E('                    _notes.append(msg)')),

    ("B 可选功能的缺失也判红（天天假红，门禁就废了）",
     J("                    (_notes if optional else _problems).append(msg)"),
     E('                    _problems.append(msg)')),

    ("C 端口没听也判红（网关装在 Docker 里、没起很正常）",
     E('                        if optional or not STRICT_PORTS:'),
     E('                        if optional and not STRICT_PORTS:')),

    ("D 静音后端不再判红（回到「她能收不能说」还不红）",
     E('            _problems.append(f"[B 未绑引擎] 角色 {name!r} 的 voice.engine 为空 "'),
     E('            _notes.append(f"[B 未绑引擎] 角色 {name!r} 的 voice.engine 为空 "')),

    ("E 放宽白名单：无分隔符的未知键也查（label/model 又被当本地路径）",
     J("    if key not in PATH_KEYS:",
       "        return False"),
     J("    if key not in PATH_KEYS and \"/\" not in v:",
       "        return False")),

    ("F PATH_KEYS 命中也要求有分隔符（漏报 model_path: 相对目录名）",
     J("    if key not in PATH_KEYS:",
       "        return False"),
     J("    if key not in PATH_KEYS or (\"/\" not in v and \"\\\\\" not in v):",
       "        return False")),

    ("G 可选降级失效：不认 auto_start: false（可选功能的缺失也判红）",
     J('    for k in ("auto_start", "enabled"):',
       "        v = node.get(k)"),
     J('    for k in ():',
       "        v = None")),

    ("H 端口探测的异常被静默吞掉（服务没起连提示都没有）",
     # 【2026-11-08 修·锚点缩进差 4 格，变异出来的是语法错误】
     # 原来写的是 20 空格的 `except OSError:`，而 `tools/_qc_path_refs.py:144` 是
     # **24 空格**。`orig.count(old) != 1` 这道锚点守卫**没能拦住**它：20 空格的串
     # 正好是 24 空格那一行的**子串**，命中次数还是 1。于是 replace 之后 `except`
     # 掉到第 4 列、缩进全乱 → `IndentationError: expected an indented block
     # after 'except' statement on line 144`。
     # 而本刀当时只判 `r.returncode != 0`，所以**把"变异把代码改坏了"算成了"抓住了"**。
     # 教训：`count(old) == 1` 只保证"只出现一次"，**不保证从行首开始**。
     E("                        except OSError:"),
     E("                        except OSError:\n"
       "                            pass\n"
       "                        if False:")),

    ("I 不认 web 根（随包素材 _role_assets/* 被误判成裂图）",
     J('    _web = os.path.join(ROOT, "src", "ui", "web", p)',
       "    if os.path.exists(_web):",
       "        return _web",
       '    return os.path.join(ROOT, p)'),
     J('    return os.path.join(ROOT, p)')),
]


def main() -> int:
    orig = open(P, "rb").read()
    h0 = hashlib.sha256(orig).hexdigest()
    bad = 0
    if not _baseline_green():
        print("FAIL 起点就不是绿的 —— 恒红的判定能让每把刀都变红，这种\"全红\"不证明任何事")
        return 1
    print("  PASS 起点绿")
    for name, old, new in MUTS:
        if orig.count(old) != 1:
            print(f"[跳过] {name}：锚点命中 {orig.count(old)} 次")
            bad += 1
            continue
        open(P, "wb").write(orig.replace(old, new))
        try:
            r = subprocess.run([PY, "-u", TEST], capture_output=True,
                               text=True, encoding="utf-8", errors="replace")
            caught = _judged(r)
            lines = [l for l in (r.stdout or "").splitlines() if l.strip()]
            why = [l for l in lines if l.strip().startswith("FAIL")]
            why = (why or lines or ["<无输出>"])[-1].strip()[:88]
            print(name)
            print("   -> " + ("已抓住：" + why if caught else "没抓住（测试失效）"))
            if not caught:
                bad += 1
        finally:
            open(P, "wb").write(orig)

    ok = hashlib.sha256(open(P, "rb").read()).hexdigest() == h0
    print(f"\n还原字节一致：{ok}")
    if not ok:
        bad += 1
    print("九刀全红" if bad == 0 else f"有 {bad} 处异常")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
