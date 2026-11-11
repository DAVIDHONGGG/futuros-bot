@echo off
REM Configura el token y el user id de Telegram con doble clic, sin abrir
REM PowerShell ni escribir comandos. Pensado para preparar cada mini PC nueva
REM (INSTALAR-MAQUINA-NUEVA.md, paso 4).
REM
REM Busca el interprete igual que run_listener.bat: no se puede llamar a
REM "python" a secas porque en Windows 11 hay un alias en WindowsApps que NO
REM es Python (solo abre la Store) y viene en el PATH de fabrica.

setlocal
cd /d "%~dp0"
set PYTHONNOUSERSITE=1

set "PY="
call :probar "%~dp0python-portable\python.exe"
if not defined PY for /d %%D in ("%LOCALAPPDATA%\Programs\Python\Python3*") do call :probar "%%D\python.exe"
if not defined PY for /d %%D in ("%ProgramFiles%\Python3*") do call :probar "%%D\python.exe"
if not defined PY for /f "delims=" %%I in ('where python 2^>nul') do call :probar "%%I"

if not defined PY (
    echo No se encontro ningun Python utilizable.
    echo Instalalo desde python.org ^(ver INSTALAR-MAQUINA-NUEVA.md^).
    pause
    exit /b 1
)

echo ============================================================
echo  Configurar Telegram para esta maquina
echo ============================================================
echo.
echo  ANTES de seguir, copia el token desde @BotFather en tu
echo  telefono o en Telegram Web DE ESTA PC: tocalo y se copia solo.
echo.
echo  El portapapeles del telefono NO llega a esta PC: si lo
echo  copiaste alla, abri Telegram aca y copialo de nuevo.
echo.
echo  Si ya lo copiaste, presiona una tecla.
echo.
pause >nul

"%PY%" configurar_telegram.py --portapapeles
echo.

if errorlevel 1 (
    echo ------------------------------------------------------------
    echo  El token NO se guardo. Revisa el motivo de arriba, copia el
    echo  token de nuevo y volve a hacer doble clic en este archivo.
    echo ------------------------------------------------------------
    echo.
    pause
    exit /b 1
)

echo ============================================================
echo  Ahora el user id
echo ============================================================
echo.
echo  Mandale cualquier mensaje a @userinfobot en Telegram y te
echo  responde tu id numerico. Escribilo aca abajo.
echo.
echo  Si todavia no lo tenes, dejalo vacio y presiona Enter: podes
echo  correr este archivo de nuevo mas tarde.
echo.

"%PY%" configurar_telegram.py --user-id
echo.

echo ============================================================
echo  Revisando la instalacion
echo ============================================================
echo.
"%PY%" check_setup.py
echo.
pause
exit /b 0

REM --- Valida un candidato a interprete -----------------------------------
:probar
if defined PY exit /b 0
if not exist "%~1" exit /b 0
"%~1" -c "import sys" >nul 2>&1
if errorlevel 1 exit /b 0
set "PY=%~1"
exit /b 0
