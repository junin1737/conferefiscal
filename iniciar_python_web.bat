@echo off
chcp 65001 >nul
title ConfereFiscal Python - Servidor Web e API da Base Local (Porta 5000)
cd /d "%~dp0"

if exist ".venv\Scripts\python.exe" (
    echo Iniciando Servidor Web ConfereFiscal Python em http://127.0.0.1:5000 ...
    ".venv\Scripts\python.exe" -m python_app.web_server
) else if exist "C:\Python312AppPedidos\python.exe" (
    echo Iniciando Servidor Web ConfereFiscal Python em http://127.0.0.1:5000 ...
    "C:\Python312AppPedidos\python.exe" -m python_app.web_server
) else (
    echo Iniciando Servidor Web ConfereFiscal Python em http://127.0.0.1:5000 ...
    python -m python_app.web_server
)
pause
