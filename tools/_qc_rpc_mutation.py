# -*- coding: utf-8 -*-
"""「RPC 通道」的变异验证：十刀，刀刀必须被抓住。

跑法：.venv/Scripts/python.exe -u tools/_qc_rpc_mutation.py

这道门守的是壳 ↔ 后端之间那条**没有类型检查**的通道：19 条等价性用例 + 安全边界。
它的失效形状和 WS 端口那道不一样 —— 端口绑定坏了是「起不来」，这条通道坏了是
**照样能起、照样返回 200，只是返回的东西不对**，或者更糟：**私有方法能调到**。

**这道门此前一次都没被变异验过**（`ls tools/_qc_*rpc*` → No such file）。

本轮先读判据，读到两处空转（都是实测，不是推测），而且它们在同一条链路上：

  1. `all(_is_exposed(p) for p in method.split("."))` → `_is_exposed(method)`
     （整串检查）→ 自测**照样全绿 exit 0**。而那个退化正是**点号绕过**：
     `sub._secret` 整串不以 `_` 开头，校验通过，私有方法被调走。
  2. `_resolve` 换成 `getattr(api, dotted, None)`（不再支持点号）→ 同样全绿。

根因：19 条等价性用例**全是单段名字**，而单段名字上「逐段检查」与「整串检查」
完全等价、「点号循环」跑一轮和直接 getattr 完全等价。**判据只覆盖了退化不产生
差别的那个输入域。**

所以本轮先补判据（子对象 `sub` + `sub.ping` 等价性 + 四段点号安全检查），十刀
才有东西可砍。**先有判据，才有刀。**

另外把参照实现也换了：等价性原来用 `getattr(api, method)` 直调，遇到点号会
AttributeError。改成 `_direct_call()` 自己走一遍点号 —— **不能拿 `_resolve`
当参照**，它坏了参照一起坏，等价性永远成立。
"""
import hashlib
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(ROOT)

P = "src/ui/rpc_server.py"
TEST = "src/tests/test_rpc_server.py"
# 这道门要真起一个 HTTP 服务（stdlib 即可），但 `_Sub` / `FakeAPI` 用不到三方包；
# 有 .venv 就用它，没有就退回当前解释器。
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
    # A 是安全边界的地基：令牌校验恒真。缺令牌 / 错令牌 / 长连接 4.1 三条会同时红。
    ("A 令牌校验恒真（谁都能调）",
     B("        return bool(self.token) and secrets.compare_digest(got, self.token)\n"),
     B("        return True  # 变异刀：不再校验令牌\n")),

    # B 是本轮的主角：逐段检查退化成整串检查。单段名字上两者等价，所以老用例
    # 一条都抓不住；只有 `sub._secret` 能区分 —— 而那个输入此前不存在。
    ("B 逐段名字检查退化成整串检查（点号绕过）",
     B('        if not method or not all(_is_exposed(p) for p in method.split(".")):\n'),
     B("        if not method or not _is_exposed(method):  # 变异刀：整串检查\n")),

    # C 砍点号解析：注释承诺「支持子对象」，但 19 条用例没有一条带点。
    ("C _resolve 不再支持点号（子对象调不到）",
     B("    obj = api\n"
       '    for part in dotted.split("."):\n'
       "        if not part:\n"
       "            return None\n"
       "        obj = getattr(obj, part, None)\n"
       "        if obj is None:\n"
       "            return None\n"
       "    return obj\n"),
     B("    return getattr(api, dotted, None)  # 变异刀：不再拆点号\n")),

    # D 砍长连接卫生：拒绝分支不排空请求体，残留字节被当成下一个请求
    # （2026-10 的真实故障：401 之后整条连接全错，Windows 上表现为 WinError 10053）。
    ("D 拒绝分支不排空请求体（污染长连接）",
     B("        if not self._drain_body():\n            self.close_connection = True\n"),
     B("        if False:  # 变异刀：不排空\n            self.close_connection = True\n")),

    ("E 去掉请求体上限（MAX_BODY 形同虚设）",
     B("        if length > MAX_BODY:\n"),
     B("        if False:  # 变异刀：不查上限\n")),

    ("F 不可 JSON 化返回值不再标记 degraded",
     B('            self._send_json({"ok": True, "result": result, "degraded": True})\n'),
     B('            self._send_json({"ok": True, "result": result})\n')),

    ("G stop() 变成空操作（端口不释放）",
     B("    def stop(self) -> None:\n        if self._httpd:\n"),
     B("    def stop(self) -> None:\n        if False:  # 变异刀：不停服务\n")),

    # H 把业务异常当成功返回：前端会拿到 200 + 半截结果，比报错更难查。
    ("H 业务异常改回 200（吞掉 500）",
     B("            }, 500)\n"),
     B("            }, 200)  # 变异刀：异常当成功\n")),

    # I 与 J 砍的是同一行的两个条件，各自对应一条判据：
    #   I → `sub.a__b`（名字中间带 dunder）
    #   J → `sub..ping`（空段）
    ("I 去掉中间 dunder 限制",
     B('    return bool(name) and not name.startswith("_") and "__" not in name\n'),
     B('    return bool(name) and not name.startswith("_")  # 变异刀\n')),

    ("J 去掉空段限制",
     B('    return bool(name) and not name.startswith("_") and "__" not in name\n'),
     B('    return not name.startswith("_") and "__" not in name  # 变异刀\n')),
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
            # 一把刀可能同时打中多条判据（A 会连坐三条、B 会连坐三条）。
            # 只报第一条会让人以为它只打中一条，所以把命中的名字全列出来。
            names = [h[1:].split("（")[0].strip() for h in hits]
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
    print("十刀全红" if bad == 0 else "有 %d 处异常" % bad)
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
