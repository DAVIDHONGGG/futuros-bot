<#
.SYNOPSIS
    Cierra los navegadores que quedaron abiertos con un perfil de ESTA
    carpeta (perfiles\).

.DESCRIPTION
    Si el listener muere o se mata (update, vigilante, corte), el Chromium
    que abrio queda huerfano y sigue teniendo el perfil tomado: el proximo
    listener no puede abrirlo. Lo llaman run_listener.bat antes de arrancar
    y actualizar.ps1 al parar.

    Solo toca procesos cuya linea de comando nombra la carpeta perfiles\ de
    este bot: ni el Chrome/Edge de uso personal ni nada del bot de MT5.

    OJO: tambien cierra una ventana abierta con abrir_perfil.py.
#>

param([switch]$Silencioso)

$ErrorActionPreference = "Continue"
$perfiles = Join-Path $PSScriptRoot "perfiles"

$procesos = @(Get-CimInstance Win32_Process -EA SilentlyContinue `
    -Filter "Name='chrome.exe' OR Name='msedge.exe' OR Name='chromium.exe' OR Name='headless_shell.exe'" |
    Where-Object { $_.CommandLine -and
                   $_.CommandLine.IndexOf($perfiles, [StringComparison]::OrdinalIgnoreCase) -ge 0 })

foreach ($p in $procesos) {
    Stop-Process -Id $p.ProcessId -Force -EA SilentlyContinue
}
if (-not $Silencioso) {
    Write-Host "Navegadores del bot cerrados: $($procesos.Count)"
}
exit 0
