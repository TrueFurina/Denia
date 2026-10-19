# -*- coding: utf-8 -*-
"""一次性探针：实测本机环境变量里的各家免费 LLM 渠道今天还能不能用。

只做一件事：发一条最小 chat 请求，报告 HTTP 状态 / 错误摘要 / 耗时。
key 一律从环境变量读，不落盘、不打印。
"""
import json
import os
import time
import urllib.error
import urllib.request

CASES = [
    ("智谱 glm-4-flash", "ZHIPU_API_KEY",
     "https://open.bigmodel.cn/api/paas/v4/chat/completions", "glm-4-flash"),
    ("智谱2 glm-4.7-flash", "ZHIPU_API_KEY_2",
     "https://open.bigmodel.cn/api/paas/v4/chat/completions", "glm-4.7-flash"),
    ("ModelScope GLM-4.7-Flash", "MODELSCOPE_API_KEY",
     "https://api-inference.modelscope.cn/v1/chat/completions", "ZhipuAI/GLM-4.7-Flash"),
    ("ModelScope DS-V4-Flash", "MODELSCOPE_API_KEY",
     "https://api-inference.modelscope.cn/v1/chat/completions",
     "deepseek-ai/DeepSeek-V4-Flash-0731"),
    ("TokenHub glm-5.3-flash", "TOKENHUB_API_KEY",
     "https://tokenhub.tencentmaas.com/v1/chat/completions", "glm-5.3-flash"),
    ("AMD DeepSeek-V4-Flash", "AMD_API_KEY",
     (os.environ.get("AMD_BASE_URL", "") or "").rstrip("/") + "/chat/completions",
     os.environ.get("AMD_MODEL", "DeepSeek-V4-Flash")),
    ("DeepSeek 官方 deepseek-flash", "DEEPSEEK_API_KEY",
     "https://api.deepseek.com/v1/chat/completions", "deepseek-flash"),
    ("讯飞 spark lite", "XFYUN_API_KEY",
     "https://spark-api-open.xf-yun.com/v1/chat/completions", "lite"),
    ("阿里云百炼 qwen-plus", "DASHSCOPE_API_KEY",
     "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions", "qwen-plus"),
]


def probe(name: str, env: str, url: str, model: str) -> tuple[str, str]:
    key = os.environ.get(env, "")
    if not key:
        return "NOENV", f"{env} 未设置"
    if not url.startswith("http"):
        return "NOURL", f"base_url 缺失：{url!r}"
    body = json.dumps({
        "model": model,
        "messages": [{"role": "user", "content": "只回复：OK"}],
        "max_tokens": 8,
        "stream": False,
    }).encode("utf-8")
    req = urllib.request.Request(url, data=body, method="POST", headers={
        "Content-Type": "application/json",
        "Authorization": f"Bearer {key}",
    })
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=25) as r:
            raw = r.read().decode("utf-8", "replace")
            dt = int((time.time() - t0) * 1000)
            try:
                d = json.loads(raw)
                txt = (d.get("choices") or [{}])[0].get("message", {}).get("content", "")
            except Exception:
                txt = raw[:60]
            return "OK", f"{dt}ms 回复={str(txt)[:20]!r}"
    except urllib.error.HTTPError as e:
        dt = int((time.time() - t0) * 1000)
        raw = e.read().decode("utf-8", "replace")
        try:
            d = json.loads(raw)
            msg = (d.get("error") or {}).get("message") or d.get("message") or raw
        except Exception:
            msg = raw
        return f"HTTP {e.code}", f"{dt}ms {str(msg)[:110]}"
    except Exception as e:  # noqa: BLE001
        dt = int((time.time() - t0) * 1000)
        return "ERR", f"{dt}ms {type(e).__name__}: {str(e)[:90]}"


def main() -> int:
    ok = 0
    for name, env, url, model in CASES:
        status, detail = probe(name, env, url, model)
        if status == "OK":
            ok += 1
        print(f"[{status:10s}] {name:26s} {detail}")
    print(f"\n可用 {ok}/{len(CASES)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
