import asyncio
import websockets
import json

async def test():
    try:
        async with websockets.connect("ws://localhost:8000/ws") as websocket:
            msg = await websocket.recv()
            print("Connected state:", msg)

            # test play
            await websocket.send(json.dumps({"action": "play_track", "track": "test track"}))
            msg2 = await websocket.recv()
            print("After play track:", msg2)
            
            # test pause
            await websocket.send(json.dumps({"action": "pause"}))
            msg3 = await websocket.recv()
            print("After pause:", msg3)
    except Exception as e:
        print("Connection failed:", e)

asyncio.run(test())
