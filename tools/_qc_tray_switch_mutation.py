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
            caught = r.returncode != 0
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
