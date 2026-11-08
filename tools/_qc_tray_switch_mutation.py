# -*- coding: utf-8 -*-
"""托盘「静音 / 看屏」的变异验证：四把刀，刀刀必须被测试抓住。

跑法：.venv/Scripts/python.exe -u tools/_qc_tray_switch_mutation.py

纪律（沿用 _qc_pathrefs_mutation.py）：锚点多行用 J() 拼；变异必须语法合法；
改完还原并核对 sha256。
"""
import hashlib
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(ROOT)

P_TRAY = "src/system/tray_manager.py"
P_WEB = "src/ui/webapp.py"
TEST = "tools/test_tray_switch.py"
PY = ".venv/Scripts/python.exe"


# 【2026-11-08 修·刀自己会空转】原来只判 `r.returncode != 0`，于是判定文件**崩了**
# 也被算成"抓住"。实测：把 `tools/test_tray_switch.py` 整个换成一句 `raise RuntimeError("自己崩了")`，
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


def J(*lines: str) -> bytes:
    return chr(10).join(lines).encode("utf-8")


MUTS = [
    # (文件, 名称, old, new)
    (P_TRAY, "A 菜单文案写死（状态变了也不提示）",
     J('            pystray.MenuItem(lambda item: "取消静音" if self._live_muted() else "静音",',
       '                             self._on_toggle_mute),'),
     J('            pystray.MenuItem("静音", self._on_toggle_mute),')),

    (P_TRAY, "B 点击后本地取反（写盘失败时菜单与实际错位）",
     J('            r = self.on_toggle_mute_callback()',
       '            if isinstance(r, dict) and "muted" in r:',
       '                self.muted = bool(r["muted"])'),
     J('            r = self.on_toggle_mute_callback()',
       '            if isinstance(r, dict) and "muted" in r:',
       '                self.muted = not self.muted')),

    (P_WEB, "C 取消静音不放回 mode（mode=mute 时 muted=False 仍然没声）",
     J('                v["muted"] = False',
       '                if mode == "mute":',
       '                    v["mode"] = "clone"'),
     J('                v["muted"] = False')),

    (P_WEB, "D 静音顺手改 mode（回到 set_voice_mode 的老毛病：丢音色方案）",
     J('            else:',
       '                v["muted"] = True',
       '            self._save_config()',
       '            # TTS 播放循环每轮实时读 raw["voice"].muted，无需重建 ns；'),
     J('            else:',
       '                v["muted"] = True',
       '                v["mode"] = "mute"',
       '            self._save_config()',
       '            # TTS 播放循环每轮实时读 raw["voice"].muted，无需重建 ns；')),

    (P_TRAY, "E 菜单只认缓存、不问实时状态（设置页改过就显示旧状态）",
     J('        if self.state_provider is not None:',
       '            try:',
       '                return bool(self.state_provider()[0])'),
     J('        if False:',
       '            try:',
       '                return bool(self.state_provider()[0])')),

    (P_TRAY, "F 记忆库入口传错 page key（点了开错页）",
     J('    def _on_open_memory(self, icon, item):',
       '        self._on_open_page("memory")'),
     J('    def _on_open_memory(self, icon, item):',
       '        self._on_open_page("chat")')),

    (P_WEB, "G 给「关系档案」编一个不存在的路由（点了白屏）",
     E('        "relation": "/index.html",'),
     E('        "relation": "/redesign/ui_redesign_relation.html",')),
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
            print(f"[跳过] {name}：锚点命中 {n} 次")
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
            open(path, "wb").write(orig)

    ok = all(hashlib.sha256(open(p, "rb").read()).hexdigest() == h
             for p, h in hashes.items())
    print(f"\n还原字节一致：{ok}")
    if not ok:
        bad += 1
    print("七刀全红" if bad == 0 else f"有 {bad} 处异常")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
