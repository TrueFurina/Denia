# -*- coding: utf-8 -*-
"""「UI 看门狗 COM 边界」的变异验证：四刀，刀刀必须被抓住。

跑法：.venv/Scripts/python.exe -u tools/_qc_ui_reload_boundary_mutation.py

四刀全部对应真实事故形态：跨线程 Reload 会冻结整个进程，闸门必须抓住。
"""
import hashlib
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(ROOT)

P = "src/ui/api_window.py"
TEST = "tools/check_ui_reload_boundary.py"
PY = ".venv/Scripts/python.exe"


# 【2026-11-08 修·刀自己会空转】原来只判 `r.returncode != 0`，于是闸门**崩了**
# 也被算成"抓住"。实测：把 check_ui_reload_boundary.py 整个换成一句
# `raise RuntimeError("闸门自己崩了")`，本刀仍然逐条打印「已抓住：<无输出>」、
# 最后打印「N 刀全红」并 exit 0 —— **一个根本跑不起来的闸门被认证成"四刀全抓"**。
# 崩了和识别是两件事：前者说明闸门没跑，后者才说明判据在工作。
# 起点也必须先绿：恒红的闸门能让**每一把**刀都变红，那种"全红"什么都不证明。
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
    ("A 去掉 BeginInvoke（回到跨线程直接调）",
     E("                ctrl.BeginInvoke(_Act(_reload))\n"),
     E("                pass  # MUT: no BeginInvoke\n")),

    ("B 守护线程里直接碰 COM（16:55:50 冻结原形态）",
     E("                now = time.time()\n                last = self._last_ui_ping\n"),
     E("                now = time.time()\n"
       "                self.CoreWebView2.Reload()  # MUT: watchdog touches COM\n"
       "                last = self._last_ui_ping\n")),

    ("C 改掉 _reload 名字（闭包没被封送）",
     E("                def _reload():\n"),
     E("                def _reload_unused():\n")),

    ("D BeginInvoke 里不传 _reload（闭包定义了没用）",
     E("                ctrl.BeginInvoke(_Act(_reload))\n"),
     E("                ctrl.BeginInvoke(_Act(lambda: None))  # MUT\n")),
]


def main() -> int:
    orig = open(P, "rb").read()
    h0 = hashlib.sha256(orig).hexdigest()
    bad = 0
    if not _baseline_green():
        print("FAIL 起点就不是绿的 —— 恒红的闸门能让每把刀都变红，这种\"全红\"不证明任何事")
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
            why = (why or lines or ["<无输出>"])[-1].strip()[:88]
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
    print("四刀全红" if bad == 0 else "有 %d 处异常" % bad)
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
