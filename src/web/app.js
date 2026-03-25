const wsUrl = `ws://${window.location.host}/ws`;
let ws;
const audio = document.getElementById("video-player"); // Switch mapping to new video element identically!
const playBtn = document.getElementById("play-pause-btn");
const discIcon = document.getElementById("disc-icon");
const sessionUsername = new URLSearchParams(window.location.search).get("username") || "anonymous";

// State
let globalState = { queue: [], current_index: -1, now_playing: null, is_playing: false, repeat_mode: "none", current_time: 0 };
let isScrubbing = false;
let isLoved = false;
let draggedQueueIndex = null;
let queueItemKeyCounts = new Map();

function setLoveButtonState(loved) {
    const btn = document.querySelector(".love-btn i");
    if (!btn) return;
    isLoved = loved;
    btn.className = loved ? "fas fa-heart on" : "far fa-heart";
    btn.style.color = loved ? "#ff4081" : "";
}

function connectWebsocket() {
    ws = new WebSocket(wsUrl);
    ws.onmessage = (event) => {
        const msg = JSON.parse(event.data);
        if (msg.type === "state") {
            updateState(msg.state);
        } else if (msg.type === "notification") {
            showToast(msg.message, msg.level);
        } else if (msg.type === "downloads_update") {
            renderDownloads(msg.downloads);
        } else if (msg.type === "lobby_info") {
            if (msg.phase === "local") {
                populateLobbyLocal(msg.local_addrs);
            } else if (msg.phase === "external") {
                populateLobbyExternal(msg.external_addr, msg.upnp_status);
            }
        }
    };
    ws.onclose = () => {
        showToast("Disconnected from server. Reconnecting...", "error");
        setTimeout(connectWebsocket, 3000);
    };
}
connectWebsocket();

function renderDownloads(downloads) {
    const list = document.getElementById("downloads-list");
    if (!list) return;
    
    list.innerHTML = "";
    if (!downloads || downloads.length === 0) {
        list.innerHTML = `<div style="text-align:center; color:var(--text-secondary); padding:20px;">No active downloads.</div>`;
        return;
    }
    
    downloads.forEach(dl => {
        const row = document.createElement("div");
        row.className = "queue-item";
        row.style.cursor = "default";
        
        let cleanProgress = dl.progress ? dl.progress.replace('~', '').trim() : "0%";
        
        if (cleanProgress === "0%" || cleanProgress === "spinning") {
            row.innerHTML = `
                <div style="flex:1; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; font-weight:500;" title="${dl.name}">${dl.name}</div>
                <i class="fas fa-circle-notch fa-spin" style="color:var(--accent); flex-shrink:0;"></i>
            `;
            list.appendChild(row);
            return;
        }

        row.innerHTML = `
            <div style="flex:1; display:flex; flex-direction:column; justify-content:center; gap:6px; overflow:hidden; padding-right:15px;">
                <div style="font-weight:500; font-size:13px; text-overflow:ellipsis; white-space:nowrap; overflow:hidden;" title="${dl.name}">${dl.name}</div>
                <div style="height:4px; background:rgba(255,255,255,0.1); border-radius:2px; overflow:hidden; width:100%;">
                    <div style="height:100%; background:var(--accent); width:${cleanProgress}; transition:width 0.3s ease;"></div>
                </div>
            </div>
            <div style="font-size:12px; color:var(--text-secondary); min-width:35px; text-align:right;">${cleanProgress}</div>
        `;
        list.appendChild(row);
    });
}

function openLobbyModal() {
    // Show the modal immediately with a skeleton loader — no waiting for network
    const container = document.getElementById("lobby-links-container");
    if (!container) return;
    container.innerHTML = `
        <div style="display:flex; align-items:center; gap:12px; color:var(--text-secondary); padding:10px;">
            <i class="fas fa-circle-notch fa-spin" style="color:var(--accent);"></i>
            <span>Scanning local interfaces...</span>
        </div>
    `;
    document.getElementById('lobby-modal').classList.remove('hidden');
    sendAction('get_lobby_info');
}

function _lobbyRow(label, addr) {
    const d = document.createElement('div');
    d.style.cssText = 'background:rgba(255,255,255,0.05); padding:12px 15px; border-radius:8px; display:flex; flex-direction:column; gap:8px; border:1px solid rgba(255,255,255,0.1);';
    d.innerHTML = `
        <div style="display:flex; justify-content:space-between; align-items:center; flex-wrap:wrap; gap:6px;">
            <span style="font-weight:bold; color:var(--text-primary);">${label}</span>
            <span style="font-size:11px; color:var(--text-secondary);">Same-network only</span>
        </div>
        <div style="display:flex; gap:10px;">
            <input type="text" value="${addr}" readonly
                style="flex:1; background:rgba(0,0,0,0.3); border:1px solid rgba(255,255,255,0.1); border-radius:4px; padding:8px 10px; color:var(--accent); outline:none; font-family:monospace; font-size:13px;"
                onclick="this.select()">
            <button class="btn" style="padding:0 12px; font-size:12px; min-width:65px;" onclick="navigator.clipboard.writeText('${addr}').then(()=>showToast('Copied!','success'));">Copy</button>
        </div>`;
    return d;
}

function populateLobbyLocal(localAddrs) {
    const container = document.getElementById("lobby-links-container");
    if (!container) return;
    container.innerHTML = '';

    (localAddrs || []).forEach(({label, addr}) => {
        const icon = addr.startsWith('[') ? 'fa-globe' : 'fa-plug';
        container.appendChild(_lobbyRow(
            `<i class="fas ${icon}" style="color:var(--accent); margin-right:8px;"></i>${label}`,
            addr
        ));
    });

    // Placeholder row for external (pulsing while Phase 2 runs)
    const ext = document.createElement('div');
    ext.id = 'lobby-external-row';
    ext.style.cssText = 'background:rgba(255,255,255,0.03); padding:12px 15px; border-radius:8px; display:flex; align-items:center; gap:12px; border:1px dashed rgba(255,255,255,0.12); animation:pulse-fade 1.5s ease-in-out infinite;';
    ext.innerHTML = `
        <i class="fas fa-circle-notch fa-spin" style="color:var(--accent); flex-shrink:0;"></i>
        <span style="color:var(--text-secondary); font-size:13px;">Testing external access &amp; UPnP port mapping...</span>`;
    container.appendChild(ext);

    // Inject keyframe if not already there
    if (!document.getElementById('lobby-pulse-style')) {
        const s = document.createElement('style');
        s.id = 'lobby-pulse-style';
        s.textContent = '@keyframes pulse-fade { 0%,100%{opacity:.5} 50%{opacity:1} }';
        document.head.appendChild(s);
    }
}

function populateLobbyExternal(externalAddr, upnpStatus) {
    const row = document.getElementById('lobby-external-row');
    if (!row) return;

    const isOk = upnpStatus === 'OK' || upnpStatus === 'OK_already';
    const upnpLabel = upnpStatus === 'OK'
        ? '<i class="fas fa-check-circle" style="margin-right:4px;"></i>Port auto-opened via UPnP'
        : upnpStatus === 'OK_already'
        ? '<i class="fas fa-check-circle" style="margin-right:4px;"></i>Port already open'
        : upnpStatus;
    const badgeColor = isOk ? 'rgba(3,218,198,0.15)' : 'rgba(255,255,255,0.05)';
    const textColor  = isOk ? '#03dac6' : 'var(--text-secondary)';

    row.style.animation = 'none';
    row.style.opacity = '1';
    row.style.border = isOk ? '1px solid rgba(3,218,198,0.3)' : '1px solid rgba(255,255,255,0.1)';
    row.style.background = isOk ? 'rgba(3,218,198,0.05)' : 'rgba(255,255,255,0.05)';

    if (externalAddr) {
        row.innerHTML = `
            <div style="flex:1; display:flex; flex-direction:column; gap:8px;">
                <div style="display:flex; justify-content:space-between; align-items:center; flex-wrap:wrap; gap:6px;">
                    <span style="font-weight:bold; color:var(--text-primary);"><i class="fas fa-globe" style="color:var(--accent); margin-right:8px;"></i>External (Internet)</span>
                    <span style="font-size:11px; background:${badgeColor}; color:${textColor}; padding:3px 8px; border-radius:4px;">${upnpLabel}</span>
                </div>
                <div style="display:flex; gap:10px;">
                    <input type="text" value="${externalAddr}" readonly
                        style="flex:1; background:rgba(0,0,0,0.3); border:1px solid rgba(255,255,255,0.1); border-radius:4px; padding:8px 10px; color:var(--accent); outline:none; font-family:monospace; font-size:13px;"
                        onclick="this.select()">
                    <button class="btn" style="padding:0 12px; font-size:12px; min-width:65px;" onclick="navigator.clipboard.writeText('${externalAddr}').then(()=>showToast('Copied!','success'));">Copy</button>
                </div>
            </div>`;
    } else {
        row.innerHTML = `
            <i class="fas fa-exclamation-triangle" style="color:#cf6679; flex-shrink:0;"></i>
            <div style="flex:1;">
                <div style="color:var(--text-primary); font-weight:bold; margin-bottom:4px;">External IP unavailable</div>
                <div style="font-size:12px; color:var(--text-secondary);">${upnpLabel}</div>
            </div>`;
    }
}

function updateState(newState) {
    const oldState = globalState;
    globalState = newState;
    
    renderQueue(); // Uses current_index to glow natively
    
    const trackChanged = oldState.now_playing !== newState.now_playing;
    
    if (trackChanged) {
        audio.pause(); // Crucial: Immediately mute the old track to stop the 0.1s replay glitch!
        
        const infoDiv = document.getElementById("player-info");
        const artContainer = document.getElementById("player-art-container");
        
        infoDiv.classList.add("fade-out");
        document.getElementById("player-art-img").classList.add("hidden");
        
        setTimeout(() => {
            if (newState.now_playing) {
                document.getElementById("now-playing-title").innerText = newState.now_playing.split(" - ")[0];
                document.getElementById("now-playing-artist").innerText = newState.now_playing.split(" - ").slice(1).join(" - ") || "Unknown";
                playAudioFile(newState.now_playing);
            } else {
                document.getElementById("now-playing-title").innerText = "Not Playing";
                document.getElementById("now-playing-artist").innerText = "Waiting for track...";
                audio.src = "";
                artContainer.classList.remove("video-mode");
                document.getElementById("fullscreen-btn").classList.add("hidden");
                audio.classList.add("hidden");
                discIcon.classList.remove("hidden");
            }
            
            infoDiv.classList.remove("fade-out");
            infoDiv.classList.add("fade-in");
            setTimeout(() => infoDiv.classList.remove("fade-in"), 300);
            
            checkLovedState();
        }, 300);
    } else {
        if (!isScrubbing && newState.now_playing && typeof newState.current_time === "number" && Math.abs((audio.currentTime || 0) - newState.current_time) > 0.75) {
            audio.currentTime = Math.max(0, newState.current_time);
        }
        if (newState.is_playing) {
            if(audio.src && audio.paused) audio.play().catch(e => console.log("Waiting for user interaction mapping", e));
        } else {
            audio.pause();
        }
    }
    
    discIcon.style.animationPlayState = newState.is_playing ? "running" : "paused";
    
    if (newState.is_playing) {
        playBtn.innerHTML = '<i class="fas fa-pause"></i>';
        if(newState.now_playing) document.querySelector('.player-art').classList.add("spinning");
    } else {
        playBtn.innerHTML = '<i class="fas fa-play"></i>';
        document.querySelector('.player-art').classList.remove("spinning");
    }
    
    const repBtn = document.getElementById("repeat-btn");
    repBtn.className = "icon-btn" + (newState.repeat_mode !== "none" ? " on" : "");
    if(newState.repeat_mode === "track") repBtn.innerHTML = '<i class="fas fa-redo" style="position:relative;"><span style="position:absolute;font-size:10px;top:5px;left:5px;">1</span></i>';
    else repBtn.innerHTML = '<i class="fas fa-redo"></i>';
}

function playAudioFile(trackName) {
    const extensions = ['.m4a', '.mp4', '.mp3', '.wav', '.webm', '.opus'];
    let currentIndex = 0;
    
    audio.classList.add("hidden");
    
    const tryNextExtension = () => {
        if(currentIndex >= extensions.length) {
            console.error("Could not load audio file for", trackName);
            showToast(`Error loading track: ${trackName}. Skipping...`, "error");
            sendAction("skip");
            return;
        }
        audio.src = `/music/${encodeURIComponent(trackName)}${extensions[currentIndex]}`;
        
        audio.oncanplay = () => {
            audio.oncanplay = null; // Prevent looping logic
            
            const artContainer = document.getElementById("player-art-container");
            const fsBtn = document.getElementById("fullscreen-btn");
            if (typeof globalState.current_time === "number") {
                const safeTime = audio.duration ? Math.min(Math.max(0, globalState.current_time), Math.max(0, audio.duration - 0.1)) : Math.max(0, globalState.current_time);
                audio.currentTime = safeTime;
            }
            
            if(audio.videoWidth > 0) {
                audio.classList.remove("hidden");
                document.getElementById("player-art-img").classList.add("hidden");
                discIcon.classList.add("hidden");
                
                artContainer.classList.add("video-mode");
                fsBtn.classList.remove("hidden");
                if(globalState.is_playing) artContainer.classList.add("spinning"); // Restore cinematic backlight!
            } else {
                artContainer.classList.remove("video-mode");
                fsBtn.classList.add("hidden");
                if(globalState.is_playing) artContainer.classList.add("spinning");
                tryLoadThumbnail(trackName);
            }
            
            if(globalState.is_playing) {
                audio.play().catch(e => console.log("Waiting for user interaction mapping", e));
            }
        };
        currentIndex++;
    };
    
    audio.onerror = tryNextExtension;
    tryNextExtension();
}

async function tryLoadThumbnail(trackName) {
    const img = document.getElementById("player-art-img");
    const exts = ['.webp', '.jpg'];
    for(let e of exts) {
        const url = `/music/${encodeURIComponent(trackName)}${e}`;
        try {
            const res = await fetch(url, {method: 'HEAD'});
            if(res.ok) {
                img.src = url;
                img.classList.remove("hidden");
                discIcon.classList.add("hidden");
                return;
            }
        } catch(err) {}
    }
    img.classList.add("hidden");
    discIcon.classList.remove("hidden");
}

async function checkLovedState() {
    if(!globalState.now_playing) return;
    try {
        const res = await fetch(`http://${window.location.host}/playlists?t=${Date.now()}`);
        const playlists = await res.json();
        const loved = playlists[`${sessionUsername}_love`] || [];
        setLoveButtonState(loved.includes(globalState.now_playing));
    } catch(e){}
}

function toggleFullscreen() {
    const container = document.getElementById("player-art-container");
    if (!document.fullscreenElement) {
        container.requestFullscreen().catch(err => {
            console.log(`Error attempting to enable fullscreen: ${err.message}`);
        });
    } else {
        document.exitFullscreen();
    }
}

function toggleAppFullscreen() {
    sendAction("toggle_app_fullscreen");
}

audio.onended = () => {
    // Implement native track repeating
    if (globalState.repeat_mode === "track") {
        audio.currentTime = 0;
        audio.play().catch(e => console.log(e));
    } else {
        document.getElementById("player-info").classList.add("fade-out"); 
        sendAction("skip");
    }
};

audio.ontimeupdate = () => {
    if(!isScrubbing) {
        const bar = document.getElementById("seek-bar");
        bar.value = (audio.currentTime / audio.duration) * 100 || 0;
        document.getElementById("current-time").innerText = formatTime(audio.currentTime);
        document.getElementById("duration-time").innerText = formatTime(audio.duration || 0);
    }
};

const seekBar = document.getElementById("seek-bar");
seekBar.onmousedown = () => isScrubbing = true;
seekBar.onmouseup = () => isScrubbing = false;
seekBar.onchange = (e) => {
    if(audio.duration) {
        const seconds = (e.target.value / 100) * audio.duration;
        audio.currentTime = seconds;
        sendAction("seek_to", { seconds });
    }
};

document.getElementById("volume-slider").oninput = (e) => {
    audio.volume = e.target.value;
};

function sendAction(action, payload = {}) {
    if(ws.readyState === WebSocket.OPEN) {
        ws.send(JSON.stringify({ action, ...payload }));
    }
}

function togglePlay() {
    sendAction(globalState.is_playing ? "pause" : "resume");
}

function toggleRepeat() {
    let nextMode = "none";
    if (globalState.repeat_mode === "none") nextMode = "playlist";
    else if (globalState.repeat_mode === "playlist") nextMode = "track";
    sendAction("set_repeat", { mode: nextMode });
}

function addUrl() {
    const input = document.getElementById("music-url-input");
    const url = input.value.trim();
    if(url) {
        const isPlaylist = document.getElementById("dl-playlist-mode").checked;
        const savePlaylist = document.getElementById("dl-save-playlist").checked;
        const start = parseInt(document.getElementById("dl-start").value) || 0;
        const end = parseInt(document.getElementById("dl-end").value) || 100;
        
        sendAction("play_url", { 
            url, 
            enable_playlist: isPlaylist, 
            save_playlist: savePlaylist, 
            playlist_start: start, 
            playlist_end: end 
        });
        input.value = "";
    }
}

function loveTrack() {
    if(globalState.now_playing) {
        const nextLoved = !isLoved;
        const action = nextLoved ? "add" : "remove";
        setLoveButtonState(nextLoved);
        fetch(`http://${window.location.host}/playlists/${encodeURIComponent(sessionUsername)}_love/edit`, {
            method: "POST",
            headers: {"Content-Type":"application/json"},
            body: JSON.stringify({ action: action, music: [globalState.now_playing] })
        }).then(() => checkLovedState()).catch(() => checkLovedState());
        showToast(nextLoved ? "Added to Loved Tracks" : "Removed from Loved Tracks", "info");
    }
}

function renderQueue() {
    const list = document.getElementById("queue-list");
    const previousPositions = new Map();
    Array.from(list.children).forEach((child) => {
        if (child.dataset.queueKey) {
            previousPositions.set(child.dataset.queueKey, child.getBoundingClientRect().top);
        }
    });

    list.innerHTML = "";
    queueItemKeyCounts = new Map();
    globalState.queue.forEach((item, index) => {
        const occurrence = (queueItemKeyCounts.get(item) || 0) + 1;
        queueItemKeyCounts.set(item, occurrence);
        const queueKey = `${item}__${occurrence}`;
        const div = document.createElement("div");
        div.className = "queue-item" + (index === globalState.current_index ? " playing-item" : "");
        div.dataset.queueKey = queueKey;
        div.dataset.queueIndex = String(index);
        div.draggable = true;
        div.onclick = (e) => {
            if (draggedQueueIndex !== null) return;
            if(e.target.closest('.remove-btn')) return;
            sendAction("jump_to_queue", { index });
        };
        div.addEventListener("dragstart", (event) => {
            draggedQueueIndex = index;
            div.classList.add("dragging");
            if (event.dataTransfer) {
                event.dataTransfer.effectAllowed = "move";
                event.dataTransfer.setData("text/plain", String(index));
            }
        });
        div.addEventListener("dragend", () => {
            draggedQueueIndex = null;
            div.classList.remove("dragging");
            document.querySelectorAll(".queue-item.drag-over").forEach((itemElem) => itemElem.classList.remove("drag-over"));
        });
        div.addEventListener("dragover", (event) => {
            event.preventDefault();
            if (draggedQueueIndex === null || draggedQueueIndex === index) return;
            document.querySelectorAll(".queue-item.drag-over").forEach((itemElem) => itemElem.classList.remove("drag-over"));
            div.classList.add("drag-over");
            if (event.dataTransfer) {
                event.dataTransfer.dropEffect = "move";
            }
        });
        div.addEventListener("dragleave", () => {
            div.classList.remove("drag-over");
        });
        div.addEventListener("drop", (event) => {
            event.preventDefault();
            div.classList.remove("drag-over");
            const oldIndex = draggedQueueIndex;
            draggedQueueIndex = null;
            if (oldIndex === null || oldIndex === index) return;
            sendAction("reorder_queue", { old_index: oldIndex, new_index: index });
        });
        div.innerHTML = `
            <span class="track-index">${index + 1}</span>
            <span class="t-name">${item}</span>
            <button class="remove-btn" onclick="removeFromQueue(${index}, this)"><i class="fas fa-times"></i></button>
        `;
        list.appendChild(div);
    });

    requestAnimationFrame(() => {
        Array.from(list.children).forEach((child) => {
            const previousTop = previousPositions.get(child.dataset.queueKey);
            if (previousTop === undefined) return;
            const currentTop = child.getBoundingClientRect().top;
            const delta = previousTop - currentTop;
            if (Math.abs(delta) < 1) return;
            child.style.transition = "none";
            child.style.transform = `translateY(${delta}px)`;
            requestAnimationFrame(() => {
                child.style.transition = "transform 220ms ease, background 0.3s ease, opacity 0.3s ease";
                child.style.transform = "";
            });
        });
    });
}

function removeFromQueue(index, btnElem) {
    const item = btnElem.parentElement;
    item.classList.add("slide-out");
    setTimeout(() => {
        sendAction("remove_from_queue", { index });
    }, 300);
}

function formatTime(secs) {
    if(isNaN(secs)) return "0:00";
    const min = Math.floor(secs / 60);
    const s = Math.floor(secs % 60);
    return `${min}:${s.toString().padStart(2, '0')}`;
}

async function openPlaylistsModal() {
    document.getElementById("playlists-modal").classList.remove("hidden");
    const res = await fetch(`http://${window.location.host}/playlists`);
    const playlists = await res.json();
    
    const grid = document.getElementById("playlists-grid");
    grid.innerHTML = "";
    
    for (const [name, tracks] of Object.entries(playlists)) {
        const card = document.createElement("div");
        card.className = "playlist-card";
        card.innerHTML = `
            <button class="edit-playlist-btn" onclick="event.stopPropagation(); openEditPlaylist('${name.replace(/'/g, "\\'")}')" title="Edit Playlist"><i class="fas fa-pen" style="font-size:14px; margin:0; color:inherit;"></i></button>
            <i class="fas fa-list"></i>
            <h4>${name}</h4>
            <p>${tracks.length} track(s)</p>
        `;
        card.onclick = () => {
            sendAction("play_playlist", { playlist_name: name });
            closePlaylistsModal();
            showToast(`Loading playlist: ${name}`, "success");
        };
        grid.appendChild(card);
    }
}

function closePlaylistsModal() {
    document.getElementById("playlists-modal").classList.add("hidden");
}

/* MUSIC LIBRARY LOGIC */
let fullLibrary = [];

async function openLibraryModal() {
    document.getElementById("library-modal").classList.remove("hidden");
    const res = await fetch(`http://${window.location.host}/db`);
    const db = await res.json();
    fullLibrary = db.music || [];
    renderLibrary(fullLibrary);
}

function renderLibrary(tracks) {
    const grid = document.getElementById("library-grid");
    grid.innerHTML = "";
    tracks.forEach(t => {
        const row = document.createElement("div");
        row.className = "queue-item"; 
        row.style.marginBottom = "5px";
        row.innerHTML = `
            <span class="t-name">${t}</span>
            <button class="action-btn" style="padding: 5px 15px; font-size:12px;" onclick="playLibraryTrack('${t.replace(/'/g, "\\'")}')"><i class="fas fa-play"></i> Play</button>
        `;
        grid.appendChild(row);
    });
}

function filterLibrary() {
    const query = document.getElementById("library-search").value.toLowerCase();
    renderLibrary(fullLibrary.filter(t => t.toLowerCase().includes(query)));
}

function playLibraryTrack(track) {
    sendAction("play_track", { track });
    showToast(`Queued ${track}`, "success");
}

function closeLibraryModal() {
    document.getElementById("library-modal").classList.add("hidden");
}

/* PLAYLIST EDITING LOGIC */
let currentEditingPlaylist = "";

async function openEditPlaylist(name) {
    currentEditingPlaylist = name;
    document.getElementById("playlists-modal").classList.add("hidden");
    document.getElementById("edit-modal").classList.remove("hidden");
    document.getElementById("edit-modal-title").innerText = `Editing: ${name}`;
    
    const [dbRes, plRes] = await Promise.all([
        fetch(`http://${window.location.host}/db`),
        fetch(`http://${window.location.host}/playlists`)
    ]);
    const db = await dbRes.json();
    const playlists = await plRes.json();
    
    fullLibrary = db.music || [];
    renderEditList(fullLibrary, playlists[name] || []);
}

function renderEditList(tracks, activeTracks) {
    const list = document.getElementById("edit-list");
    list.innerHTML = "";
    document.getElementById("edit-add-search").value = ""; 
    list.dataset.active = JSON.stringify(activeTracks);
    
    // Sort active tracks to top
    activeTracks.forEach(t => {
        const row = document.createElement("div");
        row.className = "queue-item edit-row";
        row.innerHTML = `
            <span class="t-name">${t}</span>
            <button class="remove-btn" onclick="togglePlaylistItem('${t.replace(/'/g, "\\'")}', false)"><i class="fas fa-times"></i></button>
        `;
        list.appendChild(row);
    });
}

function filterAddEditList() {
    const query = document.getElementById("edit-add-search").value.toLowerCase();
    const addList = document.getElementById("add-edit-list");
    if(!query) {
        addList.style.display = "none";
        return;
    }
    addList.style.display = "block";
    addList.innerHTML = "";
    
    const activeTracks = JSON.parse(document.getElementById("edit-list").dataset.active || "[]");
    const results = fullLibrary.filter(t => !activeTracks.includes(t) && t.toLowerCase().includes(query)).slice(0, 50);
    
    results.forEach(t => {
        const row = document.createElement("div");
        row.className = "queue-item";
        row.style.marginBottom = "5px";
        row.innerHTML = `
            <span class="t-name">${t}</span>
            <button class="action-btn" style="padding: 5px 15px; font-size:12px;" onclick="togglePlaylistItem('${t.replace(/'/g, "\\'")}', true)"><i class="fas fa-plus"></i> Add</button>
        `;
        addList.appendChild(row);
    });
}

async function togglePlaylistItem(track, add) {
    const action = add ? "add" : "remove";
    await fetch(`http://${window.location.host}/playlists/${encodeURIComponent(currentEditingPlaylist)}/edit`, {
        method: "POST",
        headers: {"Content-Type":"application/json"},
        body: JSON.stringify({ action: action, music: [track] })
    });
    // Immediately fetch library again or assume success
    document.getElementById("edit-add-search").value = "";
    document.getElementById("add-edit-list").style.display = "none";
    openEditPlaylist(currentEditingPlaylist);
}

function closeEditModal() {
    document.getElementById("edit-modal").classList.add("hidden");
    openPlaylistsModal();
}

function showToast(msg, level) {
    const container = document.getElementById("toast-container");
    const toast = document.createElement("div");
    toast.className = `toast ${level}`;
    toast.innerHTML = `<i class="fas ${level === 'success' ? 'fa-check-circle' : (level === 'error' ? 'fa-exclamation-circle' : 'fa-info-circle')}"></i> ${msg}`;
    container.appendChild(toast);
    setTimeout(() => {
        toast.classList.add("toast-out");
        setTimeout(() => toast.remove(), 300);
    }, 4000);
}
