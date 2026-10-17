#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""轻量 MCP-for-Unity HTTP 客户端辅助脚本（基于标准库 urllib，无第三方依赖）。

用法:
  python mcp_unity.py init                       # initialize 握手 + initialized 通知
  python mcp_unity.py tools/list                 # 列出所有工具
  python mcp_unity.py resources/list             # 列出所有资源
  python mcp_unity.py call <tool> '<json>'       # 调用工具，如:
                                                   python mcp_unity.py call manage_scene '{"action":"get_hierarchy"}'
  python mcp_unity.py read <uri>                 # 读取资源, 如:
                                                   python mcp_unity.py read mcpforunity://editor/state

环境变量:
  MCP_URL   server 地址，默认 http://127.0.0.1:8080
  MCP_SID   会话文件路径，默认 <脚本目录>/.mcp_session
"""
import json
import os
import sys
import urllib.request
import urllib.error
import pathlib

BASE_URL = os.environ.get("MCP_URL", "http://127.0.0.1:8080")
SID_FILE = os.environ.get("MCP_SID", str(pathlib.Path(__file__).parent / ".mcp_session"))
HEADERS = {
    "Content-Type": "application/json",
    "Accept": "application/json, text/event-stream",
}
_rpc_id = 0


def _next_id():
    global _rpc_id
    _rpc_id += 1
    return _rpc_id


def _load_session():
    try:
        return pathlib.Path(SID_FILE).read_text().strip()
    except Exception:
        return None


def _save_session(sid):
    pathlib.Path(SID_FILE).write_text(sid)


def rpc(method, params=None, notification=False):
    """发送一条 JSON-RPC 消息，返回 (session_id, 解析后的消息或事件列表)。"""
    payload = {"jsonrpc": "2.0", "method": method}
    if not notification:
        payload["id"] = _next_id()
    if params is not None:
        payload["params"] = params

    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(BASE_URL + "/mcp", data=body, headers=HEADERS, method="POST")
    session = _load_session()
    if session:
        req.add_header("Mcp-Session-Id", session)

    def _parse(content):
        content = content.replace("\r\n", "\n")  # SSE 用 CRLF，统一转 LF
        messages = []
        if content.strip().startswith("event:"):
            for block in content.split("\n\n"):
                data_line = next((ln for ln in block.splitlines() if ln.startswith("data:")), None)
                if data_line:
                    try:
                        messages.append(json.loads(data_line[5:].strip()))
                    except Exception:
                        pass
        else:
            try:
                messages.append(json.loads(content))
            except Exception:
                messages.append({"raw": content[:2000]})
        return messages

    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            resp_session = resp.headers.get("Mcp-Session-Id")
            if resp_session:
                _save_session(resp_session)
            content = resp.read().decode("utf-8")
    except urllib.error.HTTPError as e:
        return (None, {"error": {"code": e.code, "message": e.read().decode("utf-8", "replace")[:2000]}})

    messages = _parse(content)
    # streamable HTTP: 若本次响应未返回带 id 的结果(可能只回了日志通知)，
    # 用 GET 继续拉取后续消息，直到拿到结果或超时。
    want_id = payload.get("id")
    if want_id is not None and not any(m.get("id") == want_id or "result" in m for m in messages):
        for _ in range(3):
            try:
                greq = urllib.request.Request(BASE_URL + "/mcp", headers={"Accept": "application/json, text/event-stream"})
                gsid = _load_session()
                if gsid:
                    greq.add_header("Mcp-Session-Id", gsid)
                with urllib.request.urlopen(greq, timeout=15) as gresp:
                    gsid2 = gresp.headers.get("Mcp-Session-Id")
                    if gsid2:
                        _save_session(gsid2)
                    gcontent = gresp.read().decode("utf-8")
                gmsgs = _parse(gcontent)
                messages.extend(gmsgs)
                if any(m.get("id") == want_id or "result" in m for m in gmsgs):
                    break
            except Exception:
                break
    return (resp_session, messages)


def main():
    args = sys.argv[1:]
    if not args:
        print(__doc__)
        return 1

    cmd = args[0]
    if cmd == "init":
        sid, msgs = rpc("initialize", {
            "protocolVersion": "2025-03-26",
            "capabilities": {},
            "clientInfo": {"name": "mcp-unity-helper", "version": "1.0"},
        })
        print(json.dumps(msgs, ensure_ascii=False, indent=2))
        # 发送 initialized 通知
        rpc("notifications/initialized", notification=True)
        print("\n[initialized 通知已发送, session=%s]" % sid)
        return 0

    if cmd == "tools/list":
        _, msgs = rpc("tools/list")
        full = len(args) > 1 and args[1] == "full"
        if full:
            print(json.dumps(msgs, ensure_ascii=False, indent=2))
        else:
            for m in msgs:
                result = m.get("result")
                if not result:
                    print(json.dumps(m, ensure_ascii=False)[:300])
                    continue
                for t in result.get("tools", []):
                    desc = (t.get("description") or "").replace("\n", " ")[:110]
                    print("%-26s %s" % (t["name"], desc))
        return 0

    if cmd == "tools/find":
        _, msgs = rpc("tools/list")
        key = args[1].lower()
        for m in msgs:
            for t in (m.get("result") or {}).get("tools", []):
                if key in t["name"].lower() or key in (t.get("description") or "").lower():
                    print(json.dumps(t, ensure_ascii=False, indent=2))
        return 0

    if cmd == "resources/list":
        _, msgs = rpc("resources/list")
        print(json.dumps(msgs, ensure_ascii=False, indent=2))
        return 0

    if cmd == "call":
        tool = args[1]
        raw = args[2] if len(args) > 2 else ""
        if raw.strip():
            if raw.startswith("@"):
                # 从文件读取 JSON 参数，避免 shell 转义问题
                params = json.loads(pathlib.Path(raw[1:]).read_text(encoding="utf-8"))
            else:
                params = json.loads(raw)
        else:
            params = {}
        _, msgs = rpc("tools/call", {"name": tool, "arguments": params})
        print(json.dumps(msgs, ensure_ascii=False, indent=2))
        return 0

    if cmd == "read":
        uri = args[1]
        _, msgs = rpc("resources/read", {"uri": uri})
        print(json.dumps(msgs, ensure_ascii=False, indent=2))
        return 0

    print("未知命令: %s" % cmd)
    return 1


if __name__ == "__main__":
    sys.exit(main())
