# -*- coding: utf-8 -*-
"""把发布真值回填进站点（不进闸门：工具脚本，非 check_*/test_*）。

典型用法：
    # 1) 出包后，从真实产物取体积与 SHA，更新真值并回填站点
    python tools/sync_release_site.py --from-exe dist/xxx_Setup.exe --from-zip dist/xxx_src.zip

    # 2) 只手工改过 site/release.json，想把它同步到页面/文档
    python tools/sync_release_site.py

    # 3) 只看会改什么，不落盘
    python tools/sync_release_site.py --dry-run

设计要点：
  · 规格表来自 _release_numbers.py，与对账闸门共用一份，不许两边各写一遍。
  · 每个锚点必须命中期望次数才替换；命中 0 次直接报错，绝不静默跳过。
  · 全程二进制写入，保留工作副本的真实 EOL（Windows 文本模式会洗行尾）。
  · 版本号永远从 src/version.py 读，不在真值里手填，避免第二份版本真值。
"""
from __future__ import annotations

import argparse
import datetime
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _release_numbers as R  # noqa: E402


def _replace_group(text: str, pattern: str, group: str, value: str,
                   expected: int) -> str:
    matches = list(re.finditer(pattern, text))
    if len(matches) != expected:
        raise RuntimeError("锚点命中 %d 次（期望 %d）：%s"
                           % (len(matches), expected, pattern[:70]))
    out, cursor = [], 0
    for m in matches:
        a, b = m.start(group), m.end(group)
        if a < 0:
            raise RuntimeError("命名组 %s 未参与匹配：%s" % (group, pattern[:70]))
        out.append(text[cursor:a])
        out.append(value)
        cursor = b
    out.append(text[cursor:])
    return "".join(out)


def sync_texts(truth: dict, dry_run: bool = False) -> list[str]:
    changed: list[str] = []
    for rel in R.SITE_FILES:
        text = R.read_site(rel)
        new = text
        for path, pattern, key, expected in R.NUMBER_SPECS:
            if path != rel:
                continue
            new = _replace_group(new, pattern, "n", R.fmt(truth[key]), expected)
        if rel == R.VERSION_SPEC[0]:
            new = _replace_group(new, R.VERSION_SPEC[1], "v", truth["version"],
                                 R.VERSION_SPEC[2])
            new = _replace_group(new, R.SHA_SOURCE_SPEC[1], "h",
                                 truth["source_sha256"], R.SHA_SOURCE_SPEC[2])
            new = _replace_group(new, R.SHA_SETUP_SPEC[1], "h",
                                 truth["setup_sha256"], R.SHA_SETUP_SPEC[2])
        if new != text:
            changed.append(rel)
            if not dry_run:
                R.write_site(rel, new)
    return changed


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--from-exe", help="安装包路径：实测体积与 SHA-256 写真值")
    ap.add_argument("--from-zip", help="源码包路径：实测体积与 SHA-256 写真值")
    ap.add_argument("--stage-size", type=float, help="装配目录体积 MB（可选）")
    ap.add_argument("--dry-run", action="store_true", help="只报告不落盘")
    args = ap.parse_args()

    truth = R.load_truth()
    truth["version"] = "v" + R.app_version()
    for flag, key, sha_key in (("from_exe", "setup_size_mb", "setup_sha256"),
                               ("from_zip", "source_size_mb", "source_sha256")):
        path = getattr(args, flag)
        if not path:
            continue
        if not os.path.isfile(path):
            print("✗ 文件不存在：%s" % path)
            return 1
        truth[key] = R.size_mb_of(path)
        truth[sha_key] = R.sha256_of(path)
        print("  · %s → %s MB / %s…" % (os.path.basename(path),
                                        R.fmt(truth[key]), truth[sha_key][:12]))
    if args.stage_size is not None:
        truth["stage_size_mb"] = round(args.stage_size, 1)
    if args.from_exe or args.from_zip or args.stage_size is not None:
        truth["measured_at"] = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")

    if not args.dry_run:
        R.save_truth(truth)
    changed = sync_texts(truth, args.dry_run)
    print("%s 真值 version=%s 安装包=%s MB 源码包=%s MB"
          % ("[dry-run] " if args.dry_run else "", truth["version"],
             R.fmt(truth["setup_size_mb"]), R.fmt(truth["source_size_mb"])))
    print("回填文件：%s" % (", ".join(changed) if changed else "无（已一致）"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
