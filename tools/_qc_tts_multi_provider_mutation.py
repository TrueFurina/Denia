"""变异刀：`TTS 多供应商` 闸门（tools/test_tts_multi_provider.py）的四把刀。

每把刀都真改生产代码，跑完立刻还原并校验 sha256。判据是「退出码非 0 **且** 指定判据被点名」。

    python tools/_qc_tts_multi_provider_mutation.py

背景（2026-11-10）：闸门 §3 外层是 `except Exception: print("! 跳过 …")`，**不计 FAIL** ——
实测让 `synthesize` 抛一个非 RuntimeError，整段（落盘 / 异常类型 / 工厂）会整段消失
（31 → 27 条判据），闸门仍 exit 0。这刀与「跳过 = 通过」的老坑同族。
"""
from __future__ import annotations

import hashlib
import io
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PY = sys.executable
GATE = os.path.join("tools", "test_tts_multi_provider.py")
BACKENDS = os.path.join("src", "voice", "tts_backends.py")
TTS = os.path.join("src", "voice", "tts.py")
CLOUD = os.path.join("src", "models", "cloud_tts.py")

CLASS_ANCHOR = "class FishAudioBackend(TTSBackend):"
SYNTH_ANCHOR = "    async def synthesize(self, text: str, output_path: str) -> None:"
NO_KEY_PREFIX = '            raise RuntimeError("Fish Audio '
WORKER_DONE = "    def _on_worker_done(self, task"
MERGE_ANCHOR = '    provider = (PROVIDER_ALIASES.get(provider, provider) or "").strip()'


def _inject_error_first_synth(text: str) -> str:
    i = text.index(CLASS_ANCHOR)
    j = text.index(SYNTH_ANCHOR, i)
    k = text.index("\n", j) + 1
    return text[:k] + '        raise ValueError("QC：合成前抛非 RuntimeError")\n' + text[k:]


def _drop_key_hint(text: str) -> str:
    i = text.index(CLASS_ANCHOR)
    j = text.index(NO_KEY_PREFIX, i)
    end = text.index("\n", j)
    return text[:j] + '            raise RuntimeError("boom")\n' + text[end + 1:]


def _no_restart(text: str) -> str:
    j = text.index(WORKER_DONE)
    k = text.index("\n", j) + 1
    return text[:k] + "        return  # QC：不再重启 worker\n" + text[k:]


def _wipe_buckets(text: str) -> str:
    j = text.index(MERGE_ANCHOR)
    return text[:j] + '    raw.setdefault("voice", {}).setdefault("clone", {})["cloud"] = {}  # QC\n' + text[j:]


# (名字, 文件, 变换, 必须被点名的判据片段)
MUTS = [
    ("A §3 抛非 RuntimeError（静默跳过回归）", BACKENDS, _inject_error_first_synth,
     "[3] 段必须真的跑完"),
    ("B 无 Key 的报错不再点名 Key", BACKENDS, _drop_key_hint,
     "无 key 应抛错（且消息点名缺 Key"),
    ("C worker 退出后不再重启", TTS, _no_restart,
     "done_callback 在 worker 异常退出时重启 worker"),
    ("D 合并服务商时清空整桶", CLOUD, _wipe_buckets,
     "合并后 dashscope 仍在"),
]


def sha(p: str) -> str:
    with open(p, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def run_gate() -> tuple[int, str]:
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    p = subprocess.run([PY, "-u", GATE], cwd=ROOT, capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=900, env=env)
    return p.returncode, (p.stdout or "") + (p.stderr or "")


def main() -> int:
    print("== 起点必须绿 ==")
    rc, out = run_gate()
    print(f"  基线退出码={rc}")
    if rc != 0:
        print("  起点不绿 → 后面的红都不可信，退出")
        print("\n".join(out.strip().splitlines()[-12:]))
        return 1

    failures = []
    for name, rel, fn, must in MUTS:
        path = os.path.join(ROOT, rel)
        with open(path, "rb") as f:
            original = f.read()
        h0 = hashlib.sha256(original).hexdigest()
        text = io.TextIOWrapper(io.BytesIO(original), encoding="utf-8", newline="").read()
        try:
            mutated = fn(text)
            if mutated == text:
                print(f"  ✗ {name}: 变换没有产生改动")
                failures.append(name)
                continue
            with open(path, "wb") as f:
                f.write(mutated.encode("utf-8"))
            rc, out = run_gate()
        finally:
            with open(path, "wb") as f:
                f.write(original)
            restored = sha(path) == h0

        named = must in out
        ok = rc != 0 and named and restored
        print(f"  {'✓' if ok else '✗'} {name}: 退出码={rc} 点名={named} 还原一致={restored}")
        if not ok:
            failures.append(name)

    print(f"\nMUTATION_RESULT: {'ALL PASS' if not failures else 'FAILED ' + str(failures)}")
    return 0 if not failures else 1


if __name__ == "__main__":
    sys.exit(main())
