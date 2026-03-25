const wsUrl = `ws://${window.location.host}/ws`;
const audio = document.getElementById("video-player");
const playBtn = document.getElementById("play-pause-btn");
const discIcon = document.getElementById("disc-icon");
const sessionUsername = new URLSearchParams(window.location.search).get("username") || "anonymous";
const TRACK_ASSET_CACHE_VERSION = 3;
let ws;
let globalState = { queue: [], current_index: -1, now_playing: null, is_playing: false, repeat_mode: "none", current_time: 0 };
let isScrubbing = false;
let isLoved = false;
let draggedQueueIndex = null;
let queueItemKeyCounts = new Map();
let fullLibrary = [];
let currentEditingPlaylist = "";
let cachedTrackMetadata = new Map();
let trackAssetCache = new Map();
let pendingTrackAssetRequest = null;

function trackFileBase(trackName) {
    return `/music/${encodeURIComponent(trackName)}`;
}

function escapeHtml(value) {
    return String(value ?? "")
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;")
        .replace(/"/g, "&quot;")
        .replace(/'/g, "&#39;");
}

function defaultTrackAssets(trackName) {
    return {
        track: trackName,
        media_url: null,
        media_kind: "none",
        preview_url: null,
        preview_kind: "fallback",
        resolved: false,
    };
}

function normalizeTrackAssets(trackName, assetData) {
    const normalized = { ...defaultTrackAssets(trackName), ...(assetData || {}) };
    normalized.track = trackName;
    if (typeof normalized.resolved === "undefined" || normalized.resolved === null) {
        normalized.resolved = Boolean(normalized.preview_url || normalized.media_url);
    }
    return normalized;
}

function hasUsablePlaybackAsset(assetData) {
    return Boolean(assetData && assetData.media_url && !assetData.media_url.startsWith("/music/"));
}

function hasUsablePreviewAsset(assetData) {
    return Boolean(
        assetData
        && assetData.resolved
        && (!assetData.preview_url || !assetData.preview_url.startsWith("/music/"))
        && (assetData.preview_url || assetData.preview_kind === "fallback")
    );
}

function readStoredTrackAssets(trackName) {
    try {
        const raw = localStorage.getItem(`mt_asset_${trackName}`);
        if (!raw) return null;
        const parsed = JSON.parse(raw);
        if (!parsed || !parsed.cached_at) return null;
        if ((parsed.version || 0) !== TRACK_ASSET_CACHE_VERSION) {
            localStorage.removeItem(`mt_asset_${trackName}`);
            return null;
        }
        if (Date.now() - parsed.cached_at > 10 * 60 * 1000) {
            localStorage.removeItem(`mt_asset_${trackName}`);
            return null;
        }
        return parsed.data ? normalizeTrackAssets(trackName, parsed.data) : null;
    } catch (_) {
        return null;
    }
}

function storeTrackAssets(trackName, assetData) {
    if (!assetData || (!assetData.media_url && !assetData.preview_url)) return;
    try {
        const normalized = normalizeTrackAssets(trackName, assetData);
        localStorage.setItem(`mt_asset_${trackName}`, JSON.stringify({
            version: TRACK_ASSET_CACHE_VERSION,
            cached_at: Date.now(),
            data: normalized,
        }));
    } catch (_) {}
}

function parseTrackMetadata(trackName) {
    const raw = (trackName || "").trim();
    let title = raw;
    let artist = "Unknown";
    if (raw.includes(" - ")) {
        const splitIndex = raw.lastIndexOf(" - ");
        title = raw.slice(0, splitIndex).trim() || raw;
        artist = raw.slice(splitIndex + 3).trim() || "Unknown";
    }
    return { track: raw, title, artist };
}

function primeTrackMetadata(trackNames, metadataMap = {}) {
    (trackNames || []).forEach((track) => {
        cachedTrackMetadata.set(track, metadataMap[track] || parseTrackMetadata(track));
    });
}

function getTrackMetadata(trackName) {
    if (!cachedTrackMetadata.has(trackName)) {
        cachedTrackMetadata.set(trackName, parseTrackMetadata(trackName));
    }
    return cachedTrackMetadata.get(trackName);
}

async function apiFetch(path, options = {}) {
    const response = await fetch(`http://${window.location.host}${path}`, options);
    if (!response.ok) {
        let message = `Request failed (${response.status})`;
        try {
            const payload = await response.json();
            message = payload.detail || payload.message || message;
        } catch (error) {
            try {
                message = await response.text();
            } catch (_) {}
        }
        throw new Error(message);
    }
    const contentType = response.headers.get("content-type") || "";
    return contentType.includes("application/json") ? response.json() : response.text();
}

async function ensureTrackAssets(trackNames, mode = "preview") {
    const uniqueTracks = [...new Set((trackNames || []).filter(Boolean))];
    uniqueTracks.forEach((track) => {
        const current = normalizeTrackAssets(track, trackAssetCache.get(track));
        if (mode === "playback" && hasUsablePlaybackAsset(current)) return;
        if (mode !== "playback" && hasUsablePreviewAsset(current)) return;
        const stored = mode === "playback" ? readStoredTrackAssets(track) : null;
        if (!stored) return;
        if (mode === "playback" && !hasUsablePlaybackAsset(stored)) return;
        if (mode !== "playback" && !hasUsablePreviewAsset(stored)) return;
        trackAssetCache.set(track, stored);
    });

    const missingTracks = uniqueTracks.filter((track) => {
        const assetData = trackAssetCache.get(track);
        if (mode === "playback") return !hasUsablePlaybackAsset(assetData);
        return !hasUsablePreviewAsset(assetData);
    });
    if (missingTracks.length === 0) {
        return new Map(uniqueTracks.map((track) => [track, normalizeTrackAssets(track, trackAssetCache.get(track))]));
    }

    if (!pendingTrackAssetRequest) {
        pendingTrackAssetRequest = new Map();
    }

    const tracksToFetch = missingTracks.filter((track) => !pendingTrackAssetRequest.has(track));
    const waiters = missingTracks.map((track) => {
        if (!pendingTrackAssetRequest.has(track)) {
            let resolveFn;
            const promise = new Promise((resolve) => {
                resolveFn = resolve;
            });
            pendingTrackAssetRequest.set(track, { promise, resolve: resolveFn });
        }
        return pendingTrackAssetRequest.get(track).promise;
    });

    if (tracksToFetch.length > 0) {
        apiFetch("/media/assets", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ tracks: tracksToFetch, mode }),
        }).then((payload) => {
            tracksToFetch.forEach((track) => {
                const data = normalizeTrackAssets(track, payload[track]);
                trackAssetCache.set(track, data);
                storeTrackAssets(track, data);
                const waiter = pendingTrackAssetRequest.get(track);
                if (waiter) {
                    waiter.resolve(data);
                    pendingTrackAssetRequest.delete(track);
                }
            });
        }).catch(() => {
            tracksToFetch.forEach((track) => {
                const data = defaultTrackAssets(track);
                trackAssetCache.set(track, data);
                const waiter = pendingTrackAssetRequest.get(track);
                if (waiter) {
                    waiter.resolve(data);
                    pendingTrackAssetRequest.delete(track);
                }
            });
        });
    }

    await Promise.all(waiters);
    return new Map(uniqueTracks.map((track) => [track, normalizeTrackAssets(track, trackAssetCache.get(track))]));
}

async function getTrackAssets(trackName) {
    const resolved = await ensureTrackAssets([trackName], "playback");
    return resolved.get(trackName) || defaultTrackAssets(trackName);
}

function bindPreviewImage(img, url, showImage, showFallback) {
    let settled = false;
    const onLoad = () => {
        if (settled) return;
        settled = true;
        showImage();
    };
    const onError = () => {
        if (settled) return;
        settled = true;
        showFallback();
    };

    img.addEventListener("load", onLoad, { once: true });
    img.addEventListener("error", onError, { once: true });
    img.src = url;

    if (img.complete) {
        if (img.naturalWidth > 0) onLoad();
        else onError();
    }
}

function bindPreviewVideo(video, url, showVideo, showFallback) {
    let settled = false;
    const onReady = () => {
        if (settled) return;
        settled = true;
        showVideo();
    };
    const onError = () => {
        if (settled) return;
        settled = true;
        showFallback();
    };

    video.addEventListener("loadeddata", onReady, { once: true });
    video.addEventListener("error", onError, { once: true });
    video.src = url;

    if (video.readyState >= 2) {
        onReady();
    }
}

function setPlayerDisplayMode(mode) {
    const artContainer = document.getElementById("player-art-container");
    const fsBtn = document.getElementById("fullscreen-btn");
    artContainer.classList.remove("video-mode", "image-mode");
    if (mode === "video") {
        artContainer.classList.add("video-mode");
        fsBtn.classList.remove("hidden");
        return;
    }
    if (mode === "image") {
        artContainer.classList.add("image-mode");
    }
    fsBtn.classList.add("hidden");
}

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
        if (msg.type === "state") updateState(msg.state);
        else if (msg.type === "notification") showToast(msg.message, msg.level);
        else if (msg.type === "downloads_update") renderDownloads(msg.downloads);
        else if (msg.type === "lobby_info") {
            if (msg.phase === "local") populateLobbyLocal(msg.local_addrs);
            if (msg.phase === "external") populateLobbyExternal(msg.external_addr, msg.upnp_status);
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
    downloads.forEach((dl) => {
        const row = document.createElement("div");
        row.className = "queue-item";
        row.style.cursor = "default";
        const cleanProgress = dl.progress ? dl.progress.replace("~", "").trim() : "0%";
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
    const container = document.getElementById("lobby-links-container");
    if (!container) return;
    container.innerHTML = `
        <div style="display:flex; align-items:center; gap:12px; color:var(--text-secondary); padding:10px;">
            <i class="fas fa-circle-notch fa-spin" style="color:var(--accent);"></i>
            <span>Scanning local interfaces...</span>
        </div>
    `;
    document.getElementById("lobby-modal").classList.remove("hidden");
    sendAction("get_lobby_info");
}

function _lobbyRow(label, addr) {
    const d = document.createElement("div");
    d.style.cssText = "background:rgba(255,255,255,0.05); padding:12px 15px; border-radius:8px; display:flex; flex-direction:column; gap:8px; border:1px solid rgba(255,255,255,0.1);";
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
    container.innerHTML = "";
    (localAddrs || []).forEach(({ label, addr }) => {
        const icon = addr.startsWith("[") ? "fa-globe" : "fa-plug";
        container.appendChild(_lobbyRow(`<i class="fas ${icon}" style="color:var(--accent); margin-right:8px;"></i>${label}`, addr));
    });
    const ext = document.createElement("div");
    ext.id = "lobby-external-row";
    ext.style.cssText = "background:rgba(255,255,255,0.03); padding:12px 15px; border-radius:8px; display:flex; align-items:center; gap:12px; border:1px dashed rgba(255,255,255,0.12); animation:pulse-fade 1.5s ease-in-out infinite;";
    ext.innerHTML = `
        <i class="fas fa-circle-notch fa-spin" style="color:var(--accent); flex-shrink:0;"></i>
        <span style="color:var(--text-secondary); font-size:13px;">Testing external access &amp; UPnP port mapping...</span>`;
    container.appendChild(ext);
    if (!document.getElementById("lobby-pulse-style")) {
        const s = document.createElement("style");
        s.id = "lobby-pulse-style";
        s.textContent = "@keyframes pulse-fade { 0%,100%{opacity:.5} 50%{opacity:1} }";
        document.head.appendChild(s);
    }
}

function populateLobbyExternal(externalAddr, upnpStatus) {
    const row = document.getElementById("lobby-external-row");
    if (!row) return;
    const isOk = upnpStatus === "OK" || upnpStatus === "OK_already";
    const upnpLabel = upnpStatus === "OK"
        ? '<i class="fas fa-check-circle" style="margin-right:4px;"></i>Port auto-opened via UPnP'
        : upnpStatus === "OK_already"
        ? '<i class="fas fa-check-circle" style="margin-right:4px;"></i>Port already open'
        : upnpStatus;
    const badgeColor = isOk ? "rgba(3,218,198,0.15)" : "rgba(255,255,255,0.05)";
    const textColor = isOk ? "#03dac6" : "var(--text-secondary)";
    row.style.animation = "none";
    row.style.opacity = "1";
    row.style.border = isOk ? "1px solid rgba(3,218,198,0.3)" : "1px solid rgba(255,255,255,0.1)";
    row.style.background = isOk ? "rgba(3,218,198,0.05)" : "rgba(255,255,255,0.05)";
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

async function tryLoadThumbnail(trackName) {
    const img = document.getElementById("player-art-img");
    const assets = await getTrackAssets(trackName);
    if (globalState.now_playing !== trackName) return;
    if (assets.preview_kind !== "image" || !assets.preview_url) {
        img.removeAttribute("src");
        img.classList.add("hidden");
        discIcon.classList.remove("hidden");
        setPlayerDisplayMode("empty");
        return;
    }

    bindPreviewImage(img, assets.preview_url, () => {
        if (globalState.now_playing !== trackName) return;
        setPlayerDisplayMode("image");
        img.classList.remove("hidden");
        discIcon.classList.add("hidden");
    }, () => {
        if (globalState.now_playing !== trackName) return;
        setPlayerDisplayMode("empty");
        img.removeAttribute("src");
        img.classList.add("hidden");
        discIcon.classList.remove("hidden");
    });
}

function formatTime(secs) {
    if (Number.isNaN(secs)) return "0:00";
    const min = Math.floor(secs / 60);
    const s = Math.floor(secs % 60);
    return `${min}:${String(s).padStart(2, "0")}`;
}

function sendAction(action, payload = {}) {
    if (ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify({ action, ...payload }));
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
    if (!url) return;
    sendAction("play_url", {
        url,
        enable_playlist: document.getElementById("dl-playlist-mode").checked,
        save_playlist: document.getElementById("dl-save-playlist").checked,
        playlist_start: parseInt(document.getElementById("dl-start").value, 10) || 0,
        playlist_end: parseInt(document.getElementById("dl-end").value, 10) || 100,
    });
    input.value = "";
}

function toggleFullscreen() {
    const container = document.getElementById("player-art-container");
    if (!document.fullscreenElement) container.requestFullscreen().catch((err) => console.log(`Error attempting to enable fullscreen: ${err.message}`));
    else document.exitFullscreen();
}

function toggleAppFullscreen() {
    sendAction("toggle_app_fullscreen");
}

audio.onended = () => {
    if (globalState.repeat_mode === "track") {
        audio.currentTime = 0;
        audio.play().catch((e) => console.log(e));
    } else {
        document.getElementById("player-info").classList.add("fade-out");
        sendAction("skip");
    }
};

audio.ontimeupdate = () => {
    if (!isScrubbing) {
        const bar = document.getElementById("seek-bar");
        bar.value = (audio.currentTime / audio.duration) * 100 || 0;
        document.getElementById("current-time").innerText = formatTime(audio.currentTime);
        document.getElementById("duration-time").innerText = formatTime(audio.duration || 0);
    }
};

const seekBar = document.getElementById("seek-bar");
seekBar.onmousedown = () => { isScrubbing = true; };
seekBar.onmouseup = () => { isScrubbing = false; };
seekBar.onchange = (e) => {
    if (audio.duration) {
        const seconds = (e.target.value / 100) * audio.duration;
        audio.currentTime = seconds;
        sendAction("seek_to", { seconds });
    }
};

document.getElementById("volume-slider").oninput = (e) => {
    audio.volume = e.target.value;
};

function updateState(newState) {
    const oldState = globalState;
    globalState = newState;
    renderQueue();

    const trackChanged = oldState.now_playing !== newState.now_playing;
    if (trackChanged) {
        audio.pause();
        const infoDiv = document.getElementById("player-info");
        const artContainer = document.getElementById("player-art-container");
        infoDiv.classList.add("fade-out");
        document.getElementById("player-art-img").classList.add("hidden");

        setTimeout(() => {
            if (newState.now_playing) {
                const meta = getTrackMetadata(newState.now_playing);
                document.getElementById("now-playing-title").innerText = meta.title;
                document.getElementById("now-playing-artist").innerText = meta.artist;
                playAudioFile(newState.now_playing);
            } else {
                document.getElementById("now-playing-title").innerText = "Not Playing";
                document.getElementById("now-playing-artist").innerText = "Waiting for track...";
                audio.src = "";
                setPlayerDisplayMode("empty");
                audio.classList.add("hidden");
                document.getElementById("player-art-img").removeAttribute("src");
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
            if (audio.src && audio.paused) audio.play().catch((e) => console.log("Waiting for user interaction mapping", e));
        } else {
            audio.pause();
        }
    }

    discIcon.style.animationPlayState = newState.is_playing ? "running" : "paused";
    if (newState.is_playing) {
        playBtn.innerHTML = '<i class="fas fa-pause"></i>';
        if (newState.now_playing) document.querySelector(".player-art").classList.add("spinning");
    } else {
        playBtn.innerHTML = '<i class="fas fa-play"></i>';
        document.querySelector(".player-art").classList.remove("spinning");
    }

    const repBtn = document.getElementById("repeat-btn");
    repBtn.className = "icon-btn" + (newState.repeat_mode !== "none" ? " on" : "");
    repBtn.innerHTML = newState.repeat_mode === "track"
        ? '<i class="fas fa-redo" style="position:relative;"><span style="position:absolute;font-size:10px;top:5px;left:5px;">1</span></i>'
        : '<i class="fas fa-redo"></i>';
}

async function playAudioFile(trackName) {
    audio.classList.add("hidden");
    audio.removeAttribute("poster");
    document.getElementById("player-art-img").classList.add("hidden");
    discIcon.classList.remove("hidden");
    setPlayerDisplayMode("empty");
    const assets = await getTrackAssets(trackName);
    if (globalState.now_playing !== trackName) return;
    if (!assets.media_url) {
        console.error("Could not load audio file for", trackName);
        showToast(`Error loading track: ${trackName}. Skipping...`, "error");
        sendAction("skip");
        return;
    }

    audio.src = assets.media_url;
    audio.oncanplay = () => {
        audio.oncanplay = null;
        if (typeof globalState.current_time === "number") {
            const safeTime = audio.duration
                ? Math.min(Math.max(0, globalState.current_time), Math.max(0, audio.duration - 0.1))
                : Math.max(0, globalState.current_time);
            audio.currentTime = safeTime;
        }

        if (assets.media_kind === "video" && audio.videoWidth > 0) {
            audio.classList.remove("hidden");
            document.getElementById("player-art-img").classList.add("hidden");
            discIcon.classList.add("hidden");
            setPlayerDisplayMode("video");
            if (globalState.is_playing) document.getElementById("player-art-container").classList.add("spinning");
        } else {
            tryLoadThumbnail(trackName);
        }

        if (globalState.is_playing) audio.play().catch((e) => console.log("Waiting for user interaction mapping", e));
    };
    audio.onerror = () => {
        audio.onerror = null;
        console.error("Could not load audio file for", trackName);
        showToast(`Error loading track: ${trackName}. Skipping...`, "error");
        sendAction("skip");
    };
}

async function checkLovedState() {
    if (!globalState.now_playing) return;
    try {
        const playlists = await apiFetch(`/playlists?t=${Date.now()}`);
        const loved = playlists[`${sessionUsername}_love`] || [];
        setLoveButtonState(loved.includes(globalState.now_playing));
    } catch (_) {}
}

function loveTrack() {
    if (!globalState.now_playing) return;
    const nextLoved = !isLoved;
    const action = nextLoved ? "add" : "remove";
    setLoveButtonState(nextLoved);
    apiFetch(`/playlists/${encodeURIComponent(sessionUsername)}_love/edit`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ action, music: [globalState.now_playing] }),
    }).then(() => checkLovedState()).catch(() => checkLovedState());
    showToast(nextLoved ? "Added to Loved Tracks" : "Removed from Loved Tracks", "info");
}

function renderQueue() {
    const list = document.getElementById("queue-list");
    const previousPositions = new Map();
    Array.from(list.children).forEach((child) => {
        if (child.dataset.queueKey) previousPositions.set(child.dataset.queueKey, child.getBoundingClientRect().top);
    });

    list.innerHTML = "";
    queueItemKeyCounts = new Map();
    globalState.queue.forEach((item, index) => {
        const occurrence = (queueItemKeyCounts.get(item) || 0) + 1;
        queueItemKeyCounts.set(item, occurrence);
        const queueKey = `${item}__${occurrence}`;
        const meta = getTrackMetadata(item);

        const div = document.createElement("div");
        div.className = "queue-item" + (index === globalState.current_index ? " playing-item" : "");
        div.dataset.queueKey = queueKey;
        div.dataset.queueIndex = String(index);
        div.draggable = true;
        div.onclick = (e) => {
            if (draggedQueueIndex !== null) return;
            if (e.target.closest(".remove-btn")) return;
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
            if (event.dataTransfer) event.dataTransfer.dropEffect = "move";
        });
        div.addEventListener("dragleave", () => div.classList.remove("drag-over"));
        div.addEventListener("drop", (event) => {
            event.preventDefault();
            div.classList.remove("drag-over");
            const oldIndex = draggedQueueIndex;
            draggedQueueIndex = null;
            if (oldIndex === null || oldIndex === index) return;
            sendAction("reorder_queue", { old_index: oldIndex, new_index: index });
        });

        const indexSpan = document.createElement("span");
        indexSpan.className = "track-index";
        indexSpan.textContent = `${index + 1}`;

        const info = document.createElement("div");
        info.className = "track-meta";
        info.innerHTML = `<span class="t-name">${meta.title}</span><span class="t-artist">${meta.artist}</span>`;

        const removeButton = document.createElement("button");
        removeButton.className = "remove-btn";
        removeButton.innerHTML = '<i class="fas fa-times"></i>';
        removeButton.onclick = (event) => {
            event.stopPropagation();
            removeFromQueue(index, removeButton);
        };

        div.appendChild(indexSpan);
        div.appendChild(info);
        div.appendChild(removeButton);
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
    btnElem.parentElement.classList.add("slide-out");
    setTimeout(() => sendAction("remove_from_queue", { index }), 300);
}

function isProtectedPlaylist(name) {
    return name.endsWith("_love");
}

function buildPlaylistPreview(tracks, resolvedAssets = null) {
    const preview = document.createElement("div");
    preview.className = "playlist-preview-grid";
    const topTracks = (tracks || []).slice(0, 4);

    if (topTracks.length === 0) {
        const empty = document.createElement("div");
        empty.className = "playlist-preview-empty";
        empty.innerHTML = '<i class="fas fa-compact-disc"></i>';
        preview.appendChild(empty);
        return preview;
    }

    topTracks.forEach((track) => {
        const assets = (resolvedAssets && resolvedAssets.get(track))
            || trackAssetCache.get(track)
            || defaultTrackAssets(track);
        const tile = document.createElement("div");
        tile.className = "playlist-preview-tile";

        const img = document.createElement("img");
        img.className = "playlist-preview-image artwork-fallback-hidden";
        img.alt = track;
        img.loading = "eager";
        img.decoding = "async";

        const fallback = document.createElement("div");
        fallback.className = "playlist-preview-fallback";
        fallback.innerHTML = '<i class="fas fa-music"></i>';

        const video = document.createElement("video");
        video.className = "playlist-preview-video hidden";
        video.muted = true;
        video.loop = true;
        video.autoplay = true;
        video.playsInline = true;
        video.preload = "metadata";

        const showImage = () => {
            img.classList.remove("artwork-fallback-hidden", "hidden");
            video.classList.add("hidden");
            fallback.classList.add("hidden");
        };
        const showVideo = () => {
            video.classList.remove("hidden");
            img.classList.add("hidden");
            fallback.classList.add("hidden");
            const playPromise = video.play();
            if (playPromise && typeof playPromise.catch === "function") {
                playPromise.catch(() => {});
            }
        };
        const showFallback = () => {
            img.classList.add("hidden");
            video.classList.add("hidden");
            fallback.classList.remove("hidden");
        };

        const canUseLiveVideo = assets.media_kind === "video" && assets.media_url;

        if (canUseLiveVideo) {
            if (assets.preview_kind === "image" && assets.preview_url) {
                video.poster = assets.preview_url;
            }
            bindPreviewVideo(video, assets.media_url, showVideo, () => {
                if (assets.preview_kind === "image" && assets.preview_url) {
                    bindPreviewImage(img, assets.preview_url, showImage, showFallback);
                    return;
                }
                showFallback();
            });
        } else if (assets.preview_kind === "image" && assets.preview_url) {
            bindPreviewImage(img, assets.preview_url, showImage, showFallback);
        } else if (assets.preview_kind === "video" && assets.preview_url) {
            bindPreviewVideo(video, assets.preview_url, showVideo, showFallback);
        } else {
            showFallback();
        }

        tile.appendChild(img);
        tile.appendChild(video);
        tile.appendChild(fallback);
        preview.appendChild(tile);
    });

    while (preview.children.length < 4) {
        const filler = document.createElement("div");
        filler.className = "playlist-preview-tile playlist-preview-filler";
        filler.innerHTML = '<i class="fas fa-wave-square"></i>';
        preview.appendChild(filler);
    }

    return preview;
}

function createPlaylistCard(name, tracks, resolvedAssets = null) {
    const card = document.createElement("div");
    card.className = "playlist-card";
    card.dataset.playlistName = name;

    const actionBar = document.createElement("div");
    actionBar.className = "playlist-card-actions";

    const editButton = document.createElement("button");
    editButton.className = "edit-playlist-btn";
    editButton.title = "Edit Playlist";
    editButton.innerHTML = '<i class="fas fa-pen"></i>';
    editButton.onclick = (event) => {
        event.stopPropagation();
        openEditPlaylist(name);
    };
    actionBar.appendChild(editButton);

    if (!isProtectedPlaylist(name)) {
        const deleteButton = document.createElement("button");
        deleteButton.className = "delete-playlist-btn";
        deleteButton.title = "Delete Playlist";
        deleteButton.innerHTML = '<i class="fas fa-trash"></i>';
        deleteButton.onclick = async (event) => {
            event.stopPropagation();
            await deletePlaylist(name);
        };
        actionBar.appendChild(deleteButton);
    }

    const title = document.createElement("h4");
    title.innerHTML = escapeHtml(name);
    const subtitle = document.createElement("p");
    subtitle.textContent = `${tracks.length} track(s)`;

    card.appendChild(actionBar);
    card.appendChild(buildPlaylistPreview(tracks, resolvedAssets));
    card.appendChild(title);
    card.appendChild(subtitle);
    card.onclick = () => {
        sendAction("play_playlist", { playlist_name: name });
        closePlaylistsModal();
        showToast(`Loading playlist: ${name}`, "success");
    };
    return card;
}

async function refreshPlaylistsGrid() {
    const playlists = await apiFetch("/playlists");
    const playlistEntries = Object.entries(playlists)
        .sort(([a], [b]) => a.localeCompare(b, undefined, { sensitivity: "base" }));
    const previewTracks = playlistEntries.flatMap(([, tracks]) => (tracks || []).slice(0, 4));
    const previewAssets = await ensureTrackAssets(previewTracks, "preview");

    const grid = document.getElementById("playlists-grid");
    grid.innerHTML = "";
    playlistEntries
        .forEach(([name, tracks]) => grid.appendChild(createPlaylistCard(name, tracks, previewAssets)));
}

async function openPlaylistsModal() {
    document.getElementById("playlists-modal").classList.remove("hidden");
    await refreshPlaylistsGrid();
}

function closePlaylistsModal() {
    document.getElementById("playlists-modal").classList.add("hidden");
}

async function createPlaylist() {
    const input = document.getElementById("new-playlist-name");
    const name = input.value.trim();
    if (!name) {
        showToast("Playlist name cannot be empty", "error");
        return;
    }
    try {
        await apiFetch("/playlists", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ name }),
        });
        input.value = "";
        await refreshPlaylistsGrid();
        showToast(`Created playlist: ${name}`, "success");
    } catch (error) {
        showToast(error.message, "error");
    }
}

async function deletePlaylist(name) {
    if (!confirm(`Delete playlist "${name}"?`)) return;
    try {
        await apiFetch(`/playlists/${encodeURIComponent(name)}`, { method: "DELETE" });
        if (currentEditingPlaylist === name) document.getElementById("edit-modal").classList.add("hidden");
        await refreshPlaylistsGrid();
        showToast(`Deleted playlist: ${name}`, "success");
    } catch (error) {
        showToast(error.message, "error");
    }
}

function createTrackRow(track, actionLabel, actionIcon, actionHandler) {
    const meta = getTrackMetadata(track);
    const row = document.createElement("div");
    row.className = "queue-item queue-item-rich";

    const details = document.createElement("div");
    details.className = "track-meta";
    details.innerHTML = `<span class="t-name">${meta.title}</span><span class="t-artist">${meta.artist}</span>`;

    const button = document.createElement("button");
    button.className = "action-btn action-btn-inline";
    button.innerHTML = `<i class="fas ${actionIcon}"></i> ${actionLabel}`;
    button.onclick = (event) => {
        event.stopPropagation();
        actionHandler(track);
    };

    row.appendChild(details);
    row.appendChild(button);
    return row;
}

async function openLibraryModal() {
    document.getElementById("library-modal").classList.remove("hidden");
    const db = await apiFetch("/db");
    fullLibrary = db.music || [];
    primeTrackMetadata(fullLibrary, db.track_metadata || {});
    document.getElementById("library-name-search").value = "";
    document.getElementById("library-artist-search").value = "";
    renderLibrary(fullLibrary);
}

function renderLibrary(tracks) {
    const grid = document.getElementById("library-grid");
    grid.innerHTML = "";
    tracks.forEach((track) => grid.appendChild(createTrackRow(track, "Play", "fa-play", playLibraryTrack)));
}

function filterLibrary() {
    const nameQuery = document.getElementById("library-name-search").value.trim().toLowerCase();
    const artistQuery = document.getElementById("library-artist-search").value.trim().toLowerCase();
    const filtered = fullLibrary.filter((track) => {
        const meta = getTrackMetadata(track);
        return (!nameQuery || meta.title.toLowerCase().includes(nameQuery))
            && (!artistQuery || meta.artist.toLowerCase().includes(artistQuery));
    });
    renderLibrary(filtered);
}

function playLibraryTrack(track) {
    sendAction("play_track", { track });
    showToast(`Queued ${getTrackMetadata(track).title}`, "success");
}

function closeLibraryModal() {
    document.getElementById("library-modal").classList.add("hidden");
}

async function openEditPlaylist(name) {
    currentEditingPlaylist = name;
    document.getElementById("playlists-modal").classList.add("hidden");
    document.getElementById("edit-modal").classList.remove("hidden");
    document.getElementById("edit-modal-title").innerText = `Editing: ${name}`;
    document.getElementById("edit-playlist-name").value = name;

    const [db, playlists] = await Promise.all([apiFetch("/db"), apiFetch("/playlists")]);
    fullLibrary = db.music || [];
    primeTrackMetadata(fullLibrary, db.track_metadata || {});
    document.getElementById("edit-rename-row").classList.toggle("hidden", isProtectedPlaylist(name));
    renderEditList(playlists[name] || []);
}

function renderEditList(activeTracks) {
    const list = document.getElementById("edit-list");
    list.innerHTML = "";
    document.getElementById("edit-add-search").value = "";
    document.getElementById("add-edit-list").style.display = "none";
    list.dataset.active = JSON.stringify(activeTracks);

    activeTracks.forEach((track) => {
        const meta = getTrackMetadata(track);
        const row = document.createElement("div");
        row.className = "queue-item queue-item-rich edit-row";

        const details = document.createElement("div");
        details.className = "track-meta";
        details.innerHTML = `<span class="t-name">${meta.title}</span><span class="t-artist">${meta.artist}</span>`;

        const removeButton = document.createElement("button");
        removeButton.className = "remove-btn remove-btn-visible";
        removeButton.innerHTML = '<i class="fas fa-times"></i>';
        removeButton.onclick = () => togglePlaylistItem(track, false);

        row.appendChild(details);
        row.appendChild(removeButton);
        list.appendChild(row);
    });
}

function filterAddEditList() {
    const query = document.getElementById("edit-add-search").value.trim().toLowerCase();
    const addList = document.getElementById("add-edit-list");
    if (!query) {
        addList.style.display = "none";
        addList.innerHTML = "";
        return;
    }

    const activeTracks = JSON.parse(document.getElementById("edit-list").dataset.active || "[]");
    const results = fullLibrary.filter((track) => {
        if (activeTracks.includes(track)) return false;
        const meta = getTrackMetadata(track);
        return meta.title.toLowerCase().includes(query) || meta.artist.toLowerCase().includes(query);
    }).slice(0, 50);

    addList.style.display = "block";
    addList.innerHTML = "";
    results.forEach((track) => addList.appendChild(createTrackRow(track, "Add", "fa-plus", () => togglePlaylistItem(track, true))));
}

async function togglePlaylistItem(track, add) {
    try {
        await apiFetch(`/playlists/${encodeURIComponent(currentEditingPlaylist)}/edit`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ action: add ? "add" : "remove", music: [track] }),
        });
        await openEditPlaylist(currentEditingPlaylist);
    } catch (error) {
        showToast(error.message, "error");
    }
}

async function savePlaylistName() {
    const input = document.getElementById("edit-playlist-name");
    const newName = input.value.trim();
    if (!newName) {
        showToast("Playlist name cannot be empty", "error");
        return;
    }
    if (newName === currentEditingPlaylist) {
        showToast("Playlist name unchanged", "info");
        return;
    }

    try {
        const payload = await apiFetch(`/playlists/${encodeURIComponent(currentEditingPlaylist)}`, {
            method: "PATCH",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ new_name: newName }),
        });
        currentEditingPlaylist = payload.name;
        document.getElementById("edit-modal-title").innerText = `Editing: ${payload.name}`;
        document.getElementById("edit-playlist-name").value = payload.name;
        showToast(`Renamed playlist to ${payload.name}`, "success");
    } catch (error) {
        showToast(error.message, "error");
    }
}

function closeEditModal() {
    document.getElementById("edit-modal").classList.add("hidden");
    openPlaylistsModal();
}

function showToast(msg, level) {
    const container = document.getElementById("toast-container");
    const toast = document.createElement("div");
    toast.className = `toast ${level}`;
    toast.innerHTML = `<i class="fas ${level === "success" ? "fa-check-circle" : (level === "error" ? "fa-exclamation-circle" : "fa-info-circle")}"></i> ${msg}`;
    container.appendChild(toast);
    setTimeout(() => {
        toast.classList.add("toast-out");
        setTimeout(() => toast.remove(), 300);
    }, 4000);
}
