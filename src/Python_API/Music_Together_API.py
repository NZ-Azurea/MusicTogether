import asyncio
import os
import random
import glob
from typing import List, Dict, Any, Optional, Union

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException, Request
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel

from json_loader import load_db, save_db
from link_handler import (
    download_music, 
    add_playlist, 
    remove_playlist, 
    add_music_to_playlist, 
    remove_music_from_playlist, 
    add_music_to_love_playlist
)

app = FastAPI(title="Music Together API")

# Ensure music directory exists
os.makedirs("music", exist_ok=True)
app.mount("/music", StaticFiles(directory="music"), name="music")

# Mount frontend web directory so remote users can load the UI
import sys
if hasattr(sys, '_MEIPASS'):
    web_dir = os.path.join(sys._MEIPASS, 'web')
else:
    web_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "web")

if not os.path.exists(web_dir):
    os.makedirs(web_dir, exist_ok=True)
app.mount("/web", StaticFiles(directory=web_dir), name="web")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

class PlayerState:
    def __init__(self):
        self.queue: List[str] = []
        self.now_playing: Optional[str] = None
        self.is_playing: bool = False
        self.repeat_mode: str = "none" # "none", "track", "playlist"
        
player_state = PlayerState()

class ConnectionManager:
    def __init__(self):
        self.active_connections: List[WebSocket] = []

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)
        await self.send_state(websocket)

    def disconnect(self, websocket: WebSocket):
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)

    async def broadcast_state(self):
        state_msg = {
            "type": "state",
            "state": {
                "queue": player_state.queue,
                "now_playing": player_state.now_playing,
                "is_playing": player_state.is_playing,
                "repeat_mode": player_state.repeat_mode
            }
        }
        for connection in list(self.active_connections):
            try:
                await connection.send_json(state_msg)
            except Exception:
                pass

    async def send_state(self, websocket: WebSocket):
        state_msg = {
            "type": "state",
            "state": {
                "queue": player_state.queue,
                "now_playing": player_state.now_playing,
                "is_playing": player_state.is_playing,
                "repeat_mode": player_state.repeat_mode
            }
        }
        try:
            await websocket.send_json(state_msg)
        except Exception:
            pass
        
    async def send_notification(self, message: str, level: str = "info"):
        msg = {
            "type": "notification",
            "message": message,
            "level": level
        }
        for connection in list(self.active_connections):
            try:
                await connection.send_json(msg)
            except Exception:
                pass

manager = ConnectionManager()

def get_next_track():
    if not player_state.queue:
        return None
        
    if player_state.repeat_mode == "track" and player_state.now_playing:
        return player_state.now_playing
        
    next_track = player_state.queue.pop(0)
    if player_state.repeat_mode == "playlist" and player_state.now_playing:
        player_state.queue.append(player_state.now_playing)
    return next_track

async def process_url_download(url: str):
    await manager.send_notification("Starting download...", "info")
    db = load_db()
    is_playlist = "list=" in url
    try:
        # We run the blocking download_music inside a threadpool thread to not block the FastAPI async event loop!
        entry_names = await run_in_threadpool(
            download_music, url, db, 0, 50, is_playlist, is_playlist
        )
        if entry_names:
            player_state.queue.extend(entry_names)
            if not player_state.is_playing and not player_state.now_playing:
                player_state.now_playing = get_next_track()
                player_state.is_playing = True
            await manager.broadcast_state()
            await manager.send_notification(f"Added {len(entry_names)} tracks to the queue!", "success")
        else:
            await manager.send_notification("No valid tracks were downloaded.", "warning")
    except Exception as e:
        await manager.send_notification(f"Download error: {str(e)}", "error")

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await manager.connect(websocket)
    try:
        while True:
            data = await websocket.receive_json()
            action = data.get("action")
            
            if action == "play_url":
                url = data.get("url")
                if url:
                    asyncio.create_task(process_url_download(url))
                    
            elif action == "play_track":
                track = data.get("track")
                player_state.queue.append(track)
                if not player_state.is_playing and not player_state.now_playing:
                    player_state.now_playing = get_next_track()
                    player_state.is_playing = True
                await manager.broadcast_state()
                
            elif action == "play_playlist":
                playlist_name = data.get("playlist_name")
                db = load_db()
                if playlist_name in db.get("playlist", {}):
                    player_state.queue.extend(db["playlist"][playlist_name])
                    if not player_state.is_playing and not player_state.now_playing:
                        player_state.now_playing = get_next_track()
                        player_state.is_playing = True
                    await manager.broadcast_state()
                    
            elif action == "pause":
                player_state.is_playing = False
                await manager.broadcast_state()
                
            elif action == "resume" or action == "play":
                if player_state.now_playing:
                    player_state.is_playing = True
                    await manager.broadcast_state()
                elif player_state.queue:
                    player_state.now_playing = get_next_track()
                    player_state.is_playing = True
                    await manager.broadcast_state()
                    
            elif action == "stop":
                player_state.is_playing = False
                player_state.now_playing = None
                await manager.broadcast_state()
                
            elif action == "skip":
                # client says the track is done or they want to skip
                next_t = get_next_track()
                player_state.now_playing = next_t
                if not next_t:
                    player_state.is_playing = False
                await manager.broadcast_state()
                
            elif action == "shuffle":
                random.shuffle(player_state.queue)
                await manager.broadcast_state()
                
            elif action == "set_repeat":
                mode = data.get("mode", "none")
                if mode in ["none", "track", "playlist"]:
                    player_state.repeat_mode = mode
                    await manager.broadcast_state()
                    
            elif action == "love":
                username = data.get("username", "anonymous")
                track = data.get("track")
                if track:
                    db = load_db()
                    add_music_to_love_playlist(db, track, username)
                    await manager.send_notification(f"Loved '{track}'!", "success")
                    
    except WebSocketDisconnect:
        manager.disconnect(websocket)
        

class PlaylistCreateRequest(BaseModel):
    name: str

class PlaylistModifyRequest(BaseModel):
    music: Union[str, List[str]]
    action: str # "add" or "remove"

@app.get("/db")
def get_database():
    return load_db()

@app.get("/playlists")
def get_playlists():
    db = load_db()
    return db.get("playlist", {})

@app.post("/playlists")
def create_playlist(req: PlaylistCreateRequest):
    db = load_db()
    add_playlist(db, req.name)
    return {"message": "Playlist created successfully"}

@app.delete("/playlists/{name}")
def delete_playlist(name: str):
    db = load_db()
    remove_playlist(db, name)
    return {"message": "Playlist deleted successfully"}

@app.post("/playlists/{name}/edit")
def edit_playlist(name: str, req: PlaylistModifyRequest):
    db = load_db()
    music_items = req.music if isinstance(req.music, list) else [req.music]
    
    if req.action == "add":
        add_music_to_playlist(db, name, music_items)
    elif req.action == "remove":
        remove_music_from_playlist(db, name, music_items)
    else:
        raise HTTPException(status_code=400, detail="Invalid action, must be 'add' or 'remove'")
@app.get("/network_info")
def get_network_info(request: Request):
    import psutil
    import socket
    
    port = request.url.port or 54321
    interfaces = []
    
    for interface_name, interface_addresses in psutil.net_if_addrs().items():
        for address in interface_addresses:
            if address.address in ('127.0.0.1', '::1') or address.address.startswith("169.254"):
                continue
                
            if address.family == socket.AF_INET:
                interfaces.append({
                    "name": interface_name, 
                    "type": "IPv4", 
                    "ip": address.address,
                    "url": f"http://{address.address}:{port}"
                })
            elif getattr(socket, 'AF_INET6', None) and address.family == socket.AF_INET6:
                ip = address.address.split('%')[0]
                interfaces.append({
                    "name": interface_name, 
                    "type": "IPv6", 
                    "ip": ip,
                    "url": f"http://[{ip}]:{port}"
                })
    return {"port": port, "interfaces": interfaces}

def print_network_interfaces(port):
    import psutil
    import socket
    
    print("="*60)
    print("🎵 Music Together Server is running! 🎵")
    print(f"Network Port: {port}")
    print("Share these links with your friends to connect:")
    print("="*60)
    
    for interface_name, interface_addresses in psutil.net_if_addrs().items():
        for address in interface_addresses:
            # Skip loopback and auto-configured link-local addresses
            if address.address in ('127.0.0.1', '::1') or address.address.startswith("169.254"):
                continue
                
            if address.family == socket.AF_INET:
                print(f"  - [{interface_name}] IPv4: http://{address.address}:{port}")
            elif getattr(socket, 'AF_INET6', None) and address.family == socket.AF_INET6:
                ip = address.address.split('%')[0]  # Remove scope ID if present like fe80::1%10
                print(f"  - [{interface_name}] IPv6: http://[{ip}]:{port}")
    print("="*60)

if __name__ == "__main__":
    import uvicorn
    # A high, non-standard port to avoid bot spam (e.g. 54321)
    port = 54321
    print_network_interfaces(port)
    # Using '::' natively supports dual-stack IPv4 and IPv6 binding on most systems
    uvicorn.run(app, host="::", port=port)
