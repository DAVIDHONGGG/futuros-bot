@echo off
REM Arranca el listener de Telegram desde la carpeta del bot, sin importar
REM desde donde se ejecute el .bat (doble clic, tarea programada, etc).
REM Antes revisa la instalacion: si falta algo critico, avisa y no arranca.
REM
REM   run_listener.bat        interactivo: todo a la consola, y no se cierra
REM                           sola al terminar, para poder leer que paso.
REM   run_listener.bat auto   para la tarea programada: todo a logs\, y sin
REM                           pause (si no, la tarea quedaria colgada para
REM                           siempre esperando una tecla que nadie aprieta).

setlocal
cd /d "%~dp0"

REM PYTHONNOUSERSITE evita que el Python embebido del paquete se lleve las
REM librerias del site-packages del usuario.
set PYTHONNOUSERSITE=1

REM --- Encontrar un Python que sirva -------------------------------------
REM No se puede llamar a "python" a secas: en Windows 11 existe un alias en
REM WindowsApps que NO es Python (solo abre la Store) y se cuela en el PATH.
REM Por eso cada candidato se valida ejecutandolo antes de usarlo.
REM Se usa "for /d" y no "dir /s": dir no admite comodines en la parte de
REM CARPETA, asi que "...\Python3*\python.exe" no encuentra nada nunca.
set "PY="
call :probar "%~dp0python-portable\python.exe"
if not defined PY for /d %%D in ("%LOCALAPPDATA%\Programs\Python\Python3*") do call :probar "%%D\python.exe"
if not defined PY for /d %%D in ("%ProgramFiles%\Python3*") do call :probar "%%D\python.exe"
if not defined PY for /f "delims=" %%I in ('where python 2^>nul') do call :probar "%%I"

if not defined PY (
    echo No se encontro ningun Python utilizable.
    echo Instalalo desde python.org ^(ver INSTALAR-MAQUINA-NUEVA.md^).
    if /i not "%~1"=="auto" pause
    exit /b 1
)

if /i "%~1"=="auto" goto :programada

REM --- Modo interactivo ---------------------------------------------------
echo Python: %PY%
echo.
echo === Revisando la instalacion ===
"%PY%" check_setup.py
if errorlevel 1 (
    echo.
    echo Falta algo critico ^(ver arriba^). No se arranca el listener.
    pause
    exit /b 1
)

echo.
echo === Cerrando navegadores huerfanos del bot ===
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0cerrar_navegadores.ps1"

echo.
echo === Arrancando el listener ===
"%PY%" listener_futuros.py

REM Si el listener termina (error o Ctrl+C), la ventana no se cierra sola:
REM en una PC headless conviene poder leer que paso.
echo.
echo El listener termino.
pause
exit /b 0

REM --- Modo tarea programada ---------------------------------------------
:programada
if not exist "logs" mkdir "logs"
REM Un archivo por dia. La fecha sale de Python y no de %date%, que cambia de
REM formato segun la configuracion regional de cada maquina.
REM
REM Se pasa por un archivo temporal en vez de "for /f": ahi la ruta del
REM interprete va entre comillas DENTRO de unas comillas, cmd se pierde y la
REM variable quedaba vacia (el log salia como "listener-.log").
set "HOY="
"%PY%" -c "import datetime;print(datetime.date.today().isoformat())" > "%TEMP%\botfuturos-fecha.txt" 2>nul
if exist "%TEMP%\botfuturos-fecha.txt" set /p HOY=<"%TEMP%\botfuturos-fecha.txt"
if not defined HOY set "HOY=sin-fecha"
set "LOG=logs\listener-%HOY%.log"

echo ===== Arranque %HOY% =====>> "%LOG%"
"%PY%" check_setup.py >> "%LOG%" 2>&1
if errorlevel 1 (
    echo Falta algo critico: no se arranca el listener.>> "%LOG%"
    exit /b 1
)
REM Un Chromium huerfano de un listener anterior tiene el perfil tomado.
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0cerrar_navegadores.ps1" >> "%LOG%" 2>&1
"%PY%" listener_futuros.py >> "%LOG%" 2>&1
echo El listener termino con codigo %errorlevel%.>> "%LOG%"
exit /b %errorlevel%

REM --- Valida un candidato a interprete -----------------------------------
:probar
if defined PY exit /b 0
if not exist "%~1" exit /b 0
"%~1" -c "import sys" >nul 2>&1
if errorlevel 1 exit /b 0
set "PY=%~1"
exit /b 0
