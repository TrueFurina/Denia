"""角色卡图片（banner/avatar）链路回归测试。

【背景】2026-10-11 用户报"角色卡保存不了图片"。实读代码定位到**真因不是存不上**，
而是 `webapp.get_characters()` 用**白名单字段**拼返回，**漏了 banner / avatar**
⇒ 前端 c.banner / c.avatar 恒为 undefined ⇒ 角色卡头图/头像**存了也显示不出来**
（用户看到的就是"没保存成功"）。

本测试锁死三点，防止再次静默退化：
  1. get_characters 必须透出 banner / avatar 两个字段；
  2. save_character 的 avatar 字段遵循"未传则保留原值"（与 banner 同策略）；
  3. /local_img 白名单必须同时包含 data/char_banners 与 data/char_avatars
     （否则头像图取不到 = 又一处静默失效）。

纯静态/结构断言 + 最小实例化，不依赖 GUI，不写 config.yaml。
"""
from __future__ import annotations

import os
import re
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

_WEBAPP = os.path.join(ROOT, "src", "ui", "webapp.py")

PASS = 0
FAIL = 0


def check(cond: bool, label: str) -> None:
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  PASS {label}")
    else:
        FAIL += 1
        print(f"  FAIL {label}")


def main() -> int:
    src = open(_WEBAPP, encoding="utf-8").read()

    print("[1] get_characters 透出 banner / avatar")
    # 定位 get_characters 函数体（到下一个 def 为止）
    m = re.search(r"\n    def get_characters\(self\).*?(?=\n    def )", src, re.S)
    check(m is not None, "能定位 get_characters 函数体")
    body = m.group(0) if m else ""
    check('"banner": getattr(char, "banner"' in body, "函数体返回 banner")
    check('"avatar": getattr(char, "avatar"' in body, "函数体返回 avatar")

    print("[2] save_character 的 avatar 按「未传则保留原值」")
    check('"avatar": char_data.get("avatar")' in src, "save_character 读 char_data['avatar']")
    # 保留原值的语义：if char_data.get('avatar') is not None → 用传入；否则用 _existing
    check(
        re.search(r'"avatar":\s*char_data\.get\("avatar"\)\s*\n\s*if char_data\.get\("avatar"\) is not None', src),
        "avatar 未传(None)时保留原值",
    )
    check('"banner": char_data.get("banner")' in src, "banner 同款策略仍在（对照）")

    print("[3] /local_img 白名单含 char_banners + char_avatars")
    mi = re.search(r"/local_img.*?_allowed = \[(.*?)\]", src, re.S)
    check(mi is not None, "能定位 /local_img 白名单")
    allow = mi.group(1) if mi else ""
    check('"char_banners"' in allow, "白名单含 char_banners")
    check('"char_avatars"' in allow, "白名单含 char_avatars")

    print("[4] 后端选图方法存在 + 落盘目录正确")
    check("def pick_character_avatar" in src, "有 pick_character_avatar")
    check("def _char_avatar_dir" in src, "有 _char_avatar_dir")
    check('"char_avatars"' in src, "落盘目录名为 char_avatars")

    print("[5] 前端桥导出 pickCharacterAvatar")
    js_path = os.path.join(ROOT, "src", "ui", "web", "redesign", "app_backend.js")
    js = open(js_path, encoding="utf-8").read()
    check("const pickCharacterAvatar" in js, "桥定义 pickCharacterAvatar")
    check("pickCharacterAvatar," in js, "桥导出 pickCharacterAvatar（进 return）")

    print("[6] 角色页有头像字段 + 保存回传")
    hp = os.path.join(ROOT, "src", "ui", "web", "redesign", "ui_redesign_characters.html")
    h = open(hp, encoding="utf-8").read()
    check('id="eAvatar"' in h, "编辑弹窗有 #eAvatar")
    check("function setAvatar" in h, "有 setAvatar 处理")
    check("Backend.pickCharacterAvatar()" in h, "点击调 pickCharacterAvatar")
    check(re.search(r"avatar:\s*\(function\(\)\{\s*const el = m\.querySelector\('#eAvatar'\)", h) is not None,
          "保存回传 avatar（元素缺失传 undefined 保原值）")

    print("[7] 角色卡渲染优先用 c.avatar")
    check("if (c && c.avatar)" in h, "avatarHtml 优先用角色 avatar")

    print("[8] 聊天页头像接角色 avatar（theme_manager）")
    tm = open(os.path.join(ROOT, "src", "ui", "web", "redesign", "theme_manager.js"),
              encoding="utf-8").read()
    check("function applyCharacterAvatar" in tm, "有 applyCharacterAvatar")
    check("applyCharacterAvatar((act && act.avatar) || '')" in tm, "applyForActive 调它")

    print(f"\n结果: {PASS} PASS / {FAIL} FAIL")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
