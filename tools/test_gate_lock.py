"""check_all 互斥锁的判据（全部用临时锁文件，不碰 logs/.check_all.lock）。

背景：两个会话的变异刀同时跑会互相覆盖"读→改→还原"，把坏代码留在工作区。
这道门守的是：**同一时刻只允许一个 check_all 改源码**。

不进闸门：无（已登记 GATES）
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))

import _gate_lock as GL  # noqa: E402

OK = 0
FAIL = 0


def chk(name: str, cond: bool, detail: str = "") -> None:
    global OK, FAIL
    if cond:
        OK += 1
        print("  PASS %s" % name)
    else:
        FAIL += 1
        print("  FAIL %s%s" % (name, ("  ← " + detail) if detail else ""))


def main() -> int:
    real = GL.LOCK_PATH
    with tempfile.TemporaryDirectory() as d:
        GL.LOCK_PATH = os.path.join(d, ".check_all.lock")
        try:
            print("1) 拿锁 / 互斥 / 释放")
            ok, why = GL.acquire()
            chk("第一次拿锁成功", ok, why)
            chk("锁文件已生成", os.path.isfile(GL.LOCK_PATH))
            with open(GL.LOCK_PATH, encoding="utf-8") as f:
                info = json.load(f)
            chk("锁里写了 pid 和时间戳", info.get("pid") == os.getpid() and info.get("ts", 0) > 0)
            ok2, why2 = GL.acquire()
            chk("第二次拿锁被拒（互斥生效）", not ok2, why2)
            chk("拒绝原因带 PID", "PID" in why2, why2)
            GL.release()
            chk("自己持有的锁被释放", not os.path.exists(GL.LOCK_PATH))
            GL.release()
            chk("重复释放不抛异常", True)

            print("\n2) 陈旧锁（上次被强杀）要能被清掉")
            with open(GL.LOCK_PATH, "w", encoding="utf-8") as f:
                json.dump({"pid": 999999999, "ts": time.time() - GL.STALE_SECONDS - 60}, f)
            ok3, why3 = GL.acquire()
            chk("陈旧锁不阻塞新一轮", ok3, why3)
            GL.release()

            print("\n3) 活着的锁不能被偷")
            # 用一个**真的活着**、且不是本进程的 PID，否则会被当成"上次被强杀"清掉
            import subprocess
            other = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
            try:
                with open(GL.LOCK_PATH, "w", encoding="utf-8") as f:
                    json.dump({"pid": other.pid, "ts": time.time()}, f)
                ok4, why4 = GL.acquire()
                chk("时间戳新 + PID 活着 → 仍然拒绝", not ok4, why4)
                chk("别人的锁没被删掉", os.path.isfile(GL.LOCK_PATH))
                GL.release()
                chk("release() 不删别人的锁", os.path.isfile(GL.LOCK_PATH))
            finally:
                other.terminate()
                other.wait(timeout=5)
                if os.path.isfile(GL.LOCK_PATH):
                    os.remove(GL.LOCK_PATH)

            print("\n4) 畸形锁文件不能卡死")
            with open(GL.LOCK_PATH, "w", encoding="utf-8") as f:
                f.write("{ this is not json")
            ok5, why5 = GL.acquire()
            chk("畸形内容按陈旧处理", ok5, why5)
            GL.release()
        finally:
            GL.LOCK_PATH = real

    print("\n结果: %d PASS / %d FAIL" % (OK, FAIL))
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
