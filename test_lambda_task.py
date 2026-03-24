import asyncio
import threading
import time

async def my_coroutine(msg):
    print(f"Coroutine executed: {msg}")

def simulate_threadpool(loop):
    print("Thread started.")
    time.sleep(1)
    # Schedule coroutine on the loop
    loop.call_soon_threadsafe(
        lambda: asyncio.create_task(my_coroutine("Hello from thread!"))
    )
    print("Thread finished.")

async def main():
    loop = asyncio.get_running_loop()
    t = threading.Thread(target=simulate_threadpool, args=(loop,))
    t.start()
    
    # Wait to see if coroutine executes
    await asyncio.sleep(2)
    print("Main finished.")

if __name__ == "__main__":
    asyncio.run(main())
