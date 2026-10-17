# -*- coding: utf-8 -*-
"""
steamvr_vmc_bridge.py — SteamVR 追踪器 → VMC 协议 免费转发器

作用：读取 SteamVR 追踪器数据（Pico Connect 全身动捕注入的虚拟追踪器），
      转成 VMC 协议广播到 UDP 39539，Unity 的 VMCReceiveRecorder 直接接收。
      绕开 VMC 免费版"发送外部动作"付费墙 和 Tracking World 兼容性问题。

数据源（Pico 4 Ultra + PTM，Pico Connect 串流后 SteamVR 里可见）：
  - HMD          -> Head
  - Controller L -> LeftHand / Controller R -> RightHand
  - GenericTracker（按高度分配）: 最高 = Hips, 其余按 X 分 LeftFoot / RightFoot

用法：
    python steamvr_vmc_bridge.py            # 默认 127.0.0.1:39539
    python steamvr_vmc_bridge.py 39540      # 自定义端口

依赖：pip install openvr   （需要 SteamVR 正在运行）
"""
import json
import math
import socket
import sys
import time

import openvr

HOST = "127.0.0.1"
PORT = 39539
RATE = 60  # 发送频率 Hz


# ---------------- 坐标转换：OpenVR(右手系) -> Unity/VMC(左手系, Z 镜像) ----------------
def mat_to_pos_rot(m):
    """HmdMatrix34_t（OpenVR 列主序，每行是一列）-> (pos_unity, quat_unity)。
    旋转矩阵 R[i][j] = m[j][i]；再做右手->左手 Z 镜像：R'[i][j] = R[i][j]*s[i]*s[j], s=(1,1,-1)。"""
    pos = (m[0][3], m[1][3], -m[2][3])
    # 列主序读出后镜像：
    # r00=R00*1, r01=R01*1, r02=R02*(-1)
    # r10=R10*1, r11=R11*1, r12=R12*(-1)
    # r20=R20*(-1), r21=R21*(-1), r22=R22*1
    r00, r01, r02 = m[0][0], m[1][0], -m[2][0]
    r10, r11, r12 = m[0][1], m[1][1], -m[2][1]
    r20, r21, r22 = -m[0][2], -m[1][2], m[2][2]
    quat = mat_to_quat(r00, r01, r02, r10, r11, r12, r20, r21, r22)
    return pos, quat


def mat_to_quat(r00, r01, r02, r10, r11, r12, r20, r21, r22):
    """旋转矩阵 -> 四元数 (x, y, z, w)"""
    tr = r00 + r11 + r22
    if tr > 0:
        s = math.sqrt(tr + 1.0) * 2
        return ((r21 - r12) / s, (r02 - r20) / s, (r10 - r01) / s, 0.25 * s)
    if r00 > r11 and r00 > r22:
        s = math.sqrt(1.0 + r00 - r11 - r22) * 2
        return (0.25 * s, (r01 + r10) / s, (r02 + r20) / s, (r21 - r12) / s)
    if r11 > r22:
        s = math.sqrt(1.0 + r11 - r00 - r22) * 2
        return ((r01 + r10) / s, 0.25 * s, (r12 + r21) / s, (r02 - r20) / s)
    s = math.sqrt(1.0 + r22 - r00 - r11) * 2
    return ((r02 + r20) / s, (r12 + r21) / s, 0.25 * s, (r10 - r01) / s)


# ---------------- 设备识别 ----------------
def device_name(vrsystem, idx):
    try:
        return vrsystem.getStringTrackedDeviceProperty(
            idx, openvr.Prop_RenderModelName_String)
    except Exception:
        return "?"


# Pico 全身动捕 8 点虚拟追踪器的 SteamVR device index 映射
# （由 --scan 实测确认：1=髋 4=胸 2/3=左右脚踝 7/8=手肘 5/6=膝盖[位置异常不用]）
# 注意：重新连接 Pico 串流后索引可能变化，跑 --scan 重新确认，改了同步改这里。
DEFAULT_TRACKER_MAP = {
    1: "Hips",
    4: "Chest",
    2: "LeftFoot",
    3: "RightFoot",
    5: "LeftKnee",
    6: "RightKnee",
    7: "LeftElbow",
    8: "RightElbow",
}

# 实际发送的骨骼（Unity 端映射得上的才发）
SEND_BONES = ("Head", "Chest", "LeftHand", "RightHand", "LeftFoot", "RightFoot",
              "LeftElbow", "RightElbow", "LeftKnee", "RightKnee")


def _pos_of(poses, idx):
    if idx >= len(poses):
        return None
    m = poses[idx].mDeviceToAbsoluteTracking
    return (m[0][3], m[1][3], m[2][3])


def _close(a, b, thresh=0.25):
    if a is None or b is None:
        return False
    return sum((x - y) ** 2 for x, y in zip(a, b)) < thresh * thresh


def _skeleton_ok(vrsystem, poses, mapping):
    """站立骨架合理性校验（供固定索引/动态映射共用）：
    Hips 居中(相对 HMD，用户可站在世界任意位置)、
    垂直链 Chest > Hips > Knee > Foot。坐姿/堆叠时返回 False。"""
    hip = _pos_of(poses, mapping.get("Hips"))
    chest = _pos_of(poses, mapping.get("Chest"))
    if not hip or not chest:
        return False
    knee_ys = [_pos_of(poses, mapping.get(k))[1]
               for k in ("LeftKnee", "RightKnee")
               if _pos_of(poses, mapping.get(k))]
    foot_ys = [_pos_of(poses, mapping.get(k))[1]
               for k in ("LeftFoot", "RightFoot")
               if _pos_of(poses, mapping.get(k))]
    if not knee_ys or not foot_ys:
        return False
    hmd_x = 0.0
    for i in range(openvr.k_unMaxTrackedDeviceCount):
        try:
            if (i < len(poses) and poses[i].bPoseIsValid
                    and vrsystem.getTrackedDeviceClass(i)
                    == openvr.TrackedDeviceClass_HMD):
                hmd_x = poses[i].mDeviceToAbsoluteTracking[0][3]
                break
        except Exception:
            continue
    return (abs(hip[0] - hmd_x) < 0.15
            and chest[1] > hip[1]
            and hip[1] > max(knee_ys) - 0.05
            and min(knee_ys) > max(foot_ys))


_last_map = {}
_locked_map = None
_stable_frames = 0
_warn_last = 0.0


def classify_devices(vrsystem, poses):
    """动态识别设备部位 + 时间平滑防抖（避免追踪器高度接近时分类跳变）。
    分类结果连续 N 帧稳定后才锁定 dev 映射——动捕激活初期追踪器位置不稳，
    首帧直接锁定会锁到错误映射（如 Hips 锁到左膝）。"""
    global _last_map, _locked_map, _stable_frames, _warn_last
    # 已锁定：直接用固定映射，不再按 Y 重排
    if _locked_map:
        if all(_pos_of(poses, idx) is not None for idx in _locked_map.values()):
            return dict(_locked_map)
        _locked_map = None  # 有设备失效（断开/重连），重新分类
        _stable_frames = 0
    out = {}
    # HMD + 控制器动态枚举
    for i in range(openvr.k_unMaxTrackedDeviceCount):
        try:
            cls = vrsystem.getTrackedDeviceClass(i)
        except Exception:
            continue
        if cls == openvr.TrackedDeviceClass_HMD:
            out["Head"] = i
        elif cls == openvr.TrackedDeviceClass_Controller:
            try:
                role = vrsystem.getControllerRoleForTrackedDeviceIndex(i)
                if role == openvr.TrackedControllerRole_LeftHand:
                    out["LeftHand"] = i
                elif role == openvr.TrackedControllerRole_RightHand:
                    out["RightHand"] = i
            except Exception:
                pass
    # 首选：固定索引映射（Pico 全身动捕标准，见 DEFAULT_TRACKER_MAP 注释）。
    # 实测 Pico 虚拟追踪器索引稳定；且 T-Pose 展开时肘高度≈胸高，按 Y 分层会把
    # Chest 与 Elbow 混淆，因此固定索引优先，通过骨架校验后直接使用。
    fixed = {}
    for idx, bone in DEFAULT_TRACKER_MAP.items():
        if idx < len(poses) and poses[idx].bPoseIsValid:
            fixed[bone] = idx
    if len(fixed) >= 8 and _skeleton_ok(vrsystem, poses, fixed):
        out.update(fixed)
    else:
        # 固定索引不可用（重连后索引变化/骨架不合理）→ 动态按 Y 分层回退
        trackers = []
        for i in range(openvr.k_unMaxTrackedDeviceCount):
            try:
                cls = vrsystem.getTrackedDeviceClass(i)
            except Exception:
                continue
            if (cls == openvr.TrackedDeviceClass_GenericTracker
                    and i < len(poses) and poses[i].bPoseIsValid):
                m = poses[i].mDeviceToAbsoluteTracking
                trackers.append((i, m[0][3], m[1][3]))
        if len(trackers) >= 7:
            trackers.sort(key=lambda t: -t[2])  # y 降序
            # 健康检查：追踪器必须按高度分开（动捕激活状态）。
            # 未激活时 Pico 会把 8 个虚拟追踪器全堆在默认点（y 几乎相同），
            # 此时分层会随机分配骨骼，且 _locked_map 会锁定错误映射。
            y_span = trackers[0][2] - trackers[-1][2]
            if y_span < 0.3:
                print("[Bridge] 追踪器 Y 跨度 %.2fm < 0.3m，疑似动捕未激活/追踪器堆叠，等待就绪..."
                      % y_span)
                return {}
            # 高度分层：Chest(最高) > Elbow > Hips > Knee > Foot(最低)
            out["Chest"] = trackers[0][0]
            elbows = sorted(trackers[1:3], key=lambda t: t[1])
            out["LeftElbow"], out["RightElbow"] = elbows[0][0], elbows[1][0]
            out["Hips"] = trackers[3][0]
            knees = sorted(trackers[4:6], key=lambda t: t[1])
            out["LeftKnee"], out["RightKnee"] = knees[0][0], knees[1][0]
            if len(trackers) >= 8:
                feet = sorted(trackers[6:8], key=lambda t: t[1])
                out["LeftFoot"], out["RightFoot"] = feet[0][0], feet[1][0]
        else:
            out.update(fixed)  # 兜底：固定索引映射
    # 时间平滑：若骨骼的旧设备与新设备位置接近，沿用旧分配，防分类跳变
    if _last_map:
        for bone, idx in list(out.items()):
            old = _last_map.get(bone)
            if old is not None and old != idx and _close(_pos_of(poses, old), _pos_of(poses, idx)):
                out[bone] = old
    # 分类稳定性判定：平滑后与上一帧的关键骨骼映射一致才算稳定
    stable = False
    if _last_map and len(out) >= 8:
        keys = ("Chest", "Hips", "LeftFoot", "RightFoot",
                "LeftKnee", "RightKnee", "LeftElbow", "RightElbow")
        stable = all(out.get(k) == _last_map.get(k) for k in keys)
    _last_map = dict(out)
    # 骨架合理性校验（站立标准）：Hips 居中(相对 HMD)、垂直链 Chest>Hips>Knee>Foot。
    # 坐姿时膝盖高于髋、"按 Y 分层"会把 Hips/Knee 混淆，此时禁止锁定。
    reasonable = _skeleton_ok(vrsystem, poses, out)
    if not reasonable:
        if len(out) >= 8 and time.time() - _warn_last > 5:
            print("[Bridge] 骨架不合理（需站直 T-Pose：Hips 居中、Chest>Hips>Knee>Foot），"
                  "请站直等待锁定...")
            _warn_last = time.time()
        _stable_frames = 0
    else:
        # 连续 30 帧（约 0.5 秒）分类稳定且骨架合理后才锁定 dev 映射，
        # 避免动捕激活初期追踪器分布不稳或坐姿导致锁定错误映射
        _stable_frames = _stable_frames + 1 if stable else 0
        if _stable_frames >= 30:
            _locked_map = dict(out)
            print("[Bridge] 设备映射已锁定（连续 %d 帧稳定，%d 个部位）"
                  % (_stable_frames, len(out)))
    # 校验 Hips 位置合理（y 应在髋部高度范围），否则提示
    if "Hips" in out:
        y = _pos_of(poses, out["Hips"])[1]
        if not (0.05 <= y <= 1.2):
            print("[Bridge] 警告：Hips 位置异常 (y=%.2f)，追踪器分配可能出错" % y)
    return out


# ---------------- 扫描模式：打印所有设备位置 ----------------
def scan_devices(vrsystem):
    print("== 设备扫描（保持戴好设备站 T-Pose，连续采样 3 秒）==")
    for round_no in range(3):
        poses = vrsystem.getDeviceToAbsoluteTrackingPose(
            openvr.TrackingUniverseStanding, 0.0, 0)
        print("--- 采样 %d ---" % (round_no + 1))
        for i in range(openvr.k_unMaxTrackedDeviceCount):
            try:
                c = vrsystem.getTrackedDeviceClass(i)
            except Exception:
                continue
            if c == 0:
                continue
            cls_name = {1: "HMD", 2: "Controller", 3: "GenericTracker"}.get(c, str(c))
            name = "?"
            try:
                name = vrsystem.getStringTrackedDeviceProperty(
                    i, openvr.Prop_RenderModelName_String)
            except Exception:
                pass
            pos = rot = "N/A"
            if i < len(poses) and poses[i].bPoseIsValid:
                m = poses[i].mDeviceToAbsoluteTracking
                pos = "(%.2f, %.2f, %.2f)" % (m[0][3], m[1][3], m[2][3])
                rot = "(%.3f, %.3f, %.3f, %.3f)" % (m[0][0], m[1][0], m[2][0], m[0][1])
            print("dev %2d  %-14s  %-30s  pos %s  rot %s"
                  % (i, cls_name, name[:30], pos, rot))
        if round_no < 2:
            time.sleep(1.0)


# ---------------- 主循环 ----------------
def main():
    port = int(sys.argv[1]) if len(sys.argv) > 1 and sys.argv[1].isdigit() else PORT

    vrsystem = openvr.init(openvr.VRApplication_Utility)
    if "--scan" in sys.argv:
        scan_devices(vrsystem)
        openvr.shutdown()
        return
    print("[Bridge] OpenVR 初始化成功")
    print("[Bridge] SteamVR 追踪器枚举：")

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    last_print = 0.0
    frame = 0

    while True:
        poses = vrsystem.getDeviceToAbsoluteTrackingPose(
            openvr.TrackingUniverseStanding, 0.0, 0)
        mapping = classify_devices(vrsystem, poses)

        if frame % (RATE * 2) == 0 or frame == 0:
            for bone, idx in sorted(mapping.items(), key=lambda kv: kv[0]):
                print("  %-10s <- %s (dev %d)" % (bone, device_name(vrsystem, idx), idx))
            if not mapping:
                print("  [warn] 未找到任何追踪器，确认 SteamVR 运行 + Pico 串流已连接")
            # HMD 旋转诊断：确认 SteamVR 是否真的在跟踪头部旋转（排查"头不能低头/抬头"）
            for i in range(openvr.k_unMaxTrackedDeviceCount):
                try:
                    cls = vrsystem.getTrackedDeviceClass(i)
                except Exception:
                    continue
                if (cls == openvr.TrackedDeviceClass_HMD and i < len(poses)
                        and poses[i].bPoseIsValid):
                    m = poses[i].mDeviceToAbsoluteTracking
                    pos, quat = mat_to_pos_rot(m)
                    print("  [HMD] dev %d pos=(%.2f, %.2f, %.2f) rot(quat)=x%.3f y%.3f z%.3f w%.3f"
                          % (i, pos[0], pos[1], pos[2], quat[0], quat[1], quat[2], quat[3]))
            # 追踪器位置分布（诊断：确认各部位 Y 高度是否合理）
            lines = []
            for i in range(openvr.k_unMaxTrackedDeviceCount):
                try:
                    cls = vrsystem.getTrackedDeviceClass(i)
                except Exception:
                    continue
                if (cls == openvr.TrackedDeviceClass_GenericTracker
                        and i < len(poses) and poses[i].bPoseIsValid):
                    m = poses[i].mDeviceToAbsoluteTracking
                    lines.append("    dev %d y=%.2f (%.2f, %.2f, %.2f)" % (i, m[1][3], m[0][3], m[1][3], m[2][3]))
            if lines:
                print("  [追踪器] 按Y排序：")
                for l in sorted(lines, key=lambda s: -float(s.split('y=')[1].split(' ')[0])):
                    print(l)
            print("---")

        # 组装 VMC 消息（先 RootTransform 再 BoneTransform）
        msgs = []
        if "Hips" in mapping:
            i = mapping["Hips"]
            p = poses[i]
            if p.bPoseIsValid:
                m = p.mDeviceToAbsoluteTracking
                pos, rot = mat_to_pos_rot(m)
                # 调试：确认 raw(OpenVR 世界坐标) 与 发出的 VMC 坐标一致
                if frame % 60 == 0:
                    print("[DBG] Hips raw=(%.3f, %.3f, %.3f) -> vmc=(%.3f, %.3f, %.3f)"
                          % (m[0][3], m[1][3], m[2][3], pos[0], pos[1], pos[2]))
                msgs.append({"type": "RootTransform", "name": "",
                             "position": list(pos), "rotation": list(rot)})

        for bone in SEND_BONES:
            i = mapping.get(bone)
            if i is None:
                continue
            p = poses[i]
            if not p.bPoseIsValid:
                continue
            m = p.mDeviceToAbsoluteTracking
            pos, rot = mat_to_pos_rot(m)
            if bone in ("Chest", "Head", "LeftHand", "RightHand") and frame % 60 == 0:
                print("[DBG] %-9s raw=(%.3f, %.3f, %.3f) -> vmc=(%.3f, %.3f, %.3f)"
                      % (bone, m[0][3], m[1][3], m[2][3], pos[0], pos[1], pos[2]))
            msgs.append({"type": "BoneTransform", "name": bone,
                         "position": list(pos), "rotation": list(rot)})

        for msg in msgs:
            sock.sendto(json.dumps(msg).encode("utf-8"), (HOST, port))

        frame += 1
        if time.time() - last_print > 5.0:
            print("[Bridge] 运行中：%d 帧/5s，发送 %d 条消息到 %s:%d"
                  % (frame, len(msgs), HOST, port))
            last_print = time.time()
            frame = 0

        time.sleep(1.0 / RATE)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n[Bridge] 已停止")
    except Exception as e:
        print("[Bridge] 错误: %s" % e)
        try:
            openvr.shutdown()
        except Exception:
            pass
        sys.exit(1)
