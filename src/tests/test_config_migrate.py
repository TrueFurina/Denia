# -*- coding: utf-8 -*-
"""失效 TTS 配置的启动期迁移：两个迁移函数 + 唯一入口 + 原子写盘。

**为什么需要这组测试**

合并队友工作树（2026-10-19）时静态检查抓到：`migrate_stale_voice_profiles`
在 `webapp.py` 里定义完整、注释完整，但**全仓没有任何调用点**——用户已建的
音色档绑着已下线的 cosyvoice 模型时，试听永远 400，而启动期从没人去修它。
这是"函数写了没接线"的典型静默失效，测试看不出、日志也没有。

所以这里的重点不是"函数算得对不对"，而是：
  ① 唯一入口 `migrate_stale_tts_config` 是否**真的把两半都跑了**；
  ② `webapp` 启动路径是否**真的调了那个入口**（源码级断言）；
  ③ 写盘是否**真的原子**（不再出现裸 `io.open(path, "w")`）。
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from src import config_migrate as cm  # noqa: E402
from src.config import load_config   # noqa: E402

FAIL = 0
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def check(cond, msg):
    global FAIL
    print(("  ✓ " if cond else "  ✗ ") + msg)
    if not cond:
        FAIL += 1


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
print("[4] 音色档迁移：绑着已下线模型的档 → model 与 voice 一起换")
raw4 = {"voice_profiles": [
    {"id": "p1", "engine": "dashscope_cosy", "model": "cosyvoice-v3.5-plus",
     "voice": "longxiaochun"},
    {"id": "p2", "engine": "dashscope_cosy", "model": "qwen3-tts-flash",
     "voice": "Cherry"},
]}
changed4 = cm.migrate_stale_voice_profiles(raw4)
p1, p2 = raw4["voice_profiles"]
check(p1["model"] == cm.TTS_MIGRATE_TO and p1["voice"] == cm.TTS_MIGRATE_VOICE,
      "失效档的 model/voice 都已迁移")
check(p2["model"] == cm.TTS_MIGRATE_TO and p2["voice"] == cm.TTS_MIGRATE_VOICE,
      "已正确的档保持原样（幂等）")
check(changed4 == ["voice_profile:p1"], f"只报真正改动的档（实际 {changed4}）")

print()
print("[5] 历史脏数据：模型名被填进 voice 字段也要救")
raw5 = {"voice_profiles": [
    {"id": "dirty", "engine": "dashscope_cosy", "model": "qwen3-tts-flash",
     "voice": "cosyvoice-v2"}]}
check(cm.migrate_stale_voice_profiles(raw5) == ["voice_profile:dirty"],
      "voice 里含 cosyvoice 子串 → 判为失效并迁移")
check(raw5["voice_profiles"][0]["voice"] == cm.TTS_MIGRATE_VOICE, "已换成 Cherry")

print()
print("[6] 非 dashscope_cosy 引擎的档不许动（本地引擎没有模型下线问题）")
raw6 = {"voice_profiles": [
    {"id": "local", "engine": "pyttsx3", "model": "cosyvoice-v2", "voice": "x"}]}
check(cm.migrate_stale_voice_profiles(raw6) == [], "pyttsx3 档不迁移")
check(raw6["voice_profiles"][0]["model"] == "cosyvoice-v2", "原值未被改动")

print()
print("[7] 唯一入口：migrate_stale_tts_config 必须同时跑两半（死代码就是这样漏的）")
raw7 = {"voice": {"clone": {"cloud": {"dashscope_cosy": {
    "model_name": "qwen-audio-3.0-tts-plus"}}}},
    "cloud_tts": {"dashscope_cosy": {"model_name": "cosyvoice-v3-flash"}},
    "voice_profiles": [{"id": "p", "engine": "dashscope_cosy",
                        "model": "cosyvoice-v1", "voice": "v"}]}
all_changed = cm.migrate_stale_tts_config(raw7)
check(len(all_changed) == 3, f"三个点全部命中（实际 {len(all_changed)}: {all_changed}）")
check(raw7["voice_profiles"][0]["voice"] == cm.TTS_MIGRATE_VOICE,
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
                "    model: cosyvoice-v3.5-plus\n    voice: longxiaochun\n")
    import yaml
    with open(p8, encoding="utf-8") as f:
        raw8 = yaml.safe_load(f)
    ch = cm.migrate_stale_tts_config(raw8)
    check(bool(ch), f"有变更需要写盘（{ch}）")
    check(cm.dump_config_atomic(p8, raw8) is True, "原子写返回 True")
    cfg = load_config(p8)
    check(cfg.raw["voice"]["clone"]["cloud"]["dashscope_cosy"]["model_name"]
          == cm.TTS_MIGRATE_TO, "重新加载后模型名已是新值")
    check(cfg.get_voice_profile("p1")["voice"] == cm.TTS_MIGRATE_VOICE,
          "重新加载后音色档已是 Cherry")
finally:
    os.unlink(p8)

print()
print("[9] 原子写：不动调用方的 dict、失败不抛、目录不可写返回 False")
raw9 = {"cloud_tts": {"dashscope_cosy": {"model_name": "cosyvoice-v2"}},
        "api_key_probe": "plain"}
snapshot = repr(raw9)
fd, p9 = tempfile.mkstemp(suffix=".yaml", text=True)
os.close(fd)
try:
    check(cm.dump_config_atomic(p9, raw9) is True, "正常路径写盘成功")
    check(repr(raw9) == snapshot, "调用方 dict 未被就地改动（写的是副本）")
    with open(p9, encoding="utf-8") as f:
        check("cosyvoice-v2" in f.read(), "内容确实落盘")
finally:
    os.unlink(p9)
check(cm.dump_config_atomic(os.path.join(tempfile.gettempdir(),
                                         "no_such_dir_xyz", "c.yaml"),
                            {"a": 1}) is False,
      "目录不存在 → 返回 False（不抛异常，迁移失败不该让软件打不开）")

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
check("migrate_stale_tts_model(_raw_mig)" not in web_txt,
      "webapp 不再只跑模型那一半（那正是死代码的来源）")

print()
print("[11] 入口自身必须真的调用两半（源码级，防以后又拆开只留一半）")
cm_txt = open(os.path.join(ROOT, "src", "config_migrate.py"),
              encoding="utf-8").read()
entry = cm_txt.split("def migrate_stale_tts_config", 1)[-1].split("\ndef ", 1)[0]
check("migrate_stale_tts_model(" in entry, "入口里调了模型迁移")
check("migrate_stale_voice_profiles(" in entry, "入口里调了音色档迁移")

print()
if FAIL:
    print(f"❌ {FAIL} 项未通过")
    sys.exit(1)
print("✅ TTS 配置迁移测试全部通过")
