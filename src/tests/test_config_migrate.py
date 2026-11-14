# -*- coding: utf-8 -*-
"""失效 TTS 配置的启动期迁移：两个迁移函数 + 唯一入口 + 原子写盘。

**为什么需要这组测试**

合并队友工作树（2026-10-19）时静态检查抓到：`migrate_stale_voice_profiles`
在 `webapp.py` 里定义完整、注释完整，但**全仓没有任何调用点**——用户已建的
音色档绑着已下线的 cosyvoice 模型时，试听永远 400，而启动期从没人去修它。
这是"函数写了没接线"的典型静默失效，测试看不出、日志也没有。

所以这里的重点不是"函数算得对不对"，而是：
  ① 唯一入口 `migrate_stale_tts_config` 是否**真的把两半都跑了**；
  ② `webapp` 启动路径是否**真的调了那个入口**（**运行时**断言，不是源码文本）；
  ③ 写盘是否**真的原子**（不再出现裸 `io.open(path, "w")`）。

**2026-11-10 判据大修（第 43 轮变异探针）**

探针把生产代码一处一处改坏，查出五处"刀落下去门还绿"：

  1. `[10]` 五条**源码文本**断言（`"migrate_stale_tts_config(" in web_txt`）。
     把调用改写成 `_mig = []` + `if False: _mig = migrate_stale_tts_config(...)`
     之后，字符串原封不动躺在文件里 —— 五条全绿，迁移永不执行。这正是
     2026-10-19 那次「函数写了没接线」的同型失效，只是藏在分支后面。
     修法：新增 `[10b]`，真造一个 `CharacterAPI(config_path=…)`，看盘上文件变没变。
  2. `[9]` 的「调用方 dict 未被就地改动」用 `api_key_probe` 当样本，而
     `secret_box._SECRET_FIELDS` 只认 `api_key` / `cookie` —— `seal_tree` 根本不碰
     它，摘掉 `copy.deepcopy` 也绿。修法：样本换成真会被加密的
     `cloud_tts.dashscope_cosy.api_key`，并拆成「盘上是密文」+「内存里还是明文」。
  3. `[5]` 子串判定的唯一样本 `voice_id="cosyvoice-v2"` **本身就在
     `STALE_TTS_MODELS` 里**，`v in STALE_TTS_MODELS` 那一半就够了 ——
     `"cosyvoice" in v` / `"qwen-audio" in v` 两个分支从没被逼住。真实用户配置里
     是克隆 id（`cosyvoice-v3.5-plus-bailian-e683d3f9`），它不在清单里。
  4. `backup` 参数失效时门直接崩在 `open(bak)`（无汇总行）；写盘失败改抛异常时
     崩在 `[9]` 的调用点上 —— 崩掉的门说不清是哪条判据在守。修法：两处都自己接住。
  5. 整段判据被 `try/except` 吞掉就少跑几条而没人发现。修法：末尾条数总账
     `EXPECTED_CHECKS`（少跑一条就红）。

十八刀见 `tools/_qc_config_migrate_mutation.py`。
"""
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from src import config_migrate as cm  # noqa: E402
from src.config import load_config   # noqa: E402

PASS = 0
FAIL = 0
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# 【2026-11-10 判据条数总账】第 43 轮变异探针发现：本门有 5 处"刀落下去门还绿"。
# 其中一处的成因是整段判据被 `try/except` 吞掉就少跑几条，而少跑不报错。
# 所以除了逐条判据，还要一条"跑了几条"的总账 —— 条数不对本身就是红的。
EXPECTED_CHECKS = 56


def check(cond, msg):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("  ✓ " + msg)
    else:
        FAIL += 1
        print("  ✗ " + msg)


def _finish() -> int:
    """汇总 + 条数总账（少跑的判据不算通过）。"""
    global FAIL
    ran = PASS + FAIL
    if ran != EXPECTED_CHECKS:
        FAIL += 1
        print(f"  ✗ 判据条数 {ran} != 期望 {EXPECTED_CHECKS}"
              f"（有整段被跳过/异常吞掉了 —— 少跑的判据不算通过）")
    print(f"\n结果：{PASS} 通过 / {FAIL} 失败")
    return 1 if FAIL else 0


def _raw_voice(model="qwen-audio-3.0-tts-plus", voice="longxiaochun"):
    return {"voice": {"clone": {"cloud": {"dashscope_cosy": {
        "model_name": model, "voice": voice}}}}}


def _raw_cloud(model="cosyvoice-v3.5-plus"):
    return {"cloud_tts": {"dashscope_cosy": {"model_name": model}}}


print("[1] 模型迁移：两处 model_name 都要改（只改一处，另一处仍会把失效模型传给后端）")
raw = _raw_voice()
raw.update(_raw_cloud())
changed = cm.migrate_stale_tts_model(raw)
check(raw["voice"]["clone"]["cloud"]["dashscope_cosy"]["model_name"] == cm.TTS_MIGRATE_TO,
      "voice.clone…model_name 已迁移")
check(raw["cloud_tts"]["dashscope_cosy"]["model_name"] == cm.TTS_MIGRATE_TO,
      "cloud_tts…model_name 已迁移")
check(len(changed) == 2, f"返回两条变更路径（实际 {changed}）")

print()
print("[2] 幂等：已经是好值 → 返回空，不重复写盘")
raw2 = _raw_voice(model=cm.TTS_MIGRATE_TO)
raw2.update(_raw_cloud(model=cm.TTS_MIGRATE_TO))
check(cm.migrate_stale_tts_model(raw2) == [], "已是 qwen3-tts-flash → 无变更")

print()
print("[3] 结构畸形不能把启动搞崩（配置是用户手改的，什么都可能出现）")
for bad in ({"voice": []}, {"voice": "x"}, {"cloud_tts": []},
            {"cloud_tts": {"dashscope_cosy": "str"}},
            {"voice": {"clone": {"cloud": {"dashscope_cosy": 3}}}}):
    try:
        r = cm.migrate_stale_tts_model(bad)
        check(r == [], f"畸形结构不抛且无变更：{list(bad)[0]}")
    except Exception as e:  # noqa: BLE001
        check(False, f"畸形结构抛了异常：{type(e).__name__}")

print()
print("[4] 音色档迁移：绑着已下线模型的档 → model 与 voice_id 一起换")
raw4 = {"voice_profiles": [
    {"id": "p1", "engine": "dashscope_cosy", "model": "cosyvoice-v3.5-plus",
     "voice_id": "cosyvoice-v3.5-plus-bailian-e683d3f9"},
    {"id": "p2", "engine": "dashscope_cosy", "model": "qwen3-tts-flash",
     "voice_id": "Cherry"},
]}
changed4 = cm.migrate_stale_voice_profiles(raw4)
p1, p2 = raw4["voice_profiles"]
# 【2026-10-20】音色字段是 voice_id，不是 voice（voice 在 minimax 那边是 fallback）。
# 原实现写 prof["voice"]，真机实测迁移后 voice_id 原封不动 → 模型换了音色没换，
# 请求照样 418（418 是 voice_id 不可用，与 model 无关）。
check(p1["model"] == cm.TTS_MIGRATE_TO and p1["voice_id"] == cm.TTS_MIGRATE_VOICE,
      "失效档的 model/voice_id 都已迁移")
check("voice" not in p1, "不再往音色档里塞没人读的 voice 字段")
check(p2["model"] == cm.TTS_MIGRATE_TO and p2["voice_id"] == cm.TTS_MIGRATE_VOICE,
      "已正确的档保持原样（幂等）")
check(changed4 == ["voice_profile:p1"], f"只报真正改动的档（实际 {changed4}）")

print()
print("[5] 历史脏数据：模型名被填进音色字段也要救")
# 【2026-11-10 补】原先这里只有一条 `voice_id="cosyvoice-v2"`，而 `cosyvoice-v2`
# **本身就在 STALE_TTS_MODELS 里**——`v in STALE_TTS_MODELS` 那一半就够了。
# 于是 `"cosyvoice" in v` / `"qwen-audio" in v` 这两个子串分支从来没有被判据
# 真正逼住：探针把整段子串判定拆掉，门照样全绿。真正需要子串判定的是**克隆
# 音色 id**（`cosyvoice-v3.5-plus-bailian-e683d3f9`，用户配置里实际长这样）和
# 带后缀的 qwen-audio 名 —— 它们都不在清单里。
raw5 = {"voice_profiles": [
    {"id": "dirty", "engine": "dashscope_cosy", "model": "qwen3-tts-flash",
     "voice_id": "cosyvoice-v2"},
    {"id": "clone", "engine": "dashscope_cosy", "model": "qwen3-tts-flash",
     "voice_id": "cosyvoice-v3.5-plus-bailian-e683d3f9"},
    {"id": "qa", "engine": "dashscope_cosy", "model": "qwen3-tts-flash",
     "voice_id": "qwen-audio-3.0-tts-plus-clone"},
    {"id": "clean", "engine": "dashscope_cosy", "model": "qwen3-tts-flash",
     "voice_id": "Cherry"},
]}
# 前提断言：如果哪天这几个 id 被加进清单，下面的子串判据又会退化成"清单判定"，
# 变异就再也打不红。这条把前提本身钉住。
check("cosyvoice-v3.5-plus-bailian-e683d3f9" not in cm.STALE_TTS_MODELS
      and "qwen-audio-3.0-tts-plus-clone" not in cm.STALE_TTS_MODELS,
      "（前提）克隆音色 id 不在失效清单里 —— 只能靠子串判定救")
ch5 = cm.migrate_stale_voice_profiles(raw5)
check("voice_profile:clone" in ch5,
      "voice_id 是 cosyvoice 克隆 id（不在清单里）→ 子串判定判为失效")
check("voice_profile:qa" in ch5, "voice_id 含 qwen-audio 子串 → 子串判定判为失效")
check("voice_profile:clean" not in ch5, "正常音色档不被子串误伤（Cherry 保持原样）")
check(raw5["voice_profiles"][0]["voice_id"] == cm.TTS_MIGRATE_VOICE, "已换成 Cherry")
check(raw5["voice_profiles"][1]["voice_id"] == cm.TTS_MIGRATE_VOICE,
      "克隆 id 也被换成 Cherry（否则请求照样 418）")

print()
print("[6] 非 dashscope_cosy 引擎的档不许动（本地引擎没有模型下线问题）")
raw6 = {"voice_profiles": [
    {"id": "local", "engine": "pyttsx3", "model": "cosyvoice-v2", "voice_id": "x"}]}
check(cm.migrate_stale_voice_profiles(raw6) == [], "pyttsx3 档不迁移")
check(raw6["voice_profiles"][0]["model"] == "cosyvoice-v2", "原值未被改动")

print()
print("[7] 唯一入口：migrate_stale_tts_config 必须同时跑两半（死代码就是这样漏的）")
raw7 = {"voice": {"clone": {"cloud": {"dashscope_cosy": {
    "model_name": "qwen-audio-3.0-tts-plus"}}}},
    "cloud_tts": {"dashscope_cosy": {"model_name": "cosyvoice-v3-flash"}},
    "voice_profiles": [{"id": "p", "engine": "dashscope_cosy",
                        "model": "cosyvoice-v1", "voice_id": "v"}]}
all_changed = cm.migrate_stale_tts_config(raw7)
check(len(all_changed) == 3, f"三个点全部命中（实际 {len(all_changed)}: {all_changed}）")
check(raw7["voice_profiles"][0]["voice_id"] == cm.TTS_MIGRATE_VOICE,
      "音色档也在同一入口里被迁移（此前这一半从未被执行）")

print()
print("[8] 端到端：迁移写盘后，load_config 读到的是新值")
fd, p8 = tempfile.mkstemp(suffix=".yaml", text=True)
os.close(fd)
try:
    with open(p8, "w", encoding="utf-8") as f:
        f.write("voice:\n  clone:\n    cloud:\n      dashscope_cosy:\n"
                "        model_name: qwen-audio-3.0-tts-plus\n"
                "voice_profiles:\n  - id: p1\n    engine: dashscope_cosy\n"
                "    model: cosyvoice-v3.5-plus\n"
                "    voice_id: cosyvoice-v3.5-plus-bailian-e683d3f9\n")
    import yaml
    with open(p8, encoding="utf-8") as f:
        raw8 = yaml.safe_load(f)
    ch = cm.migrate_stale_tts_config(raw8)
    check(bool(ch), f"有变更需要写盘（{ch}）")
    check(cm.dump_config_atomic(p8, raw8) is True, "原子写返回 True")
    cfg = load_config(p8)
    check(cfg.raw["voice"]["clone"]["cloud"]["dashscope_cosy"]["model_name"]
          == cm.TTS_MIGRATE_TO, "重新加载后模型名已是新值")
    check(cfg.get_voice_profile("p1")["voice_id"] == cm.TTS_MIGRATE_VOICE,
          "重新加载后音色档已是 Cherry")
finally:
    os.unlink(p8)

print()
print("[9] 原子写：不动调用方的 dict、失败不抛、目录不可写返回 False")
# 【2026-11-10 补】原先这里用 `api_key_probe: "plain"` 当"不许被改动"的样本，
# 但 `api_key_probe` **不是** secret_box 认的密钥字段（认的是 api_key / cookie），
# 于是 seal_tree 根本不会碰它 —— 把 `copy.deepcopy(data)` 摘掉，调用方 dict 也
# 不会被改，判据照样绿。要逼住深拷贝，样本必须落在**真会被就地加密**的字段上。
raw9 = {"cloud_tts": {"dashscope_cosy": {"model_name": "cosyvoice-v2",
                                        "api_key": "plain-key-123"}}}
snapshot = repr(raw9)
fd, p9 = tempfile.mkstemp(suffix=".yaml", text=True)
os.close(fd)
try:
    check(cm.dump_config_atomic(p9, raw9) is True, "正常路径写盘成功")
    txt9 = open(p9, encoding="utf-8").read()
    check("cosyvoice-v2" in txt9, "内容确实落盘")
    # 这两条是一对：盘上是密文、内存里还是明文，才说明"加密的是副本"。
    # 摘掉 deepcopy 时 seal_tree 直接就地改调用方的 dict，第二条立刻红。
    check("dpapi:" in txt9, "落盘的是密文（seal 生效，样本确实会被加密）")
    check("plain-key-123" in repr(raw9),
          "调用方 dict 里的 api_key 仍是明文（加密的是写盘副本，不是原地改）")
    check(repr(raw9) == snapshot, "调用方 dict 未被就地改动（写的是副本）")
finally:
    os.unlink(p9)
# 【2026-11-10 补】这条原先是"裸调用 + 直接比较返回值"：实现一旦改成抛异常，
# 门不是红，而是崩在调用点上（无汇总行）。要断言的是"不抛"，就必须自己接住。
try:
    _bad = cm.dump_config_atomic(
        os.path.join(tempfile.gettempdir(), "no_such_dir_xyz", "c.yaml"), {"a": 1})
    check(_bad is False,
          "目录不存在 → 返回 False（不抛异常，迁移失败不该让软件打不开）")
except Exception as e:  # noqa: BLE001
    check(False, f"目录不存在时抛了 {type(e).__name__}（迁移失败不该让软件打不开）")

print()
print("[12] 原子性：落盘必须是「新文件整体换上去」，不是原地截断重写")
# 判据用 inode：os.replace 会换成临时文件的 inode；裸 open(path,"w") 原地截断
# 则 inode 不变。原地重写的风险不在"慢"，在于截断之后、写完之前崩溃 —— 那时
# 盘上就是一个半截 YAML，之后所有 save() 都会因为解析失败而永久失败（P0）。
fd, p12 = tempfile.mkstemp(suffix=".yaml", text=True)
os.close(fd)
try:
    with open(p12, "w", encoding="utf-8") as f:
        f.write("old: 1\n")
    ino_before = os.stat(p12).st_ino
    check(cm.dump_config_atomic(p12, {"new": 2}) is True, "写盘成功")
    check(os.stat(p12).st_ino != ino_before,
          "目标文件被整体替换（inode 变了）——原地重写则 inode 不变")
    with open(p12, encoding="utf-8") as f:
        check("new: 2" in f.read(), "内容已更新")
finally:
    os.unlink(p12)
src_cm = open(os.path.join(ROOT, "src", "config_migrate.py"), encoding="utf-8").read()
check("os.replace(" in src_cm, "实现里用了 os.replace")
check("os.fsync(" in src_cm, "实现里 fsync 过（掉电也不会留半截）")

print()
print("[10] 接线：webapp 启动路径必须调唯一入口，且不许再有裸写配置")
web_txt = open(os.path.join(ROOT, "src", "ui", "webapp.py"),
               encoding="utf-8").read()
check("migrate_stale_tts_config(" in web_txt,
      "webapp 调用了 migrate_stale_tts_config（唯一入口）")
check('io.open(self._config_path, "w"' not in web_txt,
      "webapp 不再有裸 io.open(config, 'w') 直写")
check("dump_config_atomic(self._config_path" in web_txt,
      "webapp 的迁移写盘走 dump_config_atomic")
# 断言必须匹配**完整调用串**：早先只查 "backup=True" 是否出现，结果被我自己的
# 注释（"backup=True：迁移自动改…"）满足了——变异摘掉实参后测试照样绿。
check("dump_config_atomic(self._config_path, _raw_mig, backup=True)" in web_txt,
      "迁移写盘带 backup=True（自动改用户配置必须留后路，yaml.dump 会丢注释）")
check("migrate_stale_tts_model(_raw_mig)" not in web_txt,
      "webapp 不再只跑模型那一半（那正是死代码的来源）")

print()
print("[10b] 接线（运行时）：真造一个 CharacterAPI，看盘上文件到底变没变")
# 【2026-11-10 补·本门最大的洞】
# [10] 全是**源码文本**断言，只证明"那行字符串还在文件里"。探针把调用改写成
#
#     _mig = []
#     if False:
#         _mig = migrate_stale_tts_config(_raw_mig)
#
# 之后，字符串原封不动躺在文件里，[10] 五条全绿，而迁移从此永不执行 ——
# 这与 2026-10-19 那次"函数写了没接线"是**同一种失效**，只是藏在 if False 后面。
# 判据必须落在行为上：造一个真实例，配置里塞失效模型，看盘上的文件改没改。
import yaml as _yaml_rt  # noqa: E402

_CA = None
try:
    from src.ui.webapp import CharacterAPI as _CA  # noqa: E402
except Exception as e:  # noqa: BLE001
    check(False, f"导入 CharacterAPI 失败（{type(e).__name__}: {e}）")
if _CA is not None:
    d10 = tempfile.mkdtemp(prefix="cfg_mig_rt_")
    p10 = os.path.join(d10, "config.yaml")
    try:
        with open(p10, "w", encoding="utf-8") as f:
            f.write("voice:\n"
                    "  clone:\n"
                    "    cloud:\n"
                    "      dashscope_cosy:\n"
                    "        model_name: qwen-audio-3.0-tts-plus\n"
                    "voice_profiles:\n"
                    "- id: rt\n"
                    "  engine: dashscope_cosy\n"
                    "  model: cosyvoice-v3.5-plus\n"
                    "  voice_id: cosyvoice-v3.5-plus-bailian-e683d3f9\n")
        _CA(config_path=p10)
        try:
            _a10 = _yaml_rt.safe_load(open(p10, encoding="utf-8")) or {}
            _cosy10 = (((_a10.get("voice") or {}).get("clone") or {})
                       .get("cloud") or {}).get("dashscope_cosy") or {}
            _m10 = _cosy10.get("model_name") if isinstance(_cosy10, dict) else None
            _profs10 = _a10.get("voice_profiles") or [{}]
            _v10 = (_profs10[0] or {}).get("voice_id")
        except Exception as e:  # noqa: BLE001  配置被写坏时别崩，让判据红
            _m10 = _v10 = f"<解析失败 {type(e).__name__}>"
        check(_m10 == cm.TTS_MIGRATE_TO,
              f"CharacterAPI 启动后盘上的模型名已迁移（实际 {_m10}）")
        check(_v10 == cm.TTS_MIGRATE_VOICE,
              f"CharacterAPI 启动后盘上的音色档已迁移（实际 {_v10}）")
        check(os.path.exists(p10 + ".bak"),
              "CharacterAPI 自动迁移前留了 .bak（用户没点任何按钮，必须留后路）")
    finally:
        shutil.rmtree(d10, ignore_errors=True)

print()
print("[11] 入口自身必须真的调用两半（源码级，防以后又拆开只留一半）")
cm_txt = open(os.path.join(ROOT, "src", "config_migrate.py"),
              encoding="utf-8").read()
entry = cm_txt.split("def migrate_stale_tts_config", 1)[-1].split("\ndef ", 1)[0]
check("migrate_stale_tts_model(" in entry, "入口里调了模型迁移")
check("migrate_stale_voice_profiles(" in entry, "入口里调了音色档迁移")

print()
print("[14] 迁移前自动备份：自动改用户配置时留后路（yaml.dump 会丢注释）")
fd, p14 = tempfile.mkstemp(suffix=".yaml", text=True)
os.close(fd)
try:
    with open(p14, "w", encoding="utf-8") as f:
        f.write("# 用户手写的注释\nold: 1\n")
    bak = p14 + ".bak"
    check(not os.path.exists(bak), "起点没有 .bak")
    check(cm.dump_config_atomic(p14, {"new": 2}, backup=True) is True, "带备份写盘成功")
    check(os.path.exists(bak), "产生了 .bak")
    # 【2026-11-10 补】`backup` 参数一旦失效，这里原来直接 open 就崩在
    # FileNotFoundError 上：门没有汇总行，变异探针只能判成"崩了"而不是
    # "某条判据红了"。崩掉的门说不清是哪条判据在守，必须自己接住。
    try:
        with open(bak, encoding="utf-8") as f:
            _bak_txt = f.read()
    except OSError as e:
        _bak_txt = f"<读不到 .bak: {type(e).__name__}>"
    check("# 用户手写的注释" in _bak_txt, ".bak 保留了原文（含注释）")
    # 已有 .bak 时不许覆盖：反复启动不能把「最初那份」冲掉
    with open(bak, "w", encoding="utf-8") as f:
        f.write("first_backup\n")
    cm.dump_config_atomic(p14, {"new": 3}, backup=True)
    with open(bak, encoding="utf-8") as f:
        check(f.read() == "first_backup\n", "已有 .bak 不被覆盖（保留最初那份）")
    # 不带 backup 时不产生新备份
    try:
        os.unlink(bak)
    except OSError:
        # 测试收尾清理：备份没生成时这里可能不存在
        pass
    cm.dump_config_atomic(p14, {"new": 4})
    check(not os.path.exists(bak), "backup=False 时不产生 .bak")
finally:
    for _p in (p14, p14 + ".bak"):
        try:
            os.unlink(_p)
        except OSError:
            # 测试收尾清理：文件不存在就不用删
            pass

print()
print("[13] 根因闸门：出厂模板 config.release.yaml 不许自带失效 TTS 模型")
# 迁移只是事后补救。真正让每个新用户一装就 400 的，是**出厂模板本身**写着已下线的
# 模型（实测 config.release.yaml 里曾同时有 cosyvoice-v3.5-plus 与
# cosyvoice-v3.5-flash）。这条闸门守的是根因：模板里再出现失效值就红。
import yaml  # noqa: E402

rel_path = os.path.join(ROOT, "config.release.yaml")
rel = yaml.safe_load(open(rel_path, encoding="utf-8")) or {}
stale_hits: list[str] = []


def _scan(o, path="$"):
    if isinstance(o, dict):
        for k, v in o.items():
            if (k in ("model", "model_name") and isinstance(v, str)
                    and v.strip().lower() in cm.STALE_TTS_MODELS):
                stale_hits.append(f"{path}.{k}={v}")
            elif (k in ("voice_id", "voice") and isinstance(v, str)
                  and ("cosyvoice" in v.lower() or "qwen-audio" in v.lower())):
                stale_hits.append(f"{path}.{k}={v[:36]}…")
            else:
                _scan(v, f"{path}.{k}")
    elif isinstance(o, list):
        for i, x in enumerate(o):
            _scan(x, f"{path}[{i}]")


_scan(rel)
check(not stale_hits, f"出厂模板无失效模型/音色（实际 {stale_hits}）")
# 反向确认：模板里确实还写着正确的模型，不是被整段删了
check("qwen3-tts-flash" in open(rel_path, encoding="utf-8").read(),
      "模板里确实写着 qwen3-tts-flash（不是靠删配置过关）")

print()
sys.exit(_finish())
