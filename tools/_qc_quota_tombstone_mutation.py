#!/usr/bin/env python
"""额度墓碑三层判据的变异刀（2026-10-22）。

2026-10-22 删掉了三段死代码：webapp 的注册点、只打日志的存根方法、前端
quota_alert 浮层与广播分支。删之前它是"三段各自看着都在工作、合起来是死的"：

    Router 有回调槽位（set_quota_alert_callback）但 _fire_quota_alert 不调；
    webapp 注册了回调，但存根只 logger.info，从不广播；
    前端有浮层、有 quota_alert 分支，但后端广播 type 全集里从来没有它。

test_quota_alert.py 因此有两层墓碑（Router 层 + 产品层）。**只测 Router 层不够**：
注册点写着"主动广播给前端弹询问浮层"，谁把它加回来，Router 层那条仍然绿，
而主动打扰已经铺回产品里了。

本脚本验三刀：
  M1 把注册点加回 webapp.py        -> 产品层判据必须红
  M2 把 quota_alert 分支加回前端   -> 前端判据必须红
  M3 只在注释里提这两个名字        -> 必须仍然全绿（证明剥注释真的在工作）

M3 不是走过场：删代码时按本仓惯例在原处留了"为什么删"的注释，注释里就含
_on_vision_quota_alert。若不剥注释，这两条判据会被自己的注释满足——同一坑在
check_bridge_guards 上踩过（那边 M4 对照组第一次跑就是红的）。

用法：python tools/_qc_quota_tombstone_mutation.py
退出码 0 = 三刀都按预期红/绿。
"""

import io
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WEBAPP = os.path.join(ROOT, "src", "ui", "webapp.py")
APPD = os.path.join(ROOT, "src", "ui", "web", "redesign", "app_backend.js")
TEST = os.path.join(ROOT, "src", "channels", "tests", "test_quota_alert.py")
PY = os.path.join(ROOT, ".venv", "Scripts", "python.exe")

# 本仓这三个文件工作区里都是 CRLF（.gitattributes 固定 eol=lf 是入库时的事）
EOL = b"\r\n"

CASE_PY = "test_product_code_does_not_register_proactive_popup"
CASE_JS = "test_frontend_has_no_quota_alert_branch"


def _enc(s):
    """中文锚点走这里：bytes 字面量不许含非 ASCII。"""
    return s.encode("utf-8")


def _read(p):
    with io.open(p, "rb") as f:
        return f.read()


def _write(p, data):
    with io.open(p, "wb") as f:
        f.write(data)


def run_test():
    """跑额度告警测试，返回 (退出码, 输出)。

    不接管道：退出码必须是 python 的，不是 tail 的（本仓踩过）。
    """
    out_path = os.path.join(ROOT, "logs", "_qc_quota_tombstone.out")
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with io.open(out_path, "wb") as fo:
        proc = subprocess.run([PY, TEST], stdout=fo, stderr=subprocess.STDOUT,
                              cwd=ROOT)
    with io.open(out_path, "r", encoding="utf-8", errors="replace") as fi:
        return proc.returncode, fi.read()


def _red_lines(out):
    return [l for l in out.splitlines() if "FAIL:" in l or "ERROR:" in l]


def case_failed(out, case):
    """输出里是不是**这条用例**红的（不能只看退出码——可能是别的原因红）。"""
    return bool(_red_lines(out)) and case in out


def main():
    bad = 0
    base_web = _read(WEBAPP)
    base_js = _read(APPD)

    # 基线必须自己是绿的，否则后面三刀的结论都不可信
    code, out = run_test()
    if code != 0:
        print("  FAIL 基线就不绿，先查测试本身：")
        for l in _red_lines(out)[:8]:
            print("    " + l[:160])
        return 1
    print("  基线全绿")

    # ---------------- M1：注册点加回 ----------------
    anchor = b"            self._activity_reporter = activity_reporter" + EOL
    inject = (
        b"            try:" + EOL
        + b'                if vision_router is not None and hasattr(vision_router, "set_quota_alert_callback"):' + EOL
        + b"                    vision_router.set_quota_alert_callback(self._on_vision_quota_alert)" + EOL
        + b"            except Exception:  # noqa: BLE001" + EOL
        + b'                logger.debug("x", exc_info=True)' + EOL
    ) + anchor
    if base_web.count(anchor) != 1:
        print("  FAIL M1：锚点命中 %d 次（应为 1）" % base_web.count(anchor))
        bad += 1
    else:
        _write(WEBAPP, base_web.replace(anchor, inject))
        try:
            code, out = run_test()
        finally:
            _write(WEBAPP, base_web)
        if code == 0:
            print("  FAIL M1 注册点加回：测试仍绿（产品层墓碑没拦）")
            bad += 1
        elif not case_failed(out, CASE_PY):
            print("  FAIL M1：红了但不是 %s 红的" % CASE_PY)
            for l in _red_lines(out)[:6]:
                print("    " + l[:160])
            bad += 1
        else:
            print("  PASS M1 注册点加回：%s 红了" % CASE_PY)

    # ---------------- M2：quota_alert 分支加回 ----------------
    # 锚点必须选在**块注释结束之后**：第一版选的是注释块中间那一行，注入的代码
    # 落在了 /* ... */ 内部，被剥注释正确剔除 → 判据绿是**对的**，刀却报 FAIL。
    # 变异刀要模拟的是"代码里用"，不是"注释里提"（后者由 M3 对照组负责）。
    anchor_js = _enc('             一起成了死代码。额度信息改随"那次失败消息"给出，见 test_quota_alert。 */') + EOL
    inject_js = anchor_js + (
        b"          else if (d.type === 'quota_alert') { if (window.__quotaAlert) window.__quotaAlert(d); }" + EOL
    )
    if base_js.count(anchor_js) != 1:
        print("  FAIL M2：锚点命中 %d 次（应为 1）" % base_js.count(anchor_js))
        bad += 1
    else:
        _write(APPD, base_js.replace(anchor_js, inject_js))
        try:
            code, out = run_test()
        finally:
            _write(APPD, base_js)
        if code == 0:
            print("  FAIL M2 quota_alert 分支加回：测试仍绿（前端墓碑没拦）")
            bad += 1
        elif not case_failed(out, CASE_JS):
            print("  FAIL M2：红了但不是 %s 红的" % CASE_JS)
            for l in _red_lines(out)[:6]:
                print("    " + l[:160])
            bad += 1
        else:
            print("  PASS M2 quota_alert 分支加回：%s 红了" % CASE_JS)

    # ---------------- M3：对照组——只在注释里提名字 ----------------
    anchor_c = _enc("            # 判据：src/channels/tests/test_quota_alert.py 的两层墓碑。") + EOL
    inject_c = anchor_c + (
        _enc("            # 对照组：注释里提 set_quota_alert_callback / _on_vision_quota_alert") + EOL
        + _enc("            # 与 quota_alert 都不算违规——判据剥掉了注释与字符串。") + EOL
    )
    anchor_js2 = _enc('             一起成了死代码。额度信息改随"那次失败消息"给出，见 test_quota_alert。 */') + EOL
    inject_js2 = anchor_js2 + _enc("          /* 对照组：注释里提 quota_alert 不算违规。 */") + EOL
    ok3 = True
    if base_web.count(anchor_c) != 1:
        print("  FAIL M3：webapp 锚点命中 %d 次" % base_web.count(anchor_c))
        ok3 = False
    if base_js.count(anchor_js2) != 1:
        print("  FAIL M3：app_backend 锚点命中 %d 次" % base_js.count(anchor_js2))
        ok3 = False
    if ok3:
        _write(WEBAPP, base_web.replace(anchor_c, inject_c))
        _write(APPD, base_js.replace(anchor_js2, inject_js2))
        try:
            code, out = run_test()
        finally:
            _write(WEBAPP, base_web)
            _write(APPD, base_js)
        if code != 0:
            print("  FAIL M3 只在注释里提名字：测试却红了（判据没剥注释，会被自己的注释满足）")
            for l in _red_lines(out)[:6]:
                print("    " + l[:160])
            bad += 1
        else:
            print("  PASS M3 对照组：注释里提这两个名字仍全绿")

    # ---------------- 还原校验 ----------------
    if _read(WEBAPP) != base_web or _read(APPD) != base_js:
        print("  FAIL 文件没有还原到原字节！")
        bad += 1
    else:
        print("  两个文件均已字节级还原")

    print("=" * 60)
    if bad:
        print("变异刀发现 %d 处问题" % bad)
        return 1
    print("三刀全部符合预期")
    return 0


if __name__ == "__main__":
    sys.exit(main())
