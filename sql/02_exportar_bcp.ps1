<#
  Exporta los cierres de mes de la tabla D10 a CSV con bcp, un archivo por periodo.

  Vía alternativa cuando pyodbc no puede conectarse desde Python (driver, permisos o política),
  pero sí se puede usar bcp (viene con las "SQL Server Command Line Utilities").
  Después se importa con:   d10synth import --tipo csv --entrada <Salida>

  Uso:
    .\sql\02_exportar_bcp.ps1 -Salida E:\export_d10 -Desde 2023-01-31 -Hasta 2024-12-31
    .\sql\02_exportar_bcp.ps1 -Salida E:\export_d10 -Periodos 2024-01-31,2024-02-29

  Supuestos (ajustar si no calzan con la tabla real):
    - periodo es DATE o DATETIME. Si es INT aaaammdd, use -PeriodoEntero.
    - autenticación Windows (-T). Para usuario SQL, reemplace -T por -U <usuario> (bcp pedirá la clave).
#>
param(
    [Parameter(Mandatory = $true)] [string] $Salida,
    [string] $Servidor = "srvdatscience02",
    [string] $Tabla = "cmfdat.dbo.D10C",
    [string] $Desde,
    [string] $Hasta,
    [string[]] $Periodos,
    [switch] $PeriodoEntero
)

$ErrorActionPreference = "Stop"
New-Item -ItemType Directory -Force $Salida | Out-Null

if (-not $Periodos) {
    if (-not $Desde -or -not $Hasta) { throw "Indique -Periodos o bien -Desde y -Hasta" }
    $Periodos = @()
    $mes = [datetime]::ParseExact($Desde, "yyyy-MM-dd", $null)
    $fin = [datetime]::ParseExact($Hasta, "yyyy-MM-dd", $null)
    $mes = New-Object DateTime $mes.Year, $mes.Month, 1
    while ($mes -le $fin) {
        $Periodos += $mes.AddMonths(1).AddDays(-1).ToString("yyyy-MM-dd")
        $mes = $mes.AddMonths(1)
    }
}

$cols = "rutis_sbif, periodo, rut_deudor, tipo_deudor, tipo_credito, tipo_mora, monto"
foreach ($p in $Periodos) {
    $archivo = Join-Path $Salida ("D10C_{0}.csv" -f $p.Replace("-", ""))
    if (Test-Path $archivo) { Write-Host "$archivo ya existe, se salta"; continue }
    $valor = if ($PeriodoEntero) { $p.Replace("-", "") } else { "'$p'" }
    $sel = if ($PeriodoEntero) { $cols } else { $cols.Replace("periodo,", "CONVERT(CHAR(10), periodo, 23) AS periodo,") }
    $q = "SELECT $sel FROM $Tabla WITH (NOLOCK) WHERE periodo = $valor"
    $tmp = "$archivo.tmp"
    Write-Host "exportando $p ..."
    # -c: texto, -t;: separador, -C 65001: UTF-8, -a 65535: paquetes grandes
    & bcp "$q" queryout $tmp -S $Servidor -T -c "-t;" -C 65001 -a 65535
    if ($LASTEXITCODE -ne 0) { Remove-Item -ErrorAction SilentlyContinue $tmp; throw "bcp falló para $p" }
    # bcp no escribe encabezado: se antepone para que 'import --tipo csv' use los nombres de columna
    $encabezado = "rutis_sbif;periodo;rut_deudor;tipo_deudor;tipo_credito;tipo_mora;monto"
    $sw = New-Object System.IO.StreamWriter($archivo, $false, (New-Object System.Text.UTF8Encoding($false)))
    $sw.WriteLine($encabezado)
    $sr = New-Object System.IO.StreamReader($tmp)
    while (($linea = $sr.ReadLine()) -ne $null) { $sw.WriteLine($linea) }
    $sr.Close(); $sw.Close()
    Remove-Item $tmp
}
Write-Host "listo: $($Periodos.Count) periodo(s) en $Salida"
