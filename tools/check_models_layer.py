"""P2 投影层自检：验证 src/models 能正确读 config，且**确实没有副作用**。

跑法：
    python tools/check_models_layer.py

检查三件事：
  1. 能 import、能读真 config、不抛异常
  2. **只读性**：调用前后 config 的深拷贝完全相等（证明没有偷偷改 raw）
  3. 视图自洽：13 个调用点齐全、summary 数值与 sites 对得上、
     VoiceView 的 active 与 config.get_active_voice_config 口径一致
"""
from __future__ import annotations

import copy
import json
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import yaml  # noqa: E402

FAILS: list[str] = []
OKS: list[str] = []


def ok(m: str) -> None:
    OKS.append(m)


def fail(m: str) -> None:
    FAILS.append(m)


def _pick_config() -> tuple:
    """本机的实配 `config.yaml`，没有就用出厂模板。

    【2026-11-09 加·「缺环境」不是「判据失败」】`config.yaml` 在 `.gitignore` 里
    （它是实配，含 api_key），所以**任何新克隆的树上都没有它** —— 这道门于是
    在每台刚装好的机器上第一行就 `FileNotFoundError`，连一条判据都没跑到。
    本门验的是「`src/models` 的视图构建对一份合法配置做了什么」，与 api_key 无关，
    出厂模板 `config.release.yaml` 就是最该被验的那份配置（它才是发给用户的）。
    两份都没有才报 77（未验证）。
    """
    for name in ("config.yaml", "config.release.yaml"):
        p = os.path.join(_ROOT, name)
        if os.path.isfile(p):
            return p, name
    return "", ""


def main() -> int:
    conf, conf_name = _pick_config()
    if not conf:
        print("[SKIP] 既没有 config.yaml 也没有 config.release.yaml —— "
              "视图构建没有夹具可跑（不是通过）")
        return 77
    if conf_name != "config.yaml":
        print("（本机没有实配 config.yaml，用出厂模板 %s 当夹具）" % conf_name)
    with open(conf, "r", encoding="utf-8") as f:
        raw = yaml.safe_load(f)

    # ---------- 1. import ----------
    try:
        from src.models import (build_model_view, build_voice_view, summarize,
                                diagnose, TTS_ENGINES, LLM_PROVIDERS,
                                field_contract_for, engine_of_provider)
        ok("import src.models 成功")
    except Exception as e:  # noqa: BLE001
        fail(f"import 失败: {type(e).__name__}: {e}")
        print("\n".join(FAILS))
        return 1

    # ---------- 2. 只读性：快照前后深比较 ----------
    before = copy.deepcopy(raw)
    try:
        mv = build_model_view(raw)
        vv = build_voice_view(raw)
        sm = summarize(raw)
        dg = diagnose(raw)
        ok("build_model_view / build_voice_view / summarize / diagnose 均未抛异常")
    except Exception as e:  # noqa: BLE001
        import traceback
        fail(f"构建视图抛异常: {type(e).__name__}: {e}\n{traceback.format_exc()}")
        print("\n".join(FAILS))
        return 1

    if isinstance(dg, list):
        ok(f"diagnose 返回列表（{len(dg)} 条）")
    else:
        fail(f"diagnose 应返回 list，实为 {type(dg).__name__}")

    if raw == before:
        ok("★ 只读性验证通过：调用前后 config 完全一致（未改任何键）")
    else:
        # 找出被改动的路径，便于定位
        diff = []
        def _cmp(a, b, p=""):
            if type(a) is not type(b):
                diff.append(p); return
            if isinstance(a, dict):
                for k in set(a) | set(b):
                    if k not in a or k not in b:
                        diff.append(f"{p}.{k}")
                    else:
                        _cmp(a[k], b[k], f"{p}.{k}")
            elif isinstance(a, list):
                if len(a) != len(b):
                    diff.append(f"{p}[len {len(a)}→{len(b)}]")
                for i, (x, y) in enumerate(zip(a, b)):
                    _cmp(x, y, f"{p}[{i}]")
            elif a != b:
                diff.append(p)
        _cmp(before, raw)
        fail(f"✘ 只读性失败！config 被改动: {diff[:10]}")

    # ---------- 3. 视图自洽 ----------
    sites = mv.get("sites") or []
    if len(sites) == 13:
        ok("13 个调用点齐全")
    else:
        fail(f"调用点数应为 13，实为 {len(sites)}")

    ids = [s["id"] for s in sites]
    dup = [i for i in set(ids) if ids.count(i) > 1]
    if dup:
        fail(f"调用点 id 重复: {dup}")
    else:
        ok("调用点 id 无重复")

    s = mv["summary"]
    if s["total"] == len(sites) and s["ready"] + s["not_ready"] == s["total"] \
            and s["in_slot"] + s["blind"] == s["total"]:
        ok(f"summary 自洽: total={s['total']} ready={s['ready']} "
           f"in_slot={s['in_slot']} blind={s['blind']}")
    else:
        fail(f"summary 不自洽: {s} vs len(sites)={len(sites)}")

    # 与 P0 基线对齐：in_slot 应为 4（P0 实测）
    if s["in_slot"] == 4:
        ok("★ 与 P0 基线一致：in_slot=4（9 个盲区）")
    else:
        fail(f"in_slot 应=4（P0 实测），实为 {s['in_slot']}")

    if s["blind"] == 9:
        ok("★ 与 P0 基线一致：blind=9")
    else:
        fail(f"blind 应=9（P0 实测），实为 {s['blind']}")

    # ---------- 4. VoiceView 与 get_active_voice_config 口径一致 ----------
    try:
        from src.config import Config
        cfg = Config(copy.deepcopy(before), path=conf)
        ns = cfg.get_active_voice_config()
        # 比对两个层面：
        #   engine_raw 必须与 Config 返回的原始 engine **逐字一致**（证明选中了同一个档/内联）
        #   engine     是本层归一后的规范值（供 create_backend 用），允许与原始值不同
        exp_raw = str(getattr(ns, "engine", "") or "") if ns else ""
        got_raw = vv["active"]["engine_raw"]
        if exp_raw == got_raw:
            ok(f"★ VoiceView 选中口径与 get_active_voice_config 一致: engine_raw={got_raw!r}")
        else:
            fail(f"VoiceView engine_raw 不一致: 期望 {exp_raw!r}，实为 {got_raw!r}")

        # 归一后的 engine 必须落在白名单内（或明确已知为未知引擎）
        from src.models import field_contract_for as _fc
        norm = _fc(got_raw)
        if norm.get("engine") == vv["active"]["engine"]:
            ok(f"归一 engine 自洽: {vv['active']['engine_raw']!r} → {vv['active']['engine']!r}")
        else:
            fail(f"归一 engine 不自洽: {norm.get('engine')!r} vs {vv['active']['engine']!r}")

        # 档数一致
        exp_n = len(cfg.list_voice_profiles())
        if exp_n == vv["profile_count"]:
            ok(f"音色档数一致: {exp_n}")
        else:
            fail(f"音色档数不一致: Config={exp_n} VoiceView={vv['profile_count']}")
    except Exception as e:  # noqa: BLE001
        fail(f"与 Config 对齐校验失败: {type(e).__name__}: {e}")

    # ---------- 5. 元数据完整性 ----------
    # 【2026-10-21 修·数量断言不该是判据】原来硬编码 `len(TTS_ENGINES) == 11`，
    # 2026-10-16 下架 edge-tts 后表变成 10 个就报 FAIL —— 这不是缺陷，是判据过时。
    # 数量本身不表达"为什么是 11"，下次增删引擎还得再改一次。
    # 真正该守的是下面那条「与 webapp 白名单逐字比对」；这里只留一个"别被误清空"的下限。
    if len(TTS_ENGINES) >= 5:
        ok(f"TTS 引擎表非空（{len(TTS_ENGINES)} 个，具体集合由 webapp 白名单比对守）")
    else:
        fail(f"TTS 引擎表只剩 {len(TTS_ENGINES)} 个，疑似被误清空")

    from src.ui import webapp as _wa  # noqa: E402
    # 与 webapp 的白名单逐字比对（防两处漂移）
    try:
        # 找 CharacterAPI 类上的白名单集合
        wanted = None
        for name in dir(_wa):
            obj = getattr(_wa, name)
            v = getattr(obj, "_VOICE_PROFILE_ENGINES", None)
            if isinstance(v, set):
                wanted = v
                break
        if wanted is None:
            fail("未能从 webapp 读到 _VOICE_PROFILE_ENGINES（跳过比对）")
        else:
            mine = set(TTS_ENGINES)
            if mine == wanted:
                ok("★ TTS 引擎表与 webapp._VOICE_PROFILE_ENGINES 逐字一致")
            else:
                fail(f"引擎表漂移: 仅我有={mine - wanted} 仅 webapp 有={wanted - mine}")
    except Exception as e:  # noqa: BLE001
        fail(f"引擎表比对异常: {type(e).__name__}: {e}")

    if engine_of_provider("bailian") == "dashscope_cosy":
        ok("engine_of_provider 映射正确（bailian→dashscope_cosy）")
    else:
        fail("engine_of_provider 映射错误")

    if not field_contract_for("不存在的引擎").get("known"):
        ok("未知引擎返回 known=False（不抛异常）")
    else:
        fail("未知引擎应返回 known=False")

    # ---------- 汇总输出 ----------
    print("=" * 62)
    print(f"P2 投影层自检：{len(OKS)} 项通过，{len(FAILS)} 项失败")
    print("=" * 62)
    for m in OKS:
        print("  [OK]  " + m)
    for m in FAILS:
        print("  [FAIL] " + m)
    print("=" * 62)
    print("调用点摘要:")
    for st in sites:
        flag = "槽位" if st["covered_by_slot"] else "盲区"
        mark = "✔" if st["ready"] else "✘"
        print(f"  {mark} [{flag}] {st['id']:<26} model={st['model'][:34]!r}")
    print("=" * 62)
    print(f"投影层体检 diagnose 结果（{len(dg)} 条）:")
    if not dg:
        print("  （无告警）")
    for w in dg:
        print(f"  [{w.get('level')}] {w.get('where')}")
        print(f"        {w.get('msg')}")
    print("=" * 62)
    return 1 if FAILS else 0


if __name__ == "__main__":
    raise SystemExit(main())
