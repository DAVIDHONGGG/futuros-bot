<#
.SYNOPSIS
    Actualiza el bot de futuros en ESTA maquina desde el repo, sin pisar su
    configuracion.

.DESCRIPTION
    Adaptado del actualizar.ps1 del bot de MT5. Pasos: verificar que se pueda
    -> parar de verdad -> git pull -> tests -> check_setup -> arrancar ->
    confirmar que el listener volvio. Si los tests fallan, REVIERTE solo.

    Diferencias con MT5, todas a proposito:
      - Mata SOLO su listener (listener_futuros.py) y su tarea
        ("bot-futuros listener"). El de MT5 mata cualquier
        "*telegram_listener.py*": por eso este bot NO se llama asi.
      - No hay plantillas que guardar: cualquier cambio local frena el update
        (config.local.json, logs/, perfiles/ y programadas.json estan en
        .gitignore y no cuentan).
      - La guarda de "orden en vuelo" lee las marcas INICIO OPERACION /
        FIN OPERACION que el ejecutor escribe; la de fin va en un finally,
        asi que una orden abortada tambien la deja.

.PARAMETER MinutosDeGuarda
    Se niega a actualizar si hay algo programado dentro de esta ventana.

.PARAMETER Force
    Saltea las guardas de "orden en vuelo" y "programada cerca".

.EXAMPLE
    .\actualizar.ps1
    .\actualizar.ps1 -Force
#>

param(
    [int]$MinutosDeGuarda = 10,
    [switch]$Force,
    [string]$Tarea = "bot-futuros listener",
    [string]$ChatId = ""
)

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

# Nombre del script del listener: es lo que se busca en la linea de comando
# de python.exe para pararlo. Unico a proposito (ver .DESCRIPTION).
$ScriptListener = "listener_futuros.py"

# --- Dejar rastro SIEMPRE ------------------------------------------------
if (-not (Test-Path "logs")) { New-Item -ItemType Directory "logs" | Out-Null }
$Transcripcion = "logs\actualizar-$(Get-Date -Format 'yyyy-MM-dd').log"
try { Start-Transcript -Path $Transcripcion -Append | Out-Null } catch { }

function Paso($t)  { Write-Host "`n=== $t ===" -ForegroundColor Cyan }
function Ok($t)    { Write-Host "  OK: $t" -ForegroundColor Green }
function Aviso($t) { Write-Host "  AVISO: $t" -ForegroundColor Yellow }

# Correr un exe y QUEDARSE con lo que dijo. El 2>&1 va ADENTRO con la
# preferencia bajada: afuera, con Stop, cada linea de stderr se envuelve en
# un NativeCommandError que TUMBA el script aunque el programa salga con 0
# (en MT5, un "warning: LF will be replaced by CRLF" de git alcanzaba).
function CorrerCapturando($exe, [string[]]$argumentos) {
    $previo = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try { $salida = & $exe @argumentos 2>&1 | ForEach-Object { "$_" } }
    finally { $ErrorActionPreference = $previo }
    return ($salida -join "`n")
}

# Igual, sin capturar. NO devuelve el codigo: en PowerShell todo lo que una
# funcion escribe ES su valor de retorno. Mirar $LASTEXITCODE despues.
function Correr($exe, [string[]]$argumentos) {
    $previo = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try { & $exe @argumentos } finally { $ErrorActionPreference = $previo }
}

function Recortar($texto, $limite) {
    if ($null -eq $texto) { return "" }
    $t = [string]$texto
    if ($t.Length -le $limite) { return $t }
    return $t.Substring(0, $limite - 60) + "`n... (recortado; completo en el log de la maquina)"
}

# --- Avisar por Telegram -------------------------------------------------
# Cuando esto lo dispara `update`, el listener ya esta muerto al terminar:
# el unico que puede contar como salio es este script.
$script:Maquina = "?"
$script:Token = ""
if (Test-Path "config.local.json") {
    try {
        $cfg = Get-Content "config.local.json" -Raw -Encoding UTF8 | ConvertFrom-Json
        $script:Token = $cfg.telegram_bot_token
        if ($cfg.machine_id) { $script:Maquina = $cfg.machine_id }
    } catch { }
}

function Avisar($texto) {
    if (-not $ChatId -or -not $script:Token) { return }
    try {
        # El token va en la URL: se sanea aca para que un error de red que
        # arrastre la URL no lo publique en el grupo.
        $limpio = [string]$texto
        if ($script:Token) { $limpio = $limpio.Replace($script:Token, "***") }
        $cuerpo = @{ chat_id = $ChatId; text = "[$($script:Maquina)] " + (Recortar $limpio 3800) }
        Invoke-RestMethod -Method Post -TimeoutSec 20 `
            -Uri "https://api.telegram.org/bot$($script:Token)/sendMessage" `
            -Body $cuerpo | Out-Null
    } catch {
        Write-Host "  (no se pudo avisar por Telegram: $($_.Exception.Message))"
    }
}

# --- Nunca dejar la maquina caida ----------------------------------------
# El paso 2 DESHABILITA la tarea (si no, el vigilante revive el listener a
# mitad del git pull). Todo camino de salida la restaura.
$script:TareaDeshabilitada = $false

function Restaurar-Tarea {
    if (-not $script:TareaDeshabilitada) { return }
    Enable-ScheduledTask -TaskName $Tarea -EA SilentlyContinue | Out-Null
    Start-ScheduledTask  -TaskName $Tarea -EA SilentlyContinue
    $script:TareaDeshabilitada = $false
    Write-Host "  (tarea rehabilitada y listener relanzado)" -ForegroundColor Yellow
}

function Morir($t) {
    Write-Host "`nABORTADO: $t" -ForegroundColor Red
    Restaurar-Tarea
    Avisar "Actualizacion ABORTADA: $t"
    exit 1
}

trap {
    Write-Host "`nERROR INESPERADO: $_" -ForegroundColor Red
    Restaurar-Tarea
    Avisar "La actualizacion fallo por un error inesperado: $_"
    exit 1
}

# Los procesos python.exe de ESTE listener (no los del bot de MT5).
function Listeners-Vivos {
    @(Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
      Where-Object { $_.CommandLine -like "*$ScriptListener*" })
}

# --- Interprete: la misma busqueda que run_listener.bat -------------------
# No "python" a secas: en Windows 11 hay un alias en WindowsApps que NO es
# Python. Y cada candidato se prueba: puede quedar una carpeta Python3xx sin
# python.exe adentro (medido en la PC de desarrollo: Python313 vacia).
function Buscar-Python {
    $candidatos = @("$PSScriptRoot\python-portable\python.exe")
    $candidatos += (Get-ChildItem "$env:LOCALAPPDATA\Programs\Python\Python3*" -Directory -EA SilentlyContinue |
                    ForEach-Object { "$($_.FullName)\python.exe" })
    $candidatos += (Get-ChildItem "$env:ProgramFiles\Python3*" -Directory -EA SilentlyContinue |
                    ForEach-Object { "$($_.FullName)\python.exe" })
    foreach ($c in $candidatos) {
        if (Test-Path $c) {
            try { & $c -c "import sys" 2>$null; if ($?) { return $c } } catch { }
        }
    }
    return $null
}

# =========================================================================
Paso "1. Se puede actualizar ahora?"

$py = Buscar-Python
if (-not $py) { Morir "no se encontro ningun Python utilizable." }
Ok "Python: $py"

if (-not (Test-Path ".git")) {
    Morir "esta carpeta no es un repo git. Conectala primero (ver INSTALAR-MAQUINA-NUEVA.md)."
}

# --- Orden EN VUELO: la guarda que NO se puede aflojar --------------------
# Se miran TODOS los logs de listener (el .bat fija el nombre al arrancar y
# no rota: un listener de ayer sigue escribiendo en el de ayer). En cada uno
# se busca la ultima "INICIO OPERACION": si despues no hay "FIN OPERACION",
# hay una operacion viva, salvo que haya empezado hace mas de
# $MINUTOS_FLUJO_MUERTO (proceso matado, corte de luz: el log se corta).
# Ante cualquier duda se asume viva: perder un update es barato.
$hoy = Get-Date -Format "yyyy-MM-dd"
$MARCA_INICIO = "INICIO OPERACION"
$MARCA_FIN = "FIN OPERACION"
$MINUTOS_FLUJO_MUERTO = 10

if ($Force) {
    Aviso "-Force: NO se verifico si hay una orden en vuelo."
} else {
    $logs = @(Get-ChildItem "logs\listener-*.log" -EA SilentlyContinue)
    $vivo = ""
    foreach ($archivo in $logs) {
        $lineas = @(Get-Content $archivo.FullName -EA SilentlyContinue)
        $iIni = -1
        for ($i = $lineas.Count - 1; $i -ge 0; $i--) {
            if ($lineas[$i].Contains($MARCA_INICIO)) { $iIni = $i; break }
        }
        if ($iIni -lt 0) { continue }

        $cerrado = $false
        for ($i = $iIni + 1; $i -lt $lineas.Count; $i++) {
            if ($lineas[$i].Contains($MARCA_FIN)) { $cerrado = $true; break }
        }
        if ($cerrado) { continue }

        $sello = "?"
        $minutos = $null
        $m = [regex]::Match($lineas[$iIni], '^([0-9]{4}-[0-9]{2}-[0-9]{2} [0-9]{2}:[0-9]{2}:[0-9]{2})')
        if ($m.Success) {
            $sello = $m.Groups[1].Value
            try {
                $arranque = [datetime]::ParseExact($sello, "yyyy-MM-dd HH:mm:ss",
                            [System.Globalization.CultureInfo]::InvariantCulture)
                $minutos = ((Get-Date) - $arranque).TotalMinutes
            } catch { $minutos = $null }
        }
        if ($minutos -ne $null -and $minutos -ge $MINUTOS_FLUJO_MUERTO) {
            Aviso ("$($archivo.Name): la operacion de las $sello no cerro, pero empezo hace " +
                   "$([int]$minutos) min. Se da por muerta.")
            continue
        }
        $vivo = "$($archivo.Name), operacion de las $sello"
        break
    }
    if ($vivo -ne "") {
        Morir "hay una operacion EN VUELO ($vivo). Espera a que termine, o usa -Force si sabes que no."
    }
    Ok "No hay ninguna operacion en vuelo"
}

if ((Test-Path "programadas.json") -and -not $Force) {
    $pendientes = @(Get-Content "programadas.json" -Raw | ConvertFrom-Json)
    $limite = (Get-Date).AddMinutes($MinutosDeGuarda)
    foreach ($p in $pendientes) {
        if (-not $p.cuando) { continue }
        $cuando = [datetime]::Parse($p.cuando)
        if ($cuando -le $limite) {
            Morir "hay algo programado para $($cuando.ToString('HH:mm')) (dentro de $MinutosDeGuarda min). Espera a que pase, o usa -Force."
        }
    }
    if ($pendientes.Count -gt 0) { Aviso "$($pendientes.Count) operacion(es) programada(s), ninguna cerca. Se rearman al reiniciar." }
}
Ok "Nada programado dentro de $MinutosDeGuarda minutos"

# Cambios locales: en esta carpeta NADA versionado cambia legitimamente en una
# maquina. Si algo cambio, lo tiene que resolver una persona.
$sucios = @(git status --porcelain | Where-Object { $_ -ne "" })
if ($sucios.Count -gt 0) {
    $lista = (($sucios | Select-Object -First 15) | ForEach-Object { $_.TrimEnd() }) -join "`n"
    if ($sucios.Count -gt 15) { $lista = $lista + "`n... y $($sucios.Count - 15) mas" }
    Morir ("hay $($sucios.Count) cambio(s) local(es). No se toco nada, la maquina " +
           "sigue con el codigo que tenia:`n" + $lista +
           "`n(?? = archivo suelto, M = modificado, D = borrado)")
}

# =========================================================================
Paso "2. Parar el listener DE VERDAD"

# Deshabilitar ANTES de parar: si no, el vigilante lo revive a mitad del pull.
# Stop-ScheduledTask mata el .bat pero deja vivo el python.exe hijo: hay que
# matarlo aparte.
Disable-ScheduledTask -TaskName $Tarea -EA SilentlyContinue | Out-Null
$script:TareaDeshabilitada = $true
Stop-ScheduledTask    -TaskName $Tarea -EA SilentlyContinue
Listeners-Vivos | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }
# TODO Etapa 2: cerrar tambien los Chromium cuyo --user-data-dir este bajo
# perfiles\ de esta carpeta. Si quedan huerfanos, el perfil queda bloqueado
# y el proximo arranque no puede abrirlo.
Start-Sleep -Seconds 2
$vivo = Listeners-Vivos
if ($vivo.Count -gt 0) { Morir "el listener sigue vivo (PID $($vivo[0].ProcessId)). No se toca el codigo con el bot corriendo." }
Ok "Listener detenido y tarea deshabilitada"

# =========================================================================
Paso "3. Traer el codigo nuevo"

$antes = (git rev-parse HEAD).Trim()
$salidaPull = CorrerCapturando "git" @("pull", "--ff-only")
Write-Host $salidaPull
if ($LASTEXITCODE -ne 0) {
    $motivo = @($salidaPull -split "`n" | Where-Object { $_.Trim() } |
                Select-Object -Last 6) -join "`n"
    Morir ("git pull fallo. Se dejo el codigo como estaba" +
           $(if ($motivo) { ":`n" + $motivo } else { "." }))
}
$despues = (git rev-parse HEAD).Trim()
if ($antes -eq $despues) { Ok "Ya estaba al dia ($($antes.Substring(0,7)))" }
else { Ok "$($antes.Substring(0,7)) -> $($despues.Substring(0,7))"; git log --oneline "$antes..$despues" }

# =========================================================================
Paso "4. Verificar (y revertir si algo no cierra)"

# -t . : el paquete tests/ se importa como tal y su red de seguridad
# (tests/__init__.py) corre antes que cualquier test.
$salidaTests = CorrerCapturando $py @("-m", "unittest", "discover", "-s", "tests", "-t", ".", "-q")
Write-Host $salidaTests
if ($LASTEXITCODE -ne 0) {
    Aviso "LOS TESTS FALLARON. Revirtiendo a $($antes.Substring(0,7))."
    git reset --hard -q $antes
    $queFallo = @($salidaTests -split "`n" | Where-Object { $_ -match '^(FAIL|ERROR):' } |
                  ForEach-Object { $_.Trim() } | Select-Object -First 8)
    $detalle = ""
    if ($queFallo.Count -gt 0) { $detalle = ":`n" + ($queFallo -join "`n") }
    Morir ("los tests fallaron con el codigo nuevo. Se volvio al anterior, que es lo " +
           "que ya funcionaba" + $detalle)
}
Ok "Tests: todos pasan"

Correr $py @("check_setup.py")
if ($LASTEXITCODE -ne 0) {
    Aviso "check_setup.py reporto algo critico (ver arriba). Se arranca igual, pero revisalo."
}

# =========================================================================
Paso "5. Volver a levantarlo"

Restaurar-Tarea
Start-Sleep -Seconds 15
$nuevo = Listeners-Vivos
if ($nuevo.Count -eq 0) {
    Morir "el listener NO volvio a arrancar. Mira logs\listener-$hoy.log. El vigilante deberia levantarlo en <=5 min de todos modos."
}
if ($nuevo.Count -gt 1) {
    Aviso "hay MAS DE UN listener ($($nuevo.Count)). Se van a robar los mensajes entre si; mata los sobrantes."
    Avisar "Actualizado, PERO quedaron $($nuevo.Count) listeners vivos: se van a robar los mensajes entre si. Hay que matar los sobrantes a mano."
} else {
    Ok "Listener corriendo, PID $($nuevo[0].ProcessId) (uno solo)"
    if ($antes -eq $despues) {
        Avisar "Ya estaba al dia ($($antes.Substring(0,7))). Tests OK, listener arriba."
    } else {
        Avisar "Actualizado $($antes.Substring(0,7)) -> $($despues.Substring(0,7)). Tests OK, listener arriba."
    }
}

Write-Host "`nActualizacion completa." -ForegroundColor Green
