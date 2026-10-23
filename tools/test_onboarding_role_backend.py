"""引导页「建角色」后端链路 · 真实自测（2026-10-12）。
不进闸门：真机探针——需真实百炼 Key 与声卡外放，人工按需跑（安全性设计见下）。

与 test_character_card_images.py（静态断言）互补：本测试用**真实 CharacterAPI 实例**
跑通引导页建角色的完整后端链路，验证「保存的角色能被激活、进软件即用」。

安全设计：把 config.yaml 复制到临时目录，CharacterAPI 指向**副本**——
保存/切换/落盘全部发生在副本上，绝不写真实 config.yaml。
真实网络调用：generate_character（用户已授权用其百炼 Key）+ test_voice（真实合成外放）。

锁定七点：
  1. get_character_templates 返回可用模板（含 sample 台词）
  2. list_voice_profiles 透出真实音色档
  3. save_character 新增 → 落盘副本
  4. save_character 同 id 再存 → 更新而非重复（引导页重存幂等）
  5. switch_character → active_character_id 生效（配置级切换链路）
  6. generate_character 真实 LLM 产出三段人设（联网）
  7. test_voice 真实合成任务 → 轮询到 done（联网，本机声卡外放）
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
import time

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

PASS = 0
FAIL = 0


def check(cond, label, extra=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  PASS {label}" + (f" :: {extra}" if extra else ""))
    else:
        FAIL += 1
        print(f"  FAIL {label}" + (f" :: {extra}" if extra else ""))


def main() -> int:
    from src.ui.webapp import CharacterAPI

    tmp = tempfile.mkdtemp(prefix="ob_role_")
    cfg_copy = os.path.join(tmp, "config.yaml")
    shutil.copy2(os.path.join(ROOT, "config.yaml"), cfg_copy)
    print(f"[沙盒] config 副本: {cfg_copy}")

    api = CharacterAPI(cfg_copy)
    from src.config import load_config  # noqa: PLC0415
    api._cfg = load_config(cfg_copy)

    print("\n[1] get_character_templates（预设人格模板）")
    r = api.get_character_templates()
    check(r.get("success") is True, "接口成功")
    tpls = r.get("templates") or []
    check(len(tpls) >= 2, f"模板数 >= 2（实际 {len(tpls)}）")
    t0 = tpls[0] if tpls else {}
    check(bool(t0.get("sample")) and bool(t0.get("character")), "模板含 sample 台词与三段人设")

    print("\n[2] list_voice_profiles（真实音色档）")
    r = api.list_voice_profiles()
    check(r.get("success") is True, "接口成功")
    profs = r.get("profiles") or []
    check(len(profs) >= 1, f"音色档 >= 1（实际 {len(profs)}）")
    vp = profs[0] if profs else {}
    check(bool(vp.get("id")) and bool(vp.get("engine")), "档位含 id/engine")

    print("\n[3] save_character 新增（引导页建角色 payload 口径）")
    char = {
        "id": "ob_test_huohuo",
        "name": "引导测试·藿藿",
        "character": "胆小怕鬼的狐人捉鬼师，被岁阳附身",
        "speaking_style": "说话细声细气，害怕时会念叨「Channels 要不要冷静一下」",
        "background": "仙舟罗浮的判官见习",
        "voice": {"engine": "dashscope_cosy"},
    }
    r = api.save_character(char)
    check(r.get("success") is True, "保存成功", str(r.get("error") or ""))
    raw = api._cfg.raw
    ids = [c.get("id") for c in raw.get("characters", [])]
    check("ob_test_huohuo" in ids, "副本 characters 已落盘")

    print("\n[4] save_character 同 id 重存（幂等）")
    char["name"] = "引导测试·藿藿改"
    r = api.save_character(char)
    check(r.get("success") is True, "重存成功")
    raw = api._cfg.raw
    same = [c for c in raw.get("characters", []) if c.get("id") == "ob_test_huohuo"]
    check(len(same) == 1 and same[0].get("name") == "引导测试·藿藿改", "更新而非新增（仅 1 条且名字已改）")

    print("\n[5] switch_character（无运行时 → 配置级切换）")
    r = api.switch_character("ob_test_huohuo")
    check(r.get("success") is True, "切换成功", str(r))
    check(raw.get("active_character_id") == "ob_test_huohuo", "active_character_id 已生效")

    print("\n[6] generate_character（真实 LLM · 用户百炼 Key）")
    r = api.generate_character({"name": "藿藿", "hint": "崩坏星穹铁道的狐人捉鬼师", "search": False})
    ok = r.get("success") is True
    check(ok, "生成成功", str(r.get("error") or r.get("need_key") or ""))
    if ok:
        for k in ("character", "speaking_style", "background"):
            v = str(r.get(k) or "")
            check(len(v) >= 20, f"三段之 {k} 充实（{len(v)} 字）")

    print("\n[7] test_voice（真实百炼合成 + 本机外放）")
    r = api.test_voice({"engine": "dashscope_cosy",
                        "api_key": (vp.get("api_key") or ""),
                        "voice_id": (vp.get("voice_id") or "")}, "你好呀，我是藿藿。")
    check(r.get("success") is True, "任务已受理", str(r.get("error") or ""))
    task_id = r.get("task_id") or ""
    if task_id:
        final = None
        for _ in range(40):
            st = api.get_voice_test_status(task_id)
            if st.get("done"):
                final = st
                break
            time.sleep(0.5)
        check(final is not None, "任务在 20s 内结束", str((final or {}).get("message") or ""))
        check(bool(final and final.get("success")), "合成+播放成功",
              str((final or {}).get("error") or (final or {}).get("message") or ""))

    print(f"\n结果: {PASS} PASS / {FAIL} FAIL")
    print(f"[沙盒] 副本留存于 {tmp}（真实 config.yaml 全程未动）")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
