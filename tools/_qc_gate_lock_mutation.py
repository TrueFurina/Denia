# -*- coding: utf-8 -*-
"""「check_all 互斥锁」的变异验证：五刀，刀刀必须被抓住。

跑法：python -u tools/_qc_gate_lock_mutation.py（用当前解释器，不依赖 .venv）

A 不清理陈旧锁        → 上次被强杀后所有门禁永久堵死
B 拿不到锁也报成功    → 两个会话又能同时改源码（锁形同虚设）
C release 无条件删锁   → 把别人的锁删掉
D 陈旧阈值改成 -1      → 活着的锁也被偷（不能写 0，见 MUTS 内注释）
E 只看 OpenProcess     → 已退出但句柄仍被持有的 PID 被判"活着"，锁清不掉
"""
import hashlib
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(ROOT)

P = "tools/_gate_lock.py"
TEST = "tools/test_gate_lock.py"
PY = sys.executable  # 干净 runner 上没有 .venv；子进程复用当前解释器


# 【2026-11-08 修·刀自己会空转】原来只判 `r.returncode != 0`，于是判定文件**崩了**
# 也被算成"抓住"。实测：把 `tools/test_gate_lock.py` 整个换成一句 `raise RuntimeError("自己崩了")`，
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
    # 【2026-11-08 修·这一行比判定本身还严】原来要求"输出里没有 Traceback"。
    # 但**判定自己故意打出来的** Traceback 也算——比如 `test_presence_controls.py`
    # 的 A3 就故意让回调抛异常，再验证 listener 没被弄死，那一段会经 logger 打到
    # stderr。于是测试 41 PASS / 0 FAIL / exit 0，本函数却判"起点不是绿的"，
    # 整把刀一进来就自爆，而报错信息把矛头指向**产品**。
    # 判据改成"退出码 0 且没有 FAIL 行"：崩了的判定一定非零退出，
    # Traceback 本身不能证明任何事。同一轮 `_judged()` 也是这么改的。
    return r.returncode == 0 and not any(l.strip().startswith("FAIL")
                                         for l in out.splitlines())



def E(s: str) -> bytes:
    return s.encode("utf-8")


MUTS = [
    ("A 不清理陈旧锁（强杀后永远跑不了门禁）",
     E("            if (not info) or age > STALE_SECONDS or not alive:\n"),
     E("            if False:\n")),

    ("B 拿不到锁也报成功（互斥失效）",
     E('            return (False, "已有 check_all 在跑（PID %s，%.0fs 前启动）"\n'
       '                    % (info.get("pid", "?"), max(0.0, age)))\n'),
     E('            return (True, "")\n')),

    ("C release 无条件删锁（删掉别人的锁）",
     E("    if not info or int(info.get(\"pid\") or 0) == os.getpid():\n"),
     E("    if True:\n")),

    # D 必须是 -1 不能是 0：_gate_lock.py 判 `age > STALE_SECONDS`，
    # 而 Windows 时钟分辨率下连续两次 time.time() 差为 0 的比例实测 75.4%，
    # 写 0 时 `age > 0` 大概率不成立 ⇒ 这把刀时灵时不灵（2026-11-08 CI 实证：
    # 本机连中三次、windows-latest runner 上直接没抓住）。写 -1 则恒为真。
    ("D 陈旧阈值改成 -1（活锁也被偷）",
     E("STALE_SECONDS = 45 * 60"),
     E("STALE_SECONDS = -1")),

    ("E 只看 OpenProcess（已退出但句柄未释放 → 判活着，锁永远清不掉）",
     E("            return code.value == STILL_ACTIVE\n"),
     E("            return True\n")),
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
            print("[跳过] %s：锚点命中 %d 次" % (name, n))
            bad += 1
            continue
        open(P, "wb").write(orig.replace(old, new, 1))
        try:
            r = subprocess.run([PY, "-u", TEST], capture_output=True,
                               text=True, encoding="utf-8", errors="replace")
            caught = _judged(r)
            lines = [l for l in (r.stdout or "").splitlines() if l.strip()]
            why = [l for l in lines if l.strip().startswith("FAIL")]
            why = (why or lines or ["<无输出>"])[0].strip()[:88]
            print(name)
            print("   -> " + ("已抓住：" + why if caught else "没抓住（测试失效）"))
            if not caught:
                bad += 1
        finally:
            open(P, "wb").write(orig)

    ok = hashlib.sha256(open(P, "rb").read()).hexdigest() == h0
    print("\n还原字节一致：%s" % ok)
    if not ok:
        bad += 1
    print("五刀全红" if bad == 0 else "有 %d 处异常" % bad)
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
