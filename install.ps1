# Instalador de BeatShell para Windows (PowerShell).
# Uso:  powershell -ExecutionPolicy Bypass -File install.ps1
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

$exe = $null
$pre = @()
foreach ($c in @("py -3", "python")) {
    $parts = $c -split " "
    $cExe = $parts[0]
    $cPre = @($parts | Select-Object -Skip 1)
    if (-not (Get-Command $cExe -ErrorAction SilentlyContinue)) { continue }
    & $cExe @cPre -c "import sys; sys.exit(sys.version_info < (3, 10))" 2>$null
    if ($LASTEXITCODE -eq 0) { $exe = $cExe; $pre = $cPre; break }
}
if (-not $exe) {
    Write-Host "X Necesitas Python 3.10 o superior: winget install Python.Python.3.12" -ForegroundColor Red
    exit 1
}

& $exe @pre -m venv .venv
& .\.venv\Scripts\python.exe -m pip install --quiet --upgrade pip
& .\.venv\Scripts\python.exe -m pip install --quiet .
Write-Host "OK Dependencias instaladas" -ForegroundColor Green

# Lanzador en una carpeta que ya está en el PATH de Windows
$bin = Join-Path $env:LOCALAPPDATA "Microsoft\WindowsApps"
$launcher = Join-Path $bin "beatshell.cmd"
"@echo off`r`n`"$PSScriptRoot\.venv\Scripts\beatshell.exe`" %*" | Set-Content -Encoding ASCII $launcher
Write-Host "OK Comando creado: beatshell" -ForegroundColor Green

if (-not (Get-Command deno -ErrorAction SilentlyContinue) -and -not (Get-Command node -ErrorAction SilentlyContinue)) {
    Write-Host "! YouTube necesita un runtime de JavaScript. Instala Deno:  winget install DenoLand.Deno" -ForegroundColor Yellow
}
Write-Host ""
Write-Host "Listo. Abre Windows Terminal y ejecuta:  beatshell"
