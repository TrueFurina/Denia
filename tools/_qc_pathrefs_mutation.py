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
     E("                    except OSError:"),
     E("                    except OSError:\n                        pass\n                    if False:")),
]


def main() -> int:
    orig = open(P, "rb").read()
    h0 = hashlib.sha256(orig).hexdigest()
    bad = 0
    for name, old, new in MUTS:
        if orig.count(old) != 1:
            print(f"[跳过] {name}：锚点命中 {orig.count(old)} 次")
            bad += 1
            continue
        open(P, "wb").write(orig.replace(old, new))
        try:
            r = subprocess.run([PY, "-u", TEST], capture_output=True,
                               text=True, encoding="utf-8", errors="replace")
            caught = r.returncode != 0
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
    print("八刀全红" if bad == 0 else f"有 {bad} 处异常")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
