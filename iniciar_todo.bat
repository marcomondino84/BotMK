@echo off
title BotMK - Launcher Global
echo ======================================================
echo       INICIANDO BOTMK + TUNEL CLOUDFLARE PUBLICO
echo ======================================================
echo.
echo Levantando servidor del Bot...
start "BotMK - Servidor Uvicorn" cmd /k ".\venv\Scripts\uvicorn.exe main:app --host 127.0.0.1 --port 8000"
timeout /t 3 >nul

echo Levantando Tunel Seguro Cloudflare...
start "BotMK - Tunel Cloudflare" cmd /k ".\cloudflared.exe tunnel --url http://127.0.0.1:8000"

echo.
echo Todo listo! Revisa la ventana de Cloudflare para ver tu link https://...trycloudflare.com
echo.
pause
