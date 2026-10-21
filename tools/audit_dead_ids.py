"""死引用看门狗：找出页面里「引用了但页面上不存在」的元素 id。

用途
----
`getElementById('foo')` 拿到 null 是**静默**的 —— JS 不报错，只是后续 `if(el)` 恒假、
那段逻辑永不执行。这类「在但不生效」的休眠代码最难发现（用户看到的是"功能没了"，
日志里什么都没有）。本脚本把它变成可度量的数字，纳入自测后即成为回归闸门。

判定链（三步，避免误报）
------------------------
1. 收集页面上所有 `getElementById('x')` 引用 → 集合 A；
2. 收集 HTML 里所有静态 `id="x"` 声明 → 集合 B；
3. 候选 = A - B。再排除**运行时创建**的 id（`el.id = 'x'` 或 `id='x'` 出现于非静态属性位置）
   → 真死引用。

另有白名单 `KNOWN_DEAD`：已人工核实、**故意保留**的死引用（例如用户要求留注释的
`#mixedMenuBtn`/`#decoupleSwitch`）。白名单里的 id 不计入失败，但会打印出来提醒。

退出码：0 = 无新增死引用；1 = 出现未登记的死引用。
"""
import io
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# 【2026-10-21 修·旧机器路径】原来是 `ROOT = r"G:\ai对话\11"` —— 仓库搬到 D:\Denia 之后，
# 这个工具**从搬家那天起就跑不了**（`!! 文件不存在：src/ui/web/...`），而它不在闸门里，
# 所以没人发现。与本仓此前修过的 5 个测试同一个坑：**硬编码旧项目根**。
# 它的 docstring 写着"纳入自测后即成为回归闸门"，但一直没进 check_all —— 本次一并处理。

# 目标页面：设置页（死引用最多、且是用户改模型/音色的主战场）
TARGETS = [
    "src/ui/web/redesign/ui_redesign_settings.html",
]

# 已人工核实、故意保留的死引用（不要在这里加没核实过的 id！）
KNOWN_DEAD = {
    # ── 2026-10-09 核实 ──
    # 用户明确要求「留着加注释」：DOM 在提交 b24b969「设置页全面审核」被有意移除
    "decoupleSwitch",
    # 以下 11 个于 2026-10-09 由 .cdp_tmp/t_settings_deadref.cjs 真机核实：
    #   · 元素确实不存在（document.getElementById 返回 null）
    #   · 所在 IIFE 确实执行了（getCompanionFeatures 被调用 6 次）
    #   · 全程 0 个未捕获异常 ⇒ 每个访问点都有 if(el) 守卫，属"休眠"而非"崩溃"
    # 典型受害者：「陪伴能力」那段（featList/featModeChip/featSaved/featSave/engList）
    # —— 引擎卡勾选动画、显存超限提示、"读取功能配置失败"提示因此永不显示。
    # 保留原因：清理需动 3 个 IIFE 与若干 CSS 选择器，收益低、回归面大；
    # 先作为"已知休眠"登记，等真的要动这块 UI 时一并清。
    "developer",      # #developer 段已并入新表单，旧容器不存在
    "engList",        # 引擎卡列表容器已删 ⇒ drawEng() 提前 return
    "featList",       # 能力卡列表容器已删
    "featModeChip",   # 档位芯片已删
    "featSave",       # 能力保存按钮已删
    "featSaved",      # 保存提示已删
    "ovCards",        # 总览卡片容器已改名/重构
    "redesignBuild",  # 构建戳徽标已删
    "reloadUiBtn",    # 「重载界面」按钮已删（改为导航内自动重载）
    "scopeLabel",     # 作用域标签已删
    "styleFixedName", # 固定互动风格名标签已删
}

OUT = []


def log(*a):
    OUT.append(" ".join(str(x) for x in a))


# 收集「运行时创建」的 id：`xxx.id = 'foo'` 或 模板串里 `id="foo"`
RE_RUNTIME1 = re.compile(r"\.id\s*=\s*['\"]([A-Za-z0-9_-]+)['\"]")
RE_RUNTIME2 = re.compile(r"\.setAttribute\(\s*['\"]id['\"]\s*,\s*['\"]([A-Za-z0-9_-]+)['\"]")
# 静态声明：只匹配 HTML 标签属性里的 id="..."（排除 JS 字符串里拼的）
RE_STATIC = re.compile(r"<[A-Za-z][^>]*?\bid=\"([A-Za-z0-9_-]+)\"[^>]*>")
RE_GET = re.compile(r"getElementById\(\s*['\"]([A-Za-z0-9_-]+)['\"]\s*\)")

fail_total = 0

for rel in TARGETS:
    p = os.path.join(ROOT, rel)
    if not os.path.isfile(p):
        log("!! 文件不存在：%s" % rel)
        fail_total += 1
        continue
    s = io.open(p, encoding="utf-8").read()

    refs = set(RE_GET.findall(s))
    static = set(RE_STATIC.findall(s))
    runtime = set(RE_RUNTIME1.findall(s)) | set(RE_RUNTIME2.findall(s))

    dead = sorted(refs - static - runtime)
    known = sorted(set(dead) & KNOWN_DEAD)
    unknown = sorted(set(dead) - KNOWN_DEAD)

    log("")
    log("=== %s ===" % os.path.basename(rel))
    log("  getElementById 引用 : %d" % len(refs))
    log("  静态 id 声明        : %d" % len(static))
    log("  运行时创建 id       : %d" % len(runtime & refs))
    log("  真死引用            : %d（白名单 %d / 未登记 %d）"
        % (len(dead), len(known), len(unknown)))

    if known:
        log("  ── 白名单（已核实·故意保留）──")
        for k in known:
            log("     [known] %s" % k)

    if unknown:
        log("  ── ★ 未登记死引用（需要处理或加白名单）──")
        for k in unknown:
            # 给出首次出现行号，方便定位
            m = RE_GET.search(s, 0)
            ln = 0
            for mm in RE_GET.finditer(s):
                if mm.group(1) == k:
                    ln = s[:mm.start()].count("\n") + 1
                    break
            log("     [DEAD] %-18s 首见于第 %d 行" % (k, ln))
        log("  ⇒ 这些 id 无任何创建点。若确认是历史遗留死代码，")
        log("    请①清理对应逻辑 或 ②加进本脚本的 KNOWN_DEAD 并注明原因。")
        fail_total += len(unknown)
    else:
        log("  [OK]   无未登记死引用")

log("")
log("=" * 60)
if fail_total:
    log("结论：发现 %d 处未登记死引用 ✗" % fail_total)
else:
    log("结论：无未登记死引用 ✅")
log("=" * 60)

# 落盘一份，便于 diff 历史
# 【2026-10-21 修·别往仓库目录写产物】原来写 `docs/_audit_dead_ids.txt` —— 而 docs/ 不是
# 忽略目录，于是**每跑一次工作区就多一个未跟踪文件**（`git status` 里一直是 `?? docs/...`），
# 谁顺手 `git add -A` 就会把生成物提交进去。生成物一律写到 logs/（已 gitignore）。
try:
    d = os.path.join(ROOT, "logs")
    os.makedirs(d, exist_ok=True)
    io.open(os.path.join(d, "audit_dead_ids.txt"), "w",
            encoding="utf-8").write("\n".join(OUT))
except Exception:  # noqa: BLE001
    pass

sys.stdout.write("\n".join(OUT) + "\n")
sys.exit(1 if fail_total else 0)
