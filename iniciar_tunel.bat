@echo off
title Cloudflare Tunnel - BotMK
echo ======================================================
echo    INICIANDO TUNEL SEGURO CLOUDFLARE PARA BOTMK
echo ======================================================
echo.
echo Tu bot estara disponible publicamente con certificado SSL HTTPS.
echo.
.\cloudflared.exe tunnel --url http://127.0.0.1:8000
pause
