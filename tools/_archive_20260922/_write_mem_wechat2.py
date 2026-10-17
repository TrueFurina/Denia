# -*- coding: utf-8 -*-
"""追加本轮 A/B 结论（含对「跑飞是新缓存导致」的否证）。"""
import os, time

MEM = r"G:\ai对话\.workbuddy\memory\MEMORY.md"
LOG = r"G:\ai对话\.workbuddy\memory\2026-09-21.md"

ADD_TO_2V = (
    "⚠️ **该裸测试台 6 次运行 5 次跑飞**（生成退化复读、不吐停止符，"
    "`keepalive ping timeout`）——S1 2/2、S2 2/3、S3 1/1，**与 cache 无关**，"
    "是「无 VAD/无回合控制、喂完静音干等」的通用行为 ⇒ **该测试台的「时长」不能当话痨判据**。"
    "⚠️ **F0 系统性抬升**：三方产出中位 F0 均在 260~270Hz，而参考音 238.8Hz（+22~31Hz ≈ +2 半音），"
    "**三方一致 ⇒ 路径性特征，不是 cache 差异**（可能正是用户觉得「和预期不一样」的来源）。"
    "⚠️ **单次客观指标不足以排序**：同臂重复跑 cos 波动（S1 0.6743→0.6604，S2 0.6413→0.6749）"
    "与臂间差异同量级 ⇒ 必须靠听感，或每臂跑 3+ 次取分布。"
    "现有排名参考：与参考余弦 S1/S2≈0.66~0.67、S3 0.597；>4k 占比 S3 最亮 25.7%（S1/S2 14~19%，参考 28.7%）。"
)


def main():
    b = open(MEM, "rb").read()
    nl = "\r\n" if b"\r\n" in b else "\n"
    t = b.decode("utf-8").replace("\r\n", "\n")
    anchor = ("三方盲听对照 `tools/ab_wechat_3way.py`（同一输入+同一 prepare 参考音，"
              "**唯一变量=声码器 cache**）。")
    assert t.count(anchor) == 1, "§2v 锚点 %d 次" % t.count(anchor)
    t = t.replace(anchor, anchor + ADD_TO_2V, 1)
    out = t.replace("\n", nl)
    assert "\r\r" not in out
    open(MEM, "wb").write(out.encode("utf-8"))
    print("MEMORY.md 已补 §2v  (>%d 字节)" % len(out.encode("utf-8")))

    lb = open(LOG, "rb").read()
    lnl = "\r\n" if b"\r\n" in lb else "\n"
    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    entry = """
### %s 补充：三方 A/B 客观对照 + 「跑飞」否证

**跑飞否证**（重要）：初看 S1/S3 都跑飞 142~150s、而 S2 正常 21.6s，怀疑是新缓存导致话痨。反向验证拿 S2 再跑两次 ⇒ **同样跑飞**。6 次汇总：跑飞 5 次、正常 1 次，与 cache 无关。根因＝该裸测试台无 VAD/无回合控制，喂完静音就干等，模型退化成复读循环且不吐停止符（`keepalive ping timeout`）。**该台的"时长"不能当话痨判据。**

**F0 系统性抬升**（新发现）：三方产出 F0 中位 260~270Hz vs 参考 238.8Hz（+22~31Hz ≈ +2 半音），三方一致 ⇒ 属该声码器路径特征，非 cache 差异。

**客观指标不足以排序**：同臂重复跑，与参考余弦在 0.66~0.67 之间摆动（臂间差异同量级）⇒ 必须靠听感或每臂 3+ 次取分布。已有倾向：S3(录屏) 与参考余弦最低 0.597，S1/S2 相当；>4k 占比 S3 最亮。

**环境清理**：A/B 的 worker 与 llama-server 已随脚本退出自动清理（19060/22731 均 free）。用户自有实例（gateway 8006 + worker 22700）仍在跑旧缓存 ⇒ 必须重启才能听到新音色。

**待办**：用户盲听 `verify_wechat/AB_快听_等电平.wav`（S1→S2→S3）后定夺；若需要统计级结论再每臂跑 3 次。
""" % ts
    outb = lb.decode("utf-8").replace("\r\n", "\n") + entry
    outb = outb.replace("\n", lnl)
    assert "\r\r" not in outb
    open(LOG, "wb").write(outb.encode("utf-8"))
    print("今日日志已追加 (>%d 字节)" % len(outb.encode("utf-8")))


if __name__ == "__main__":
    main()
