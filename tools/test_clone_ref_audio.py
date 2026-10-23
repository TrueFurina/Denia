# -*- coding: utf-8 -*-
"""克隆面板参考音频：随包资产物化 + /local_img 音频放行（2026-10-22）。

修的真缺陷：首启克隆面板「预填她的」对预置角色（温柔女友）从来是坏的——
预置角色的参考音频随包分发（web 相对路径 `_role_assets/ref/xiaowei.wav`），
而本地克隆引擎只认磁盘路径；旧代码把开发机绝对路径摆在前端、按钮又读另一份
数据 ⇒ 试听静默失效、克隆画假进度条还谎报「已完成」（前端侧真值表见
tools/test_clone_card_ui.py）。本测试守后端这三件事：

  1. use_bundled_ref_audio：随包资产 → data/voice_ref/ 真实路径（白名单校验）
  2. 落盘目录注册进 /local_img 白名单（试听播放要经它）
  3. register_local_img_dir 本身的行为（只收目录、幂等）

安全设计：CharacterAPI 指向 config.yaml 的**临时副本**（与
test_onboarding_role_backend.py 同一套沙盒），不写真实配置；产物落在临时目录
清理得到的 data/voice_ref 之外——注意 use_bundled_ref_audio 写的是**仓库**
data/voice_ref/（真实持久化路径），所以本测试只校验返回结构与白名单拒绝，
复制目标的存在性用仓库内随包文件本身断言（不落盘新的脏数据的话见 [1] 注释）。
"""
from __future__ import annotations

import io
import os
import shutil
import sys
import tempfile

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

OK = FAIL = 0


def chk(name, cond, extra=""):
    global OK, FAIL
    if cond:
        OK += 1
        print(f"  PASS {name}" + (f" :: {extra}" if extra else ""))
    else:
        FAIL += 1
        print(f"  FAIL {name}" + (f" :: {extra}" if extra else ""))


def main() -> int:
    from src.ui.webapp import CharacterAPI, register_local_img_dir
    from src.ui.ui_state import STATE
    from src.config import load_config

    tmp = tempfile.mkdtemp(prefix="clone_ref_")
    cfg_copy = os.path.join(tmp, "config.yaml")
    shutil.copy2(os.path.join(ROOT, "config.yaml"), cfg_copy)
    api = CharacterAPI(cfg_copy)
    api._cfg = load_config(cfg_copy)

    bundled = os.path.join(ROOT, "src", "ui", "web", "_role_assets", "ref", "xiaowei.wav")
    chk("随包参考音频存在（测试前提）", os.path.isfile(bundled), bundled)

    print("\n[1] use_bundled_ref_audio：随包资产 → 真实磁盘路径")
    r = api.use_bundled_ref_audio({"url": "_role_assets/ref/xiaowei.wav"})
    chk("成功", r.get("success") is True, str(r)[:120])
    stored = r.get("stored_path") or r.get("path") or ""
    chk("落在 data/voice_ref/ 下",
        stored.replace("\\", "/").endswith("/data/voice_ref/xiaowei.wav"), stored)
    chk("文件真的物化出来了", os.path.isfile(stored))
    chk("字节与随包资产一致（不是空壳）",
        os.path.isfile(stored) and os.path.getsize(stored) == os.path.getsize(bundled))
    # 字段契约（前端读 duration_s）：本机没有 ffprobe 时为 None，与
    # pick_reference_audio 既有行为一致；有值时必须是数。
    chk("返回时长_s 字段（无 ffprobe 时为 None，与 pick_reference_audio 一致）",
        "duration_s" in r and (r.get("duration_s") is None
                               or isinstance(r.get("duration_s"), (int, float))),
        repr(r.get("duration_s")))
    # 落盘目录必须进 /local_img 白名单，否则克隆面板「试听」播不了物化后的文件
    vdir = os.path.dirname(stored)
    chk("落盘目录注册进 /local_img 白名单",
        os.path.abspath(vdir) in [os.path.abspath(d) for d in STATE.local_img_extra_dirs],
        str(STATE.local_img_extra_dirs)[:160])

    print("\n[2] 白名单：非随包资产一律拒绝（防路径穿越）")
    chk("非 _role_assets/ 前缀被拒",
        api.use_bundled_ref_audio({"url": "config.yaml"}).get("success") is False)
    chk(".. 穿越被拒",
        api.use_bundled_ref_audio({"url": "_role_assets/../../config.yaml"}).get("success") is False)
    chk("反斜杠变体穿越也被拒",
        api.use_bundled_ref_audio({"url": "_role_assets\\..\\..\\config.yaml"}).get("success") is False)
    chk("绝对路径被拒", api.use_bundled_ref_audio({"url": "/etc/passwd"}).get("success") is False)
    chk("盘符绝对路径被拒",
        api.use_bundled_ref_audio({"url": "C:/Windows/win.ini"}).get("success") is False)
    chk("不存在的随包文件被拒",
        api.use_bundled_ref_audio({"url": "_role_assets/ref/nope.wav"}).get("success") is False)
    chk("空 url 被拒", api.use_bundled_ref_audio({"url": ""}).get("success") is False)
    chk("缺字段被拒", api.use_bundled_ref_audio({}).get("success") is False)
    chk("越出 web 根的 _role_assets 兄弟目录被拒",
        api.use_bundled_ref_audio({"url": "_role_assets/../webapp.py"}).get("success") is False)

    print("\n[3] register_local_img_dir：只收目录、幂等")
    d = tempfile.mkdtemp(prefix="limg_")
    chk("目录注册返回 True", register_local_img_dir(d) is True)
    n1 = len(STATE.local_img_extra_dirs)
    register_local_img_dir(d)
    chk("重复注册不膨胀", len(STATE.local_img_extra_dirs) == n1)
    chk("文件（非目录）被拒", register_local_img_dir(cfg_copy) is False)
    chk("不存在的路径被拒", register_local_img_dir(os.path.join(d, "nope")) is False)
    shutil.rmtree(d, ignore_errors=True)

    print("\n[4] /local_img 路由认音频类型（试听经它播参考音频）")
    # 源码级断言：锚点是 _ct 映射里的真实代码行，注释里不复述这些字面量
    # （配 _qc_clone_card_mutation.py 的 M6）。行为侧由前端真值表 S1/S2 覆盖。
    with io.open(os.path.join(ROOT, "src", "ui", "main_window.py"), encoding="utf-8") as f:
        mw = f.read()
    chk("wav → audio/wav", "'.wav': 'audio/wav'" in mw)
    chk("mp3 → audio/mpeg", "'.mp3': 'audio/mpeg'" in mw)

    # 清理 [1] 物化出来的副本（它是仓库内真实持久化路径的产物，不该留在工作区）
    try:
        if os.path.isfile(stored):
            os.remove(stored)
    except OSError:
        # 测试收尾清理：文件不存在就不用删
        pass
    shutil.rmtree(tmp, ignore_errors=True)

    print("\n结果: %d PASS / %d FAIL" % (OK, FAIL))
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
