# -*- coding: utf-8 -*-
"""「语义检索兜底」的变异验证：五把刀，刀刀必须被测试抓住。

跑法：.venv/Scripts/python.exe -u tools/_qc_embed_fallback_mutation.py

纪律（沿用 _qc_pathrefs_mutation.py 的教训）：
· 锚点多行用 J() 逐行拼，别在字符串里写 \\n；
· 变异必须保持语法合法，否则测试是被 SyntaxError 崩红的、没有区分力；
· 改完必须还原，并核对 sha256 与原始字节一致。
"""
import hashlib
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(ROOT)

P = "src/llm/embed_client.py"
TEST = "tools/test_embed_fallback.py"
PY = ".venv/Scripts/python.exe"


# 【2026-11-08 修·刀自己会空转】原来只判 `r.returncode != 0`，于是判定文件**崩了**
# 也被算成"抓住"。实测：把 `tools/test_embed_fallback.py` 整个换成一句 `raise RuntimeError("自己崩了")`，
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
    ("A auto 不再上云（本地挂了就永远关键词召回）",
     J('        if fn is not None:',
       '            return fn',
       '        if provider == "ollama":',
       '            return None'),
     J('        if fn is not None:',
       '            return fn',
       '        return None')),

    ("B 探测失败也把函数交出去（每次召回都失败一次）",
     E('        if not probe:'),
     E('        if False:')),

    ("C off 也偷偷上云（用户关了语义检索，还在花钱/上传）",
     J('    if provider in ("off", "none", "disabled", "false"):',
       '        return None'),
     J('    if provider in ("off", "none", "disabled", "false"):',
       '        return make_cloud_embed_fn(',
       '            str(raw.get("base_url") or ""),',
       '            str(raw.get("api_key") or ""),',
       '            str(raw.get("model") or DEFAULT_MODEL),',
       '        )')),

    ("D 单次失败抛异常（调用方按 None 处理，直接崩）",
     J('        except Exception:  # noqa: BLE001  单次失败只丢这一次的向量',
       '            logger.debug("云端嵌入失败", exc_info=True)',
       '            return None'),
     J('        except Exception:  # noqa: BLE001  单次失败只丢这一次的向量',
       '            raise')),

    ("E 不归一结尾斜杠（拼出 //embeddings）",
     E('    url = base_url.rstrip("/") + "/embeddings"'),
     E('    url = base_url + "/embeddings"')),
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
        n = orig.count(old)
        if n != 1:
            print(f"[跳过] {name}：锚点命中 {n} 次")
            bad += 1
            continue
        open(P, "wb").write(orig.replace(old, new, 1))
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
    print("五刀全红" if bad == 0 else f"有 {bad} 处异常")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
