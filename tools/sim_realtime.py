# -*- coding: utf-8 -*-
"""主动互动包 · 中速实时仿真（倍速可调，真实调用免费智谱 glm-4-flash）。

与 `sim_proactive_pack.py`（几秒压缩 7 天）的区别：
  · 时间按倍速**真实流逝**：每跳推进 STEP 个虚拟秒，再真实 sleep(STEP/scale) 秒，
    于是"1 真实秒 = scale 个虚拟秒"。默认 scale=300 → 约 30~40 分钟跑完 7 天，能挂着看过程。
  · LLM 用**真实智谱**（不是占位台词）：她每次开口都是免费模型当场生成的真实句子。
  · 过程实时打印（虚拟日期时刻 + 本轮结果），每次开口打 ★ 行；到期写报告 + 弹系统通知。

真实性说明（为什么加速不影响结论）：
  · 触发判据只有两类——①时间差（你离开多久→idle/silence/share）②虚拟时刻（几点→饭点/熬夜/节日）。
    本仿真让 now / last_input_ts / 冷却 / 日上限**全用同一个虚拟时钟**，判据完全自洽，
    加速只是让同样的决策更快发生，不改变"何时触发/频度/类型分布/像不像真人"。
  · 唯一花真实时间的是 LLM 生成那一秒，与触发决策无关。
  · 沿用 sim 的"她开口后用户回 15 分钟"模型（否则沉默档会无限刷屏）——这是仿真固有简化，与倍速无关。
  · 减法闸 llm_gate 关闭 = fail-open 口径（闸门故障时放行），已在离线仿真验证不改变开口决策分布；
    关闭只为省免费额度/加速，每次开口仍走真实 LLM 生成。

依赖 src.proactive_pack 的真实触发器/情境（import 时即锁定当前版本，与之后改软件解耦——
因为本脚本只 import 不改 src；若你要"冻结快照防改"，见 tools/real_week_watch 思路）。
"""
from __future__ import annotations
import argparse, asyncio, json, logging, sys, time, types, tempfile, urllib.request
from pathlib import Path

ROOT = r"G:\ai对话\11"
sys.path.insert(0, ROOT)
sys.path.insert(0, str(Path(ROOT) / "tools"))

import src.proactive_pack.pack as packmod
from src.proactive_pack.pack import ProactivePack
import tools.sim_proactive_pack as S   # 复用：作息/记忆/素材/情境/闸门镜像/通知

logging.getLogger("proactive_pack").setLevel(logging.WARNING)

ZHIPU_KEY = "ae1bd969f017416a8a0a725d48fcf9f7.mUTU7jBoatpll5c9"
ZHIPU_URL = "https://open.bigmodel.cn/api/paas/v4/chat/completions"


class ZhipuLLM:
    """真实智谱 glm-4-flash（OpenAI 兼容）。非推理模型 -> 天然关思考。失败退避重试 1 次。"""

    def __init__(self):
        self.calls = 0
        self.fails = 0

    async def chat(self, messages, options=None):
        self.calls += 1
        sys_prompt = messages[0]["content"] if messages and messages[0].get("role") == "system" else ""
        user_turn = next((m["content"] for m in messages if m.get("role") == "user"), "")
        prompt = (sys_prompt + "\n" + user_turn) if sys_prompt else user_turn
        body = json.dumps({
            "model": "glm-4-flash",
            "messages": [{"role": "user", "content": prompt}],
            "stream": False, "max_tokens": 80, "temperature": 0.9,
        }).encode("utf-8")
        for attempt in range(2):
            try:
                req = urllib.request.Request(
                    ZHIPU_URL, data=body,
                    headers={"Authorization": "Bearer " + ZHIPU_KEY, "Content-Type": "application/json"})
                with urllib.request.urlopen(req, timeout=25) as r:
                    d = json.loads(r.read().decode("utf-8"))
                    return (d["choices"][0]["message"]["content"] or "").strip()
            except Exception as e:  # noqa: BLE001
                if attempt == 0:
                    await asyncio.sleep(2.0)   # 429/网络抖动：退避重试一次
                    continue
                self.fails += 1
                logging.warning("[sim_realtime] 智谱调用失败（跳过本次开口）：%s", e)
                return ""


async def main():
    ap = argparse.ArgumentParser(description="主动互动包 · 中速实时仿真")
    ap.add_argument("--days", type=float, default=7.0, help="仿真几天（默认 7）")
    ap.add_argument("--step", type=int, default=600, help="每跳推进多少虚拟秒（默认 600=10 虚拟分钟）")
    ap.add_argument("--scale", type=float, default=300.0,
                    help="倍速：1 真实秒 = scale 虚拟秒。默认 300≈30-40 分钟跑完 7 天；"
                         "60≈2.8 小时(最贴近真实)；600≈18 分钟；3000≈5 分钟")
    args = ap.parse_args()

    base_dir = Path(tempfile.mkdtemp(prefix="simrt_"))
    (base_dir / "proactive_pack_feed.json").write_text(
        json.dumps(S.FEED_ITEMS, ensure_ascii=False), encoding="utf-8")

    host = S.FakeHost()
    llm = ZhipuLLM()
    pack = ProactivePack(host=host, llm=llm, base_dir=base_dir, name="达妮娅")
    flags = dict(pack.flags)
    flags.update({k: True for k in ("enabled", "festival", "pending", "followup",
                                    "recall", "rhythm", "silence", "share")})
    flags["llm_gate"] = False   # fail-open 口径，省额度/加速；每次开口仍真实生成
    flags["feed_hot"] = False
    flags["day_cap"] = 6
    pack.flags = flags

    packmod.collect = S.scenario_collect

    emissions = []
    captured = []

    async def _record(text):
        captured.append(text)

    pack._emit_fn = _record

    def _sim_gate(self, a, b=None):
        # relation_gate(cid, now) → b 非空；light_gate(now) → b 为 None。镜像真实 _proactive_decision
        now = b if b is not None else a
        h = time.localtime(now).tm_hour
        if h >= 22 or h < 8:
            return False, "sim_silence"
        if len([e for e in emissions if e["day"] == time.localtime(now).tm_wday]) >= int(flags.get("day_cap", 6)):
            return False, "sim_daycap"
        if emissions and (now - emissions[-1]["ts"]) < 20 * 60:
            return False, "sim_cooldown"
        return True, "sim_ok"

    pack._relation_gate = types.MethodType(_sim_gate, pack)
    pack._light_gate = types.MethodType(_sim_gate, pack)

    STEP = args.step
    END = S.SIM_START + args.days * S.DAY_SECONDS
    sleep_s = (STEP / args.scale) if args.scale > 0 else 0.0
    total_ticks = int((END - S.SIM_START) // STEP) + 1
    emit_log = Path(ROOT) / "tools" / "_realtime_emissions.jsonl"
    emit_log.write_text("", encoding="utf-8")

    print("=" * 64)
    print("主动互动包 · 中速实时仿真（真实免费智谱 glm-4-flash，思考已关）")
    print("倍速 scale=%s（1 真实秒=%s 虚拟分钟）｜每跳 %d 虚拟秒｜共约 %d 跳" % (
        args.scale, round(args.scale / 60.0, 1), STEP, total_ticks))
    print("预计 %.0f 分钟跑完 %g 天（另加每次开口的 LLM 生成时间）｜Ctrl-C 可提前停止并出报告" % (
        total_ticks * sleep_s / 60.0, args.days))
    print("=" * 64)
    sys.stdout.flush()

    t = S.SIM_START
    wall0 = time.time()
    last_vh = None
    interrupted = False
    try:
        while t <= END:
            if S.user_active_at(t):
                host._last_input_ts = t
            ok = await pack.maybe_emit(now=t)
            if ok:
                kind = getattr(pack, "_last_kind", "?")
                text = getattr(pack, "_last_text", "") or (captured[-1] if captured else "")
                lt = time.localtime(t)
                emissions.append({"ts": t, "day": lt.tm_wday, "hour": lt.tm_hour,
                                  "kind": kind, "text": text})
                with emit_log.open("a", encoding="utf-8") as f:
                    f.write(json.dumps({"virt": time.strftime("%m-%d %H:%M", lt),
                                        "kind": kind, "text": text}, ensure_ascii=False) + "\n")
                print("★ [虚拟 %s] 开口 kind=%-9s | %s" % (
                    time.strftime("%m-%d %H:%M", lt), kind, text))
                sys.stdout.flush()
                # 她开口后模拟"用户回了 15 分钟"（真实里用户会回，否则沉默档无限刷屏）
                host._last_input_ts = t
                S.RETURN_BLIPS.append(t)
            vh = int((t - S.SIM_START) // 3600)
            if vh != last_vh:
                last_vh = vh
                print("… [虚拟 %s] 观测中｜已开口 %d 次｜真实已过 %.1f 分钟" % (
                    time.strftime("%m-%d %H:%M", time.localtime(t)),
                    len(emissions), (time.time() - wall0) / 60.0))
                sys.stdout.flush()
            t += STEP
            if sleep_s > 0:
                await asyncio.sleep(sleep_s)
    except KeyboardInterrupt:
        interrupted = True
        print("\n[手动停止] 提前收尾并出报告…")

    # ---- 统计 + 报告 + 通知 ----
    WK = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]
    by_kind, by_day, quiet = {}, {i: 0 for i in range(7)}, 0
    for e in emissions:
        by_kind[e["kind"]] = by_kind.get(e["kind"], 0) + 1
        by_day[e["day"]] += 1
        if e["hour"] >= 22 or e["hour"] < 8:
            quiet += 1
    gaps = [(emissions[i + 1]["ts"] - emissions[i]["ts"]) / 60.0 for i in range(len(emissions) - 1)]
    min_gap = min(gaps) if gaps else 0

    report = Path(ROOT) / "tools" / "proactive_pack_realtime_report.md"
    L = ["# 主动互动包 · 中速实时仿真报告\n"]
    L.append("- 生成时间：%s" % time.strftime("%Y-%m-%d %H:%M:%S"))
    L.append("- 仿真跨度：%g 天｜倍速 scale=%s｜LLM：glm-4-flash（免费·思考已关）" % (args.days, args.scale))
    L.append("- 是否跑满：%s" % ("否（手动提前停止）" if interrupted else "是"))
    L.append("- 总开口：**%d 次（日均 %.1f）**｜LLM 调用 %d 次、失败 %d 次（失败即跳过该次）" % (
        len(emissions), len(emissions) / max(args.days, 1e-9), llm.calls, llm.fails))
    L.append("\n## 按类型\n")
    for k in sorted(by_kind):
        L.append("- `%s`：%d" % (k, by_kind[k]))
    L.append("\n## 按天\n")
    for i in range(int(min(7, args.days))):
        L.append("- %s：%d" % (WK[i], by_day.get(i, 0)))
    L.append("\n## 安全阀\n")
    L.append("- 静默时段(22:00–08:00)违规开口：**%d**" % quiet)
    L.append("- 相邻两条最小间隔：**%.0f 分钟**" % min_gap)
    L.append("\n## 逐条时间线（真实台词）\n")
    for e in emissions:
        L.append("- %s %02d:%02d `kind=%-9s` %s" % (
            WK[e["day"]], e["hour"], time.localtime(e["ts"]).tm_min, e["kind"], e["text"]))
    report.write_text("\n".join(L), encoding="utf-8")

    print("\n" + "=" * 64)
    print("总开口 %d 次 / %g 天（日均 %.1f）｜LLM 调用 %d、失败 %d｜静默时段违规 %d｜最小间隔 %.0f 分钟" % (
        len(emissions), args.days, len(emissions) / max(args.days, 1e-9), llm.calls, llm.fails, quiet, min_gap))
    print("报告已写入：%s" % report)
    print("=" * 64)

    S.notify_windows("达妮娅 · 中速实时仿真完成",
                     "%g 天共开口 %d 次（日均 %.1f）；报告：%s" % (
                         args.days, len(emissions), len(emissions) / max(args.days, 1e-9), report.name))

    try:
        import shutil
        shutil.rmtree(base_dir, ignore_errors=True)
    except Exception:
        pass


if __name__ == "__main__":
    asyncio.run(main())
