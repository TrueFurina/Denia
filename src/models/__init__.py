"""P2 只读投影层：模型与音色的统一「发现」接口。

这个包解决什么问题
------------------
P0 基线实测：项目里 **13 个模型调用点，只有 4 个在槽位系统覆盖内**，
9 个是盲区（`llm.openai` / `llm.minicpm` / `cascade` / `agent` / `ocr` /
**`voice_profiles`** / **`voice.clone`** / **`onboarding_tts`** / 凭证）。
后果是：角色卡 TTS、引导页、设置页各自维护一套"当前用什么、还缺什么"的判断，
两边不一致就是历史 bug 的温床。

本包做的是**把"发现"和"校验"从各个入口里抽出来，收到一处**——
**只读，绝不写配置、绝不改任何调用链路的入参形状**。

三层设计
--------
1. `protocols.py` —— 元数据：每种引擎/厂商的字段契约与端点形状（纯数据，无逻辑）
2. `catalog.py`   —— 读 config → 投影成统一视图（`ModelView` / `VoiceView`）
3. `__init__.py`  —— 对外 API（本文件）

与现有代码的关系（**关键：零侵入**）
----------------------------------
· 不改 `model_registry.py` 的任何东西（它仍是 6 槽位的单相真相源）；
  catalog 把它的输出**收纳进更大的统一视图**，不取代它。
· 不改 `tts_backends.py` 的 `create_backend(engine, config)` 入参形状，
  也不改 10 个 Backend 的 `getattr(config, ...)` 字段名（19 字段契约原样保留）。
· 不改 `webapp.py` 任何现有 js_api 方法的返回形状——
  新接口是**额外新增**的，老前端不调它就完全不受影响。

P2 的验收判据
-------------
新增本包后，跑 `tools/model_chain_baseline.py` 得到的 after.json
必须与 baseline.json **完全一致**。因为本包只读、只新增、不接管任何写入。
"""
from __future__ import annotations

from .catalog import (
    build_model_view,
    build_voice_view,
    summarize,
    diagnose,
    CALL_SITES,
)
from .protocols import (
    TTS_ENGINES,
    LLM_PROVIDERS,
    field_contract_for,
    engine_of_provider,
)
from .writer import (
    commit_model_write,
    normalize_whitespace,
    unify_bailian_keys,
    suggest_for_changed,
)

__all__ = [
    "build_model_view",
    "build_voice_view",
    "summarize",
    "diagnose",
    "CALL_SITES",
    "TTS_ENGINES",
    "LLM_PROVIDERS",
    "field_contract_for",
    "engine_of_provider",
    "commit_model_write",
    "normalize_whitespace",
    "unify_bailian_keys",
    "suggest_for_changed",
]
