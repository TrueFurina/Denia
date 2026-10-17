# -*- coding: utf-8 -*-
"""主动互动包 · 「强生成器 + 关思考」公平对照（真实调用）。

上一轮探针暴露：glm-4.5/4.7 是推理模型，当生成器**不关思考**时会把思维链
（"首先，用户要求我作为达妮娅…"）直接说出口，体验灾难。本脚本做公平对照：
把强生成器的思考关掉，看"聪明生成 + 关思考"是否真的比弱生成 glm-4-flash 更好。

对照：gen = glm-4-flash(基线, 非推理) vs glm-4.5-flash(关思考) vs glm-4.7-flash(关思考)
统一审查 = glm-4.5-flash(关思考)。每个生成器 × 3 场景 × 2 次；最后用强模型做用户视角评分。
"""
from __future__ import annotations

import asyncio
import json
import logging
import sys
import tempfile
import time
import types
import urllib.request
from pathlib import Path

ROOT = r"G:\ai对话\11"
sys.path.insert(0, ROOT)
from src.proactive_pack import judge as judge_mod            # noqa: E402
from src.proactive_pack.pack import ProactivePack            # noqa: E402
from src.proactive_pack import prompts as prompts_mod        # noqa: E402

logging.basicConfig(level=logging.WARNING)
log = logging.getLogger("probe2")

ZHIPU_KEY = "ae1bd969f017416a8a0a725d48fcf9f7.mUTU7jBoatpll5c9"
ZHIPU_URL = "https://open.bigmodel.cn/api/paas/v4/chat/completions"


class ZhipuLLM:
    def __init__(self, model, max_tokens=140, think_off=False, timeout=40.0):
        self.model = model
        self.max_tokens = max_tokens
        self.extra = {"thinking": {"type": "disabled"}} if think_off else {}
        self.timeout = timeout

    async def chat(self, messages, options=None):
        prompt = "\n".join(str(m.get("content") or "") for m in messages)
        opts = options or {}
        body = json.dumps({
            "model": self.model, "messages": [{"role": "user", "content": prompt}],
            "stream": False, "max_tokens": int(opts.get("max_tokens") or self.max_tokens),
            "temperature": float(opts.get("temperature", 0.0)), **self.extra,
        }).encode("utf-8")
        req = urllib.request.Request(ZHIPU_URL, data=body,
            headers={"Authorization": "Bearer " + ZHIPU_KEY, "Content-Type": "application/json"})
        def _call():
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                d = json.loads(r.read().decode("utf-8"))
            m = (d["choices"][0]["message"].get("content")
                 or d["choices"][0]["message"].get("reasoning_content") or "").strip()
            return m
        for a in range(3):
            try:
                return (await asyncio.to_thread(_call) or "").strip()
            except Exception as e:  # noqa: BLE001
                log.warning("[probe2] %s 失败(%s)", self.model, e)
                await asyncio.sleep(3 * (a + 1))
        return ""


MATERIAL = (
    "现在：晚上 21:30（周二）\n距上次互动：过了一夜（约 20.0 小时）\n"
    "你们已经聊过约 26 轮，最早一次在 41 天前。\n"
    "更早之前聊过的事（可以自然回指，但只能引用这里写到的）：\n"
    "- 我最近在学做菜，昨天第一次煎牛排还行\n- 周末想去看那部新上映的科幻片\n"
    "最近几轮对话：\n用户：最近在忙那个小程序，快上线了\n达妮娅：那挺好啊，别熬太晚\n用户：嗯，今晚打算早点睡"
)
SCENARIOS = [
    ("silence", "用户离开挺久，想自然招呼一声（带锚点）", MATERIAL),
    ("followup", "刚才聊到小程序快上线了，想接着问一句", MATERIAL),
    ("rhythm", "快到晚饭点了，想随口关心一句（无旧材料）",
     "现在：晚上 18:40（周二）\n距上次互动：约 3 小时"),
]


class Host:
    def __init__(self):
        self._cfg = types.SimpleNamespace(active_character_id="danya")
        self._persona = types.SimpleNamespace(name="达妮娅")
        self._idle_threshold_s = 180.0
        self._last_input_ts = time.time() - 20 * 3600
        self.emitted = []
    async def _schedule_duplex_emit(self, text, **k): self.emitted.append(text)
    def _record_duplex_ai_line(self, t): pass
    async def _log_duplex_source(self, k, t): pass
    def _clean_duplex_speech(self, t): return (t or "").strip()
    def _similar_to_recent(self, t): return False


def build_pack(gen_model, think_off):
    gen = ZhipuLLM(model=gen_model, max_tokens=140, think_off=think_off)
    rev = ZhipuLLM(model="glm-4.5-flash", max_tokens=500, think_off=True)
    pack = ProactivePack(host=Host(), llm=gen, base_dir=Path(tempfile.mkdtemp()),
                         name="达妮娅", judge_llm=rev)
    f = dict(pack.flags)
    f.update({k: True for k in ("enabled", "followup", "pending", "recall",
                                "rhythm", "silence", "llm_gate")})
    f["festival"] = False; f["share"] = False; f["judge_attempts"] = 3
    f["day_cap"] = 9; pack.flags = f
    pack._relation_gate = lambda c, n: (True, "t")
    pack._light_gate = lambda n: (True, "t")
    return pack


RUBRIC = (
    "你是资深「AI 伴侣」用户体验评审。下面是一句 AI 伴侣主动发给用户的话，"
    "请从用户角度打分（每维 1-5，5 最好），只关注体验：\n"
    "自然度：像真人随口说、不僵硬？\n人设：像有性格的伴侣而非客服/机器人？\n"
    "互动欲：作为用户想回她一句吗？\n信息量：有具体锚点而非废话？\n"
    "最后输出四行：\n自然度: x\n人设: x\n互动欲: x\n信息量: x\n总评: <一句话>\n"
)


async def critic(text):
    if not text:
        return {"natural":0,"persona":0,"engage":0,"info":0,"note":"(未开口)"}
    c = ZhipuLLM(model="glm-4.5-flash", max_tokens=200, think_off=True)
    import re
    raw = await c.chat([{"role":"user","content": RUBRIC + "\n\n待评：\n「"+text+"」"}])
    def g(l):
        m = re.search(l+r"\s*[:：]\s*([1-5])", raw); return int(m.group(1)) if m else 0
    return {"natural":g("自然度"),"persona":g("人设"),"engage":g("互动欲"),"info":g("信息量"),
            "note":(raw.split("总评")[-1].strip()[:40] if "总评" in raw else "")}


async def main():
    t0 = time.time()
    gens = [("glm-4-flash", False, "弱生成(非推理,基线)"),
            ("glm-4.5-flash", True, "强生成(关思考)"),
            ("glm-4.7-flash", True, "强生成(关思考)")]
    records = []
    for model, think_off, label in gens:
        print("\n========== 生成端 %s (%s) ==========" % (model, label))
        for kind, brief, sit in SCENARIOS:
            trg = types.SimpleNamespace(kind=kind, brief=brief)
            prompt = prompts_mod.build_prompt(trg, sit, "达妮娅")
            for run in range(2):
                host = Host()
                pack = build_pack(model, think_off)
                t1 = time.time()
                text, passed = await pack._generate_with_review(trg, prompt, sit)
                dt = time.time() - t1
                rej = len(pack._last_rejects)
                print("  [%s|%s|r%d] 过=%-5s 驳=%d %.1fs | %s"
                      % (model, kind, run, passed, rej, dt, (text or "(未开口)")[:34]))
                records.append({"gen": model, "label": label, "kind": kind,
                                "passed": passed, "text": text, "rejects": rej,
                                "dt": round(dt,1)})
                await asyncio.sleep(1.0)
    # 评分
    print("\n========== E) 用户视角评分 ==========")
    scored = []
    for r in records:
        s = await critic(r["text"]) if (r["passed"] and r["text"]) else {"natural":0,"persona":0,"engage":0,"info":0,"note":"(未开口)"}
        scored.append({**r, "score": s})
        print("  [%s|%s] 自然%d 人设%d 互动%d 信息%d | %s"
              % (r["gen"], r["kind"], s["natural"], s["persona"], s["engage"],
                 s["info"], r["text"][:26]))
        await asyncio.sleep(0.8)

    report = ["# 主动互动包 · 强生成器(关思考)公平对照", "",
              "- 时间 %s；统一审查 glm-4.5-flash(关思考)" % time.strftime("%Y-%m-%d %H:%M"),
              "- 用时 %.1fs\n" % (time.time()-t0),
              "## 最终开口逐句", "",
              "| 生成模型 | 场景 | 通过 | 驳回 | 耗时 | 最终开口 |",
              "|---|---|---|---|---|---|"]
    for r in records:
        report.append("| %s | %s | %s | %d | %.1f | %s |" % (
            r["label"], r["kind"], "是" if r["passed"] else "否", r["rejects"],
            r["dt"], (r["text"] or "(未开口)").replace("|","/")))
    report += ["", "## 用户视角评分(1-5)", "",
               "| 生成模型 | 场景 | 自然 | 人设 | 互动欲 | 信息量 | 总评 |",
               "|---|---|---|---|---|---|---|"]
    for r in scored:
        s = r["score"]
        report.append("| %s | %s | %d | %d | %d | %d | %s |" % (
            r["label"], r["kind"], s["natural"], s["persona"], s["engage"],
            s["info"], s["note"]))
    report += ["", "## 聚合平均", ""]
    for model, think_off, label in gens:
        rs = [r for r in scored if r["gen"] == model]
        passed = sum(1 for r in rs if r["passed"])
        avg_rej = sum(r["rejects"] for r in rs)/max(1,len(rs))
        sc = [r["score"] for r in rs if r.get("score")]
        if sc:
            aN=sum(x["natural"] for x in sc)/len(sc); aP=sum(x["persona"] for x in sc)/len(sc)
            aE=sum(x["engage"] for x in sc)/len(sc); aI=sum(x["info"] for x in sc)/len(sc)
            report.append("- **%s**：开口 %d/%d，平均驳回 %.1f，自然%.1f/人设%.1f/互动%.1f/信息%.1f"
                          % (label, passed, len(rs), avg_rej, aN, aP, aE, aI))
        else:
            report.append("- **%s**：开口 %d/%d" % (label, passed, len(rs)))
    report += [""]
    p = Path(ROOT)/"docs"/"proactive_pack_gen_thinkoff.md"
    p.write_text("\n".join(report), encoding="utf-8")
    print("\n报告：%s" % p)


if __name__ == "__main__":
    asyncio.run(main())
