<#
  Instala d10synth en el PC de destino (crea .venv en la raíz del proyecto).

  Uso (desde la raíz del proyecto):
    .\scripts\instalar.ps1 -Cuda cu124              # online, con GPU
    .\scripts\instalar.ps1 -Cuda cpu                # online, sin GPU
    .\scripts\instalar.ps1 -Offline                 # usa .\wheelhouse (ver preparar_offline.ps1)

  Si PowerShell bloquea el script:  Set-ExecutionPolicy -Scope Process Bypass
#>
param(
    [string] $Cuda = "cu124",
    [switch] $Offline,
    [string] $Wheelhouse = "wheelhouse",
    [string] $Python = "python"
)
$ErrorActionPreference = "Stop"

& $Python -c "import sys; assert sys.version_info[:2] == (3, 12), sys.version"
if ($LASTEXITCODE -ne 0) { throw "Se requiere Python 3.12 (use -Python 'py -3.12' o la ruta completa)" }

if (-not (Test-Path .venv)) { & $Python -m venv .venv }
$py = ".\.venv\Scripts\python.exe"

if ($Offline) {
    $o = @("--no-index", "--find-links", $Wheelhouse)
    & $py -m pip install @o --upgrade pip setuptools wheel
    & $py -m pip install @o torch
    & $py -m pip install @o -r requirements.lock.txt
    & $py -m pip install @o --no-build-isolation -e ".[sql,dev]"
} else {
    & $py -m pip install --upgrade pip
    & $py -m pip install torch --index-url "https://download.pytorch.org/whl/$Cuda"
    & $py -m pip install -r requirements.lock.txt
    & $py -m pip install -e ".[sql,dev]"
}
if ($LASTEXITCODE -ne 0) { throw "falló la instalación" }

Write-Host "`nVerificación del entorno:"
& .\.venv\Scripts\d10synth.exe --config config/d10.yaml doctor
Write-Host "`nTests (pipeline completo sobre data mock, ~30 s):"
& $py -m pytest -q
