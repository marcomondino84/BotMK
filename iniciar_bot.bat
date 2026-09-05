@echo off
title BotMK - Servidor Principal FastAPI
cd /d "%~dp0"
echo ======================================================
echo          INICIANDO SERVIDOR BOTMK EN PUERTO 8000
echo ======================================================
echo.
echo Levantando servidor del Bot...
start "BotMK - Servidor Uvicorn" cmd /k "cd /d "%~dp0" && "%~dp0venv\Scripts\uvicorn.exe" main:app --host 127.0.0.1 --port 8000"

echo Esperando inicializacion (3 seg)...
ping 127.0.0.1 -n 4 >nul

echo Abriendo Dashboard en tu navegador...
start http://localhost:8000

echo.
echo Servidor levantado en segundo plano!
echo Puedes minimizar esta ventana.
echo.
pause
