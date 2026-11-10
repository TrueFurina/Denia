# -*- coding: utf-8 -*-
"""主动开口冒烟门（tools/test_proactive_smoke.py）的变异刀。

每把刀 = 把**生产代码**改坏一处 → 本门必须变红，而且必须红在**指定的那条判据**上。

判据口径（沿用第 38~47 轮）：本门自己打 `PASS=N  FAIL=M`；
「红了」= 非零退出 + 有汇总行（不是半路崩掉），
「命中」= 判据名出现在 `❌` 行里。

为什么要有这套刀（第 47 轮探针：52 把刀捅出 16 处洞）：

  ① **源码文本判据量不出接线**：管理器「委托 engine」原来只靠
     `inspect.getsource` 找字符串，改成「调用 engine 但丢掉返回值」照样绿（A17）；
     让位判据 `_in_call()` 同理，注释里留着字符串就能骗过源码文本判据（C08）。
  ② **判据绿的原因跟它名字无关**：「不回翻倍」只查理由串里有没有「翻倍」两个字，
     没量冷却时长（B06）；warmth 权重零判据是因为样本全为 0、唯一非 0 样本已饱和
     （A03）；B6 原 else 分支是无条件 `ok(..., True, "skip")`（B18）。
  ③ **桩钉死的分支不可见**：`reached_decision` 是门自己塞的标记、生产从不写（C04）；
     `_last_input_ts` / `_last_proactive` / `_duplex_last_speak_ts` 被钉成
     「守卫必然放行」，空闲 / 批次 / 8s / 开机起算四道守卫整段不可达（C13~C16）。
  ④ **文档里的判据不算判据**：`_on_user_text_input` 与 `subscribe()` 一次都没执行过，
     handler 写 `_last_input_ts`、USER_TEXT 订阅、无 bus 提前返回可以整段删掉（C10~C12）。
  ⑤ **判据崩 ≠ 判据错**：畸形 affinity 原是裸调用，实现一抛异常整个门连汇总行都没有
     （A18 探针报 CRASH）。全部走 `_safe` 包装，坏实现只报 ❌ 不崩门。

  另有一处**真接线漏洞**被钉死：双工让位只看 `_duplex_call_mode`，
  `_duplex_app_call_active` 这条 OR 分支门里原本一个样本都没有（C08）。

运行：  python tools/_qc_proactive_smoke_mutation.py
"""
import hashlib
import os
import re
import subprocess
import sys

PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PY = sys.executable
GATE = [os.path.join("tools", "test_proactive_smoke.py")]

# (编号, 文件, 原锚点, 变异后, 必须红在哪条判据, 为什么, 锚点是否允许多处)
MUTS = [
    ('A01', 'src/companion/relationship.py', '0.4 * float(aff.get("trust", 0.0))', '0.2 * float(aff.get("trust", 0.0))', '30 → 慢慢熟悉中', 'A01 trust 权重 0.4→0.2', False),
    ('A02', 'src/companion/relationship.py', '+ 0.4 * float(aff.get("intimacy", 0.0))', '+ 0.2 * float(aff.get("intimacy", 0.0))', '42 → 处得挺舒服', 'A02 intimacy 权重 0.4→0.2', False),
    ('A03', 'src/companion/relationship.py', '+ 0.2 * float(aff.get("warmth", 0.0)))', '+ 0.0 * float(aff.get("warmth", 0.0)))', 'A13b warmth 参与计分（0/0/150 → 慢慢熟悉中）', 'A03 warmth 权重 0.2→0', False),
    ('A04', 'src/companion/relationship.py', '        if score >= 78.0:\n            return "形影不离"', '        if score >= 88.0:\n            return "形影不离"', '78 → 形影不离', 'A04 形影不离门槛 78→88', False),
    ('A05', 'src/companion/relationship.py', '        if score >= 66.0:\n            return "真的很在乎你"', '        if score >= 76.0:\n            return "真的很在乎你"', '66 → 真的很在乎你', 'A05 真的很在乎你门槛 66→76', False),
    ('A06', 'src/companion/relationship.py', '        if score >= 54.0:\n            return "越来越亲近"', '        if score >= 64.0:\n            return "越来越亲近"', '54 → 越来越亲近', 'A06 越来越亲近门槛 54→64', False),
    ('A07', 'src/companion/relationship.py', '        if score >= 42.0:\n            return "处得挺舒服"', '        if score >= 52.0:\n            return "处得挺舒服"', '42 → 处得挺舒服', 'A07 处得挺舒服门槛 42→52', False),
    ('A08', 'src/companion/relationship.py', '        if score >= 30.0:\n            return "慢慢熟悉中"', '        if score >= 40.0:\n            return "慢慢熟悉中"', '30 → 慢慢熟悉中', 'A08 慢慢熟悉中门槛 30→40', False),
    ('A09', 'src/companion/relationship.py', '        except Exception:\n            return "萍水相逢"', '        except Exception:\n            return "刚认识"', 'affinity 畸形 → 萍水相逢兜底不抛', 'A09 畸形输入兜底改成刚认识', False),
    ('A10', 'src/companion/relationship.py', '            aff = (state or {}).get("affinity") or {}', '            aff = (state or {}).get("affinity")', '缺 affinity → 不崩，给『刚认识』', 'A10 affinity=None 不再兜底成空字典', False),
    ('A11', 'src/companion/relationship.py', '            return "形影不离"', '            return "刚认识"', '78 → 形影不离', 'A11 形影不离返回刚认识', False),
    ('A12', 'src/companion/relationship.py', '            return "真的很在乎你"', '            return "刚认识"', '66 → 真的很在乎你', 'A12 真的很在乎你返回刚认识', False),
    ('A13', 'src/companion/relationship.py', '            return "越来越亲近"', '            return "刚认识"', '54 → 越来越亲近', 'A13 越来越亲近返回刚认识', False),
    ('A14', 'src/companion/relationship.py', '            return "处得挺舒服"', '            return "刚认识"', '42 → 处得挺舒服', 'A14 处得挺舒服返回刚认识', False),
    ('A15', 'src/companion/relationship.py', '            return "慢慢熟悉中"', '            return "刚认识"', '30 → 慢慢熟悉中', 'A15 慢慢熟悉中返回刚认识', False),
    ('A16', 'src/companion/relationship.py', '        return "刚认识"\n\n    def _is_newbie', '        return "萍水相逢"\n\n    def _is_newbie', '0 分 → 刚认识', 'A16 最低档返回萍水相逢', False),
    ('A17', 'src/companion/relationship.py', '        return self.engine.relationship_progress_label(state)\n', '        self.engine.relationship_progress_label(state)  # noqa: 调用但不采纳\n        return "刚认识"\n', 'A14 管理器委托真的把 state 交给 engine 并采纳返回值', 'A17 委托改成调用但不采纳返回值（源码文本仍在）', False),
    ('A18', 'src/companion/relationship.py', '        except Exception:\n            return "萍水相逢"', '        except ValueError:\n            return "萍水相逢"', 'affinity 畸形 → 萍水相逢兜底不抛', 'A18 异常兜底只接 ValueError', False),
    ('B01', 'src/companion/relationship.py', '("off", "false", "0", "否")', '("false", "0", "否")', 'B1 主动已关 → 拒', 'B01 主动开关不再认 off', False),
    ('B02', 'src/companion/relationship.py', '    if h >= SILENCE_START_H or h < SILENCE_END_H:\n        return False, "静默时段"', '    if False:\n        return False, "静默时段"', 'B2 静默时段(23:00) → 拒', 'B02 静默时段整段失效', False),
    ('B03', 'src/companion/relationship.py', '    h = time.localtime(now).tm_hour', '    h = 15', 'B2 静默时段(23:00) → 拒', 'B03 本地小时写死 15', False),
    ('B04', 'src/companion/relationship.py', '    if last_p and (now - last_p) < cooldown:\n        return False, "冷却中" + ("(你不回→间隔翻倍)" if pending else "")', '    if False:\n        return False, "冷却中" + ("(你不回→间隔翻倍)" if pending else "")', 'B3 冷却中 → 拒', 'B04 冷却整段失效', False),
    ('B05', 'src/companion/relationship.py', '    freq = FREQ_HOURS.get(str(settings.get("frequency", "normal")), 24.0)', '    freq = 0.0', 'B3 冷却中 → 拒', 'B05 频次旋钮默认 24→0', False),
    ('B06', 'src/companion/relationship.py', '    cooldown = freq * 3600.0 * (2.0 if pending else 1.0)', '    cooldown = freq * 3600.0', 'B4b 不回确实把冷却拉到 48h（30h 仍冷却）', 'B06 不回翻倍拆掉', False),
    ('B07', 'src/companion/relationship.py', '    pending = bool(state.get("pending_response"))', '    pending = False', 'B4 冷却+你不回翻倍 → 拒', 'B07 pending_response 永远 False', False),
    ('B08', 'src/companion/relationship.py', '    last_p = float(state.get("last_proactive_ts") or 0.0)', '    last_p = 0.0', 'B3 冷却中 → 拒', 'B08 last_proactive_ts 永远 0', False),
    ('B09', 'src/companion/relationship.py', 'int(dp.get("count", 0)) >= DAILY_PROACTIVE_CAP', 'int(dp.get("count", 0)) > DAILY_PROACTIVE_CAP', 'B5 日上限 → 拒', 'B09 日上限 >= 改 >', False),
    ('B10', 'src/companion/relationship.py', '    if dp.get("date") == _today(now) and int(dp.get("count", 0)) >= DAILY_PROACTIVE_CAP:\n        return False, "日上限"', '    if False:\n        return False, "日上限"', 'B5 日上限 → 拒', 'B10 日上限整段失效', False),
    ('B11', 'src/companion/relationship.py', '    dp = state.get("daily_proactive") or {}', '    dp = {}', 'B5 日上限 → 拒', 'B11 daily_proactive 不读', False),
    ('B12', 'src/companion/relationship.py', '    if gap_h >= COLD_GAP_H and not arch.get("proactive_when_cold"):\n        return False, "冷落且原型不主动"', '    if False:\n        return False, "冷落且原型不主动"', 'B6 冷落+原型不主动 → 拒', 'B12 冷落原型闸门整段失效', False),
    ('B13', 'src/companion/relationship.py', '    gap_h = ((now - last_in) / 3600.0) if last_in else 999.0', '    gap_h = 0.0', 'B6 冷落+原型不主动 → 拒', 'B13 gap_h 永远 0', False),
    ('B14', 'src/companion/relationship.py', '    arch = ARCHETYPES.get(str(state.get("archetype") or "") or "", ARCHETYPES[DEFAULT_ARCHETYPE])', '    arch = ARCHETYPES[DEFAULT_ARCHETYPE]', 'B6b 冷落但原型黏人 → 照常开口', 'B14 原型永远用默认', False),
    ('B15', 'src/companion/relationship.py', '    return True, "ok"\n\n\n# ----------------------------------------------------------------------------\n# P6 观测日志', '    return False, "ok"\n\n\n# ----------------------------------------------------------------------------\n# P6 观测日志', 'B7 正常 → 开口', 'B15 正常放行改成拒绝', False),
    ('B16', 'src/companion/relationship.py', '    now = time.time() if now is None else float(now)\n    settings = state.get("settings") or {}', '    now = time.time()\n    settings = state.get("settings") or {}', 'B3 冷却中 → 拒', 'B16 入参 now 被无视', False),
    ('B17', 'src/companion/relationship.py', '    if gap_h >= COLD_GAP_H and not arch.get("proactive_when_cold"):', '    if gap_h >= 0 and not arch.get("proactive_when_cold"):', 'B7 正常 → 开口', 'B17 冷落阈值降到 0', False),
    ('B18', 'src/companion/relationship.py', 'ARCHETYPES: dict[str, dict[str, Any]] = {\n    "黏人":   {"decay": {"trust": 0.80, "intimacy": 0.70, "warmth": 0.90},\n               "hurt": 1.00, "proactive_when_cold": True,  "attitude_gain": 1.10},\n    "傲娇":   {"decay": {"trust": 0.80, "intimacy": 0.70, "warmth": 1.30},\n               "hurt": 1.10, "proactive_when_cold": False, "attitude_gain": 0.75},\n    "温柔":   {"decay": {"trust": 0.90, "intimacy": 0.85, "warmth": 0.90},\n               "hurt": 0.90, "proactive_when_cold": False, "attitude_gain": 1.00},\n    "毒舌":   {"decay": {"trust": 0.85, "intimacy": 0.80, "warmth": 1.15},\n               "hurt": 1.05, "proactive_when_cold": False, "attitude_gain": 0.90},\n    "高冷":   {"decay": {"trust": 0.80, "intimacy": 0.75, "warmth": 1.25},\n               "hurt": 0.80, "proactive_when_cold": False, "attitude_gain": 0.60},\n    "元气":   {"decay": {"trust": 0.85, "intimacy": 0.80, "warmth": 0.85},\n               "hurt": 0.95, "proactive_when_cold": True,  "attitude_gain": 1.05},\n}', 'ARCHETYPES: dict[str, dict[str, Any]] = {\n    "黏人":   {"decay": {"trust": 0.80, "intimacy": 0.70, "warmth": 0.90},\n               "hurt": 1.00, "proactive_when_cold": True,  "attitude_gain": 1.10},\n    "傲娇":   {"decay": {"trust": 0.80, "intimacy": 0.70, "warmth": 1.30},\n               "hurt": 1.10, "proactive_when_cold": True,  "attitude_gain": 0.75},\n    "温柔":   {"decay": {"trust": 0.90, "intimacy": 0.85, "warmth": 0.90},\n               "hurt": 0.90, "proactive_when_cold": True,  "attitude_gain": 1.00},\n    "毒舌":   {"decay": {"trust": 0.85, "intimacy": 0.80, "warmth": 1.15},\n               "hurt": 1.05, "proactive_when_cold": True,  "attitude_gain": 0.90},\n    "高冷":   {"decay": {"trust": 0.80, "intimacy": 0.75, "warmth": 1.25},\n               "hurt": 0.80, "proactive_when_cold": True,  "attitude_gain": 0.60},\n    "元气":   {"decay": {"trust": 0.85, "intimacy": 0.80, "warmth": 0.85},\n               "hurt": 0.95, "proactive_when_cold": True,  "attitude_gain": 1.05},\n}', 'B6a 前提：至少一个不主动原型且黏人是主动原型', 'B18 所有原型都 proactive_when_cold=True（B6 走 skip 分支）', False),
    ('C01', 'src/companion/proactive.py', '            if self._in_call():\n                return\n            if self._llm is None or not self._llm_generate:', '            if False:\n                return\n            if self._llm is None or not self._llm_generate:', 'C-realtime 通话中：让位不打扰', 'C01 通话让位整段失效', False),
    ('C02', 'src/companion/proactive.py', "        return bool(getattr(self, '_duplex_call_mode', None)) or bool(getattr(self, '_duplex_app_call_active', False))", '        return False', 'C-前提：app 通话标志让 _in_call() 为真', 'C02 _in_call 永远 False', False),
    ('C03', 'src/companion/proactive.py', "        return bool(getattr(self, '_duplex_call_mode', None)) or bool(getattr(self, '_duplex_app_call_active', False))", '        return True', 'C-realtime 档位但未通话：主动管线照常走到闸门', 'C03 _in_call 永远 True', False),
    ('C04', 'src/companion/proactive.py', '            if self._in_call():\n                return\n            if self._llm is None or not self._llm_generate:\n                return', '            if self._in_call():\n                return\n            if False:\n                return', 'C-非双工(无llm)：没走到关系闸门', 'C04 无 llm 前置守卫拆掉（reached_decision 生产从不写）', False),
    ('C05', 'src/companion/proactive.py', '            if _cid:\n                try:\n                    from .relationship import (get_relationship_manager,', '            if False:\n                try:\n                    from .relationship import (get_relationship_manager,', 'C-realtime 档位但未通话：主动管线照常走到闸门', 'C05 关系闸门 _cid 分支整段失效', False),
    ('C06', 'src/companion/proactive.py', '                    _ok, _why = _proactive_decision(get_relationship_manager().get(_cid), now)', '                    _ok, _why = (False, "test")', 'C-realtime 档位但未通话：主动管线照常走到闸门', 'C06 关系闸门改成常量假结果（spy 不再被调用）', False),
    ('C07', 'src/companion/proactive.py', '                    if not _ok:\n', '                    if False:\n', 'C-闸门拦下：记 gate_block 且不往下走', 'C07 闸门拦下后不再 return（放行后续 emit）', False),
    ('C08', 'src/companion/proactive.py', '            if self._in_call():\n                return\n            if self._llm is None or not self._llm_generate:', "            # self._in_call() 语义内联（等价改写，观测不到接线）\n            if bool(getattr(self, '_duplex_call_mode', None)):\n                return\n            if self._llm is None or not self._llm_generate:", 'C-app 通话标志独立生效：让位不打扰', 'C08 让位判据内联且丢掉 OR（源码文本里仍留 self._in_call()）', False),
    ('C09', 'src/companion/proactive.py', '    async def _on_user_text_input(self, e) -> None:', '    def _on_user_text_input(self, e) -> None:', '_on_user_text_input 是 async def（await h(event) 不会 TypeError）', 'C09 _on_user_text_input 改成同步 def', False),
    ('C10', 'src/companion/proactive.py', '            self._last_input_ts = time.time()\n        except Exception:  # noqa: BLE001\n            pass', '            pass\n        except Exception:  # noqa: BLE001\n            pass', 'D-USER_TEXT handler 真的刷新 _last_input_ts', 'C10 handler 不再写 _last_input_ts', False),
    ('C11', 'src/companion/proactive.py', '            bus.subscribe(EventType.USER_TEXT, self._on_user_text_input)', '            pass', 'D-USER_TEXT 订阅存在且绑到 async handler', 'C11 USER_TEXT 订阅整段消失', False),
    ('C12', 'src/companion/proactive.py', "        if bus is None or not hasattr(bus, 'subscribe'):\n            return", '        if False:\n            return', 'D-无 bus：不改内部状态', 'C12 无 bus 提前返回整段失效', False),
    ('C13', 'src/companion/proactive.py', '            if _idle < self._idle_threshold_s:\n                return', '            if False:\n                return', 'C-刚输入过（空闲未达标）：不评估关系闸门', 'C13 空闲阈值判定整段失效', False),
    ('C14', 'src/companion/proactive.py', '            if now - self._last_proactive_or_zero() < self._batch_interval_s:\n                return', '            if False:\n                return', 'C-批次冷却未过：不评估关系闸门', 'C14 批次冷却判定整段失效', False),
    ('C15', 'src/companion/proactive.py', "            _last_spk = float(getattr(self, '_duplex_last_speak_ts', 0.0) or 0.0)\n            if _last_spk and (time.time() - _last_spk) < 8.0:\n                return", '            if False:\n                return', 'C-刚播过一句 8s 内：不评估关系闸门', 'C15 8s 防打断判定整段失效', False),
    ('C16', 'src/companion/proactive.py', "            if not _last_in:\n                _last_in = float(getattr(self, '_session_boot_ts', 0.0) or 0.0)\n            if not _last_in:\n                return  # 连开机时刻都拿不到（理论上不会发生）→ 才放弃", '            if not _last_in:\n                return  # 旧 bug：0.0 是 falsy 直接放弃', 'C-从没输入过：用开机时刻当空闲起点（不是放弃判定）', 'C16 _last_in==0 回落开机时刻拆掉（旧死触发 bug）', False),
]

def _sha(p):
    with open(p, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def _run():
    return subprocess.run([PY] + GATE, cwd=PROJ, capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=900)


def _out(r):
    return (r.stdout or "") + (r.stderr or "")


def _summary(r):
    m = re.search(r"PASS=\d+\s+FAIL=\d+", _out(r))
    return m.group(0) if m else "(无汇总行)"


def _judged(r):
    """非零退出 + 本门自己打了汇总行（而不是半路崩掉）。"""
    return r.returncode != 0 and bool(
        re.search(r"PASS=\d+\s+FAIL=\d+", _out(r)))


def _hit(r, name):
    return any(ln.strip().startswith("❌") and name in ln
               for ln in _out(r).splitlines())


def main():
    base = _run()
    if not (base.returncode == 0
            and re.search(r"PASS=\d+\s+FAIL=0", _out(base))):
        # 带 FAIL 前缀：空变异体检要能区分「套件自己报了失败」和「套件崩了」。
        print("FAIL 基线不是绿的 —— 后面的红不能证明任何事：")
        print(_out(base)[-1500:])
        return 2
    print("基线：绿 | %s" % _summary(base))

    rows = []
    for key, rel, old, new, expect, why, all_hits in MUTS:
        p = os.path.join(PROJ, *rel.split("/"))
        orig = open(p, encoding="utf-8").read()
        n = orig.count(old)
        if n == 0 or (not all_hits and n != 1):
            print("  %-6s 锚点命中 %d 次（应恰好 1 次），锚点已腐烂" % (key, n))
            rows.append((key, "ANCHOR", why))
            continue
        h0 = _sha(p)
        try:
            open(p, "w", encoding="utf-8", newline="").write(orig.replace(old, new))
            r = _run()
        finally:
            open(p, "w", encoding="utf-8", newline="").write(orig)
            if _sha(p) != h0:
                raise SystemExit("还原失败（字节不一致）：%s" % rel)
        judged = _judged(r)
        hit = _hit(r, expect)
        tag = "红·命中" if (judged and hit) else ("红·跑偏" if judged else "没红")
        print("  %-6s %-8s %s" % (key, tag, _summary(r)))
        if judged and not hit:
            print("      ↳ 红了但没红在「%s」上：%s"
                  % (expect, [ln[:80] for ln in _out(r).splitlines()
                              if ln.strip().startswith("❌")][:3]))
        rows.append((key, tag, why))

    print("\n" + "=" * 72)
    ok = sum(1 for _, t, _ in rows if t == "红·命中")
    print("刀 %d 把：红且命中 %d、红但跑偏 %d、没红 %d、锚点腐烂 %d"
          % (len(rows), ok,
             sum(1 for _, t, _ in rows if t == "红·跑偏"),
             sum(1 for _, t, _ in rows if t == "没红"),
             sum(1 for _, t, _ in rows if t == "ANCHOR")))
    if ok != len(rows):
        print("\n没验住的：")
        for key, t, why in rows:
            if t != "红·命中":
                print("  [%s] %s —— %s" % (t, key, why))
        return 1
    print("全部 %d 把刀都红在指定判据上。" % ok)
    return 0


if __name__ == "__main__":
    sys.exit(main())
