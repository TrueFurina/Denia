# -*- coding: utf-8 -*-
"""把「配置引用体检」写进 CHANGELOG：1 行速览 + 1 个明细小节。

带 assert 的专用脚本（2026-10-19 教训：通用插入脚本对每个 `### ` 都执行一遍，
把 CHANGELOG 撑到 3136 行、同一小节出现 42 次）。这里显式只处理第一个 `### `，
并断言标题只出现 1 次。
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(ROOT)

P = "CHANGELOG.md"
raw = open(P, "rb").read()
NL = "\r\n" if raw.count(b"\r\n") else "\n"
text = raw.decode("utf-8")
lines = text.split(NL)

TITLE = "### 2026-10-19 · 按类清扫「配置指向不存在的东西」+ 一个只有 6 处命中的体检"

BODY = """**来由**：修 ASR 时发现 `model_path` 指向不存在的目录、而"非空值会把自动
下载堵死"。这跟前面三处（能想/能说/能听）完全同型——**不是功能写错，是验证
缺失**：配置看着都填了，从没被验证过。所以写个扫描器按类扫一遍，不等下一处。

**扫出来的实情**：`config.release.yaml` 只有 6 处可校验引用，其中
`proactive.local_omni_engine.model_dir = 'H:\\ceshi'` 不存在——但该子树
`auto_start: false`（可选功能，指向用户自己的 Comni 目录），判红就是假红。
微信网关 2531 没起同样属于"没起可选服务"。所以判级必须分三层。

**判级口径**（和前几项统一：用户能自己解决的只黄，产品级失效/配置指错才红）：
· 路径键（白名单 `PATH_KEYS`）指向不存在 → FAIL；
· 角色 `voice.engine` 空/不认识 → FAIL（静音后端，能收不能说）；
· 挂在 `auto_start/enabled: false` 子树下的缺失 → 只提示；
· 本机服务端口没在听 → 默认只提示，`--strict-ports` 才判红
  （端口是运行环境，网关在 Docker 里没起很正常，判红会天天假红）。

**白名单制是第三次改版才定的**（前两次都在注释里留着，省得再踩）：
一版按"值里有 / 或 \\\\"判 → `model_path: foo` 这种相对目录名漏报；
二版未知键也查 → `label`（OCR 锚点文字）、`realtime_model`（模型 ID）、
`ws_url` 全被当本地路径，噪声淹没问题。
现版只查登记的路径键——宁可漏几个没登记的键，也不要一片红。

**测试 + 变异**：`test_config_path_refs.py` 12 组；变异 8 刀全红
（漏报 / 假红 / 端口判红 / 静音不判红 / 白名单放宽 / 相对名漏报 /
可选降级失效 / 异常被静默吞）。过程中又抓到两个覆盖盲区：
① 脚本裸跑 `ModuleNotFoundError: No module named 'src'` 而**测试全绿**
   （测试自己 insert 了 ROOT）——"测试能跑"和"脚本能跑"是两件事；
② 早先用 `globals()` 在 scan() 里动态注入收集器，`check_module_globals`
   报"walk 引用了未定义的全局 '_checked'"——静态检查抓得到就别跟它斗。

门禁 **42 → 44 项全过、0 跳过**。"""

ROW = ("| `TBD8` | 01:2x | feat | 按类清扫「配置指向不存在的东西」：路径白名单 + "
       "角色引擎 + 本机服务三层判级（可选功能/端口默认只提示） | 12 组断言 + "
       "8 刀变异全红；门禁 42→44 项 |")

assert TITLE not in text, "小节已存在，别重复插"
first = next(i for i, l in enumerate(lines) if l.startswith("### "))

out = []
for i, ln in enumerate(lines):
    if i == first:
        out.append(TITLE)
        out.extend(BODY.split("\n"))
        out.append("")
    if ln.startswith("| `ab9ba0d` |"):
        out.append(ROW)
    out.append(ln)

text2 = NL.join(out)
assert text2.count(TITLE) == 1, "标题出现次数不是 1"
assert text2.count(ROW.split(" | ")[0]) == 1, "速览行插入异常"
open(P, "w", encoding="utf-8", newline="").write(text2)
print("已写入：速览 1 行 + 明细小节 1 个")
print("总行数", len(lines), "->", len(text2.split(NL)))
