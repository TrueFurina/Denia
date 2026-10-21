"""聊天记录导出（2026-10-21 新增功能）自测。

被测：`CharacterAPI.export_chat_history` + `src/companion/chat_export.py` +
`ChatLogStore.load_all`。

背景：`export_markdown` 这个桥早就有（日报/记忆页在用），但**聊天记录本身没有出口**——
`data/chatlog/<char>.jsonl` 里的完整历史，用户只能截屏。本测试守三条红线：

  1. **导出的是全部**，不是最近 200 条（`load_recent` 的默认上限是界面恢复用的）；
  2. **没记录时明确报错，不写空文件**——只有标题的 .md 用户会当成"导出成功但没内容"；
  3. **角色名里带 `/ : * ?` 之类也不能炸**（Windows 文件名非法字符），
     否则用户看到的是"导出失败"，真因藏在一个 OSError 里。

写盘目标被替换成临时目录：`export_chat_history` 里调的是 `self.export_markdown`，
这里用鸭子类型的 fake 对象接住（真实实现会写到用户桌面，测试不该碰）。
"""
import os
import re
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.companion.chat_export import render_bundle, render_markdown  # noqa: E402
from src.companion.history_store import ChatLogStore  # noqa: E402
from src.ui.webapp import CharacterAPI  # noqa: E402

OK = FAIL = 0


def chk(name, cond, extra=""):
    global OK, FAIL
    if cond:
        OK += 1
        print("  PASS", name, extra)
    else:
        FAIL += 1
        print("  FAIL", name, extra)


class _Char:
    def __init__(self, cid, name):
        self.id = cid
        self.name = name


class _FakeApi:
    """只提供 export_chat_history 用到的四样东西。

    用**未绑定方法**挂上来（`CharacterAPI.export_chat_history`），这样测的是产品里
    真正会跑的那份代码，而不是测试自己复刻的一份——复刻只会证明"我两次写得一样"。
    """

    # staticmethod 通过类访问拿到的是**普通函数**，直接挂到 fake 上会被当实例方法绑定
    # （于是 self 被当成第一个参数传进去）。必须再包一层 staticmethod。
    _safe_file_stem = staticmethod(CharacterAPI._safe_file_stem)
    export_chat_history = CharacterAPI.export_chat_history

    def __init__(self, base, chars, active="danya"):
        self._chatlog = ChatLogStore(base)
        self._cfg = type("Cfg", (), {"list_characters": lambda _self: chars})()
        self._active = active
        self.written = []          # [(name, content)]
        self.fail_write = False

    def _active_char_id(self):
        return self._active

    def export_markdown(self, data=None):
        """替身：只记录，不写桌面。"""
        if self.fail_write:
            return {"success": False, "error": "写入失败，请查看日志"}
        d = data or {}
        self.written.append((d.get("name") or "", d.get("content") or ""))
        return {"success": True, "files": ["<tmp>/" + (d.get("name") or "")],
                "folder": "<tmp>"}


def _seed(store, cid, n, start=None):
    """造 n 条消息（user/ai 交替）。"""
    t0 = start if start is not None else (time.time() - n * 60)
    for i in range(n):
        store.append(cid, {"role": "user" if i % 2 == 0 else "ai",
                           "text": "第 %d 条" % (i + 1),
                           "ts": t0 + i * 60})


def main():
    print("[1] render_markdown 基本形态")
    t0 = 1760000000.0
    recs = [{"role": "user", "text": "今天好累", "ts": t0},
            {"role": "ai", "text": "那先别动了", "ts": t0 + 60}]
    md = render_markdown(recs, char_name="达妮娅", char_id="danya")
    chk("一级标题带角色名", md.startswith("# 聊天记录 · 达妮娅"), md.splitlines()[0])
    chk("统计条数正确", "消息数：2（我 1 / 她 1）" in md)
    chk("user 标成「我」", "· 我**" in md)
    chk("ai 标成角色名", "· 达妮娅**" in md)
    chk("按日期分小节", md.count("\n## ") >= 1)
    chk("正文都在", "今天好累" in md and "那先别动了" in md)

    print("[2] render_markdown 脏数据不炸")
    dirty = [{"role": "user", "text": "x", "ts": "not-a-number"},
             {"role": "ai", "ts": t0}]
    md2 = render_markdown(dirty, char_name="A")
    chk("坏时间戳退化成「未知时间」而不是抛异常", "未知时间" in md2)
    chk("缺正文写成（空消息）", "（空消息）" in md2)
    chk("空列表也能渲染（不抛）", render_markdown([], char_name="A").startswith("# 聊天记录"))

    print("[3] 附图提示")
    md3 = render_markdown([{"role": "user", "text": "看图", "ts": t0,
                            "images": ["a", "b"]}])
    chk("图片数量如实写出", "（附图 ×2）" in md3)

    print("[4] ChatLogStore.load_all 是「全部」，不是最近 200")
    base = tempfile.mkdtemp(prefix="chat_exp_")
    store = ChatLogStore(base)
    _seed(store, "danya", 250)
    chk("load_all 拿到 250 条", len(store.load_all("danya")) == 250,
        "n=%d" % len(store.load_all("danya")))
    chk("load_recent 默认仍是 200（界面恢复语义没被改坏）",
        len(store.load_recent("danya")) == 200)
    chk("load_recent(n=0) 等价于全部", len(store.load_recent("danya", 0)) == 250)

    print("[5] export_chat_history · 当前角色")
    api = _FakeApi(base, [_Char("danya", "达妮娅"), _Char("other", "小号")])
    r = api.export_chat_history({})
    chk("success", r.get("success") is True, str(r.get("error") or ""))
    chk("count 报的是全部 250 条（不是 200）", r.get("count") == 250, str(r.get("count")))
    chk("写了 1 个文件", len(api.written) == 1)
    name, content = api.written[0] if api.written else ("", "")
    chk("文件名带角色名", "聊天记录_达妮娅" in name, name)
    chk("文件名以 .md 结尾且带时间戳", name.endswith(".md") and name.count("_") >= 3, name)
    chk("内容含最早那条（证明导的是全部）", "第 1 条" in content)
    chk("内容含最后那条", "第 250 条" in content)

    print("[6] export_chat_history · 没记录时明确报错（对照组：不许写空文件）")
    api2 = _FakeApi(base, [_Char("danya", "达妮娅"), _Char("empty", "空号")])
    r2 = api2.export_chat_history({"character_id": "empty"})
    chk("success 为 False", r2.get("success") is False)
    chk("给出可读原因", bool((r2.get("error") or "").strip()), str(r2.get("error")))
    chk("**没有**写任何文件（空文件比报错更误导）", api2.written == [])

    print("[7] export_chat_history · scope=all")
    _seed(store, "other", 5)
    api3 = _FakeApi(base, [_Char("danya", "达妮娅"), _Char("other", "小号"),
                           _Char("empty", "空号")])
    r3 = api3.export_chat_history({"scope": "all"})
    chk("success", r3.get("success") is True, str(r3.get("error") or ""))
    chk("count 是各角色之和", r3.get("count") == 255, str(r3.get("count")))
    name3, content3 = api3.written[0] if api3.written else ("", "")
    chk("文件名标「全部」", "聊天记录_全部" in name3, name3)
    chk("两个有记录的角色都在", "## 角色：达妮娅" in content3 and "## 角色：小号" in content3)
    chk("空角色被跳过（不写只有标题的小节）", "## 角色：空号" not in content3)

    print("[8] 角色名含 Windows 非法字符也不炸")
    api4 = _FakeApi(base, [_Char("weird", 'a/b:c*d?e"f<g>h|i')])
    _seed(store, "weird", 2)
    r4 = api4.export_chat_history({"character_id": "weird"})
    chk("success（没有 OSError）", r4.get("success") is True, str(r4.get("error") or ""))
    n4 = api4.written[0][0] if api4.written else ""
    # 注意要 `bool(n4)` 打头：只写 `not any(c in n4 ...)` 的话，**没写出文件时 n4 是空串、
    # 断言恒真** —— 判据会被"什么都没发生"满足，是本仓踩过多次的假绿。
    chk("文件名不含非法字符", bool(n4) and not any(c in n4 for c in '\\/:*?"<>|'), n4)

    print("[9] 存储未就绪 / 写盘失败")
    api5 = _FakeApi(base, [_Char("danya", "达妮娅")])
    api5._chatlog = None
    r5 = api5.export_chat_history({})
    chk("_chatlog 为 None 时明确报错（不 AttributeError）",
        r5.get("success") is False and "未就绪" in (r5.get("error") or ""),
        str(r5.get("error")))
    api6 = _FakeApi(base, [_Char("danya", "达妮娅")])
    api6.fail_write = True
    r6 = api6.export_chat_history({})
    chk("写盘失败时把原因透传（不谎报成功）",
        r6.get("success") is False and bool(r6.get("error")), str(r6.get("error")))

    print("[10] render_bundle 全是空角色时返回空串")
    chk("返回空串（调用方据此报错，不写空文件）",
        render_bundle([("a", "A", []), ("b", "B", [])]) == "")

    print("[11] 前端入口：左键=当前角色，右键=全部角色")
    # 为什么查源码：`scope="all"` 是后端能力，若前端没有入口，它就是"参数没人调"的半截功能
    # ——本仓把这种形态算作缺陷。这里守两件事：①右键入口别被删掉；②左键仍走默认（当前角色）。
    # 必须先剥注释再断言：否则我自己在注释里复述一遍 `scope: 'all'` 就能满足判据
    # （本仓踩过：`"backup=True" in txt` 被注释满足）。
    _html_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                              "src", "ui", "web", "index.html")
    _html = ""
    if os.path.exists(_html_path):
        _html = open(_html_path, encoding="utf-8").read()
    _nc = re.sub(r"/\*.*?\*/", "", _html, flags=re.S)
    _nc = re.sub(r"<!--.*?-->", "", _nc, flags=re.S)
    _nc = re.sub(r"(?m)^\s*//.*$", "", _nc)
    _i = _nc.find("var ec = $('exportChat');")
    # 块的边界用「下一个绑定块的开头」定位，不用 `_i + 2000` 这种魔法窗口：
    # 窗口写小了会把判据切在块外（假红），写大了会把隔壁块算进来（假绿），
    # 而两种错都不会有人发现——只有等某天有人往这里加代码时才突然翻车。
    _end = _nc.find("var ft = $('focusToggle');", _i + 1) if _i != -1 else -1
    _blk = _nc[_i:_end] if (_i != -1 and _end != -1) else ""
    chk("按钮 #exportChat 存在", 'id="exportChat"' in _nc)
    chk("能定位到导出绑定块（块尾标记 var ft 在它后面）", bool(_blk))
    chk("按钮 title 写明右键可导出全部", bool(re.search(r'id="exportChat"[^>]*title="[^"]*右键', _nc)))
    chk("绑了 contextmenu（右键入口，别删）",
        "ec.addEventListener('contextmenu'" in _blk)
    chk("右键传 scope: 'all'", "scope: 'all'" in _blk)
    chk("左键仍走默认（当前角色），不是把两个范围搞反",
        bool(re.search(r"ec\.addEventListener\('click'.*?doExport\(\{\}", _blk)))

    print("\n结果: %d PASS / %d FAIL" % (OK, FAIL))
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
