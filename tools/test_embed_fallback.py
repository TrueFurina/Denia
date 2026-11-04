# -*- coding: utf-8 -*-
"""记忆语义检索「本地优先 + 云端兜底」的判级测试（全离线）。

守什么
------
1. `provider=auto`：本地不可用 → 转云端，且真的能返回向量；
2. `provider=ollama`：本地不可用 → 返回 None（**不许偷偷上云**）；
3. `provider=off`：返回 None（显式关闭就是关闭）；
4. 云端探测失败 → 返回 None（降级，不抛异常、不留一个半残的函数）；
5. 单次调用失败 → 返回 None（只丢这一次的向量，不影响后续）；
6. `base_url` 尾部斜杠归一（不能拼出 `//embeddings`）；
7. 没配 `base_url` / `api_key` → 一次请求都不发。

为什么全离线
------------
闸门里不能打真网络：会偶发假红，而假红比没有闸门更糟（本项目已踩过"狼来了"）。
所以替换掉 `embed_client._post_json` 这一层传输，逻辑全部照跑。

跑法：.venv/Scripts/python.exe -u tools/test_embed_fallback.py
"""
from __future__ import annotations

import os
import sys
from types import SimpleNamespace

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import src.llm.embed_client as EC  # noqa: E402
import src.llm.ollama_client as OC  # noqa: E402

OK = FAIL = 0


def chk(name: str, cond: bool, extra: str = "") -> None:
    global OK, FAIL
    if cond:
        OK += 1
        print("  PASS " + name + ((" :: " + extra) if extra else ""))
    else:
        FAIL += 1
        print("  FAIL " + name + ((" :: " + extra) if extra else ""))


def fake_cfg(provider: str = "auto", **embed) -> SimpleNamespace:
    block = {"provider": provider, "model": "text-embedding-v3",
             "base_url": "https://example.invalid/v1", "api_key": "sk-test"}
    block.update(embed)
    return SimpleNamespace(
        raw={"memory": {"embed": block}},
        ns=SimpleNamespace(ollama=SimpleNamespace(host="http://127.0.0.1:1")),
    )


CALLS: list = []


def post_ok(url, payload, api_key, timeout):
    CALLS.append(url)
    return {"data": [{"embedding": [0.1, 0.2, 0.3]}]}


def post_fail(url, payload, api_key, timeout):
    CALLS.append(url)
    raise RuntimeError("boom")


def post_ok_then_fail(url, payload, api_key, timeout):
    CALLS.append(url)
    if len(CALLS) == 1:          # 第一次是构造时的探测
        return {"data": [{"embedding": [0.1]}]}
    raise RuntimeError("later boom")


def post_empty(url, payload, api_key, timeout):
    """探测返回 200 但结构里没有向量（换模型/网关抽风时真会这样）。"""
    CALLS.append(url)
    return {"data": []}


def main() -> int:
    # 本地永远不可用：把 ollama 的工厂换成返回 None（不改产品代码）。
    OC.make_embed_fn = lambda *a, **k: None  # type: ignore[assignment]

    print("1) provider=auto：本地不可用 → 转云端并返回向量")
    EC._post_json = post_ok  # type: ignore[assignment]
    CALLS.clear()
    fn = EC.make_embed_fn_auto(fake_cfg("auto"))
    chk("拿到 embed_fn", fn is not None)
    vec = fn("我上次说我最怕什么") if fn else None
    chk("返回向量", vec == [0.1, 0.2, 0.3], str(vec))
    chk("确实打了 /embeddings", any(u.endswith("/v1/embeddings") for u in CALLS), str(CALLS[:1]))

    print("\n2) provider=ollama：本地不可用 → None（不许偷偷上云）")
    CALLS.clear()
    fn = EC.make_embed_fn_auto(fake_cfg("ollama"))
    chk("返回 None", fn is None)
    chk("一次云端请求都没发", CALLS == [], str(CALLS))

    print("\n3) provider=off：显式关闭")
    CALLS.clear()
    chk("返回 None", EC.make_embed_fn_auto(fake_cfg("off")) is None)
    chk("没发请求", CALLS == [])

    print("\n4) 云端探测失败 → None（降级，不抛）")
    EC._post_json = post_fail  # type: ignore[assignment]
    try:
        fn = EC.make_cloud_embed_fn("https://example.invalid/v1", "sk-test")
        chk("返回 None 而不是抛异常", fn is None)
    except Exception as e:  # noqa: BLE001
        chk("返回 None 而不是抛异常", False, type(e).__name__)

    print("\n4b) 探测返回空向量 → None（不交出半残函数）")
    # 这条是给变异刀 B 留的：探测"没抛异常但没给向量"与"抛异常"是两条路，
    # 只测后者时，把 `if not probe: return None` 改成 `if False:` 也照样绿。
    EC._post_json = post_empty  # type: ignore[assignment]
    chk("返回 None", EC.make_cloud_embed_fn("https://example.invalid/v1", "sk-test") is None)

    print("\n5) 单次调用失败 → None（只丢这一次）")
    EC._post_json = post_ok_then_fail  # type: ignore[assignment]
    CALLS.clear()
    fn = EC.make_cloud_embed_fn("https://example.invalid/v1", "sk-test")
    chk("探测成功拿到函数", fn is not None)
    if fn:
        try:
            chk("单次失败返回 None", fn("x") is None)
        except Exception as e:  # noqa: BLE001
            chk("单次失败返回 None", False, type(e).__name__)

    print("\n6) base_url 尾部斜杠归一")
    EC._post_json = post_ok  # type: ignore[assignment]
    CALLS.clear()
    EC.make_cloud_embed_fn("https://example.invalid/v1/", "sk-test")
    chk("没有 //embeddings", all("//embeddings" not in u for u in CALLS), str(CALLS[:1]))
    chk("仍以 /v1/embeddings 结尾", any(u.endswith("/v1/embeddings") for u in CALLS), str(CALLS[:1]))

    print("\n7) 缺 base_url / api_key → 不发请求")
    CALLS.clear()
    chk("缺 api_key → None", EC.make_cloud_embed_fn("https://example.invalid/v1", "") is None)
    chk("缺 base_url → None", EC.make_cloud_embed_fn("", "sk-test") is None)
    chk("一次请求都没发", CALLS == [], str(CALLS))

    print("\n8) 响应结构异常 → None（不崩）")
    chk("空 data", EC._extract_embedding({"data": []}) is None)
    chk("缺字段", EC._extract_embedding({"data": [{}]}) is None)
    chk("非 dict", EC._extract_embedding("oops") is None)

    print("\n结果: %d PASS / %d FAIL" % (OK, FAIL))
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
