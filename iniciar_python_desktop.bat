@echo off
chcp 65001 >nul
title ConfereFiscal Python - Gerenciador de Base Local e Conciliacao Desktop
cd /d "%~dp0"

if exist ".venv\Scripts\python.exe" (
    echo Iniciando ConfereFiscal Desktop (Python .venv)...
    ".venv\Scripts\python.exe" -m python_app.desktop_app
) else if exist "C:\Python312AppPedidos\python.exe" (
    echo Iniciando ConfereFiscal Desktop (Python 3.12)...
    "C:\Python312AppPedidos\python.exe" -m python_app.desktop_app
) else (
    echo Iniciando ConfereFiscal Desktop (Python do sistema)...
    python -m python_app.desktop_app
)
pause
