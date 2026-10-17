"""本地模型下载器后端自测（2026-10-12 · B2 落码）

覆盖 model_catalog 新增的四个能力（不改变既有下载逻辑）：
  1. model_dir_info      —— 目录体检：剩余空间 + 已安装档位
  2. delete_model_quant  —— 卸载（只删精确匹配的 .gguf）
  3. stop_model_download —— 暂停(delete_partial=False) / 取消(delete_partial=True)
  4. get_download_status —— 停止后的状态语义

红线：取消只删「形如 <目标文件>* 且不等于目标文件」的残留（.incomplete/分片），
      绝不动已成形的 .gguf —— 否则会误删用户已下好的模型。
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.companion import model_catalog as mc  # noqa: E402

OK = FAIL = 0


def chk(name, cond, extra=""):
    global OK, FAIL
    if cond:
        OK += 1
        print("  PASS", name, extra)
    else:
        FAIL += 1
        print("  FAIL", name, extra)


def main():
    print("[1] model_dir_info")
    d = tempfile.mkdtemp(prefix="mdltest_")
    info = mc.model_dir_info(d)
    chk("返回 success", info.get("success") is True)
    chk("目录存在", info.get("exists") is True)
    chk("有剩余空间", info.get("free_gb") is not None, str(info.get("free_gb")))
    chk("未安装任何档", info.get("installed") == [])
    info2 = mc.model_dir_info(os.path.join(d, "not_exist_dir", "x"))
    chk("目录不存在也能体检（向上找父盘）",
        info2.get("exists") is False and info2.get("free_gb") is not None)

    print("[2] delete_model_quant（未下载）")
    r = mc.delete_model_quant("Q4_K_M", d)
    chk("未下载时安全失败", r.get("success") is False and "不存在" in str(r.get("error")))
    chk("未知档位拒绝", mc.delete_model_quant("NOPE", d).get("success") is False)

    print("[3] 造假文件 → 判定已安装 + 卸载")
    q = mc.QUANTS[0]
    p = os.path.join(d, q["file"])
    with open(p, "wb") as f:
        f.write(b"\0" * (2 * 1024 * 1024))
    info3 = mc.model_dir_info(d)
    chk("识别为已安装", any(i["id"] == q["id"] for i in info3.get("installed", [])))
    r2 = mc.delete_model_quant(q["id"], d)
    chk("卸载成功", r2.get("success") is True, str(r2))
    chk("文件确实删除", not os.path.isfile(p))

    print("[4] stop_model_download（无任务安全）")
    chk("空 task_id 安全", mc.stop_model_download("").get("success") is False)
    chk("不存在任务安全", mc.stop_model_download("dl_nope_1").get("success") is False)

    print("[5] 取消（delete_partial=True）：只清残留，不动正式文件")
    tid = "dl_test_1"
    with open(p, "wb") as f:
        f.write(b"\0" * 1024)
    part = p + ".incomplete"
    with open(part, "wb") as f:
        f.write(b"\0" * 512)
    with mc._download_lock:
        mc._download_state[tid] = {"status": "running", "log": "x",
                                   "model_dir": d, "file": q["file"], "quant_id": q["id"]}
    r3 = mc.stop_model_download(tid, delete_partial=True)
    chk("取消成功", r3.get("success") is True, str(r3))
    chk("状态变 canceled", mc.get_download_status(tid).get("status") == "canceled")
    chk("残留分片被清理", not os.path.isfile(part))
    chk("正式文件未被误删", os.path.isfile(p))
    os.remove(p)

    print("[6] 暂停（delete_partial=False）：保留断点")
    with mc._download_lock:
        mc._download_state[tid] = {"status": "running", "log": "x",
                                   "model_dir": d, "file": q["file"], "quant_id": q["id"]}
    r4 = mc.stop_model_download(tid, delete_partial=False)
    chk("暂停成功", r4.get("success") is True)
    chk("状态变 paused", mc.get_download_status(tid).get("status") == "paused")

    print("\n结果: %d PASS / %d FAIL" % (OK, FAIL))
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
