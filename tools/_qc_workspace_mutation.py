# -*- coding: utf-8 -*-
"""「工作区边界」的变异验证：六刀，刀刀必须被抓住。

跑法：.venv/Scripts/python.exe -u tools/_qc_workspace_mutation.py
"""
import hashlib
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(ROOT)

P = "src/llm/agent_tools.py"
TEST = "tools/test_workspace_guard.py"
PY = ".venv/Scripts/python.exe"


# 【2026-11-08 修·刀自己会空转】原来只判 `r.returncode != 0`，于是判定文件**崩了**
# 也被算成"抓住"。实测：把 `tools/test_workspace_guard.py` 整个换成一句 `raise RuntimeError("自己崩了")`，
# 本刀仍然逐条打印「已抓住：<无输出>」、最后打印「N 刀全红」并 exit 0 ——
# **一个连第一行都跑不完的判定，被认证成"全抓"**。
# 崩了和识别是两件事：前者说明判定没跑，后者才说明判据在工作。
# 起点也必须先绿：恒红的判定能让**每一把**刀都变红，那种"全红"什么都不证明。
def _judged(r) -> bool:
    """判定文件是「判出了问题」而不是「崩了」或「什么都没说」。

    【2026-11-08 第二版·第一版太严】第一版还要求「输出里没有 Traceback」，
    实测把**先印 FAIL、随后收尾时崩掉**的判定判成了"没抓住"：
      · `_qc_asr_mutation` ①②：印了 `FAIL lang='zh-cn'`，随后 vosk 桩抛
        `AttributeError: 'Model' object has no attribute '_handle'`
      · `_qc_config_bootstrap_mutation` B：印了 `FAIL 状态 created`，随后读临时
        `config.yaml` 抛 `FileNotFoundError`
    两次都是**仪器太窄**（本轮第三次）。真正的判别是**有没有点名**：
    退化桩只印一个 `exit 1`，一个字都没有 —— 那才叫"什么都没说"。
    """
    out = (r.stdout or "") + (r.stderr or "")
    return r.returncode != 0 and ("FAIL" in out or "✗" in out)


def _run_test():
    return subprocess.run([PY, "-u", TEST], capture_output=True, text=True,
                          encoding="utf-8", errors="replace")


def _baseline_green() -> bool:
    r = _run_test()
    out = (r.stdout or "") + (r.stderr or "")
    # 【2026-11-08 修·这一行比判定本身还严】原来要求"输出里没有 Traceback"。
    # 但**判定自己故意打出来的** Traceback 也算——比如 `test_presence_controls.py`
    # 的 A3 就故意让回调抛异常，再验证 listener 没被弄死，那一段会经 logger 打到
    # stderr。于是测试 41 PASS / 0 FAIL / exit 0，本函数却判"起点不是绿的"，
    # 整把刀一进来就自爆，而报错信息把矛头指向**产品**。
    # 判据改成"退出码 0 且没有 FAIL 行"：崩了的判定一定非零退出，
    # Traceback 本身不能证明任何事。同一轮 `_judged()` 也是这么改的。
    return r.returncode == 0 and not any(l.strip().startswith("FAIL")
                                         for l in out.splitlines())



def B(s: str) -> bytes:
    return s.encode("utf-8")


MUTS = [
    ("A 越界不拦（回到「想写哪就写哪」）",
     B("    if not inside:\n"),
     B("    if False:\n")),

    ("B 不解析符号链接（链接指向区外就能逃出去）",
     B("    cand = os.path.realpath(cand)\n"),
     B("    cand = cand\n")),

    ("C 不检查空路径（没给名字就落到工作区根）",
     B('    if not raw:\n        raise WorkspaceEscape("未提供文件名")\n'),
     B("")),

    # 【2026-11-09 加·补的是判定里最要命的那一条】上面三把刀砍的都是
    # `_safe_path` 本身（抛不抛、归一不归一、空不空）。但 `test_workspace_guard.py`
    # 第 6 节守的是另一件事：**越界之后不许落盘**。判据里那三行
    #   · 返回里说明越界
    #   · 区外文件没有被创建
    #   · 沙箱里也没多出这个文件      ← 这一条专门钉 2026-10 那个 bug
    # 之前没有任何刀指着它。也就是说：如果有人把 `_write_file` 的 except 分支
    # 改回「改名塞进沙箱再回报已写入」，四把刀里前三把**全是绿的** —— 因为
    # `_safe_path` 一个字节都没动。用户会以为改的是 `../外面/x.txt`，
    # 实际写进了工作区，而且回报「已写入」。
    # 刀的形状就是那个 bug 的形状：except 分支不返回错误，改成重写到沙箱根。
    ("D 越界时改名写进沙箱（2026-10 那个 bug 的形状）",
     B('        return str(e)\n    try:\n'
       '        with open(p, "w", encoding="utf-8") as f:\n'),
     B('        p = os.path.join(workspace_dir(), os.path.basename(name))'
       '  # 变异刀：越界就改名写进沙箱\n    try:\n'
       '        with open(p, "w", encoding="utf-8") as f:\n')),

    # 【2026-11-09 加·同一类越界的另外两个兄弟】`_read_file` / `_append_file`
    # 和 `_write_file` 是同一段形状的三份复制粘贴。判据（第 6b 节）刚补上，
    # 刀也要跟着补 —— 否则"补了判据"只是一句话，谁也不能保证它以后还能开口。
    # E 砍的是**读**：越界不返回错误，改成拼到工作区再读一次。
    #   这是三把刀里后果最重的一把 —— 写坏一个文件是脏数据，读穿了是
    #   **任意文件读取**：密钥盒、config.yaml、导出的凭据都能进模型上下文。
    ("E 读越界也照读（任意文件读取）",
     B('        return str(e)\n    try:\n        if not os.path.exists(p):\n'),
     B('        p = os.path.abspath(os.path.join(workspace_dir(), name))'
       '  # 变异刀：越界也照读\n    try:\n'
       '        if not os.path.exists(p):\n')),

    # F 砍的是**追加**：2026-10 那个 bug 在 append 分支上的同形复制。
    # 写和追加走的是两个分支，D 刀红不能证明 F 刀也红 —— 这正是要各砍一刀的理由。
    ("F 追加越界改名写进沙箱",
     B('        return str(e)\n    try:\n'
       '        with open(p, "a", encoding="utf-8") as f:\n'),
     B('        p = os.path.join(workspace_dir(), os.path.basename(name))'
       '  # 变异刀：越界就改名写进沙箱\n    try:\n'
       '        with open(p, "a", encoding="utf-8") as f:\n')),
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
            print("[跳过] %s：锚点命中 %d 次" % (name, n))
            bad += 1
            continue
        open(P, "wb").write(orig.replace(old, new, 1))
        try:
            r = subprocess.run([PY, "-u", TEST], capture_output=True,
                               text=True, encoding="utf-8", errors="replace")
            caught = _judged(r)
            lines = [l for l in (r.stdout or "").splitlines() if l.strip()]
            why = [l for l in lines if l.strip().startswith("FAIL")]
            why = (why or lines or ["<无输出>"])[-1].strip()[:88]
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
    print("六刀全红" if bad == 0 else "有 %d 处异常" % bad)
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
