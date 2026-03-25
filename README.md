# Music Together

Music Together is a local-first shared music lobby. One host runs the backend server and other clients can connect to the same session, share a queue, sync playback, and maintain per-user loved tracks.

## V1 Status

This repository is the finished V1 of the project.

## Features

- Shared queue and synchronized playback state
- Playback seek bar synced to the server timeline
- Per-user loved playlists
- Username on both host and connect flows
- Launcher memory for the last username and IP address used
- Lobby network discovery with local and external address hints
- Background UPnP / external IP discovery so the backend stays responsive
- Production build support through PyInstaller

## Requirements

### Runtime

- Python 3.13.x recommended and tested with this workspace
- `ffmpeg` available on `PATH` for media playback and conversion work handled by `yt-dlp`
- Windows desktop environment for the launcher

### Python dependencies

These are listed in [`requirements.txt`](/E:/Vscode/Python/Perso/Music_together/requirements.txt):

- `yt-dlp`
- `fastapi`
- `uvicorn`
- `websockets`
- `pydantic`
- `psutil`
- `pywebview`
- `pyinstaller`
- `miniupnpc`

## Project Layout

- [`src/main.py`](/E:/Vscode/Python/Perso/Music_together/src/main.py) launches the desktop app and starts the backend
- [`src/Python_API/Music_Together_API.py`](/E:/Vscode/Python/Perso/Music_together/src/Python_API/Music_Together_API.py) contains the FastAPI backend and websocket protocol
- [`src/Python_API/json_loader.py`](/E:/Vscode/Python/Perso/Music_together/src/Python_API/json_loader.py) reads and writes the JSON database
- [`src/Python_API/link_handler.py`](/E:/Vscode/Python/Perso/Music_together/src/Python_API/link_handler.py) handles downloads and playlist management
- [`src/web/index.html`](/E:/Vscode/Python/Perso/Music_together/src/web/index.html) is the frontend entry point
- [`src/web/app.js`](/E:/Vscode/Python/Perso/Music_together/src/web/app.js) contains the client logic
- [`src/web/index.css`](/E:/Vscode/Python/Perso/Music_together/src/web/index.css) contains the styling

## Persistent Data

The app stores its state in [`db.json`](/E:/Vscode/Python/Perso/Music_together/db.json).

It contains:

- downloaded music metadata
- playlists
- launcher settings such as the last username and IP address used

## Running From Source

1. Create and activate a virtual environment.
2. Install the dependencies from `requirements.txt`.
3. Make sure `ffmpeg` is installed and available on `PATH`.
4. Run:

```powershell
python src/main.py
```

## Production Build

The build script is [`build.bat`](/E:/Vscode/Python/Perso/Music_together/build.bat).

- Set `BUILD_PRODUCTION=1` to build the hidden-console production EXE.
- Set `BUILD_PRODUCTION=0` to build a debug EXE with a console.

The production build uses PyInstaller and bundles the backend Python package and frontend files into the executable.

## License

This project uses the [PolyForm Noncommercial License 1.0.0](https://polyformproject.org/licenses/noncommercial/1.0.0/).

- noncommercial use is allowed
- commercial use is not allowed by default
- the required notice in [`LICENSE`](/E:/Vscode/Python/Perso/Music_together/LICENSE) must be kept in redistributions

## Notes

- The backend now avoids blocking the event loop for the lobby network discovery path.
- The launcher remembers the last username and IP locally and in the JSON database.
- Production startup output is kept ASCII-safe to avoid console encoding crashes.
