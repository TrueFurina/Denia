# -*- coding: utf-8 -*-
"""把 config.yaml 的主链路从阿里云百炼（账户 400 停用）切到免费渠道。

原则：
  · 密钥不落盘 —— 一律写 `${ENV_VAR}` 引用（见 src/config.py 的 _expand_env_refs）；
  · 每处替换断言「命中恰好 1 次」，改错就整脚本退出，不留半成品；
  · 保持 CRLF 与缩进，不重排 YAML。
"""
import io
import sys

P = "config.yaml"


def L(*lines: str) -> bytes:
    return b"\r\n".join(x.encode("utf-8") for x in lines)


SUBS = [
    # ① 主聊天（llm.openai）：百炼 qwen-plus → 智谱 glm-4-flash
    #    vision 开关保持 true —— 它是 20 个视觉模型的**总闸**，改 false 会连坐。
    (L("  openai:",
       "    api_key: ''",
       "    base_url: https://dashscope.aliyuncs.com/compatible-mode/v1",
       "    max_tokens: null",
       "    model: qwen-plus"),
     L("  openai:",
       "    # 【2026-10-18】阿里云百炼账户 400 \"not in good standing\"（欠费/风控），",
       "    # 主聊天切到智谱 glm-4-flash（永久免费不限量，实测 1.0s，支持工具调用）。",
       "    # key 用环境变量引用，不落盘：setx ZHIPU_API_KEY <key> 后重启。",
       "    api_key: ${ZHIPU_API_KEY}",
       "    base_url: https://open.bigmodel.cn/api/paas/v4",
       "    max_tokens: null",
       "    model: glm-4-flash"),
     "主聊天 llm.openai"),

    # ② 通话大脑（cascade）：max_tokens 只有 90，必须用非推理模型
    (L("cascade:",
       "  base_url: https://dashscope.aliyuncs.com/compatible-mode/v1",
       "  api_key: ''",
       "  model: qwen-plus"),
     L("cascade:",
       "  # 通话大脑：90 token 上限，不能用带 reasoning 的模型（思考链会吃光额度、",
       "  # 首字还会慢半拍）。glm-4-flash 实测无 reasoning、1.0s 首字。",
       "  base_url: https://open.bigmodel.cn/api/paas/v4",
       "  api_key: ${ZHIPU_API_KEY}",
       "  model: glm-4-flash"),
     "通话大脑 cascade"),

    # ③ Agent 工具链：要推理，用 glm-4.7-flash
    (L("  llm:",
       "    base_url: https://dashscope.aliyuncs.com/compatible-mode/v1",
       "    api_key: ''",
       "    model: qwen3.8-flash"),
     L("  llm:",
       "    # Agent 要规划多步，用 4.7-flash（带 reasoning，实测工具调用正常）",
       "    base_url: https://open.bigmodel.cn/api/paas/v4",
       "    api_key: ${ZHIPU_API_KEY}",
       "    model: glm-4.7-flash"),
     "Agent 工具链 agent.llm"),

    # ④ 聊天页当前端口：显式指向 zai-glm（否则会按列表顺序挑到百炼的空 key 端口）
    (L("  manual_provider: ''", "  mode: manual"),
     L("  manual_provider: zai-glm", "  mode: manual"),
     "聊天当前端口 manual_provider"),

    # ⑤ 智谱聊天端口填 key
    #    锚点末尾必须带 CRLF：`id: zai-glm` 是 `id: zai-glm-4v-flash` 的前缀，
    #    不带换行会同时命中视觉那条（第一次跑就是栽在这里）。
    (L("  - api_key: ''",
       "    base_url: https://open.bigmodel.cn/api/paas/v4",
       "    id: zai-glm") + bytes((13, 10)),
     L("  - api_key: ${ZHIPU_API_KEY}",
       "    base_url: https://open.bigmodel.cn/api/paas/v4",
       "    id: zai-glm") + bytes((13, 10)),
     "聊天端口 zai-glm"),

    # ⑥ 视觉模型（active 就是它，只差 key）
    (L("  - api_key: ''",
       "    base_url: https://open.bigmodel.cn/api/paas/v4",
       "    id: zai-glm-4v-flash") + bytes((13, 10)),
     L("  - api_key: ${ZHIPU_API_KEY}",
       "    base_url: https://open.bigmodel.cn/api/paas/v4",
       "    id: zai-glm-4v-flash") + bytes((13, 10)),
     "视觉 zai-glm-4v-flash"),

    # ⑦ 云端克隆音色（备用；主用本地 voxcpm，不受欠费影响）
    (L("  engine: dashscope_cosy", "  api_key: ''"),
     L("  engine: dashscope_cosy",
       "  # 备用云端克隆（主用本地 voxcpm，欠费不影响）；充值后自动可用",
       "  api_key: ${DASHSCOPE_API_KEY}"),
     "云端克隆 voice_profiles"),

    # ⑧ 云端双工 omni（当前 prefer_cloud=false + local_fallback=true，走本地）
    (L("cloud_omni:", "  model: ''", "  api_key: ''"),
     L("cloud_omni:", "  model: ''", "  api_key: ${DASHSCOPE_API_KEY}"),
     "云端双工 cloud_omni"),

    # ⑨ 首次运行引导里的 LLM（force_first_run: true，这段一定会被用到）
    (L("  - id: onboarding-llm",
       "    name: 云端 LLM（引导绑定）",
       "    provider: openai",
       "    base_url: https://dashscope.aliyuncs.com/compatible-mode/v1",
       "    api_key: ''",
       "    model: qwen-plus"),
     L("  - id: onboarding-llm",
       "    name: 云端 LLM（引导绑定·智谱免费档）",
       "    provider: openai",
       "    base_url: https://open.bigmodel.cn/api/paas/v4",
       "    api_key: ${ZHIPU_API_KEY}",
       "    model: glm-4-flash"),
     "引导页 LLM onboarding-llm"),
]

# ⑩ 往聊天页的端口列表补几条实测可用的免费源（插在 zai-glm 之后）
ANCHOR = L("  - api_key: ${ZHIPU_API_KEY}",
           "    base_url: https://open.bigmodel.cn/api/paas/v4",
           "    id: zai-glm",
           "    model: glm-4-flash",
           "    name: 智谱 GLM-4-Flash（免费·非推理·最稳·兜底）",
           "    provider: openai",
           "    timeout_s: 60",
           "    _howto: 填你自己的服务商 Key 与 base_url；别把别人的 Key 打包进来")

EXTRA = L(
    "  - api_key: ${ZHIPU_API_KEY}",
    "    base_url: https://open.bigmodel.cn/api/paas/v4",
    "    id: zai-glm-4.7-flash",
    "    model: glm-4.7-flash",
    "    name: 智谱 GLM-4.7-Flash（免费·带推理·人格更细）",
    "    provider: openai",
    "    timeout_s: 60",
    "    _howto: 免费档热门时段会限流，重试即可",
    "  - api_key: ${MODELSCOPE_API_KEY}",
    "    base_url: https://api-inference.modelscope.cn/v1",
    "    id: modelscope-ds",
    "    model: deepseek-ai/DeepSeek-V4-Flash-0731",
    "    name: 魔搭 DeepSeek-V4-Flash（2000 次/天·上下文强）",
    "    provider: openai",
    "    timeout_s: 60",
    "    _howto: 每天 0 点刷新额度；热门模型高峰 429 属正常",
    "  - api_key: ${TOKENHUB_API_KEY}",
    "    base_url: https://tokenhub.tencentmaas.com/v1",
    "    id: tokenhub-glm",
    "    model: glm-5.3-flash",
    "    name: TokenHub GLM-5.3-Flash（体验包·旗舰思考）",
    "    provider: openai",
    "    timeout_s: 60",
    "    _howto: 一次性体验额度，用完报 402",
    "  - api_key: ${AMD_API_KEY}",
    "    base_url: ${AMD_BASE_URL}",
    "    id: amd-ds",
    "    model: DeepSeek-V4-Flash",
    "    name: AMD Token Factory DeepSeek-V4-Flash（长期免费·20 RPM）",
    "    provider: openai",
    "    timeout_s: 60",
    "    _howto: 日限额 $1，重置时间北京 0:00",
    "  - api_key: ${DEEPSEEK_API_KEY}",
    "    base_url: https://api.deepseek.com/v1",
    "    id: deepseek-official",
    "    model: deepseek-flash",
    "    name: DeepSeek 官方 deepseek-flash（余额 ¥18.7·兜底要花钱）",
    "    provider: openai",
    "    timeout_s: 60",
    "    _howto: 全免费源都挂了才用；非高峰时段价格减半",
)


def main() -> int:
    rc = 0
    for path in ("config.yaml", "config.release.yaml"):
        print(f"\n=== {path} ===")
        if _switch(path):
            rc = 1
    return rc


def _switch(P: str) -> int:
    raw = open(P, "rb").read()
    # 幂等：已经切过的文件不重复切（config.yaml 第一次跑完就是新形态）
    if b"${ZHIPU_API_KEY}" in raw:
        print("已是免费渠道配置，跳过")
        return 0
    orig_len = len(raw)
    for old, new, label in SUBS:
        n = raw.count(old)
        if n != 1:
            print(f"✗ {label}：命中 {n} 次（应为 1），中止，未写盘")
            return 1
        raw = raw.replace(old, new)
        print(f"✓ {label}")

    n = raw.count(ANCHOR)
    if n != 1:
        print(f"✗ 免费渠道插入锚点命中 {n} 次，中止")
        return 1
    raw = raw.replace(ANCHOR, ANCHOR + b"\r\n" + EXTRA)
    print("✓ 补 5 个免费渠道端口（魔搭/TokenHub/AMD/DeepSeek/4.7-flash）")

    open(P, "wb").write(raw)
    crlf = raw.count(bytes((13, 10)))
    print(f"\n已写盘：{orig_len} → {len(raw)} 字节（CRLF={crlf}）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
