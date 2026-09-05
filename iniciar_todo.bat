@echo off
title BotMK - Launcher Global
cd /d "%~dp0"
echo ======================================================
echo          INICIANDO BOTMK + TUNEL FIJO NGROK
echo ======================================================
echo.
echo [1/3] Levantando servidor del Bot...
start "BotMK - Servidor Uvicorn" cmd /k "title Servidor BotMK && venv\Scripts\python.exe main.py"

echo [2/3] Esperando inicializacion del servidor (3 seg)...
ping 127.0.0.1 -n 4 >nul

echo [3/3] Levantando Tunel Seguro Fijo Ngrok...
start "BotMK - Tunel Ngrok" cmd /k "title Tunel Ngrok && ngrok.exe http --url=promptly-buggy-ibuprofen.ngrok-free.dev 127.0.0.1:8000"

ping 127.0.0.1 -n 3 >nul
echo Abriendo Dashboard en tu PC...
start http://localhost:8000

echo.
echo ======================================================
echo  TODO LISTO! TU BOT ESTA FUNCIONANDO
echo ======================================================
echo.
echo 1. EN TU PC:
echo    http://localhost:8000
echo.
echo 2. TU LINK FIJO (EN CUALQUIER LUGAR, 4G/5G O CELULAR):
echo    https://promptly-buggy-ibuprofen.ngrok-free.dev
echo.
echo 3. ACCESO DIRECTO EN CASA (MISMO WI-FI):
echo    http://192.168.0.23:8000
echo.
echo * REGLA DE ORO:
echo   NO CIERRES las dos ventanas de CMD ("Servidor BotMK" y "Tunel Ngrok").
echo   Simplemente minimizalas.
echo ======================================================
echo.
pause


