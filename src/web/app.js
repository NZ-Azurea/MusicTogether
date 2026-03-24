const wsUrl = `ws://${window.location.host}/ws`;
let ws;
const audio = document.getElementById("audio-player");
const playBtn = document.getElementById("play-pause-btn");
const discIcon = document.getElementById("disc-icon");

// State
let globalState = { queue: [], now_playing: null, is_playing: false, repeat_mode: "none" };
let isScrubbing = false;

function connectWebsocket() {
    ws = new WebSocket(wsUrl);
    ws.onmessage = (event) => {
        const msg = JSON.parse(event.data);
        if (msg.type === "state") {
            updateState(msg.state);
        } else if (msg.type === "notification") {
            showToast(msg.message, msg.level);
        }
    };
    ws.onclose = () => {
        showToast("Disconnected from server. Reconnecting...", "error");
        setTimeout(connectWebsocket, 3000);
    };
}
connectWebsocket();

function updateState(newState) {
    const oldState = globalState;
    globalState = newState;
    
    renderQueue();
    
    if (oldState.now_playing !== newState.now_playing) {
        if (newState.now_playing) {
            document.getElementById("now-playing-title").innerText = newState.now_playing.split(" - ")[0];
            document.getElementById("now-playing-artist").innerText = newState.now_playing.split(" - ").slice(1).join(" - ") || "Unknown";
            
            // Seamlessly loop through common YT extensions fetched by yt-dlp to find our local file
            playAudioFile(newState.now_playing);
            
        } else {
            document.getElementById("now-playing-title").innerText = "Not Playing";
            document.getElementById("now-playing-artist").innerText = "Waiting for track...";
            audio.src = "";
            discIcon.classList.remove("spinning");
        }
    }
    
    if (newState.is_playing) {
        if(audio.src && audio.paused) audio.play().catch(e => console.log("Waiting for user interaction mapping", e));
        playBtn.innerHTML = '<i class="fas fa-pause"></i>';
        discIcon.classList.add("spinning");
    } else {
        audio.pause();
        playBtn.innerHTML = '<i class="fas fa-play"></i>';
        discIcon.classList.remove("spinning");
    }
    
    const repBtn = document.getElementById("repeat-btn");
    repBtn.className = "icon-btn" + (newState.repeat_mode !== "none" ? " on" : "");
    if(newState.repeat_mode === "track") repBtn.innerHTML = '<i class="fas fa-redo" style="position:relative;"><span style="position:absolute;font-size:10px;top:5px;left:5px;">1</span></i>';
    else repBtn.innerHTML = '<i class="fas fa-redo"></i>';
}

function playAudioFile(trackName) {
    // We attempt these typical extensions downloaded by yt-dlp natively
    const extensions = ['.webm', '.m4a', '.mp4', '.mp3', '.opus'];
    let currentIndex = 0;
    
    const tryNextExtension = () => {
        if(currentIndex >= extensions.length) {
            console.error("Could not load audio file for", trackName);
            showToast(`Error loading track: ${trackName}. Skipping...`, "error");
            sendAction("skip");
            return;
        }
        audio.src = `/music/${encodeURIComponent(trackName)}${extensions[currentIndex]}`;
        currentIndex++;
    };
    
    audio.onerror = tryNextExtension;
    tryNextExtension();
}

audio.onended = () => {
    sendAction("skip");
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
    if(audio.duration) audio.currentTime = (e.target.value / 100) * audio.duration;
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
        sendAction("play_url", { url });
        input.value = "";
    }
}

function loveTrack() {
    if(globalState.now_playing) {
        sendAction("love", { username: "Host", track: globalState.now_playing });
        document.querySelector('.love-btn i').className = "fas fa-heart";
        setTimeout(() => document.querySelector('.love-btn i').className = "far fa-heart", 1000);
    }
}

function renderQueue() {
    const list = document.getElementById("queue-list");
    list.innerHTML = "";
    globalState.queue.forEach((item, index) => {
        const div = document.createElement("div");
        div.className = "queue-item";
        div.innerHTML = `<span class="track-index">${index + 1}</span><span class="t-name">${item}</span>`;
        list.appendChild(div);
    });
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
