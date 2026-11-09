# -*- coding: utf-8 -*-
"""「WS 端口绑定」的变异验证：八刀，刀刀必须被抓住。

跑法：.venv/Scripts/python.exe -u tools/_qc_ws_port_bind_mutation.py

这道门守的是 2026-10-20 03:11 那次崩溃：上一个实例退出几秒后重启，bind 8765
直接 OSError 10048 → 「初始化失败 → 监督线程跨线程拆 pywebview 窗口 →
fatal COM 0x8001010d」，用户侧只剩 crash dump。

**这道门此前一次都没被变异验过。** `tools/check_all.py` 里那句注释写着
「两刀变异已验：删重试→「确有重试」红；改回裸 raise→三条诊断断言红」，
但仓库里没有任何 `_qc_*ws*` 文件 —— 手工验过的刀不会回潮，注释也不会。

本轮先读判据，读到两处空转（都是实测，不是推测）：

  1. 把 `logger.warning(...)` 整段删掉 → 自测**照样全绿 exit 0**。
     「先重试再报错」里的「重试」在日志上毫无痕迹，用户事后翻日志
     看不到「试过 6 次」，只看到最后一次错误。
  2. 把 `serve()` 提到循环外（**只真的 bind 一次**，然后空睡 5 次再抛同样的错）
     → 自测**照样全绿**。耗时 5.0s 骗过了「确有重试（耗时 ≥ 5s）」。

第 2 条是致命的：闸门测的「重试」只是「等了 5 秒」，不是「又 bind 了 5 次」。
而 2026-10-20 那个 bug 的形状恰恰是「占用 1~2 秒后释放」—— 空睡 5 秒一秒都
修不了它。原文件只有两端（一直空闲 / 一直被占），中间那段从来没测过。

所以本轮先补判据（契约 2 的日志断言 + 契约 4「中途释放必须重绑」），八刀才有
东西可砍。**先有判据，才有刀。**

另外顺手修了一个正文缺陷：报错串里 `rstrip("。")` 把句号吃掉不再补，拼出来是
「…只允许使用一次若这是第二个达妮娅实例…」，崩溃弹窗里就是一整句读不通的话
（H 刀守的就是这个）。
"""
import hashlib
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(ROOT)

P = "src/ws_server.py"
TEST = "src/tests/test_ws_port_bind.py"
# 这道门要真的 bind 端口（websockets），必须用装了依赖的解释器；
# 没有 .venv 就退回当前解释器，别把「解释器不存在」伪装成「判据坏了」。
PY = ".venv/Scripts/python.exe"
if not os.path.exists(PY):
    PY = sys.executable


def _judged(r) -> bool:
    """判定是「判出了问题」而不是「崩了」。

    本门输出约定是每条 `✗ 名字`、末尾 `FAIL：N 项未通过`。
    崩溃（SyntaxError / ImportError / Traceback）同样非零退出，但不会打印
    `✗` / `FAIL：` —— 那正是「崩了」与「认出来了」的分界。
    """
    out = (r.stdout or "") + (r.stderr or "")
    return r.returncode != 0 and ("✗" in out or "FAIL：" in out)


def _run_test():
    return subprocess.run([PY, "-u", TEST], capture_output=True, text=True,
                          encoding="utf-8", errors="replace")


def _baseline_green() -> bool:
    """起点必须绿：恒红的判定能让每一把刀都变红，那种"全红"什么都不证明。"""
    r = _run_test()
    out = (r.stdout or "") + (r.stderr or "")
    return r.returncode == 0 and "✗" not in out and "FAIL：" not in out


def B(s: str) -> bytes:
    return s.encode("utf-8")


MUTS = [
    # A 是注释里点名的那一刀：删掉重试。只试一次，立刻抛。
    ("A 只 bind 一次（删掉重试循环）",
     B("        for attempt in range(1, _BIND_ATTEMPTS + 1):\n"),
     B("        for attempt in range(1, 2):  # 变异刀：不再重试\n")),

    # B 比 A 阴险：重试循环还在，6 次也真的走完了，但每次**不等**。
    # Windows 上旧实例释放监听后 10048 还会持续 1~2s，不等待等于没重试。
    # 判据上「确有重试」靠耗时，这一刀专打「次数够、间隔为 0」。
    ("B 重试但不等待（sleep 1s → 0s）",
     B("                    await asyncio.sleep(1.0)\n"),
     B("                    await asyncio.sleep(0)  # 变异刀：重试间隔归零\n")),

    # C 是注释里点名的第二刀：改回裸 raise（只有 traceback，没有 PID / 托盘指引）。
    ("C 改回裸 raise（丢掉四条诊断）",
     B('        holder = _describe_port_holder(self._port)\n'
       "        raise OSError(\n"
       '            "WebSocket 端口 %s 绑定失败（已重试 %d 次）：%s%s"\n'
       '            "若这是第二个达妮娅实例，请先退出正在运行的那个（系统托盘右键 → 退出）再启动；"\n'
       '            "两个实例共用 data/ 目录，不能同时运行。"\n'
       '            # 【2026-11-09 修·两句话粘在一起】原来这里只有 rstrip("。")，把 OSError\n'
       '            # 末尾的句号吃掉却不再补，拼出来是「…只允许使用一次若这是第二个达妮娅\n'
       '            # 实例…」——崩溃弹窗里就是这么一整句读不通。剥掉再补一个句号即可。\n'
       '            % (self._port, _BIND_ATTEMPTS, str(last_err).rstrip("。") + "。", holder)\n'
       "        ) from last_err\n"),
     B("        holder = _describe_port_holder(self._port)\n"
       "        raise last_err  # 变异刀：裸 raise，退回裸 traceback\n")),

    # D 砍「该开口时闭嘴」：占用探测永远返回空串，报错退化成文字版裸 traceback
    # （有指引但没有「谁占着」）。
    ("D 占用探测永远闭嘴（holder 恒空串）",
     B("    try:\n        import psutil\n"
       "    except Exception:  # noqa: BLE001\n        return \"\"\n"),
     B("    try:\n        import psutil\n"
       "    except Exception:  # noqa: BLE001\n        return \"\"\n"
       "    return \"\"  # 变异刀：占用探测永远闭嘴\n")),

    # E 是反方向：空闲时也开嘴。误报比漏报更能杀死一道闸门 ——
    # 每次都报「端口没被占用」等于没报，还会污染日志。
    ("E 空闲时也开嘴（误报噪声）",
     B("    except Exception:  # noqa: BLE001\n        return \"\"\n    return \"\"\n"),
     B("    except Exception:  # noqa: BLE001\n        return \"\"\n"
       "    return \"端口 %s 未发现占用。\" % port  # 变异刀：空闲也开嘴\n")),

    # F 砍本轮补的日志判据：重试照旧、报错照旧，只是**一声不响**。
    ("F 重试静默无声（删掉 logger.warning）",
     B("                if attempt < _BIND_ATTEMPTS:\n"
       "                    logger.warning(\n"
       '                        "WebSocket 端口 %s 绑定失败（%d/%d 次：%s），1s 后重试",\n'
       "                        self._port, attempt, _BIND_ATTEMPTS, e)\n"
       "                    await asyncio.sleep(1.0)\n"),
     B("                if attempt < _BIND_ATTEMPTS:\n"
       "                    await asyncio.sleep(1.0)\n")),

    # G 是本轮最想守的那一刀：serve() 提到循环外，只真的 bind 一次，
    # 然后空睡 5 次再抛同样的错。修之前这道门对它全绿（实测）。
    ("G 只 bind 一次 + 空睡 5 次（假重试）",
     B("        last_err = None\n"
       "        for attempt in range(1, _BIND_ATTEMPTS + 1):\n"
       "            try:\n"
       "                self._server = await serve(self._handler, self._host, self._port)\n"
       '                logger.info("WebSocket 服务启动 ws://%s:%s", self._host, self._port)\n'
       "                return\n"
       "            except OSError as e:\n"
       "                last_err = e\n"
       "                if attempt < _BIND_ATTEMPTS:\n"
       "                    logger.warning(\n"
       '                        "WebSocket 端口 %s 绑定失败（%d/%d 次：%s），1s 后重试",\n'
       "                        self._port, attempt, _BIND_ATTEMPTS, e)\n"
       "                    await asyncio.sleep(1.0)\n"),
     B("        last_err = None\n"
       "        try:  # 变异刀：只真的 bind 一次\n"
       "            self._server = await serve(self._handler, self._host, self._port)\n"
       '            logger.info("WebSocket 服务启动 ws://%s:%s", self._host, self._port)\n'
       "            return\n"
       "        except OSError as e:\n"
       "            last_err = e\n"
       "        for attempt in range(1, _BIND_ATTEMPTS + 1):\n"
       "            if attempt < _BIND_ATTEMPTS:\n"
       "                logger.warning(\n"
       '                    "WebSocket 端口 %s 绑定失败（%d/%d 次：%s），1s 后重试",\n'
       "                    self._port, attempt, _BIND_ATTEMPTS, last_err)\n"
       "                await asyncio.sleep(1.0)\n")),

    # H 砍本轮修的句读缺陷：把补回去的句号又吃掉。
    # 注意第一版写成 `rstrip("。")  # 注释`，行尾注释把后面的 `, holder)` 一起吞了
    # —— 那是 SyntaxError，测试直接崩，`_judged` 拒绝算它抓住（崩了 ≠ 认出来了）。
    # 改成 `+ ""` 才是「语法正确、行为退回旧 bug」。
    ("H 两句话粘在一起（吃掉补回的句号）",
     B('str(last_err).rstrip("。") + "。"'),
     B('str(last_err).rstrip("。") + ""')),
]


def main() -> int:
    orig = open(P, "rb").read()
    h0 = hashlib.sha256(orig).hexdigest()
    bad = 0
    if not _baseline_green():
        print("FAIL 起点就不是绿的 —— 恒红的判定能让每把刀都变红，这种\"全红\"不证明任何事")
        return 1
    print("  PASS 起点绿")
    for name, old, new in MUTS:
        n = orig.count(old)
        if n != 1:
            print("[跳过] %s：锚点命中 %d 次（应恰好 1 次），锚点已腐烂" % (name, n))
            bad += 1
            continue
        open(P, "wb").write(orig.replace(old, new, 1))
        try:
            r = _run_test()
            caught = _judged(r)
            lines = [l.strip() for l in (r.stdout or "").splitlines() if l.strip()]
            hits = [l for l in lines if l.startswith("✗")]
            # 一把刀可能同时打中多条判据（C 会连坐四条诊断、G 会连坐「确有重试」）。
            # 只报第一条会让人以为它只打中一条，所以把命中的名字全列出来。
            names = [h[1:].split("：")[0].strip() for h in hits]
            why = ("%d 项：" % len(names)) + " / ".join(names) if names \
                else (lines[-1][:88] if lines else "<无输出>")
            why = why[:110]
            print(name)
            print("   -> " + ("已抓住：" + why if caught else "没抓住（测试失效）"))
            if not caught:
                bad += 1
        finally:
            open(P, "wb").write(orig)

    ok = hashlib.sha256(open(P, "rb").read()).hexdigest() == h0
    print("\n还原字节一致：%s" % ok)
    if not ok:
        bad += 1
    print("八刀全红" if bad == 0 else "有 %d 处异常" % bad)
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
