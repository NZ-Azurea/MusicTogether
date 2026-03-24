@echo off
:: Strip SSLKEYLOGFILE from the CMD environment so pip doesn't crash on permissions
set SSLKEYLOGFILE=

:: Set this to 1 to build the background Production version, or 0 for Debug version with console!
set BUILD_PRODUCTION=1

if "%BUILD_PRODUCTION%"=="1" (
    set CONSOLE_FLAG=--noconsole
    set EXE_NAME="Music_Together"
) else (
    set CONSOLE_FLAG=
    set EXE_NAME="Music_Together_Debug"
)

echo Building %EXE_NAME% Executable...
call .venv\Scripts\activate.bat

:: Make sure pyinstaller is installed
pip install pyinstaller pywebview

:: Build the executable
:: --noconsole removes the background cmd window (though we might want it for server logs?)
:: --add-data copies the 'web' frontend folder into the internal pyinstaller bundle
pyinstaller --name %EXE_NAME% %CONSOLE_FLAG% ^
            --onefile ^
            --icon "Asset/logo.ico" ^
            --add-data "src/web;web" ^
            --add-data "Asset;Asset" ^
            --paths "src/Python_API" ^
            --hidden-import "Music_Together_API" ^
            --hidden-import "urllib" ^
            --hidden-import "urllib.request" ^
            --hidden-import "urllib3" ^
            --hidden-import "uvicorn" ^
            --hidden-import "fastapi" ^
            --hidden-import "websockets" ^
            src/main.py

echo Done! Custom executable generated in the dist/ folder.
pause
