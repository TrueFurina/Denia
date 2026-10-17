# -*- coding: utf-8 -*-
"""回滚后重新生成克隆音频并体检，验证"能正常说话"。

用法：
  python _regen_after_rollback.py            # 生成
  python _regen_after_rollback.py --check    # 只对已有文件体检
"""
import os
import sys
import json
import time
import urllib.request

sys.path.insert(0, r"H:\daniya_voice_tools\client_venv\Lib\site-packages")

PORT = int(os.environ.get("COSY_PORT", "9883"))
URL = f"http://127.0.0.1:{PORT}"
REF = r"G:\ai对话\11\assets\voices\daniya_ref.wav"
OUT = r"G:\ai对话\11\data\after_rollback_16k"

CASES = [
    ("r01_jintian", "今天过得怎么样？", None),
    ("r02_zhongyu", "你终于来啦。", None),
    ("r03_zhejian", "这件事我想了想，还是觉得应该告诉你。", None),
]


def opener_noproxy():
    return urllib.request.build_opener(urllib.request.ProxyHandler({}))


def health():
    op = opener_noproxy()
    try:
        with op.open(URL + "/health", timeout=5) as r:
            return json.loads(r.read().decode("utf-8"))
    except Exception as e:
        return {"_err": repr(e)}


def tts(text, out_path, instruct=None, ref=REF):
    op = opener_noproxy()
    body = {"text": text, "ref_wav_path": ref, "stream": False}
    if instruct:
        body["instruct"] = instruct
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(URL + "/tts", data=data,
                                 headers={"Content-Type": "application/json"})
    t0 = time.time()
    with op.open(req, timeout=600) as r:
        raw = r.read()
    dt = time.time() - t0
    with open(out_path, "wb") as f:
        f.write(raw)
    return dt, len(raw)


def main():
    os.makedirs(OUT, exist_ok=True)
    if "--check" not in sys.argv:
        h = health()
        print("HEALTH", h)
        if "_err" in h:
            print("服务未就绪，请先启动 cosyvoice_server.py")
            return 2
        for name, text, ins in CASES:
            p = os.path.join(OUT, name + ".wav")
            try:
                dt, n = tts(text, p, ins)
                print(f"OK {name} {dt:5.1f}s {n:8d}B  <- {text[:20]}")
            except Exception as e:
                print(f"FAIL {name}: {e!r}")
    # 体检
    import numpy as np
    import soundfile as sf

    def stat(p):
        x, sr = sf.read(p, always_2d=False)
        if x.ndim > 1:
            x = x.mean(axis=1)
        x = x.astype(np.float64)
        n = 1024
        if len(x) < n:
            return None
        idx = np.arange(0, len(x) - n, 256)
        F = np.stack([x[i:i + n] for i in idx]) * np.hanning(n)[None, :]
        P = np.abs(np.fft.rfft(F, axis=1)) ** 2
        fr = np.fft.rfftfreq(n, 1.0 / sr)
        tot = P.sum(axis=1) + 1e-12
        cen = float(np.median((P * fr).sum(axis=1) / tot))
        flat = float(np.median(np.exp(np.mean(np.log(P + 1e-12), axis=1)) /
                               (np.mean(P, axis=1) + 1e-12)))
        cum = np.cumsum(P, axis=1) / (P.sum(axis=1, keepdims=True) + 1e-12)
        f95 = float(np.median(fr[(cum >= 0.95).argmax(axis=1)]))
        rms = float(np.sqrt(np.mean(x ** 2) + 1e-20))
        return dict(dur=len(x) / sr, sr=sr, cen=cen, flat=flat, f95=f95,
                    rms_db=20 * np.log10(rms + 1e-20))

    print()
    print(f"{'file':<22}{'dur':>7}{'cen':>8}{'flat':>9}{'f95':>7}{'rmsdB':>8}")
    files = [os.path.join(OUT, f) for f in sorted(os.listdir(OUT)) if f.endswith(".wav")]
    for p in files:
        st = stat(p)
        if st:
            print(f"{os.path.basename(p)[:20]:<22}{st['dur']:>6.2f}s{st['cen']:>8.0f}"
                  f"{st['flat']:>9.4f}{st['f95']:>7.0f}{st['rms_db']:>8.1f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
