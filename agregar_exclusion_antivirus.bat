@echo off
title Agregar Exclusion en Windows Defender
echo ================================================================
echo   AGREGAR BOTMK A EXCLUSIONES DE WINDOWS DEFENDER
echo ================================================================
echo.
echo Se solicitara autorizacion de Administrador para que el antivirus
echo de Windows NUNCA vuelva a bloquear ni borrar 'ngrok.exe' en la noche.
echo.
powershell -Command "Start-Process powershell -Verb RunAs -ArgumentList '-Command', 'Add-MpPreference -ExclusionPath \"%~dp0\"; Write-Host \"Exclusion agregada con exito para %~dp0\" -ForegroundColor Green; Start-Sleep -Seconds 3'"
echo Listo. Ya puedes cerrar esta ventana.
pause
