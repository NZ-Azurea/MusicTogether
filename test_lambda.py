import asyncio
import threading
import time

async def background_task(name, status):
    print(f"Task executed for {name}: {status}")

def simulate_threadpool(loop):
    def on_progress(name, status):
        # Let's test if this correctly binds parameters or if it misses them!
        loop.call_soon_threadsafe(
            lambda: asyncio.create_task(background_task(name, status))
        )
        
    on_progress("Track A", "start")
    time.sleep(0.1)
    on_progress("Track B", "start")

async def main():
    loop = asyncio.get_running_loop()
    t = threading.Thread(target=simulate_threadpool, args=(loop,))
    t.start()
    await asyncio.sleep(1)

if __name__ == "__main__":
    asyncio.run(main())
