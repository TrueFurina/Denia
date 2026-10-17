"""dump src.models 的两个视图为 JSON，便于人眼/diff 核对（只读）。"""
import json, os, sys
_H = os.path.dirname(os.path.abspath(__file__)); _R = os.path.dirname(_H)
sys.path.insert(0, _R)
import yaml
from src.models import build_model_view, build_voice_view, summarize

with open(os.path.join(_R, "config.yaml"), encoding="utf-8") as f:
    raw = yaml.safe_load(f)

out = {
    "summary": summarize(raw),
    "model_view": build_model_view(raw),
    "voice_view": build_voice_view(raw),
}
dst = os.path.join(_R, "docs", "models_layer_view.json")
with open(dst, "w", encoding="utf-8") as f:
    json.dump(out, f, ensure_ascii=False, indent=2)
print("written:", dst)
print(json.dumps(out["summary"], ensure_ascii=False, indent=2))
print("--- voice.active ---")
print(json.dumps(out["voice_view"]["active"], ensure_ascii=False, indent=2))
print("--- voice.profiles ---")
print(json.dumps(out["voice_view"]["profiles"], ensure_ascii=False, indent=2))
