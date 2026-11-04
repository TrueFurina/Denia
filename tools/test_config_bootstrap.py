"""首启缺配置的兜底：`src.config.ensure_config_file` 的四态判定。

背景（2026-11-04 验包实测）：队友照 README 走、唯独漏了
`cp config.release.yaml config.yaml`，原来 `main.py` 直接在 load_config 里抛
FileNotFoundError —— 窗口一个都不出现，只落一个 crash 日志，用户看到的是
"双击没反应"。修法是在启动层显式判定 + 明说做了什么。

这道闸门守四件事，缺一条就会变成"帮倒忙"：
  1. 已存在 → 什么都不做（**绝不拿模板盖用户实配**，那是数据丢失级错误）
  2. 缺失 + 旁边有模板 → 复制，且字节一致
  3. 缺失 + 没模板 → 返回 missing（让 main 打出该执行的那条命令）
  4. 复制失败 → 返回 error（不抛、不谎报 created）

不进闸门：无（已登记 GATES）
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from src.config import CONFIG_TEMPLATE_NAMES, ensure_config_file  # noqa: E402

OK = 0
FAIL = 0


def chk(name: str, cond: bool, detail: str = "") -> None:
    global OK, FAIL
    if cond:
        OK += 1
        print("  PASS %s" % name)
    else:
        FAIL += 1
        print("  FAIL %s%s" % (name, ("  ← " + detail) if detail else ""))


def main() -> int:
    print("配置首启兜底（ensure_config_file）")

    print("\n1) 已存在 → exists，且**不覆盖**")
    with tempfile.TemporaryDirectory() as d:
        cfg = os.path.join(d, "config.yaml")
        with open(cfg, "w", encoding="utf-8") as f:
            f.write("sentinel: user_real_config\n")
        with open(os.path.join(d, "config.release.yaml"), "w", encoding="utf-8") as f:
            f.write("template: should_not_win\n")
        st, detail = ensure_config_file(cfg)
        chk("状态 exists", st == "exists", st)
        with open(cfg, encoding="utf-8") as f:
            body = f.read()
        chk("用户实配没被模板盖掉", "sentinel" in body, body[:40])

    print("\n2) 缺失 + 有模板 → created，字节一致")
    with tempfile.TemporaryDirectory() as d:
        tpl = os.path.join(d, "config.release.yaml")
        payload = "app:\n  name: x\n" * 50
        with open(tpl, "w", encoding="utf-8") as f:
            f.write(payload)
        cfg = os.path.join(d, "config.yaml")
        st, detail = ensure_config_file(cfg)
        chk("状态 created", st == "created", st)
        chk("detail 指向模板", os.path.basename(detail) == "config.release.yaml", detail)
        with open(cfg, encoding="utf-8") as f:
            chk("内容字节一致", f.read() == payload)

    print("\n3) 模板优先级：release 优先于 example")
    with tempfile.TemporaryDirectory() as d:
        for name in ("config.example.yaml", "config.release.yaml"):
            with open(os.path.join(d, name), "w", encoding="utf-8") as f:
                f.write("from: %s\n" % name)
        cfg = os.path.join(d, "config.yaml")
        st, detail = ensure_config_file(cfg)
        chk("选中 release", os.path.basename(detail) == "config.release.yaml", detail)
        with open(cfg, encoding="utf-8") as f:
            chk("内容来自 release", "config.release" in f.read())
        chk("模板名清单里 release 排第一",
            CONFIG_TEMPLATE_NAMES[0] == "config.release.yaml", str(CONFIG_TEMPLATE_NAMES))

    print("\n4) 缺失 + 没模板 → missing（不抛异常）")
    with tempfile.TemporaryDirectory() as d:
        cfg = os.path.join(d, "config.yaml")
        st, detail = ensure_config_file(cfg)
        chk("状态 missing", st == "missing", st)
        chk("没有凭空造出文件", not os.path.exists(cfg))

    print("\n5) 目录都不存在 → missing（不崩）")
    with tempfile.TemporaryDirectory() as d:
        cfg = os.path.join(d, "no_such_dir", "config.yaml")
        st, _ = ensure_config_file(cfg)
        chk("状态 missing", st == "missing", st)

    print("\n6) 复制失败 → error（不谎报 created）")
    with tempfile.TemporaryDirectory() as d:
        # 把**模板**占成目录：os.path.exists 为真、copyfile 必抛 OSError，
        # 且不依赖权限位（Windows 下 chmod 不可靠，写不出稳定的只读夹具）。
        tpl = os.path.join(d, "config.release.yaml")
        os.makedirs(tpl)
        cfg = os.path.join(d, "config.yaml")
        st, detail = ensure_config_file(cfg)
        chk("状态 error", st == "error", st)
        chk("detail 是原因而不是空", bool(str(detail).strip()), repr(detail))
        chk("没有留下半截文件", not os.path.exists(cfg))

    print("\n7) 相对路径也认（main 传的是 args.config 原样）")
    cwd = os.getcwd()
    with tempfile.TemporaryDirectory() as d:
        with open(os.path.join(d, "config.release.yaml"), "w", encoding="utf-8") as f:
            f.write("y: 2\n")
        try:
            os.chdir(d)
            st, _ = ensure_config_file("config.yaml")
            chk("相对路径 created", st == "created", st)
            chk("文件真的在当前目录", os.path.isfile(os.path.join(d, "config.yaml")))
        finally:
            os.chdir(cwd)

    print("\n结果: %d PASS / %d FAIL" % (OK, FAIL))
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
