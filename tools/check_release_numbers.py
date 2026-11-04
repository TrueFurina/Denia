# -*- coding: utf-8 -*-
"""发布口径对账：站点里每一个对外数字都必须等于 site/release.json 的真值。

背景（2026-11-04 实证）：出包实测安装包 175.5 MB，而 README / 定位文档 /
旧口径里还写着 72.8 MB，差 47.8 MB；官网当夜才校准。数字散在 HTML meta、
CTA、统计块、下载卡、PUBLISH.md 表格里，靠人手改必然漂，而且**越把数字放
第一屏，错得越致命**（这些数字是产品最强的可验证卖点）。

判据（四条，缺一不可）：
  1. 真值文件字段齐全、体积为正、schema_version=1。
  2. 规格表里每个锚点命中次数 == 期望，且捕获值 == 真值（防「改了真值没同步」
     也防「改了页面没改真值」）。
  3. 站点里任何 `X.Y MB` token 都必须是三个受管值之一（防新增数字漏挂锚点）。
  4. 版本号与 SHA 对账：版本取自 src/version.py 单一定义处，shaSource/shaSetup
     必须与真值一致（空值也算一致）。

不变量：本闸门只读不写；回填动作在 sync_release_site.py，两者共用同一张规格表。
"""
from __future__ import annotations

import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _release_numbers as R  # noqa: E402

OK = 0
FAIL = 0


def chk(name: str, cond: bool, detail: str = "") -> None:
    global OK, FAIL
    if cond:
        OK += 1
        print("  PASS %s" % name)
    else:
        FAIL += 1
        print("  FAIL %s%s" % (name, ("  <- " + detail) if detail else ""))


def main() -> int:
    try:
        truth = R.load_truth()
    except Exception as exc:
        print("  FAIL 真值文件可解析 <- %s" % exc)
        print("\n结果: 0 PASS / 1 FAIL")
        return 1

    print("1) 真值文件")
    chk("release.json 字段齐全且体积为正", True)
    try:
        ver = "v" + R.app_version()
    except Exception as exc:
        ver = ""
        chk("src/version.py 可读", False, str(exc))
    else:
        chk("src/version.py 可读", True)
    chk("真值版本 == src/version.py（%s）" % ver,
        truth["version"] == ver, truth["version"])

    print("\n2) 受管锚点逐条对账")
    texts = {rel: R.read_site(rel) for rel in R.SITE_FILES}
    for rel, pattern, key, expected in R.NUMBER_SPECS:
        ms = list(re.finditer(pattern, texts[rel]))
        label = "%s #%s" % (os.path.basename(rel), key)
        if len(ms) != expected:
            chk(label, False, "锚点命中 %d 次，期望 %d" % (len(ms), expected))
            continue
        vals = [m.group("n") for m in ms]
        want = R.fmt(truth[key])
        chk("%s = %s" % (label, want), all(v == want for v in vals),
            ",".join(vals))

    print("\n3) 站点里不允许出现未受管的小数 MB 数字")
    allowed = R.allowed_numbers(truth)
    for rel in R.SITE_FILES:
        vals = [re.sub(r"\s*MB$", "", v)
                for v in re.findall(r"\d+\.\d+\s*MB", texts[rel])]
        stray = sorted({v for v in vals if v not in allowed})
        chk("%s 无游离体积数字" % os.path.basename(rel), not stray,
            ",".join(stray))

    print("\n4) 版本与 SHA 对账")
    html = texts["site/index.html"]
    vm = list(re.finditer(R.VERSION_SPEC[1], html))
    chk("页面 RELEASE.version 与真值一致",
        len(vm) == R.VERSION_SPEC[2] and vm[0].group("v") == truth["version"],
        ",".join(m.group("v") for m in vm))
    for label, spec, key in (("shaSource", R.SHA_SOURCE_SPEC, "source_sha256"),
                             ("shaSetup", R.SHA_SETUP_SPEC, "setup_sha256")):
        ms = list(re.finditer(spec[1], html))
        chk("页面 %s 与真值一致" % label,
            len(ms) == spec[2] and ms[0].group("h") == truth[key],
            ",".join(m.group("h")[:12] for m in ms))

    print("\n结果: %d PASS / %d FAIL" % (OK, FAIL))
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
