@echo off
echo Building Music Together Executable...
call .venv\Scripts\activate.bat

:: Make sure pyinstaller is installed
pip install pyinstaller pywebview

:: Build the executable
:: --noconsole removes the background cmd window (though we might want it for server logs?)
:: --onedir is honestly safer than --onefile for fast booting, but users usually want --onefile. Let's do --onefile
:: --add-data copies the 'web' frontend folder into the internal pyinstaller bundle
pyinstaller --name "Music_Together" ^
            --onefile ^
            --add-data "src/web;web" ^
            --hidden-import "uvicorn" ^
            --hidden-import "fastapi" ^
            --hidden-import "websockets" ^
            src/main.py

echo Done! Custom executable generated in the dist/ folder.
pause
