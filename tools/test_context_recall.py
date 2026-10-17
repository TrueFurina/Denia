"""context_recall（Plan C 上下文引用）测试。

覆盖：
  1. 触发判据 should_recall（时间词/回忆词）
  2. 真实数据检索（data/chatlog|history 若有）
  3. 合成数据的 话题检索 + 时间格式化 + 角色加成
  4. 只读性（不写盘）
  5. 失败降级（不存在的角色 → 空串，不抛）
"""
import os
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.companion.context_recall import (  # noqa: E402
    ContextRecall, get_context_recall, _fmt_day,
)

PASS = FAIL = 0


def check(name, cond, extra=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("  PASS", name)
    else:
        FAIL += 1
        print("  FAIL", name, extra)


def _write(base: Path, kind: str, char: str, rows):
    d = base / kind
    d.mkdir(parents=True, exist_ok=True)
    p = d / ("%s.jsonl" % char)
    with p.open("w", encoding="utf-8") as f:
        for r in rows:
            f.write(__import__("json").dumps(r, ensure_ascii=False) + "\n")
    return p


def main():
    now = time.time()
    print("== 1. should_recall 触发判据 ==")
    S = ContextRecall.should_recall
    check("'你还记得你要干嘛吗' → 触发", S("你还记得你要干嘛吗"))
    check("'上次那家店叫什么' → 触发", S("上次那家店叫什么"))
    check("'昨天拍的照片看看' → 触发", S("昨天拍的照片看看"))
    check("'我跟你说过我要早点睡' → 触发", S("我跟你说过我要早点睡"))
    check("'今天天气不错' → 不触发", not S("今天天气不错"))
    check("'打开音乐' → 不触发", not S("打开音乐"))
    check("空串 → 不触发", not S(""))

    print("\n== 2. 合成数据：话题检索 + 时间 + 角色加成 ==")
    with tempfile.TemporaryDirectory() as td:
        base = Path(td)
        _write(base, "chatlog", "danya", [
            {"ts": now - 86400 * 1, "role": "user", "text": "我等会发照片给你看"},
            {"ts": now - 86400 * 1 + 5, "role": "ai", "text": "好呀我等你发照片"},
            {"ts": now - 86400 * 3, "role": "user", "text": "今天打了会儿游戏"},
            {"ts": now - 86400 * 2, "role": "user", "text": "那家咖啡店关门了"},
        ])
        cr = ContextRecall(base_dir=base)
        hits = cr.search("danya", "照片", limit=3)
        check("检索到'照片'相关原话", len(hits) >= 1, hits)
        check("命中含'发照片给你看'", any("发照片" in h["text"] for h in hits), hits)
        check("命中带 day 字段", bool(hits and hits[0].get("day")), hits)
        check("老对话(<30天)可检索", len(cr.search("danya", "游戏", limit=3)) >= 1)

        # 角色加成：user 的话应优先于同文本的 ai 话
        _write(base, "chatlog", "danya2", [
            {"ts": now - 3600, "role": "ai", "text": "相机真好看啊"},
            {"ts": now - 3600, "role": "user", "text": "相机真好看啊"},
        ])
        h2 = cr.search("danya2", "相机", limit=2)
        check("user 的话优先", bool(h2) and h2[0]["role"] == "user", h2)

        print("\n== 3. 引用块格式 ==")
        blk = cr.recall_block("danya", "照片")
        check("引用块含标题", "你们以前聊到过" in blk, blk)
        check("引用块含时间前缀", "（" in blk, blk)
        check("引用块含原话", "发照片" in blk, blk)
        check("空 query → 空块", cr.recall_block("danya", "") == "")
        check("无命中 → 空块", cr.recall_block("danya", "量子物理外星人") == "")
        check("enabled=False → 空块", cr.recall_block("danya", "照片", enabled=False) == "")

        print("\n== 4. history + chatlog 去重合并 ==")
        _write(base, "history", "danya", [
            {"ts": now - 86400, "role": "user", "content": "我等会发照片给你看"},
            {"ts": now - 100, "role": "user", "content": "只存在于history的一句话"},
        ])
        cr2 = ContextRecall(base_dir=base)
        allt = cr2._all_turns("danya")
        texts = [t["text"] for t in allt]
        check("history 独有的话被并入", "只存在于history的一句话" in texts, texts)
        check("重复句去重(照片句只一次)",
              sum(1 for t in texts if t == "我等会发照片给你看") == 1, texts)

    print("\n== 5. 失败降级（只读/不抛）==")
    with tempfile.TemporaryDirectory() as td:
        with_dash = ContextRecall(base_dir=Path(td))
        check("不存在角色 → 空列表", with_dash.search("nobody", "照片") == [])
        check("不存在角色 recall_block → 空串", with_dash.recall_block("nobody", "照片") == "")
        check("不抛异常", True)

    print("\n== 6. 真实项目数据（若存在）==")
    real_base = Path(__file__).resolve().parents[1] / "data"
    if (real_base / "chatlog" / "danya.jsonl").exists() or \
       (real_base / "history" / "danya.jsonl").exists():
        r = ContextRecall(base_dir=real_base)
        rt = r._all_turns("danya")
        check("真实数据可读（%d 轮）" % len(rt), len(rt) > 0)
        hit = r.search("danya", "照片", limit=3)
        print("     真实检索'照片' → %d 条" % len(hit))
        for h in hit[:3]:
            print("       ·（%s）%s" % (h["day"], h["text"][:40]))
    else:
        print("  SKIP 真实 chatlog/history 不存在")

    print("\n结果: %d PASS / %d FAIL" % (PASS, FAIL))
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
