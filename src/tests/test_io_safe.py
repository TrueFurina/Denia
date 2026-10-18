"""src/io_safe.py 的行为测试（GBK/None 双态兜底）。

用子进程做真态验证：None 流只能靠真 pythonw/真赋值模拟，
进程内 monkeypatch 换不来「真 None 启动态」的语义。
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

_fails: list[str] = []


def check(cond: bool, msg: str) -> None:
    print(("  ✓ " if cond else "  ✗ ") + msg)
    if not cond:
        _fails.append(msg)


def _run_snippet(body: str, env_extra: dict | None = None) -> str:
    """在干净编码环境的子进程里跑片段，返回落盘报告。"""
    report = os.path.join(tempfile.mkdtemp(prefix="io_safe_t_"), "report.txt")
    code = f"""
import sys
report = open({report!r}, 'w', encoding='utf-8')
try:
{body}
except Exception as e:
    import traceback
    report.write('EXC: ' + type(e).__name__ + '\\n')
    traceback.print_exc(file=report)
report.close()
"""
    env = {k: v for k, v in os.environ.items()
           if k not in ("PYTHONUTF8", "PYTHONIOENCODING")}
    if env_extra:
        env.update(env_extra)
    subprocess.run([sys.executable, "-c", code], capture_output=True,
                   env=env, cwd=ROOT, timeout=120)
    with open(report, encoding="utf-8") as fh:
        return fh.read()


def main() -> int:
    print("[1] None 流（桌面双击 pythonw 真实态）→ 补成 UTF-8 devnull，rich 打 ✓ 不炸")
    r = _run_snippet("""
    sys.stdout = None
    sys.stderr = None
    from src.io_safe import ensure_safe_stdio
    ensure_safe_stdio()
    report.write(f'stdout={type(sys.stdout).__name__} '
                 f'enc={getattr(sys.stdout, "encoding", "?")}\\n')
    import rich.console
    rich.console.Console().print('[green]✓[/]')
    report.write('rich ok\\n')
""")
    check("EXC" not in r, "None 流下 rich print 无异常")
    check("enc=utf-8" in r, f"补上的流是 UTF-8（pywebview 的 GBK devnull 不再出现）: {r}")
    check("rich ok" in r, "rich 正常走完")

    print("[2] GBK 流（管道/重定向真实态）→ reconfigure 成 UTF-8，写 ✓ 不炸")
    r = _run_snippet("""
    import io
    sink = open({sink!r}, 'w', encoding='gbk')
    sys.stdout = sink
    from src.io_safe import ensure_safe_stdio
    ensure_safe_stdio()
    sys.stdout.write('✓中文\\n')
    sys.stdout.flush()
    report.write('write ok, enc=' + str(sys.stdout.encoding) + '\\n')
""".format(sink=os.path.join(tempfile.gettempdir(), "io_safe_gbk_sink.txt")))
    check("EXC" not in r and "write ok" in r, "GBK 流上写 ✓ 不炸")
    sink_path = os.path.join(tempfile.gettempdir(), "io_safe_gbk_sink.txt")
    with open(sink_path, encoding="utf-8") as fh:
        content = fh.read()
    check("✓中文" in content,
          f"流已转 UTF-8，✓ 与中文都完好落盘: {content!r}")

    print("[3] 正常 UTF-8 流 → 幂等不破坏")
    r = _run_snippet("""
    from src.io_safe import ensure_safe_stdio
    ensure_safe_stdio()
    enc1 = sys.stdout.encoding
    ensure_safe_stdio()
    report.write(f'enc={enc1}->{sys.stdout.encoding}\\n')
    sys.stdout.write('ok\\n')
""")
    check("EXC" not in r and "enc=utf-8->utf-8" in r, "幂等且流保持可用")

    print()
    if _fails:
        print(f"FAIL：{len(_fails)} 项未通过")
        return 1
    print("PASS：io_safe 三态验证成立")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
