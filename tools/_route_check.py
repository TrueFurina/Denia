# -*- coding: utf-8 -*-
import sys, asyncio, traceback
sys.path.insert(0, "G:/ai对话/11")
OUT = "G:/ai对话/11/docs/_route_check.txt"
lines = []
try:
    from src.config import load_config
    from src.llm import create_llm

    cfg = load_config("G:/ai对话/11/config.yaml")
    router = create_llm(cfg)
    lines.append("router_type=%s" % type(router).__name__)
    lines.append("mode=%s manual_id=%s" % (getattr(router, "_mode", "?"), getattr(router, "_manual_id", "?")))
    lines.append("entries=%s" % [(e.get("id"), e.get("model")) for e in getattr(router, "_entries", [])])

    msg = [{"role": "user", "content": "用一句话跟我打个招呼，你是桌面伴侣。"}]
    reply = asyncio.run(router.chat(msg, {"temperature": 0.7}))
    lines.append("reply=%s" % (reply or "")[:500])
    try:
        lines.append("last_used=%s" % router.last_used_info())
    except Exception as e:
        lines.append("last_used_err=%r" % e)
    try:
        lines.append("model_name=%s" % router.model_name)
    except Exception as e:
        lines.append("model_name_err=%r" % e)
except Exception:
    lines.append("FATAL:\n" + traceback.format_exc())

with open(OUT, "w", encoding="utf-8") as f:
    f.write("\n".join(lines))
