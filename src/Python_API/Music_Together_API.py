import asyncio
import os
import random
import glob
from typing import List, Dict, Any, Optional, Union

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException, Request
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse
from pydantic import BaseModel

from json_loader import load_db, save_db
from link_handler import (
    download_music, 
    add_playlist, 
    remove_playlist, 
    add_music_to_playlist, 
    remove_music_from_playlist, 
    add_music_to_love_playlist,
    safe_filename
)

app = FastAPI(title="Music Together API")

# Ensure music directory exists
os.makedirs("music", exist_ok=True)

@app.get("/music/{filename}")
def get_music_file(filename: str):
    name, ext = os.path.splitext(filename)
    safe_name = safe_filename(name)
    path = os.path.join("music", f"{safe_name}{ext}")
    if os.path.exists(path):
        return FileResponse(path)
    raise HTTPException(status_code=404, detail="File not found")

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
        self.current_index: int = -1
        self.is_playing: bool = False
        self.repeat_mode: str = "none" # "none", "track", "playlist"
        
    @property
    def now_playing(self):
        if 0 <= self.current_index < len(self.queue):
            return self.queue[self.current_index]
        return None
        
player_state = PlayerState()

class ConnectionManager:
    def __init__(self):
        self.active_connections: List[WebSocket] = []
        self.active_downloads: Dict[str, str] = {}

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
                "current_index": player_state.current_index,
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
                "current_index": player_state.current_index,
                "now_playing": player_state.now_playing,
                "is_playing": player_state.is_playing,
                "repeat_mode": player_state.repeat_mode
            }
        }
        try:
            await websocket.send_json(state_msg)
            
            dl_list = [{"name": k, "progress": v} for k, v in self.active_downloads.items()]
            await websocket.send_json({
                "type": "downloads_update",
                "downloads": dl_list
            })
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

    async def update_download_progress(self, entry_name: str, status: str):
        import time
        if status == "start":
            self.active_downloads[entry_name] = "0%"
        elif status.startswith("progress:"):
            self.active_downloads[entry_name] = status.split(":", 1)[1]
        else:
            self.active_downloads.pop(entry_name, None)
            
        # UI Throttle: Cap WebSocket congestion to ~10 frames per second
        self._last_dl_broadcast = getattr(self, "_last_dl_broadcast", 0)
        is_critical = status in ("start", "done", "error")
        now = time.time()
        
        if is_critical or (now - self._last_dl_broadcast > 0.1):
            dl_list = [{"name": k, "progress": v} for k, v in self.active_downloads.items()]
            msg = {"type": "downloads_update", "downloads": dl_list}
            for connection in list(self.active_connections):
                try:
                    await connection.send_json(msg)
                except Exception:
                    pass
            self._last_dl_broadcast = now

manager = ConnectionManager()

async def process_url_download(url: str, enable_playlist: bool = False, save_playlist: bool = True, start_idx: int = 0, end_idx: int = 100):
    await manager.send_notification("Starting download...", "info")
    
    # Inject preliminary loading UI state while yt-dlp computes metadata headers synchronously in the thread
    await manager.update_download_progress("Fetching Metadata...", "start")
    
    db = load_db()
    
    try:
        loop = asyncio.get_running_loop()
        def on_track_progress(entry_name, status):
            loop.call_soon_threadsafe(
                lambda: asyncio.create_task(manager.update_download_progress(entry_name, status))
            )
            
        entry_names = await run_in_threadpool(
            download_music, url, db, 
            playlist_start=start_idx, 
            playlist_end=end_idx, 
            enable_playlist=enable_playlist, 
            save_playlist=save_playlist,
            progress_callback=on_track_progress
        )
        if entry_names:
            was_empty = (player_state.now_playing is None)
            start_pos = len(player_state.queue)
            player_state.queue.extend(entry_names)
            if was_empty and not player_state.is_playing:
                player_state.current_index = start_pos
                player_state.is_playing = True
            await manager.broadcast_state()
            await manager.send_notification(f"Added {len(entry_names)} tracks to the queue!", "success")
        else:
            await manager.send_notification("No valid tracks were downloaded.", "warning")
    except Exception as e:
        await manager.send_notification(f"Download error: {str(e)}", "error")
    finally:
        # Guarantee removal from UI exactly before organic downloads or crashes exit the execution loop
        await manager.update_download_progress("Fetching Metadata...", "done")

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await manager.connect(websocket)
    try:
        while True:
            data = await websocket.receive_json()
            action = data.get("action")
            
            if action == "play_url":
                url = data.get("url")
                enable_playlist = data.get("enable_playlist", False)
                save_playlist = data.get("save_playlist", True)
                start_idx = data.get("playlist_start", 0)
                end_idx = data.get("playlist_end", 100)
                if url:
                    asyncio.create_task(process_url_download(url, enable_playlist, save_playlist, start_idx, end_idx))
                    
            elif action == "get_lobby_info":
                import socket as _sock, psutil as _psutil
                port = 54321

                # --- Phase 1: local IPs (instant, no network call needed) ---
                primary_ip4 = None
                primary_ip6 = None
                try:
                    s = _sock.socket(_sock.AF_INET, _sock.SOCK_DGRAM)
                    s.connect(("8.8.8.8", 80))
                    primary_ip4 = s.getsockname()[0]
                    s.close()
                except Exception:
                    pass
                try:
                    s6 = _sock.socket(_sock.AF_INET6, _sock.SOCK_DGRAM)
                    s6.connect(("2001:4860:4860::8888", 80))
                    primary_ip6 = s6.getsockname()[0].split('%')[0]
                    s6.close()
                except Exception:
                    pass

                local_addrs = []
                seen = set()
                # Primary IPv4 first
                if primary_ip4:
                    local_addrs.append({"label": "Local IPv4 (Primary)", "addr": f"{primary_ip4}:{port}"})
                    seen.add(primary_ip4)
                # Primary IPv6
                if primary_ip6:
                    local_addrs.append({"label": "Local IPv6 (Primary)", "addr": f"[{primary_ip6}]:{port}"})
                    seen.add(primary_ip6)
                # All other non-loopback interfaces
                for iface, addrs in _psutil.net_if_addrs().items():
                    for a in addrs:
                        if a.family == _sock.AF_INET:
                            ip = a.address
                            if ip not in seen and not ip.startswith("127.") and not ip.startswith("169.254"):
                                local_addrs.append({"label": iface, "addr": f"{ip}:{port}"})
                                seen.add(ip)
                        elif getattr(_sock, 'AF_INET6', None) and a.family == _sock.AF_INET6:
                            ip = a.address.split('%')[0]
                            if ip not in seen and ip != '::1' and not ip.startswith('fe80'):
                                local_addrs.append({"label": f"{iface} (IPv6)", "addr": f"[{ip}]:{port}"})
                                seen.add(ip)

                # Send Phase 1 immediately so the modal opens right away
                await websocket.send_json({
                    "type": "lobby_info",
                    "phase": "local",
                    "local_addrs": local_addrs
                })

                # --- Phase 2: external IP + UPnP (runs in background) ---
                async def _phase2():
                    import urllib.request
                    external_addr = None
                    upnp_status = "no UPnP gateway found — forward port 54321 manually in your router"

                    # --- Try UPnP first: asks the router directly for WAN IP (works for IPv4 AND IPv6) ---
                    try:
                        import miniupnpc
                        u = miniupnpc.UPnP()
                        # Pass ipv6=True to attempt discovering IPv6 IGDs directly
                        try:
                            found = u.discover(delay=1500, localport=0, ipv6=True)
                        except TypeError:
                            # Fallback if the installed miniupnpc version doesn't support the ipv6 arg
                            u.discoverdelay = 1500
                            found = u.discover()
                            
                        if found > 0:
                            u.selectigd()
                            # Get the WAN IP directly from the gateway — correct for both v4 and v6
                            wan_ip = u.externalipaddress()
                            if wan_ip:
                                # Format IPv6 with brackets, IPv4 plain
                                if ':' in wan_ip:
                                    external_addr = f"[{wan_ip}]:{port}"
                                else:
                                    external_addr = f"{wan_ip}:{port}"
                            # Now try to open the port
                            if u.getspecificportmapping(port, 'TCP'):
                                upnp_status = "OK_already"
                            else:
                                u.addportmapping(port, 'TCP', u.lanaddr, port, 'Music Together', '')
                                upnp_status = "OK"
                    except ImportError:
                        upnp_status = "miniupnpc not installed"
                    except Exception as e:
                        upnp_status = f"UPnP error: {e}"

                    # --- Fallback: ask ipify if UPnP didn't give us a WAN IP ---
                    if not external_addr:
                        try:
                            ext4 = urllib.request.urlopen("https://api4.ipify.org", timeout=4).read().decode('utf-8')
                            external_addr = f"{ext4}:{port}"
                        except Exception:
                            pass
                    if not external_addr:
                        try:
                            ext6 = urllib.request.urlopen("https://api6.ipify.org", timeout=4).read().decode('utf-8')
                            external_addr = f"[{ext6}]:{port}"
                        except Exception:
                            pass

                    await websocket.send_json({
                        "type": "lobby_info",
                        "phase": "external",
                        "external_addr": external_addr,
                        "upnp_status": upnp_status
                    })

                asyncio.create_task(_phase2())

            elif action == "play_track":
                track = data.get("track")
                player_state.queue.append(track)
                if not player_state.is_playing and player_state.now_playing is None:
                    player_state.current_index = len(player_state.queue) - 1
                    player_state.is_playing = True
                await manager.broadcast_state()
                
            elif action == "play_playlist":
                playlist_name = data.get("playlist_name")
                db = load_db()
                if playlist_name in db.get("playlist", {}):
                    was_empty = (player_state.now_playing is None)
                    start_idx = len(player_state.queue)
                    player_state.queue.extend(db["playlist"][playlist_name])
                    if was_empty and not player_state.is_playing:
                        player_state.current_index = start_idx
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
                    player_state.current_index = 0
                    player_state.is_playing = True
                    await manager.broadcast_state()
                    
            elif action == "stop":
                player_state.is_playing = False
                player_state.current_index = -1
                await manager.broadcast_state()
                
            elif action == "skip":
                player_state.current_index += 1
                if player_state.current_index >= len(player_state.queue):
                    if player_state.repeat_mode == "playlist" and len(player_state.queue) > 0:
                        player_state.current_index = 0
                    else:
                        player_state.is_playing = False
                await manager.broadcast_state()
                
            elif action == "shuffle":
                if player_state.now_playing:
                    current_track = player_state.now_playing
                    random.shuffle(player_state.queue)
                    player_state.current_index = player_state.queue.index(current_track)
                else:
                    random.shuffle(player_state.queue)
                await manager.broadcast_state()
                
            elif action == "remove_from_queue":
                index = data.get("index")
                if index is not None and 0 <= index < len(player_state.queue):
                    player_state.queue.pop(index)
                    if index < player_state.current_index:
                        player_state.current_index -= 1
                    elif index == player_state.current_index:
                        if player_state.current_index >= len(player_state.queue):
                            player_state.is_playing = False
                    await manager.broadcast_state()
                    
            elif action == "clear_queue":
                player_state.queue.clear()
                player_state.current_index = -1
                player_state.is_playing = False
                await manager.broadcast_state()
                
            elif action == "jump_to_queue":
                index = data.get("index")
                if index is not None and 0 <= index < len(player_state.queue):
                    player_state.current_index = index
                    player_state.is_playing = True
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
