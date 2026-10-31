<#
  Arma un paquete de instalación OFFLINE (wheelhouse) en un PC con internet, para llevarlo
  al PC de destino si éste no puede descargar paquetes.

  Debe ejecutarse con el mismo Python 3.12 (64 bits, Windows) que tendrá el destino.

  Uso:
    .\scripts\preparar_offline.ps1 -Cuda cu124
    .\scripts\preparar_offline.ps1 -Cuda cpu        # sin GPU

  Resultado: carpeta .\wheelhouse (~3 GB con CUDA). Copie el proyecto completo con esa carpeta.
#>
param(
    [string] $Cuda = "cu124",
    [string] $Destino = "wheelhouse"
)
$ErrorActionPreference = "Stop"
New-Item -ItemType Directory -Force $Destino | Out-Null

python -c "import sys; assert sys.version_info[:2] == (3, 12), sys.version"
if ($LASTEXITCODE -ne 0) { throw "Se requiere Python 3.12" }

Write-Host "1/3 torch ($Cuda)"
python -m pip download torch --index-url "https://download.pytorch.org/whl/$Cuda" -d $Destino
if ($LASTEXITCODE -ne 0) { throw "falló la descarga de torch" }

Write-Host "2/3 dependencias fijadas (requirements.lock.txt)"
python -m pip download -r requirements.lock.txt -d $Destino
if ($LASTEXITCODE -ne 0) { throw "falló la descarga de dependencias" }

Write-Host "3/3 herramientas de build (para pip install -e)"
python -m pip download "setuptools>=68" wheel pip -d $Destino
if ($LASTEXITCODE -ne 0) { throw "falló la descarga de setuptools/wheel" }

Get-ChildItem $Destino | Measure-Object -Property Length -Sum | ForEach-Object {
    Write-Host ("listo: {0} archivos, {1:N1} GB en {2}" -f $_.Count, ($_.Sum / 1GB), $Destino)
}
