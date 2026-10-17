import asyncio
import json
import sys
import websockets


async def main():
    text = sys.argv[1] if len(sys.argv) > 1 else "测试字幕显示"
    async with websockets.connect("ws://localhost:8765") as ws:
        await ws.send(json.dumps({"message_type": "user_input", "payload": {"text": text}}, ensure_ascii=False))
        print("[FAST] sent:", text, flush=True)
        await asyncio.sleep(2)


asyncio.run(main())
