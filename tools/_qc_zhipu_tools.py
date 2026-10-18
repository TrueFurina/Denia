# -*- coding: utf-8 -*-
"""一次性探针：智谱两档免费模型是否支持 function calling（agent/工具链要用）。"""
import json
import os
import time
import urllib.request

URL = "https://open.bigmodel.cn/api/paas/v4/chat/completions"
KEY = os.environ.get("ZHIPU_API_KEY", "")
TOOLS = [{
    "type": "function",
    "function": {
        "name": "get_time",
        "description": "取当前时间",
        "parameters": {"type": "object", "properties": {}, "required": []},
    },
}]


def probe(model: str) -> None:
    body = json.dumps({
        "model": model,
        "messages": [{"role": "user", "content": "现在几点了？用工具查。"}],
        "tools": TOOLS,
        "max_tokens": 120,
        "temperature": 0.3,
    }).encode("utf-8")
    req = urllib.request.Request(URL, data=body, method="POST", headers={
        "Content-Type": "application/json", "Authorization": f"Bearer {KEY}"})
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            d = json.loads(r.read().decode("utf-8", "replace"))
        dt = int((time.time() - t0) * 1000)
        msg = (d.get("choices") or [{}])[0].get("message", {})
        tc = msg.get("tool_calls") or []
        print(f"[OK  ] {model:16s} {dt:5d}ms tools={len(tc)} "
              f"content={str(msg.get('content'))[:30]!r} "
              f"reasoning={'有' if msg.get('reasoning_content') else '无'}")
    except Exception as e:  # noqa: BLE001
        dt = int((time.time() - t0) * 1000)
        print(f"[FAIL] {model:16s} {dt:5d}ms {type(e).__name__}: {str(e)[:100]}")


if __name__ == "__main__":
    if not KEY:
        raise SystemExit("ZHIPU_API_KEY 未设置")
    for m in ("glm-4-flash", "glm-4.7-flash", "glm-4.5-air", "glm-4.5-flash"):
        probe(m)
