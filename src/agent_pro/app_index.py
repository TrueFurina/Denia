# -*- coding: utf-8 -*-
"""agent_pro · 通用应用索引（app_index）。

回答"别人电脑上名字不一样 / 版本迭代 / 白名单外软件怎么办"——不做任何硬编码映射，
启动时一次把系统里**所有**可启动的应用扫成索引，查询全部走本地匹配：

  ① 开始菜单 .lnk（用户 + 公共）—— Win32 应用的权威来源
  ② 注册表 Uninstall 表（HKLM/HKCU/WOW6432Node）—— DisplayName + InstallLocation/DisplayIcon
  ③ 注册表 App Paths —— 命令名 → exe
  ④ Get-StartApps —— UWP / 商店应用（AppID，shell:AppsFolder 启动）
  ⑤ data/agent_pro_aliases.json —— 手工别名（可选补充，非必需）

索引缓存到 data/agent_pro_app_index.json，TTL 过期才重扫（冷启动约 1-3s，查询毫秒级）。
"""
from __future__ import annotations

import difflib
import json
import logging
import os
import re
import subprocess
import time
from pathlib import Path
from typing import Optional

logger = logging.getLogger("agent_pro")

_INDEX_FILENAME = "agent_pro_app_index.json"
_INDEX_VERSION = 5   # 5 起保留候选 token(_alts)；版本不符则重建


# ---------------------------------------------------------------------------
# 规范化与打分（纯函数，selftest 直测）
# ---------------------------------------------------------------------------
_STRIP_TAIL = re.compile(r"(win32|win64|x64|x86|版本|software|app|application|"
                         r"\(\d+\)|\d+(\.\d+)+)$", re.I)


_FILE_DESC_CACHE: dict[str, str] = {}


def file_desc(exe: str) -> str:
    """读 exe 的本地化文件描述（notepad.exe → 「记事本」）。

    这是中英名之间**唯一通用且无需硬编码**的桥：描述文本随系统语言变化，
    不必维护「记事本→notepad」这类映射表。读不到返回空串。
    """
    if not exe:
        return ""
    key = exe.lower()
    if key in _FILE_DESC_CACHE:
        return _FILE_DESC_CACHE[key]
    val = ""
    try:
        import win32api
        trans = win32api.GetFileVersionInfo(exe, r"\VarFileInfo\Translation")
        if trans:
            lang, cp = trans[0]
            k = r"\StringFileInfo\%04x%04x\FileDescription" % (lang, cp)
            val = str(win32api.GetFileVersionInfo(exe, k) or "").strip()
    except Exception:  # noqa: BLE001
        val = ""
    _FILE_DESC_CACHE[key] = val
    return val


def normalize(name: str) -> str:
    s = (name or "").strip().lower()
    s = _STRIP_TAIL.sub("", s)  # 先去版本尾巴（此时点号/空格还在）
    s = re.sub(r"[\s_\-·\.（）()【】\[\]]+", "", s)
    return s


def score_match(target: str, name: str, exe_stem: str = "",
                desc: str = "") -> float:
    """0~100。精确=100，包含=80/65，difflib 相似度兜底。

    候选维度：索引名 / exe 文件名 / **exe 本地化描述**（中英互通的关键）。
    """
    t, n = normalize(target), normalize(name)
    if not t:
        return 0.0
    best = 0.0
    for cand in (n, normalize(exe_stem), normalize(desc)):
        if not cand:
            continue
        if cand == t:
            best = max(best, 100.0)
        elif t in cand:
            best = max(best, 80.0 + min(10.0, len(cand) - len(t)))
        elif cand in t and len(cand) >= 2:
            best = max(best, 65.0)
        else:
            best = max(best, difflib.SequenceMatcher(None, t, cand).ratio() * 55.0)
    return best


# ---------------------------------------------------------------------------
# 各来源扫描（每个都独立容错，坏一个不影响其它）
# ---------------------------------------------------------------------------
def _startmenu_lnks() -> list[dict]:
    out: list[dict] = []
    seen: set[str] = set()
    roots = []
    for env, sub in (("APPDATA", r"Microsoft\Windows\Start Menu"),
                     ("PROGRAMDATA", r"Microsoft\Windows\Start Menu")):
        base = os.environ.get(env)
        if base:
            roots.append(os.path.join(base, sub))
    for root in roots:
        if not os.path.isdir(root):
            continue
        for dp, _dn, fn in os.walk(root):
            for f in fn:
                if not f.lower().endswith(".lnk"):
                    continue
                p = os.path.join(dp, f)
                key = p.lower()
                if key in seen:
                    continue
                seen.add(key)
                name = Path(f).stem
                out.append({"name": name, "exe": "", "source": "startmenu",
                            "token": {"type": "lnk", "value": p}})
    return out


def _registry_uninstall() -> list[dict]:
    out: list[dict] = []
    try:
        import winreg
    except Exception:  # noqa: BLE001
        return out
    paths = [
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall"),
        (winreg.HKEY_CURRENT_USER, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
    ]
    for hive, sub in paths:
        try:
            key = winreg.OpenKey(hive, sub)
        except OSError:
            continue
        with key:
            i = 0
            while True:
                try:
                    child = winreg.EnumKey(key, i)
                    i += 1
                except OSError:
                    break
                try:
                    with winreg.OpenKey(key, child) as ck:
                        def _v(name: str) -> str:
                            try:
                                v, _t = winreg.QueryValueEx(ck, name)
                                return str(v or "")
                            except OSError:
                                return ""
                        disp = _v("DisplayName")
                        if not disp:
                            continue
                        exe = ""
                        icon = _v("DisplayIcon")
                        if icon:
                            exe = icon.split(",")[0].strip().strip('"')
                        if not exe or not os.path.exists(exe):
                            loc = _v("InstallLocation").strip('"')
                            if loc and os.path.isdir(loc):
                                # 目录里挑一个像主程序的 exe（与 DisplayName 同名的优先）
                                stem = normalize(disp)
                                cands = []
                                try:
                                    for f in os.listdir(loc):
                                        if f.lower().endswith(".exe"):
                                            p2 = os.path.join(loc, f)
                                            cands.append(p2)
                                except OSError:
                                    pass
                                for c in cands:
                                    if normalize(Path(c).stem) == stem:
                                        exe = c
                                        break
                                if not exe and len(cands) == 1:
                                    exe = cands[0]
                        if exe and os.path.exists(exe):
                            out.append({"name": disp, "exe": exe, "source": "uninstall",
                                        "token": {"type": "exe", "value": exe}})
                except OSError:
                    continue
    return out


def _app_paths() -> list[dict]:
    out: list[dict] = []
    try:
        import winreg
        key = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                             r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths")
    except OSError:
        return out
    with key:
        i = 0
        while True:
            try:
                child = winreg.EnumKey(key, i)
                i += 1
            except OSError:
                break
            try:
                v = winreg.QueryValue(key, child)
                exe = str(v or "").strip('"')
                if exe and os.path.exists(exe):
                    out.append({"name": Path(child).stem, "exe": exe, "source": "apppaths",
                                "token": {"type": "exe", "value": exe}})
            except OSError:
                continue
    return out


def _uwp_apps() -> list[dict]:
    """Get-StartApps 一次拉全（缓存进索引），替代每次查询 2-20s。"""
    out: list[dict] = []
    try:
        # 【2026-10-16 修·真因】原写法把 tab 写成 `'`t'`（PowerShell 单引号包裹）——
        # 单引号内**不做反引号转义**，实际输出的是字面量 "`t"，于是下面 split("\t")
        # 全部失败 → UWP 段恒为空；日志却归因成"通道受限"，把真因掩盖了。
        # 实测同一命令用 [char]9 能正常返回 275 行。
        # 另一个坑：text=True 会按系统 ANSI 解码，中文应用名会变成乱码 → 同样匹配不上，
        # 所以自己按 utf-8 → gbk 顺序解码。
        o = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command",
             "Get-StartApps | ForEach-Object { $_.Name + [char]9 + $_.AppID }"],
            capture_output=True, timeout=45)
        raw = o.stdout or b""
        text = ""
        for enc in ("utf-8", "gbk"):
            try:
                text = raw.decode(enc)
                break
            except Exception:  # noqa: BLE001
                continue
        if not text:
            text = raw.decode("utf-8", errors="replace")
        for line in text.splitlines():
            parts = line.split("\t", 1)
            if len(parts) == 2 and parts[1].strip():
                out.append({"name": parts[0].strip(), "exe": "", "source": "uwp",
                            "token": {"type": "appid", "value": parts[1].strip()}})
        if not out:
            logger.warning("[agent_pro] Get-StartApps 无可用条目（UWP/商店应用段缺失）；"
                           "其余四段不受影响。")
    except Exception as e:  # noqa: BLE001
        logger.warning("[agent_pro] Get-StartApps 调用失败（UWP 段为空）：%s", e)
    return out


def _pe_is_gui(path: str) -> bool:
    """读 PE 头判断是否 GUI 子系统（IMAGE_SUBSYSTEM_WINDOWS_GUI = 2）。

    用途：系统目录里绝大多数 exe 是命令行工具（cmd/ipconfig/...），用户说
    「打开 X」时指的是 GUI 应用。用子系统字段筛，比维护名单通用得多。
    读不到/解析失败一律返回 False（宁可漏，不可错）。
    """
    try:
        import struct
        with open(path, "rb") as f:
            if f.read(2) != b"MZ":
                return False
            f.seek(0x3C)
            off = struct.unpack("<I", f.read(4))[0]
            if off <= 0 or off > 0x10000000:
                return False
            f.seek(off)
            if f.read(4) != b"PE\x00\x00":
                return False
            f.seek(off + 0x5C)          # OptionalHeader.Subsystem
            return struct.unpack("<H", f.read(2))[0] == 2
    except Exception:  # noqa: BLE001
        return False


def _system_gui_apps() -> list[dict]:
    """第 5 路：系统自带 GUI 应用（Windows\\System32 与 Windows 根目录）。

    为什么需要它：Win11 把记事本/画图/计算器等做成了商店应用，不再出现在
    开始菜单 lnk / 卸载表 / App Paths 里，Get-StartApps 通道受限时整段缺失
    ——「记事本」就成了查不到的软件。这里从系统目录补齐，条件：
      ① PE 子系统 = GUI（剔掉命令行工具）
      ② 有本地化文件描述（中文系统上即中文名，如 notepad.exe → 记事本）
    全程零硬编码，换任何 Windows / 任何系统语言都成立。
    """
    out: list[dict] = []
    rootdir = os.environ.get("SystemRoot") or r"C:\Windows"
    seen: set[str] = set()
    for sub in ("System32", ""):
        d = os.path.join(rootdir, sub)
        try:
            names = os.listdir(d)
        except OSError:
            continue
        for f in names:
            if not f.lower().endswith(".exe"):
                continue
            full = os.path.join(d, f)
            low = full.lower()
            if low in seen:
                continue
            seen.add(low)
            if not _pe_is_gui(full):
                continue
            desc = file_desc(full)
            if not desc:
                continue
            out.append({"name": desc, "exe": full, "desc": desc, "source": "system",
                        "token": {"type": "exe", "value": full}})
    return out


def _dedupe(records: list[dict]) -> list[dict]:
    """同名去重：主记录按 uninstall > apppaths > startmenu > uwp > system 选。

    但**落选的启动 token 收进 `_alts`**：同一个应用常常既有安装目录里的 exe
    （uninstall 段）又有 AppsFolder 的 AppID（uwp 段），两条通道可靠性并不相同
    —— 打包应用从 AppsFolder 启动才等价于"开始菜单点一下"（实测汽水音乐的
    launcher.exe 直启秒退，而 AppID 通道可用）。保留候选，让启动层顺序降级重试。
    """
    prio = {"uninstall": 0, "apppaths": 1, "startmenu": 2, "uwp": 3, "system": 4}
    best: dict[str, dict] = {}
    alts: dict[str, list[dict]] = {}
    for r in records:
        k = normalize(r["name"])
        if not k:
            continue
        cur = best.get(k)
        if cur is None:
            best[k] = dict(r)
            continue
        tk = r.get("token")
        if tk and tk.get("value"):
            alts.setdefault(k, []).append(dict(tk))
        if prio.get(r["source"], 9) < prio.get(cur["source"], 9):
            old = cur.get("token")
            if old and old.get("value"):
                alts.setdefault(k, []).append(dict(old))
            best[k] = dict(r)
    for k, rec in best.items():
        cand = list(alts.get(k) or [])
        main = rec.get("token") or {}
        seen = {(main.get("type"), main.get("value"))}
        uniq: list[dict] = []
        for t in cand:
            key = (t.get("type"), t.get("value"))
            if key in seen:
                continue
            seen.add(key)
            uniq.append(t)
        if uniq:
            rec["_alts"] = uniq
    return list(best.values())


def build_index() -> list[dict]:
    recs: list[dict] = []
    for fn in (_startmenu_lnks, _registry_uninstall, _app_paths, _uwp_apps,
               _system_gui_apps):
        try:
            recs.extend(fn())
        except Exception:  # noqa: BLE001
            logger.exception("[agent_pro] 索引来源 %s 失败（跳过）", fn.__name__)
    # 补本地化描述（中英互通桥）：只有 exe 通道能读到，lnk/UWP 段保持空。
    for r in recs:
        if not r.get("desc"):
            r["desc"] = file_desc(r.get("exe") or "")
    return _dedupe(recs)


def _cache_path(base_dir: Path) -> Path:
    return base_dir / _INDEX_FILENAME


def load_index(ttl_h: float = 24.0, base_dir: Optional[Path | str] = None,
               force_rebuild: bool = False) -> list[dict]:
    """带 TTL 缓存的索引读取。过期/损坏 → 重建并写回。"""
    from .config import DEFAULT_BASE_DIR
    base = Path(base_dir) if base_dir else DEFAULT_BASE_DIR
    p = _cache_path(base)
    if not force_rebuild and p.exists():
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
            ts = float(d.get("ts", 0))
            if (time.time() - ts < ttl_h * 3600.0
                    and isinstance(d.get("apps"), list)
                    and int(d.get("v", 0)) == _INDEX_VERSION):
                return d["apps"]
        except Exception as e:  # noqa: BLE001
            logger.warning("[agent_pro] 索引缓存损坏，重建（%s）：%s", p, e)
    apps = build_index()
    try:
        base.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".json.tmp")
        tmp.write_text(json.dumps({"ts": time.time(), "v": _INDEX_VERSION,
                                  "apps": apps}, ensure_ascii=False), encoding="utf-8")
        tmp.replace(p)
    except Exception as e:  # noqa: BLE001
        logger.warning("[agent_pro] 索引写缓存失败（不影响本次使用）：%s", e)
    return apps


def match(target: str, apps: list[dict], top_k: int = 3) -> list[tuple[dict, float]]:
    """返回 [(记录, 分数)] 降序；分数 < 40 的丢弃。

    系统段（source=system）特殊处理——它是从系统目录兜底捞出来的，会混入大量
    内部组件（如「Windows SQM 合并计算器」）。两条约束：
      ① 组件名比用户说法长出 6 字以上 → 丢弃（「计算器」不该命中它）；
      ② 整体降权 15% → 只要有正式来源（卸载表/开始菜单/App Paths）的同名应用，
         就优先返回那个。
    """
    scored = []
    tn = normalize(target)
    for r in apps:
        sc = score_match(target, r.get("name", ""), Path(r.get("exe") or "").stem,
                         r.get("desc", ""))
        if sc < 40.0:
            continue
        if r.get("source") == "system":
            cn = normalize(r.get("desc") or r.get("name") or "")
            # 允许的额外长度随查询词长度走：「资源管理器」对「Windows 资源管理器」
            # 只多 7 字，是正确命中；而「计算器」对「Windows SQM 合并计算器」多 11 字，是噪音。
            if tn and cn and tn in cn and (len(cn) - len(tn)) > len(tn) + 4:
                continue
            sc *= 0.85
        scored.append((r, sc))
    scored.sort(key=lambda x: -x[1])
    return scored[:top_k]


from typing import Optional  # noqa: E402  (文件尾导入保持顶部整洁无必要，放这里仅为类型注解)
