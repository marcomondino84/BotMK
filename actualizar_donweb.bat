@echo off
cd /d "%~dp0"
python deploy_donweb.py
if %ERRORLEVEL% neq 0 (
    echo Error en el deploy a DonWeb.
    pause
    exit /b %ERRORLEVEL%
)
echo Deploy finalizado con exito.
