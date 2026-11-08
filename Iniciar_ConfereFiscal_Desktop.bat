@echo off
chcp 65001 >nul
cd /d "%~dp0"
if exist "%~dp0runtime\pythonw.exe" (
    start "" "%~dp0runtime\pythonw.exe" -m python_app.desktop_app
) else if exist "C:\Python312AppPedidos\pythonw.exe" (
    start "" "C:\Python312AppPedidos\pythonw.exe" -m python_app.desktop_app
) else (
    python -m python_app.desktop_app
)
