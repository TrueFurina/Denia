# -*- coding: utf-8 -*-
"""引导页集成门（tools/test_onboarding_integration.py）的变异刀。

每把刀 = 把**生产代码**改坏一处 → 本门必须变红，而且必须红在**指定的那条断言**上。
「红了」不等于「认出来了」：
  · 崩了（Traceback）不算 —— 那说明门是被异常打死的，不是被判据拦住的；
  · 红在别的断言上也不算 —— 说明这条判据其实没在守它名字里承诺的东西。

判据口径（沿用第 38 轮的教训）：
  **不看 `FAIL` 字样**，看本门自己的汇总行 `RESULT: ALL GREEN` / `PASS = N   FAIL = M`
  以及 `  FAIL  <断言名>` 这一行。被测代码自己会打印形似失败的诊断行，按字面过滤会误判。

运行：  python tools/_qc_onboarding_mutation.py
"""
import hashlib
import os
import subprocess
import sys

PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PY = sys.executable
GATE = [os.path.join("tools", "test_onboarding_integration.py")]


def _run_gate():
    return subprocess.run([PY] + GATE, cwd=PROJ, capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=1200)


def _out(r):
    return (r.stdout or "") + (r.stderr or "")


def _judged(r):
    """非零退出 + 没崩 + 本门汇总行明确说有几条没过。"""
    out = _out(r)
    if "Traceback (most recent call last)" in out:
        return False
    if r.returncode == 0:
        return False
    for ln in out.splitlines():
        if "FAIL =" in ln and "PASS =" in ln:
            try:
                return int(ln.split("FAIL =")[1].split()[0]) > 0
            except (IndexError, ValueError):
                return False
    return False


def _fail_lines(r):
    return [ln.strip() for ln in _out(r).splitlines() if ln.strip().startswith("FAIL  ")]


def _hit(r, frag):
    return any(frag in ln for ln in _fail_lines(r))


def _sha(p):
    with open(p, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


# ---------------------------------------------------------------------------
# 刀表： (键, 文件, 原文, 改成, 必须红在这条断言上, 说明, 是否全量替换)
# ---------------------------------------------------------------------------
MUTS = [
    # ===== A. 静态契约 =====
    ("flow_drop_role", "src/ui/web/first_launch_setup.html",
     "  ['screenRole','screenSummary'].forEach(id=>{",
     "  ['screenSummary'].forEach(id=>{",
     "[A4] 首启实际链路",
     "首启实际链路丢掉「建角色」屏 —— 用户装完直接进体检，没有角色可建", False),

    ("flow_order", "src/ui/web/first_launch_setup.html",
     "  ['screenRole','screenSummary'].forEach(id=>{",
     "  ['screenSummary','screenRole'].forEach(id=>{",
     "[A4] 首启实际链路",
     "顺序颠倒：先体检再建角色（体检会照着「还没有角色」判定）", False),

    ("flow_bad_id", "src/ui/web/first_launch_setup.html",
     "  ['screenRole','screenSummary'].forEach(id=>{",
     "  ['screenRole','screenSummaryX'].forEach(id=>{",
     "[A4] 实际链路引用的屏 id",
     "链路引用一个不存在的屏 id —— getElementById 拿 null，流程静默少一屏", False),

    ("rollback_drop_role", "src/ui/web/first_launch_setup.html",
     "'云端付费': ['screenProvider','screenMic','screenRole','screenSummary'],",
     "'云端付费': ['screenProvider','screenMic','screenSummary'],",
     "[A4·回滚] MODE_SEQ",
     "回滚表里云端链路少了「建角色」——回滚过去就是条断链", False),

    ("bridge_rename", "src/ui/web/redesign/app_backend.js",
     "  const getCapabilitySlots = () => call('get_capability_slots');",
     "  const getCapabilitySlotsGone = () => call('get_capability_slots');",
     "[A1] 引导页",
     "桥面把 getCapabilitySlots 改名 —— 引导页第 1 步还在调，运行时 TypeError", False),

    # 这把刀要同时拆掉两种调用写法：下载器里 `B.xxx(` 和 `Backend.xxx(` 混用，
    # 只改一种，另一种还能撑住「解析出调用」的下限，刀就不成立（第一版就是这么假红的）。
    ("no_bridge_calls", "src/ui/web/redesign/model_downloader.js",
     (("Backend.", "Bk."), ("B.", "BB.")),
     None,
     "[A1] 每个前端文件都真的解析出桥调用",
     "下载器整体不再走 Backend 桥 —— 静态契约里「全部存在」变成真空真", True),

    ("a8_cap", "src/ui/web/onboarding_role.js",
     'var _payload = {cap:"tts", provider:p.id, model:model};',
     'var _payload = {cap:"chat", provider:p.id, model:model};',
     "[A8] 建角色步写 TTS",
     "建角色步把 TTS 写进「对话」槽 —— 声音配了但不出声", False),

    ("a8_providers", "src/ui/web/onboarding_role.js",
     "var TTS_PROVIDERS = [",
     "var TTS_PROVIDERS_LOCAL = [",
     "[A8] 建角色步的厂商清单",
     "建角色步自建厂商表、不再读真相源 TTS_PROVIDERS", False),

    ("a9_shell", "src/ui/main_window.py",
     "if(window.top!==window)return;",
     "if(false)return;",
     "[A9]",
     "外壳守卫不再对 iframe 放行 —— 能力槽嵌入页整页被拦", False),

    # ===== B. 真后端契约 =====
    ("b3_model_name", "src/models/cloud_tts.py",
     '        "model_name": model_name or _prev.get("model_name") or "",',
     '        "model_name": "",',
     "[B3] 模型名也一同落桶",
     "写能力槽只落 Key 不落模型名 —— 读回来还是上一个模型", False),

    ("b4_empty_key", "src/models/cloud_tts.py",
     '        "api_key": api_key or _prev.get("api_key") or "",',
     '        "api_key": api_key or "",',
     "[B4] 空 api_key 不擦除",
     "「空字符串=不改」被拆掉 —— 引导页只回传部分字段就静默擦掉已配 Key", False),

    ("b6_delete_success", "src/companion/model_catalog.py",
     '        return {"success": False, "error": "该档位文件不存在（可能未下载或已删除）"}',
     '        return {"success": True}',
     "[B6] deleteModelQuant 未装档",
     "卸载不存在的档位报成功 —— UI 会显示「已卸载」，磁盘上本来就没有", False),

    ("b6_download_unknown", "src/companion/model_catalog.py",
     '    if quant is None:\n'
     '        return {"success": False, "error": f"未知量化档: {quant_id}"}\n'
     '    # 并发守卫',
     '    if quant is None:\n'
     '        return {"success": True}\n'
     '    # 并发守卫',
     "[B6] 未知量化档被拒",
     "未知量化档也返回成功 —— 后面会去下一个不存在的文件", False),

    ("b10_m3d_guard", "src/ui/webapp.py",
     '            if char_id != "danya":',
     "            if False:",
     "[B10] 后端兜底",
     "非达妮娅角色的 model_3d 后端兜底被拆 —— 引导页建的角色会被挂上 Live2D", False),

    ("b11_runtime_reject", "src/ui/webapp.py",
     '                return {"success": False, "error": f"角色不存在: {char_id}"}',
     '                return {"success": True, "active_id": char_id}',
     "[B11] 运行时桥",
     "运行时桥说角色不存在，接口却报成功 —— active_character_id 会指向空", False),

    ("b11_fallback_reject", "src/ui/webapp.py",
     '\n        return {"success": False, "error": f"角色不存在: {char_id}"}',
     '\n        return {"success": True, "active_id": char_id}',
     "[B11] switchCharacter 不存在角色被拒",
     "配置级回退路径也接受不存在的角色", False),

    ("b17_validate_fail", "src/ui/webapp.py",
     '            return {"success": True, "ok": (not has_fail), "checks": checks}',
     '            return {"success": False, "ok": False, "checks": checks}',
     "[B17] validateOnboarding 接受精简",
     "体检接口正常跑完却报 success=False —— 前端按 success 判定，完成按钮会点不动", False),

    ("b17_ok_invert", "src/ui/webapp.py",
     '            return {"success": True, "ok": (not has_fail), "checks": checks}',
     '            return {"success": True, "ok": has_fail, "checks": checks}',
     "[B17] ok 的定义自洽",
     "ok 取反：有 fail 反而说没问题 —— 体检页绿灯放行", False),

    ("b18_mode", "src/ui/webapp.py",
     '            raw["companion_mode"] = token',
     '            raw["companion_mode"] = "chat"',
     "[B18] companion_mode",
     "档位名不再映射，一律写成 chat —— 首启选的「混合推荐」落盘成纯聊天", False),

    ("b19_engines", "src/ui/webapp.py",
     '        for eng in ("voxcpm", "cosyvoice", "qwen3tts", "mimo_tts", "gpt_sovits"):',
     '        for eng in ("voxcpm", "cosyvoice", "qwen3tts", "gpt_sovits"):',
     "[B19] 覆盖 5 引擎",
     "克隆引擎清单少一家（MiMo 云端）—— 下载器页少一张卡，且不报错", False),

    # ===== C. 异常与健壮性 =====
    ("c1_guard_gone", "src/ui/webapp.py",
     '        if not self._cfg:\n'
     '            return {"success": False, "error": "配置未加载"}\n'
     '        try:\n'
     '            from src.companion.model_catalog import get_model_catalog as _cat',
     '        try:\n'
     '            from src.companion.model_catalog import get_model_catalog as _cat',
     "[C1] 这 14 个端点",
     "getModelCatalog 的 _cfg 守卫被删 —— 配置没加载也照跑", False),

    ("c1_success_true", "src/ui/webapp.py",
     '        if not self._cfg:\n'
     '            return {"success": False, "error": "配置未加载"}\n'
     '        if isinstance(path, dict):',
     '        if not self._cfg:\n'
     '            return {"success": True}\n'
     '        if isinstance(path, dict):',
     "[C1] _cfg=None 时",
     "守卫还在，但返回成功 —— 前端拿到 success 就往下走，后面全是空数据", False),

    ("c2_dirty_throw", "src/models/slot_config.py",
     "    for cap, meta in CAPABILITY_SLOTS.items():",
     '    for cap, meta in CAPABILITY_SLOTS.items():\n'
     '        raw["voice"]["clone"]  # MUT: 脏数据必抛',
     "[C2]",
     "读能力槽不再容忍脏数据 —— 配置里 voice 缺失就整页崩", False),

    ("c3_unknown_engine", "src/ui/webapp.py",
     '        if engine not in ("qwen3tts", "voxcpm", "cosyvoice"):\n'
     '            return {"success": False, "error": f"未知本地 TTS 引擎: {engine}"}',
     '        if False:\n'
     '            return {"success": False, "error": f"未知本地 TTS 引擎: {engine}"}',
     "[C3] setTtsModelDir 未知引擎",
     "未知引擎也照写目录 —— 配置里多一个不存在的引擎键", False),
]


def main():
    base = _run_gate()
    if not _judged_green(base):
        print("基线不是绿的（PASS/FAIL 见下），后面的红不能证明任何事：")
        print(_out(base)[-1500:])
        return 2
    print("基线：绿 | %s" % _summary(base))

    rows = []
    for key, rel, old, new, expect, why, all_hits in MUTS:
        p = os.path.join(PROJ, *rel.split("/"))
        orig = open(p, encoding="utf-8").read()
        # old 可以是单条字符串，也可以是 ((old1,new1),(old2,new2)) —— 一把刀改多处
        pairs = list(old) if isinstance(old, tuple) else [(old, new)]
        counts = [orig.count(o) for o, _ in pairs]
        if any(c == 0 for c in counts) or (not all_hits and any(c != 1 for c in counts)):
            print("  %-22s 锚点命中 %s（应恰好 1 次），锚点已腐烂" % (key, counts))
            rows.append((key, "ANCHOR", 0, why))
            continue
        h0 = _sha(p)
        try:
            text = orig
            for _o, _n in pairs:
                text = text.replace(_o, _n)
            open(p, "w", encoding="utf-8", newline="").write(text)
            r = _run_gate()
        finally:
            open(p, "w", encoding="utf-8", newline="").write(orig)
            if _sha(p) != h0:
                raise SystemExit("还原失败（字节不一致）：%s" % rel)
        judged = _judged(r)
        hit = _hit(r, expect)
        tag = "红·命中" if (judged and hit) else ("红·跑偏" if judged else "没红")
        print("  %-22s %-8s %s" % (key, tag, _summary(r)))
        if judged and not hit:
            print("      ↳ 红了但没红在「%s」上：%s" % (expect, _fail_lines(r)[:3]))
        rows.append((key, tag, sum(counts), why))

    print("\n" + "=" * 72)
    ok = sum(1 for _, t, _, _ in rows if t == "红·命中")
    print("刀 %d 把：红且命中 %d、红但跑偏 %d、没红 %d、锚点腐烂 %d"
          % (len(rows), ok,
             sum(1 for _, t, _, _ in rows if t == "红·跑偏"),
             sum(1 for _, t, _, _ in rows if t == "没红"),
             sum(1 for _, t, _, _ in rows if t == "ANCHOR")))
    if ok != len(rows):
        print("\n没验住的：")
        for key, t, _, why in rows:
            if t != "红·命中":
                print("  [%s] %s —— %s" % (t, key, why))
        return 1
    print("全部 %d 把刀都红在指定判据上。" % ok)
    return 0


def _summary(r):
    for ln in _out(r).splitlines():
        if "PASS =" in ln and "FAIL =" in ln:
            return ln.strip()
    return "(无汇总行)"


def _judged_green(r):
    return r.returncode == 0 and "RESULT: ALL GREEN" in _out(r)


if __name__ == "__main__":
    sys.exit(main())
