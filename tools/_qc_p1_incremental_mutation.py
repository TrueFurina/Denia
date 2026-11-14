# -*- coding: utf-8 -*-
"""P1 增量写接口门（tools/test_p1_incremental.py）的变异刀。

每把刀 = 把**生产代码**改坏一处 → 本门必须变红，而且必须红在**指定的那条判据**上。

判据口径（沿用第 38~46 轮）：本门不是 unittest，自己打
`总计: PASS=N  FAIL=M`；「红了」= 非零退出 + 有汇总行（不是半路崩掉），
「命中」= 判据名出现在 `[FAIL]` 行里。

为什么要有这套刀（第 46 轮探针：63 把刀捅出 40 处零判据，按成因分五类）：

  ① **文档字符串里的判据不算判据**：原门第 ⑤ 条写着「老接口
     save_text_chat_providers 行为不变（回归）」，但整份文件一次都没调用过它 ——
     老接口 8 处分支全零判据（S01~S08）。
  ② **「配置回到原样」绿在「压根没改过」上**：桩不改 raw，回滚删掉也照样绿
     （第 44 轮饱和度同型）。现在桩先真改内存态再失败（M14）。
  ③ **副作用全被桩吃掉**：_apply_llm_live / _cfg.ns 重建 / commit_model_write /
     删除与幂等路径的写盘次数，一条判据都没有（U22~U25、D08~D12）。
  ④ **只走 happy path**：平铺入参、非法值归一、去尾斜杠、timeout 坏值、caps 缺键、
     vision、ollama 豁免、三端口回落点、关闭路径两步失败、step2 失败、
     视觉管线失败不阻断（U01~U21、D01/D04/D06、M06/M08~M10）。
  ⑤ **判据崩 ≠ 判据错**：`③ 新增项字段完整` 原直接下标 providers[2]，
     实现一坏就 IndexError 炸掉整个门、连汇总行都没有。改成 _at() 安全读。

  ★ 探针还挖出一个**真 bug**：`_set_mixed_mode_atomic` 关闭路径只把两步成败
    记进 steps 却不检查，双工回 local 失败时照样返回 success=True（steps 里明明是
    ('duplex', False)），与它自己的 docstring「任一步失败时回滚」矛盾。已修，
    并由 ⑩ 两条判据 + M17/M18 两把刀钉死：谁再把修复拆掉，刀架立刻红。

运行：  python tools/_qc_p1_incremental_mutation.py
"""
import hashlib
import os
import re
import subprocess
import sys

PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PY = sys.executable
GATE = [os.path.join("tools", "test_p1_incremental.py")]
P = "src/ui/webapp.py"

# (编号, 文件, 原锚点, 变异后, 必须红在哪条判据, 为什么, 锚点是否允许多处)
MUTS = [
    ('U01', 'src/ui/webapp.py',
     '        src = d.get("provider") if isinstance(d.get("provider"), dict) else d',
     '        src = d',
     '⑧ 嵌套入参也认',
     'U01 嵌套 provider 入参失效（只认平铺）', False),
    ('U02', 'src/ui/webapp.py',
     '        src = d.get("provider") if isinstance(d.get("provider"), dict) else d',
     '        src = d.get("provider")',
     '⑧ 平铺入参也认',
     'U02 平铺入参失效（只认嵌套）', False),
    ('U03', 'src/ui/webapp.py',
     '        pid = str(src.get("id") or "").strip()\n        if not pid:\n            return {"success": False, "error": "缺少 id"}',
     '        pid = str(src.get("id") or "").strip()',
     '⑧ 缺 id ⇒ 失败且表不变',
     'U03 缺 id 不拦', False),
    ('U04', 'src/ui/webapp.py',
     '            target = next((x for x in providers if isinstance(x, dict) and x.get("id") == pid), None)',
     '            target = None',
     '① 目标已改（name/model）',
     'U04 就地定位失效（每次都当新增）', False),
    ('U05', 'src/ui/webapp.py',
     '            def _restore():\n                if is_new:\n                    if target in providers:\n                        providers.remove(target)\n                elif _target_backup is not None:\n                    target.clear()\n                    target.update(_target_backup)',
     '            def _restore():\n                pass',
     '④ 失败后原有项未被破坏',
     'U05 失败整体不还原', False),
    ('U06', 'src/ui/webapp.py',
     '                elif _target_backup is not None:\n                    target.clear()\n                    target.update(_target_backup)',
     '                elif False:\n                    target.clear()\n                    target.update(_target_backup)',
     '④ 失败后原有项未被破坏',
     'U06 失败只还原新增、不还原已有', False),
    ('U07', 'src/ui/webapp.py',
     '            for k in _str_fields:\n                if k in src:\n                    v = str(src.get(k) or "").strip()',
     '            for k in _str_fields:\n                if True:\n                    v = str(src.get(k) or "").strip()',
     '① upsert 返回 success',
     'U07 增量语义失效（四个字段无条件覆盖）', False),
    ('U08', 'src/ui/webapp.py',
     '                    if k == "provider":\n                        v = v.lower()\n                        if v not in ("openai", "openai-compatible", "ollama"):\n                            v = "openai-compatible"',
     '                    if k == "provider":\n                        v = v.lower()',
     '⑧ 非法 provider 归一 openai-compatible',
     'U08 provider 白名单归一拆掉', False),
    ('U09', 'src/ui/webapp.py',
     '                        v = v.rstrip("/")',
     '                        pass',
     '⑧ base_url 去尾斜杠',
     'U09 base_url 不去尾斜杠', False),
    ('U10', 'src/ui/webapp.py',
     '                        v = v or pid',
     '                        pass',
     '⑧ name 空回落到 id',
     'U10 name 空不回落到 id', False),
    ('U11', 'src/ui/webapp.py',
     '                    target["timeout_s"] = 30',
     '                    target["timeout_s"] = 0',
     '⑧ timeout_s 坏值回落 30',
     'U11 timeout_s 坏值不回落 30', False),
    ('U12', 'src/ui/webapp.py',
     '            if "api_key" in src:\n                target["api_key"] = str(src.get("api_key") or "").strip()',
     '            if True:\n                target["api_key"] = str(src.get("api_key") or "").strip()',
     '② 不传 api_key ⇒ 保留（不是漏传即清）',
     'U12 api_key 漏传也清空', False),
    ('U13', 'src/ui/webapp.py',
     '            if "vision" in src:\n                target["vision"] = bool(src.get("vision"))',
     '            if False:\n                target["vision"] = bool(src.get("vision"))',
     '⑧ vision 显式给出才写',
     'U13 vision 字段丢失', False),
    ('U14', 'src/ui/webapp.py',
     '            if isinstance(src.get("caps"), dict):\n                target["caps"] = {',
     '            if False:\n                target["caps"] = {',
     '⑧ caps 三键齐全',
     'U14 caps 整块不写', False),
    ('U15', 'src/ui/webapp.py',
     '                    "inputs": src["caps"].get("inputs") or [],',
     '                    "inputs": src["caps"].get("inputs"),',
     '⑧ caps 缺键补空列表',
     'U15 caps 缺键不补空列表', False),
    ('U16', 'src/ui/webapp.py',
     '            if not str(target.get("model") or "").strip():',
     '            if False:',
     '④ 缺 model ⇒ 失败',
     'U16 缺 model 校验拆掉', False),
    ('U17', 'src/ui/webapp.py',
     '            if target.get("provider") != "ollama" and not str(target.get("base_url") or "").strip():',
     '            if False:',
     '⑧ 缺 base_url 的新端口 ⇒ 失败',
     'U17 缺 base_url 校验拆掉', False),
    ('U18', 'src/ui/webapp.py',
     '            tc["providers"] = providers\n            # ---- mode / manual_provider（可选；只给才动）----',
     '            pass\n            # ---- mode / manual_provider（可选；只给才动）----',
     '⑧ 空配置新增真的落表',
     'U18 providers 回写丢失（空配置新增）', False),
    ('U19', 'src/ui/webapp.py',
     '                tc["mode"] = _m if _m in ("auto", "manual") else "auto"',
     '                tc["mode"] = _m',
     '⑧ 非法 mode 归一 auto',
     'U19 非法 mode 不归 auto', False),
    ('U20', 'src/ui/webapp.py',
     '                if _mp and _mp not in _ids:',
     '                if False:',
     '⑥ manual 指向不存在 ⇒ 失败',
     'U20 manual 指向不存在仍放行', False),
    ('U21', 'src/ui/webapp.py',
     '            if _looks_like_bailian_key(_pk):\n                _unify_bailian_key(raw, _pk, source="upsert_text_chat_provider")',
     '            if False:\n                _unify_bailian_key(raw, _pk, source="upsert_text_chat_provider")',
     '⑧ 百炼 Key 触发 Key 归一',
     'U21 百炼 Key 不归一', False),
    ('U22', 'src/ui/webapp.py',
     '                _cmw(raw, changed="text_chat.providers", source="upsert_text_chat_provider")',
     '                pass',
     '⑧ upsert 写入收口被调用',
     'U22 upsert 写入收口不调', False),
    ('U23', 'src/ui/webapp.py',
     '            self._save_config()\n            from ..config import _to_ns\n            self._cfg.ns = _to_ns(raw)\n            applied = self._apply_llm_live()\n            return {"success": True, "applied_live": applied, "id": pid, "created": is_new,',
     '            pass\n            from ..config import _to_ns\n            self._cfg.ns = _to_ns(raw)\n            applied = self._apply_llm_live()\n            return {"success": True, "applied_live": applied, "id": pid, "created": is_new,',
     '⑥ 成功时只写一次盘',
     'U23 upsert 不写盘（对照）', False),
    ('U24', 'src/ui/webapp.py',
     '            self._cfg.ns = _to_ns(raw)\n            applied = self._apply_llm_live()\n            return {"success": True, "applied_live": applied, "id": pid, "created": is_new,',
     '            pass\n            applied = self._apply_llm_live()\n            return {"success": True, "applied_live": applied, "id": pid, "created": is_new,',
     '⑧ upsert 命名空间重建',
     'U24 upsert 命名空间不重建', False),
    ('U25', 'src/ui/webapp.py',
     '            applied = self._apply_llm_live()\n            return {"success": True, "applied_live": applied, "id": pid, "created": is_new,',
     '            applied = None\n            return {"success": True, "applied_live": applied, "id": pid, "created": is_new,',
     '⑧ upsert 热切换被调用',
     'U25 upsert 不热切换路由', False),
    ('U26', 'src/ui/webapp.py',
     '"id": pid, "created": is_new,',
     '"id": pid, "created": False,',
     '③ 新增返回 created=True',
     'U26 created 恒 False（对照）', False),
    ('U27', 'src/ui/webapp.py',
     '            return {"success": True, "applied_live": applied, "id": pid, "created": is_new,\n                    "mode": tc.get("mode", "auto"), "manual_provider": tc.get("manual_provider", "")}',
     '            return {"success": True, "applied_live": applied, "id": pid, "created": is_new}',
     '⑧ 返回值带 mode 与 manual_provider',
     'U27 返回值不带 mode/manual_provider', False),
    ('D01', 'src/ui/webapp.py',
     '        pid = (provider_id or (data or {}).get("id") or "").strip()',
     '        pid = (provider_id or "").strip()',
     "⑨ data={'id':...} 入口也认",
     "D01 data={'id':...} 入口失效", False),
    ('D02', 'src/ui/webapp.py',
     '            if len(kept) == before:',
     '            if False:',
     '⑤ 删不存在 ⇒ 幂等成功 removed=False',
     'D02 幂等短路拆掉（对照）', False),
    ('D03', 'src/ui/webapp.py',
     '            if tc.get("manual_provider") == pid:',
     '            if False:',
     '⑤ manual_provider 自动回落到剩下的',
     'D03 删当前 manual 不回落（对照）', False),
    ('D04', 'src/ui/webapp.py',
     '            if tc.get("manual_provider") == pid:',
     '            if True:',
     '⑨ 删的不是当前 manual ⇒ 不回落',
     'D04 删任意端口都回落（过度回落）', False),
    ('D05', 'src/ui/webapp.py',
     '                tc["manual_provider"] = kept[0]["id"] if kept else ""',
     '                tc["manual_provider"] = ""',
     '⑤ manual_provider 自动回落到剩下的',
     'D05 回落到空而非剩余首项（对照）', False),
    ('D06', 'src/ui/webapp.py',
     '                tc["manual_provider"] = kept[0]["id"] if kept else ""',
     '                tc["manual_provider"] = kept[-1]["id"] if kept else ""',
     '⑨ 三端口时回落到剩余首项',
     'D06 回落到最后一项（三端口才分得出）', False),
    ('D07', 'src/ui/webapp.py',
     '                if not kept:\n                    tc["mode"] = "auto"',
     '                if False:\n                    tc["mode"] = "auto"',
     '⑤ 删空 ⇒ manual 清空 + mode 回 auto',
     'D07 删空不回 auto（对照）', False),
    ('D08', 'src/ui/webapp.py',
     '                return {"success": True, "removed": False, "id": pid,\n                        "manual_provider": tc.get("manual_provider", "")}',
     '                self._save_config()\n                return {"success": True, "removed": False, "id": pid,\n                        "manual_provider": tc.get("manual_provider", "")}',
     '⑨ 幂等删不写盘',
     'D08 幂等删也写盘', False),
    ('D09', 'src/ui/webapp.py',
     '                _cmw(raw, changed="text_chat.providers", source="delete_text_chat_provider")',
     '                pass',
     '⑨ delete 写入收口被调用',
     'D09 delete 写入收口不调', False),
    ('D10', 'src/ui/webapp.py',
     '            self._save_config()\n            from ..config import _to_ns\n            self._cfg.ns = _to_ns(raw)\n            applied = self._apply_llm_live()\n            return {"success": True, "removed": True,',
     '            pass\n            from ..config import _to_ns\n            self._cfg.ns = _to_ns(raw)\n            applied = self._apply_llm_live()\n            return {"success": True, "removed": True,',
     '⑨ 删除成功写一次盘',
     'D10 delete 不写盘', False),
    ('D11', 'src/ui/webapp.py',
     '            self._cfg.ns = _to_ns(raw)\n            applied = self._apply_llm_live()\n            return {"success": True, "removed": True,',
     '            pass\n            applied = self._apply_llm_live()\n            return {"success": True, "removed": True,',
     '⑨ delete 命名空间重建',
     'D11 delete 命名空间不重建', False),
    ('D12', 'src/ui/webapp.py',
     '            applied = self._apply_llm_live()\n            return {"success": True, "removed": True,',
     '            applied = None\n            return {"success": True, "removed": True,',
     '⑨ delete 热切换被调用',
     'D12 delete 不热切换路由', False),
    ('M01', 'src/ui/webapp.py',
     '        if d.get("atomic"):\n            return self._set_mixed_mode_atomic(enabled, d)',
     '        if False:\n            return self._set_mixed_mode_atomic(enabled, d)',
     '⑦ 标记 atomic',
     'M01 atomic 分支拆掉（对照）', False),
    ('M02', 'src/ui/webapp.py',
     '        if d.get("atomic"):\n            return self._set_mixed_mode_atomic(enabled, d)',
     '        if True:\n            return self._set_mixed_mode_atomic(enabled, d)',
     '⑦ 老调用（无 atomic）仍走原路径',
     'M02 老调用误走原子路径（对照）', False),
    ('M03', 'src/ui/webapp.py',
     '        d = data or {}\n        enabled = bool(d.get("enabled", True))\n        if d.get("atomic"):',
     '        d = data or {}\n        enabled = True\n        if d.get("atomic"):',
     '⑦ 关闭时双工回 local',
     'M03 enabled 恒 True（对照）', False),
    ('M04', 'src/ui/webapp.py',
     '            _snapshot = _copy.deepcopy(raw)',
     '            _snapshot = None',
     '⑦ 中途失败 ⇒ 标记已回滚',
     'M04 快照不取（对照）', False),
    ('M05', 'src/ui/webapp.py',
     '                if not (_s1 and _s1.get("success")):\n                    raise RuntimeError((_s1 or {}).get("error") or "双工引擎切换失败")',
     '                if False:\n                    raise RuntimeError((_s1 or {}).get("error") or "双工引擎切换失败")',
     '⑦ 中途失败 ⇒ success=False',
     'M05 step1 失败不抛（对照）', False),
    ('M06', 'src/ui/webapp.py',
     '                if not (_s2 and _s2.get("success")):\n                    raise RuntimeError((_s2 or {}).get("error") or "混合开关失败")',
     '                if False:\n                    raise RuntimeError((_s2 or {}).get("error") or "混合开关失败")',
     '⑩ step2 失败 ⇒ success=False 且回滚',
     'M06 step2 失败不抛', False),
    ('M07', 'src/ui/webapp.py',
     '                _s1 = self.save_duplex_settings({"engine": "cloud", "decouple_screen_voice": True})',
     '                _s1 = self.save_duplex_settings({"engine": "cloud"})',
     '⑦ 双工切 cloud + 解耦看屏',
     'M07 开启不解耦看屏（对照）', False),
    ('M08', 'src/ui/webapp.py',
     '                    self.set_vision_enabled(True)',
     '                    pass',
     '⑩ 视觉管线真的被调用',
     'M08 视觉管线根本不调', False),
    ('M09', 'src/ui/webapp.py',
     '                except Exception as _e:  # noqa: BLE001\n                    steps.append(("vision", False))\n                    logger.warning("混合模式：视觉管线开启失败（不阻断）: %s", _e)',
     '                except Exception as _e:  # noqa: BLE001\n                    steps.append(("vision", False))\n                    raise',
     '⑩ 视觉失败不阻断且 steps 标记 False',
     'M09 视觉管线失败改成阻断', False),
    ('M10', 'src/ui/webapp.py',
     '                _s1 = self._proactive.set_mixed_mode(False)',
     '                _s1 = self._proactive.set_mixed_mode(True)',
     '⑩ 关闭时混合开关真的被关',
     'M10 关闭路径混合开关没关（反而开）', False),
    ('M11', 'src/ui/webapp.py',
     '                _s2 = self.save_duplex_settings({"engine": "local"})',
     '                _s2 = self.save_duplex_settings({"engine": "cloud"})',
     '⑦ 关闭时双工回 local',
     'M11 关闭路径双工不回 local（对照）', False),
    ('M12', 'src/ui/webapp.py',
     '            self._save_config()\n            self._cfg.ns = _to_ns(raw)\n            logger.info("混合模式（原子）已切换: enabled=%s steps=%s", enabled, steps)',
     '            self._save_config()\n            self._save_config()\n            self._cfg.ns = _to_ns(raw)\n            logger.info("混合模式（原子）已切换: enabled=%s steps=%s", enabled, steps)',
     '⑦ 只写一次盘（关键：不是 3 次）',
     'M12 一次写盘变两次（对照）', False),
    ('M13', 'src/ui/webapp.py',
     '            self._cfg.ns = _to_ns(raw)\n            logger.info("混合模式（原子）已切换: enabled=%s steps=%s", enabled, steps)',
     '            pass\n            logger.info("混合模式（原子）已切换: enabled=%s steps=%s", enabled, steps)',
     '⑩ 原子成功路径命名空间重建',
     'M13 原子路径命名空间不重建', False),
    ('M14', 'src/ui/webapp.py',
     '                    self._cfg.raw.clear()\n                    self._cfg.raw.update(_snapshot)\n                    self._cfg.ns = _to_ns(self._cfg.raw)',
     '                    pass',
     '⑩ 回滚真生效（step1 改过内存态）',
     'M14 回滚形同虚设', False),
    ('M15', 'src/ui/webapp.py',
     '"steps": steps, "rolled_back": _snapshot is not None}',
     '"steps": steps, "rolled_back": False}',
     '⑦ 中途失败 ⇒ 标记已回滚',
     'M15 失败不标记 rolled_back（对照）', False),
    ('M16', 'src/ui/webapp.py',
     '            return {"success": True, "enabled": enabled, "atomic": True, "steps": steps}',
     '            return {"success": True, "enabled": enabled, "atomic": True, "steps": []}',
     '⑦ steps 三段齐全',
     'M16 成功不回 steps（对照）', False),
    ('S01', 'src/ui/webapp.py',
     '                if pid in seen_ids:\n                    return {"success": False, "error": f"厂商 id 重复: {pid}"}',
     '                if False:\n                    return {"success": False, "error": f"厂商 id 重复: {pid}"}',
     '⑪ 老接口重复 id ⇒ 失败',
     'S01 老接口重复 id 不拦', False),
    ('S02', 'src/ui/webapp.py',
     '                if provider not in ("openai", "openai-compatible", "ollama"):\n                    provider = "openai-compatible"',
     '                if False:\n                    provider = "openai-compatible"',
     '⑪ 老接口非法 provider 归一',
     'S02 老接口非法 provider 不归一', False),
    ('S03', 'src/ui/webapp.py',
     '                if not model:\n                    return {"success": False, "error": f"厂商 {name} 缺少 model"}',
     '                if False:\n                    return {"success": False, "error": f"厂商 {name} 缺少 model"}',
     '⑪ 老接口缺 model ⇒ 失败',
     'S03 老接口缺 model 不拦', False),
    ('S04', 'src/ui/webapp.py',
     '                    if not base_url:\n                        return {"success": False,\n                                "error": f"厂商 {name} 缺少 base_url（OpenAI 兼容接口地址）"}',
     '                    if False:\n                        return {"success": False,\n                                "error": f"厂商 {name} 缺少 base_url（OpenAI 兼容接口地址）"}',
     '⑪ 老接口缺 base_url ⇒ 失败',
     'S04 老接口缺 base_url 不拦', False),
    ('S05', 'src/ui/webapp.py',
     '                        item["api_key"] = (_old.get("api_key") or "") if _old else ""',
     '                        item["api_key"] = ""',
     '⑪ 老接口漏传 api_key ⇒ 保留原 Key',
     'S05 老接口漏传 api_key 反而清空', False),
    ('S06', 'src/ui/webapp.py',
     '            if mode not in ("auto", "manual"):\n                mode = "auto"',
     '            if False:\n                mode = "auto"',
     '⑪ 老接口非法 mode 归一 auto',
     'S06 老接口非法 mode 不归 auto', False),
    ('S07', 'src/ui/webapp.py',
     '                if mp not in seen_ids:\n                    return {"success": False, "error": f"默认厂商不存在: {mp}"}',
     '                if False:\n                    return {"success": False, "error": f"默认厂商不存在: {mp}"}',
     '⑪ 老接口 manual 不存在 ⇒ 失败',
     'S07 老接口 manual 不存在仍放行', False),
    ('S08', 'src/ui/webapp.py',
     '            self._save_config()\n            from ..config import _to_ns\n            self._cfg.ns = _to_ns(raw)\n            applied = self._apply_llm_live()\n            out_providers = []',
     '            pass\n            from ..config import _to_ns\n            self._cfg.ns = _to_ns(raw)\n            applied = self._apply_llm_live()\n            out_providers = []',
     '⑪ 老接口整表替换写一次盘',
     'S08 老接口不写盘', False),
    ('M17', 'src/ui/webapp.py',
     '                _s2 = self.save_duplex_settings({"engine": "local"})\n                steps.append(("duplex", bool(_s2 and _s2.get("success"))))\n                if not (_s2 and _s2.get("success")):\n                    raise RuntimeError((_s2 or {}).get("error") or "双工引擎切换失败")',
     '                _s2 = self.save_duplex_settings({"engine": "local"})\n                steps.append(("duplex", bool(_s2 and _s2.get("success"))))\n                if False:\n                    raise RuntimeError((_s2 or {}).get("error") or "双工引擎切换失败")',
     '⑩ 关闭时双工失败 ⇒ success=False',
     '把第 46 轮修好的关闭路径双工失败检查拆掉 —— 应红在 ⑩ 关闭时双工失败', False),
    ('M18', 'src/ui/webapp.py',
     '                _s1 = self._proactive.set_mixed_mode(False)\n                steps.append(("mixed", bool(_s1 and _s1.get("success"))))\n                if not (_s1 and _s1.get("success")):\n                    raise RuntimeError((_s1 or {}).get("error") or "混合开关失败")',
     '                _s1 = self._proactive.set_mixed_mode(False)\n                steps.append(("mixed", bool(_s1 and _s1.get("success"))))\n                if False:\n                    raise RuntimeError((_s1 or {}).get("error") or "混合开关失败")',
     '⑩ 关闭时混合开关失败 ⇒ success=False',
     '把第 46 轮修好的关闭路径混合开关失败检查拆掉 —— 应红在 ⑩ 关闭时混合开关失败', False),
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
    m = re.search(r"总计:\s*PASS=\d+\s+FAIL=\d+", _out(r))
    return m.group(0) if m else "(无汇总行)"


def _judged(r):
    """非零退出 + 本门自己打了汇总行（而不是半路崩掉）。"""
    return r.returncode != 0 and bool(
        re.search(r"总计:\s*PASS=\d+\s+FAIL=\d+", _out(r)))


def _hit(r, name):
    return any(ln.strip().startswith("[FAIL]") and name in ln
               for ln in _out(r).splitlines())


def main():
    base = _run()
    if not (base.returncode == 0
            and re.search(r"总计:\s*PASS=\d+\s+FAIL=0", _out(base))):
        # 带 FAIL 前缀：空变异体检要能区分「套件自己报了失败」和「套件崩了」。
        print("FAIL 基线不是绿的 —— 后面的红不能证明任何事：")
        print(_out(base)[-1500:])
        return 2
    print("基线：绿 | %s" % _summary(base))

    rows = []
    for key, rel, old, new, expect, why, all_hits in MUTS:
        p = os.path.join(PROJ, *rel.split("/"))
        orig = open(p, encoding="utf-8").read()
        pairs = list(old) if isinstance(old, tuple) else [(old, new)]
        counts = [orig.count(o) for o, _ in pairs]
        if any(c == 0 for c in counts) or (not all_hits and any(c != 1 for c in counts)):
            print("  %-6s 锚点命中 %s（应恰好 1 次），锚点已腐烂" % (key, counts))
            rows.append((key, "ANCHOR", why))
            continue
        h0 = _sha(p)
        try:
            text = orig
            for _o, _n in pairs:
                text = text.replace(_o, _n)
            open(p, "w", encoding="utf-8", newline="").write(text)
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
                              if ln.strip().startswith("[FAIL]")][:3]))
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
