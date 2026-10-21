"""把聊天记录渲染成 Markdown（纯函数，不碰磁盘）。

【2026-10-21 加·「导出聊天记录」】
背景：`export_markdown` 这个桥早就有了（日报页/记忆页在用），但**聊天记录本身没有导出**——
`data/chatlog/<char>.jsonl` 里躺着完整历史，用户没有任何入口把它取出来。想备份、想换机器、
想把一段对话发给别人，都只能截屏。

设计上的两个取舍：

1. **渲染与写盘分开**。写盘走既有的 `export_markdown`（它已经处理了 OneDrive 重定向桌面
   这些坑），这里只负责"记录列表 → 一段 Markdown 文本"。分开的好处是渲染可以纯函数测，
   不必为了验格式去动用户的桌面目录。
2. **不改 chatlog 的读法**。`ChatLogStore.load_recent(n)` 是"最近 N 条"语义（界面恢复只要
   200 条），导出要的是全部；用 `load_recent(cid, 10**9)` 这种魔法数一旦被人"优化"成合理
   上限，导出就会**静默只导最近一段**——所以单独给了 `load_all()`。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Iterable

# 角色标签：user 是"我"，ai 用角色名。别人接手时最常问的就是"这行谁说的"。
_ROLE_ME = "我"


def _fmt_ts(ts: Any) -> str:
    """时间戳 → 'YYYY-MM-DD HH:MM'。坏值不抛异常（历史里可能有脏数据）。"""
    try:
        return datetime.fromtimestamp(float(ts)).strftime("%Y-%m-%d %H:%M")
    except Exception:  # noqa: BLE001  单条脏数据不该毁掉整份导出
        return "未知时间"


def _day_of(ts: Any) -> str:
    try:
        return datetime.fromtimestamp(float(ts)).strftime("%Y-%m-%d")
    except Exception:  # noqa: BLE001
        return "未知日期"


def _role_label(rec: dict, char_name: str) -> str:
    return _ROLE_ME if rec.get("role") == "user" else (char_name or "她")


def _body(rec: dict) -> str:
    """正文 + 附图提示。正文里的换行原样保留（Markdown 会折行，但内容不丢）。"""
    text = str(rec.get("text") or "").strip()
    try:
        n_img = len(rec.get("images") or [])
    except TypeError:
        n_img = 0
    if n_img:
        text = (text + "\n\n" if text else "") + "（附图 ×%d）" % n_img
    return text or "（空消息）"


def render_markdown(
    records: Iterable[dict],
    *,
    char_name: str = "",
    char_id: str = "",
    exported_at: datetime | None = None,
) -> str:
    """记录列表 → 一份可直接读的 Markdown。

    `records` 按时间升序（chatlog 是 append-only，天然有序）。按日期分小节，
    每条消息写成 `**HH:MM · 谁**` + 正文两段——比表格好读，也比纯文本好检索。
    """
    recs = [r for r in records if isinstance(r, dict)]
    now = exported_at or datetime.now()
    me = sum(1 for r in recs if r.get("role") == "user")
    her = len(recs) - me

    head = ["# 聊天记录 · %s" % (char_name or char_id or "AI"), ""]
    head.append("- 角色：%s%s" % (char_name or "未命名",
                                 "（id: %s）" % char_id if char_id else ""))
    head.append("- 消息数：%d（我 %d / 她 %d）" % (len(recs), me, her))
    if recs:
        head.append("- 时间范围：%s → %s" % (_fmt_ts(recs[0].get("ts")),
                                            _fmt_ts(recs[-1].get("ts"))))
    head.append("- 导出时间：%s" % now.strftime("%Y-%m-%d %H:%M"))
    head.append("")
    head.append("---")

    lines = list(head)
    cur_day = None
    for r in recs:
        day = _day_of(r.get("ts"))
        if day != cur_day:
            cur_day = day
            lines.append("")
            lines.append("## %s" % day)
        hhmm = _fmt_ts(r.get("ts"))[-5:]
        lines.append("")
        lines.append("**%s · %s**" % (hhmm, _role_label(r, char_name)))
        lines.append("")
        lines.append(_body(r))

    lines.append("")
    return "\n".join(lines)


def render_bundle(sections: Iterable[tuple[str, str, list[dict]]],
                  *, exported_at: datetime | None = None) -> str:
    """多角色合并成一份（`scope="all"`）。

    `sections` 是 `(char_id, char_name, records)` 的序列。空角色跳过——
    导出一堆只有标题的空小节，用户会以为导出坏了。
    """
    now = exported_at or datetime.now()
    parts = ["# 全部聊天记录", "",
             "- 导出时间：%s" % now.strftime("%Y-%m-%d %H:%M"), ""]
    used = 0
    for cid, name, recs in sections:
        recs = [r for r in recs if isinstance(r, dict)]
        if not recs:
            continue
        used += 1
        parts.append("")
        parts.append("---")
        parts.append("")
        parts.append("## 角色：%s" % (name or cid))
        parts.append("")
        # 每个角色的正文复用单角色渲染，但去掉它自己的一级标题，避免文档里一堆 H1。
        body = render_markdown(recs, char_name=name, char_id=cid, exported_at=now)
        body = body.split("\n---\n", 1)[-1].lstrip("\n")
        parts.append(body.rstrip())
    if used == 0:
        return ""
    parts.append("")
    return "\n".join(parts)
