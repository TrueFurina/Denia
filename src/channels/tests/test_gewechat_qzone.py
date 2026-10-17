"""Gewechat / Qzone 客户端单测（无真实网关/网络）。

验证：
  - GeweClient：token/qr/login/发文本/图片/视频 调用参数正确（mock _post）
  - QzoneClient：g_tk 计算、JSONP 剥壳、get_moments 解析、publish 表单
运行：python src/channels/tests/test_gewechat_qzone.py
"""
from __future__ import annotations

import asyncio
import os
import sys
from types import SimpleNamespace

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from src.channels.gewechat import GeweClient  # noqa: E402
from src.channels.qzone import QzoneClient, _gtk  # noqa: E402


class MockGewe(GeweClient):
    def __init__(self) -> None:
        super().__init__("http://x:2531")
        self.calls = []

    def _post(self, path, params):
        self.calls.append((path, params))
        if path.endswith("GetToken"):
            return {"data": {"token": "T123"}}
        if path.endswith("GetQr"):
            return {"data": {"uuid": "UUID1"}}
        if path.endswith("CheckQr"):
            return {"code": 0, "data": {"appId": "APP1"}} if params.get("uuid") == "UUID1" else {"code": -1}
        return {"ret": 0}


async def test_gewe_client() -> None:
    c = MockGewe()
    tok = c.get_token()
    assert tok == "T123" and c.token == "T123", c.token
    qr = c.get_qr("")
    assert qr["data"]["uuid"] == "UUID1"
    res = c.check_qr("", "UUID1")
    assert res["code"] == 0 and (res.get("data") or {}).get("appId") == "APP1"
    c.app_id = (res.get("data") or {}).get("appId")  # 通道登录成功后写入（模拟）
    c.post_text("wxid_a", "hi")
    assert c.calls[-1] == ("message/PostText", {"appId": "APP1", "toWxid": "wxid_a", "content": "hi", "ats": ""})
    c.post_image("wxid_a", "http://img/x.jpg")
    assert c.calls[-1][0] == "message/PostImage" and c.calls[-1][1]["imgUrl"] == "http://img/x.jpg"
    c.post_video("wxid_a", "http://v/x.mp4")
    assert c.calls[-1][0] == "message/PostVideo"
    print("✓ GeweClient: token/qr/login/发文本/图片/视频 参数正确")


def test_qzone_gtk() -> None:
    cookie = "p_skey=abc123; skey=xyz; o_uin=12345"
    c = QzoneClient(cookie, "12345")
    assert c.token == _gtk("abc123"), c.token
    c2 = QzoneClient("skey=xyz;", "1")
    assert c2.token == _gtk("xyz")
    print("✓ QzoneClient.g_tk: p_skey 优先、skey 回退 正确")


class MockQzone(QzoneClient):
    def __init__(self) -> None:
        super().__init__("p_skey=abc; o_uin=12345", "12345")
        self.gets = []
        self.posts = []

    def _get(self, url):
        self.gets.append(url)
        return '_preloadCallback({"code":0,"msglist":[{"tid":"1","content":"今天天气真好","likecnt":3}]});'

    def publish(self, text):
        self.posts.append(text)
        return {"code": 0, "msg": "ok"}


def test_qzone_moments() -> None:
    c = MockQzone()
    msgs = c.get_moments(10)
    assert msgs and msgs[0]["content"] == "今天天气真好", msgs
    assert "emotion_cgi_msglist_v6" in c.gets[0] and "g_tk=" in c.gets[0]
    print("✓ QzoneClient.get_moments: JSONP 剥壳 + 解析正确")


def test_qzone_publish() -> None:
    c = MockQzone()
    r = c.publish("测试说说")
    assert c.posts == ["测试说说"] and r.get("code") == 0
    print("✓ QzoneClient.publish 调用正确")


async def main() -> None:
    await test_gewe_client()
    test_qzone_gtk()
    test_qzone_moments()
    test_qzone_publish()
    print("\n全部通过 ✅")


if __name__ == "__main__":
    asyncio.run(main())
