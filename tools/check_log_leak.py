# -*- coding: utf-8 -*-
"""门禁：凭据不得全量打进日志。

为什么需要它
------------
2026-10-18 盘点产品化缺口时发现：`main_window.py` 把本地静态服务的启动令牌
以 `%s` 全量打进日志（日志会留存、可能被截图或随排障材料外发）。同一仓库里
`webapp.py` 对 api_key 早就用了 `%.8s` 截断——同类问题两种处理，说明「不能靠
每个人每次都记得」，要变成会 exit(1) 的进程。

口径刻意收窄（2026-10-18 实测教训）
------------------------------------
第一版用宽口径「logger 行含 token/key 任意词 + 裸 %s」，全仓命中 16 处，
其中 15 处是 `key=场景键`（scene_key / action 键）这种非密钥语义，全是误报。
收窄到：格式串**明确指凭据**（令牌 / api_key / secret）且参数里有
含 `token` 的裸变量、以无精度 `%s` 传入。代价是多行调用的续行看不到
（只扫单行）——漏报好过误报，误报的门禁很快会被习惯性跳过。

用法
----
    python tools/check_log_leak.py            # 扫 src/
退出码：0 = 干净；1 = 有违规。
"""
from __future__ import annotations

import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCAN_DIRS = ["src"]

# re.S 必须加：拼接后的调用串里有 \n，`(.*)` 默认过不去——上一版门禁
# 就是因为这个对生产代码的多行调用形态永久失明（变异验证抓出来的）。
LOGGER_CALL = re.compile(r'\blogger\.\w+\((.*)', re.S)
CRED_WORD = re.compile(r'令牌|api[_ ]?key|apikey|访问密钥|secret', re.I)
# 参数名允许限定名（STATE.web_start_token）与下划线复合（api_key_value）
BARE_TOKEN_ARG = re.compile(r'[,(\s][\w.]*tokens?[\w.]*\s*[,)]', re.I)
# %.8s 这类带精度的占位符（截断=合规）；%s 前面没有 .N 就算裸
BARE_PERCENT_S = re.compile(r'(?<!\.\d)%s')


PLACEHOLDER = re.compile(r'%(?:\.\d+)?[sdrf]')


def _split_top_args(argstr: str) -> list[str]:
    """按括号深度 0 的顶层逗号切参数（字符串字面量内的逗号会干扰计数，
    但方向是多报不漏，且配合后面的占位符对齐足够用）。"""
    parts, buf, depth, q = [], [], 0, None
    for ch in argstr:
        if q:
            buf.append(ch)
            if ch == q:
                q = None
            continue
        if ch in "\"'":
            q = ch
        elif ch in "([{":
            depth += 1
        elif ch in ")]}":
            depth -= 1
        elif ch == "," and depth == 0:
            parts.append("".join(buf).strip())
            buf = []
            continue
        buf.append(ch)
    tail = "".join(buf).strip()
    if tail:
        parts.append(tail)
    # argstr 以「格式串后的分隔逗号」开头时会产出空首段；Python 语法不允许
    # 连续逗号，空段一定是切分伪影，过滤掉否则占位符/参数数目对不上位
    return [p for p in parts if p]


def violations_for_call(joined: str) -> bool:
    """一条（可跨行拼接过的）logger 调用是否把凭据裸打。

    精度必须做位置对齐（2026-10-18 变异暴露）：`"...%s...令牌 %.8s..."` 里
    另两个裸 %s 对应的是 URL 和目录（不敏感），只看「格式串里存在裸 %s」
    会把合规代码也抓进来。
    """
    m = LOGGER_CALL.search(joined)
    if not m:
        return False
    rest = m.group(1)
    if not CRED_WORD.search(rest):
        return False
    # 格式串 = 第一个引号到下一个同种引号；其后是参数区
    q = None
    for idx, ch in enumerate(rest):
        if ch in "\"'":
            if q is None:
                q = ch
            elif ch == q:
                break
    if q is None:
        return False
    fmt, argstr = rest[:idx + 1], rest[idx + 1:]
    holders = PLACEHOLDER.findall(fmt)
    args = _split_top_args(argstr)
    if len(holders) != len(args):
        # 动态格式（"%s" * n 等）对不了位：保守放行，不误报
        return False
    for ai, arg in enumerate(args):
        # 参数常带结尾的 `)`（join 自调用串末段）；先剥掉再要求整体是
        # token 类限定名——函数调用（get_token()）剥不掉会放行，宁可漏报
        a = arg.strip().rstrip(")").strip()
        if not re.fullmatch(r'[\w.]*tokens?[\w.]*', a, re.I):
            continue  # 参数不是 token 类变量
        ph = holders[ai] if ai < len(holders) else ""
        if not ph.startswith("%."):
            return True  # token 参数对到了一个无精度占位符
    return False


def _join_call(lines: list[str], start: int, max_extra: int = 10) -> str:
    """从 start 行起拼接一个 logger 调用（括号平衡为止）。

    为什么必须跨行：真事（2026-10-18）——`logger.info("...启动令牌 %s...")` 的
    格式串与参数分写两行，单行口径下 token 参数根本不在视野里，规则对生产
    代码形态永久失明，变异验证也抓不住。粗略数括号可能被字符串字面量里的
    括号骗到，但方向是多扫而非漏扫，可接受。
    """
    buf = lines[start]
    depth = buf.count("(") - buf.count(")")
    extra = 0
    while depth > 0 and start + extra + 1 < len(lines) and extra < max_extra:
        extra += 1
        nxt = lines[start + extra]
        buf += " " + nxt.strip()
        depth += nxt.count("(") - nxt.count(")")
    return buf


def scan_file(path: str) -> list[tuple[int, str]]:
    out: list[tuple[int, str]] = []
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        lines = fh.readlines()
    for i, line in enumerate(lines):
        if not LOGGER_CALL.search(line):
            continue
        joined = _join_call(lines, i)
        if violations_for_call(joined):
            out.append((i + 1, joined.strip()[:120]))
    return out


def main() -> int:
    bad: list[str] = []
    scanned = 0
    for sub in SCAN_DIRS:
        base = os.path.join(ROOT, sub)
        for cur, dirs, files in os.walk(base):
            dirs[:] = [d for d in dirs
                       if d not in ("__pycache__", "web", "node_modules")]
            for fn in files:
                if not fn.endswith(".py"):
                    continue
                p = os.path.join(cur, fn)
                scanned += 1
                for no, text in scan_file(p):
                    rel = os.path.relpath(p, ROOT)
                    bad.append(f"{rel}:{no}: {text}")
    if bad:
        print(f"✗ {len(bad)} 处凭据疑似全量进日志（格式串指凭据 + 裸 %s + token 变量）：")
        for b in bad:
            print("  " + b)
        print("  修法：参数用 %.8s 截断（范例见 src/ui/webapp.py 的 api_key=%.8s）")
        return 1
    print(f"✓ 凭据日志卫生：{scanned} 个 .py 无违规")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
