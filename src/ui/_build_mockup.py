"""把真实数据内联进设计稿，产出**单文件**预览稿。

为什么内联：file:// 下 fetch 本地 JSON 会被 CORS 挡掉，内联成 <script> 才能真正
"双击即看、零依赖"。代价是文件大一点（约 2MB），对设计稿完全可以接受。
"""
import json
from pathlib import Path

MOCK = Path(r"src\ui\_mock")
tpl = (MOCK / "stickers_mockup.html").read_text(encoding="utf-8")
data = (MOCK / "mock_data.json").read_text(encoding="utf-8")

if "__MOCK_DATA__" not in tpl:
    raise SystemExit("模板里找不到 __MOCK_DATA__ 占位符")

out_html = tpl.replace("var DATA = __MOCK_DATA__;", "var DATA = " + data + ";")
out = MOCK / "表情包管理_设计稿_v2.html"
out.write_text(out_html, encoding="utf-8")

mb = out.stat().st_size / 1024 / 1024
print(f"已生成单文件设计稿：{out}")
print(f"  大小 {mb:.2f} MB（内联 {len(json.loads(data)['packs'])} 个包的真实数据）")
print(f"  直接双击或在 DSH 里打开即可预览")
