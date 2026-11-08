# -*- coding: utf-8 -*-
"""「在场感增强」的变异验证：六刀，刀刀必须被抓住。

跑法：.venv/Scripts/python.exe -u tools/_qc_presence_mutation.py

六刀对应六条最容易悄悄退化的契约：
  A 最大化重新被当成全屏（用户一最大化浏览器她就消失）
  B 退出全屏没有迟滞（切一下窗她闪一下）
  C 抢用户手动隐藏的桌宠（退出全屏把她翻出来）
  D 非法组合键不再校验（写错就吞掉热键）
  E 重复组合键不再去重（后一个覆盖前一个）
  F 配置关闭也被无视（用户关不掉）
"""
import hashlib
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(ROOT)

P_GUARD = "src/system/fullscreen_guard.py"
P_HOT = "src/system/hotkeys.py"
TEST = "tools/test_presence_controls.py"
PY = ".venv/Scripts/python.exe"


# 【2026-11-08 修·刀自己会空转】原来只判 `r.returncode != 0`，于是判定文件**崩了**
# 也被算成"抓住"。实测：把 `tools/test_presence_controls.py` 整个换成一句 `raise RuntimeError("自己崩了")`，
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


MUTS = [
    (P_GUARD, "A 最大化重新算全屏（P0-2 最致命的回退）",
     E("        if zoomed:\n            return False\n"),
     E("        if False:\n            return False\n")),

    (P_GUARD, "B 退出全屏没有迟滞",
     E("        if self._clear_streak < self.show_after:\n            return None\n"),
     E("        if False:\n            return None\n")),

    (P_GUARD, "C 抢用户手动隐藏的桌宠",
     E("                self._suppressed = not pet_visible\n"),
     E("                self._suppressed = False\n")),

    (P_HOT, "D 非法组合键不校验",
     E("        if not _validate(str(combo)):\n"),
     E("        if False:\n")),

    (P_HOT, "E 重复组合键不去重",
     E("        if key in out:\n"),
     E("        if False:\n")),

    (P_HOT, "F 配置关了也照起热键",
     E('    if not isinstance(section, dict) or not section.get("hotkeys_enabled", False):\n'),
     E("    if False:\n")),
]


def main() -> int:
    originals = {}
    for path, *_ in MUTS:
        if path not in originals:
            originals[path] = open(path, "rb").read()
    hashes = {p: hashlib.sha256(b).hexdigest() for p, b in originals.items()}
    bad = 0
    if not _baseline_green():
        print("FAIL 起点就不是绿的 —— 恒红的判定能让每把刀都变红，这种\"全红\"不证明任何事")
        return 1
    print("  PASS 起点绿")

    for path, name, old, new in MUTS:
        orig = originals[path]
        n = orig.count(old)
        if n != 1:
            print("[跳过] %s：锚点命中 %d 次" % (name, n))
            bad += 1
            continue
        open(path, "wb").write(orig.replace(old, new, 1))
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
            for p, b in originals.items():
                open(p, "wb").write(b)

    ok = all(hashlib.sha256(open(p, "rb").read()).hexdigest() == h
             for p, h in hashes.items())
    print("\n还原字节一致：%s" % ok)
    if not ok:
        bad += 1
    print("六刀全红" if bad == 0 else "有 %d 处异常" % bad)
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
