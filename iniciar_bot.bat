@echo off
title BotMK - Servidor Principal FastAPI
echo ======================================================
echo          INICIANDO SERVIDOR BOTMK EN PUERTO 8000
echo ======================================================
echo.
.\venv\Scripts\uvicorn.exe main:app --host 127.0.0.1 --port 8000
pause
