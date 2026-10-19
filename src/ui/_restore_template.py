"""从已生成的单文件稿里还原模板（把内联数据换回占位符）。

为什么需要：模板 `stickers_mockup.html` 是"带占位符的源码"，
`_build_mockup.py` 把真实数据内联进去生成最终稿。
模板误删后，用本脚本从成品还原，避免只能改成品（成品 1.9MB，编辑易错）。
"""
import re
from pathlib import Path

MOCK = Path(r"src\ui\_mock")
built = MOCK / "表情包管理_设计稿_v2.html"
tpl = MOCK / "stickers_mockup.html"

t = built.read_text(encoding="utf-8")
# 把 `var DATA = {...};` 换回占位符（非贪婪到行尾的 };）
new, n = re.subn(r"var DATA = \{.*?\};", "var DATA = __MOCK_DATA__;", t, count=1, flags=re.DOTALL)
if n == 0:
    raise SystemExit("未找到 var DATA = {...}; 无法还原模板")
tpl.write_text(new, encoding="utf-8")
print(f"已还原模板：{tpl}")
print(f"  大小 {tpl.stat().st_size/1024:.1f} KB（占位符版，便于编辑）")
