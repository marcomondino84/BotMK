@echo off
title Habilitar Acceso Red Local - BotMK
:: Comprobar si se ejecuta como Administrador
net session >nul 2>&1
if %errorLevel% neq 0 (
    echo.
    echo ======================================================================
    echo  ERROR: Este archivo requiere permisos de Administrador.
    echo  Por favor haz clic DERECHO en este archivo y selecciona:
    echo  "EJECUTAR COMO ADMINISTRADOR"
    echo ======================================================================
    echo.
    pause
    exit /b 1
)

echo ======================================================================
echo  Habilitando puerto 8000 en el Firewall de Windows para el BotMK...
echo ======================================================================
echo.

netsh advfirewall firewall delete rule name="BotMK Web Port 8000" >nul 2>&1
netsh advfirewall firewall add rule name="BotMK Web Port 8000" dir=in action=allow protocol=TCP localport=8000 >nul 2>&1

echo [OK] Regla de Firewall creada con exito.
echo.
echo Tu IP local actual es: 192.168.0.23
echo.
echo Desde tu celular conectado al mismo Wi-Fi, abre en el navegador:
echo   http://192.168.0.23:8000
echo.
echo ======================================================================
pause
