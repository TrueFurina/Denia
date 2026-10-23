# -*- coding: utf-8 -*-
"""配置里的 `${VAR}` 引用：展开、缺失提示、保存不落盘。"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from src.config import load_config  # noqa: E402

FAIL = 0


def check(cond, msg):
    global FAIL
    print(("  ✅ " if cond else "  ❌ ") + msg)
    if not cond:
        FAIL += 1


def write_tmp(text: str) -> str:
    fd, p = tempfile.mkstemp(suffix=".yaml", text=True)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(text)
    return p


YAML = """\
llm:
  provider: openai
  openai:
    base_url: https://open.bigmodel.cn/api/paas/v4
    model: glm-4-flash
    api_key: ${DANIYA_TEST_KEY}
cascade:
  base_url: https://example.com/v1
  model: m
  api_key: ${DANIYA_TEST_KEY}
  persona_file: ''
  behavior_file: ''
"""

print("① 环境变量存在 → 展开成明文")
os.environ["DANIYA_TEST_KEY"] = "sk-test-12345"
p = write_tmp(YAML)
cfg = load_config(p)
check(cfg.raw["llm"]["openai"]["api_key"] == "sk-test-12345", "llm.openai.api_key 已展开")
check(cfg.raw["cascade"]["api_key"] == "sk-test-12345", "cascade.api_key 已展开")
check(not [i for i in cfg.config_issues if "环境变量" in str(i)], "无缺失告警")

print("② save() 后文件里仍是 ${VAR}，不含明文")
cfg.save()
body = open(p, encoding="utf-8").read()
check("${DANIYA_TEST_KEY}" in body, "模板被写回")
check("sk-test-12345" not in body, "明文 key 没有落盘")

print("③ 环境变量缺失 → 空串 + 可见告警（不抛异常）")
os.environ.pop("DANIYA_TEST_KEY", None)
p2 = write_tmp(YAML)
cfg2 = load_config(p2)
check(cfg2.raw["llm"]["openai"]["api_key"] == "", "缺变量时展开为空串")
hit = [i for i in cfg2.config_issues if "DANIYA_TEST_KEY" in str(i)]
check(bool(hit), f"config_issues 里点名了缺的变量：{hit[:1]}")

print("④ 用户改过值 → save 时以新值为准（不还原成模板）")
os.environ["DANIYA_TEST_KEY"] = "sk-old"
p3 = write_tmp(YAML)
cfg3 = load_config(p3)
cfg3.raw["llm"]["openai"]["api_key"] = "sk-new-manual"
cfg3.save()
body3 = open(p3, encoding="utf-8").read()
check("sk-new-manual" in body3 or body3.count("dpapi:") >= 1, "新值被写入（可能已 DPAPI 加密）")
check(body3.count("${DANIYA_TEST_KEY}") == 1, "未改动的 cascade 仍保留模板")

for q in (p, p2, p3):
    try:
        os.unlink(q)
    except OSError:
        # 测试收尾清理：文件不存在就不用删
        pass

print()
if FAIL:
    print(f"✗ {FAIL} 项失败")
    raise SystemExit(1)
print("✅ 全部通过")
