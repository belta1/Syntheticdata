# 2. Instalación

## Requisitos

### Hardware

| Recurso | Mínimo | Recomendado | Comentario |
|---|---|---|---|
| RAM | 16 GB | 64 GB o más | El ajuste y la evaluación cargan la data importada en memoria: unos **0,7 GiB por millón de filas** reales. Ver [dimensionamiento](03_importacion_datos.md#38-dimensionamiento) |
| Disco | 50 GB libres | 200 GB, SSD | Staging en parquet zstd: unos 8–15 bytes por fila. La data sintética ocupa algo parecido. Si el origen es CSV, se necesita espacio temporal adicional en la misma carpeta |
| GPU | ninguna | NVIDIA con 8 GB o más de VRAM | Opcional: acelera `generate` y la parte de privacidad de `evaluate`. Sin GPU todo funciona en CPU |
| CPU | 4 núcleos | 8 o más | polars y scikit-learn usan todos los núcleos |

### Software

| Componente | Versión | Para qué |
|---|---|---|
| Windows | 10/11 o Server 2019+ (64 bits) | probado en Windows 11; en Linux funciona igual, salvo los scripts `.ps1` |
| Python | **3.12**, 64 bits | requerido (`requires-python >= 3.12`) |
| Driver NVIDIA | compatible con la versión CUDA de torch (p. ej. ≥ 550 para CUDA 12.4) | sólo con GPU |
| ODBC Driver 17 u 18 for SQL Server | | sólo si se importa directo desde SQL (`importacion.tipo: sql`) |
| SQL Server Command Line Utilities (bcp) | | sólo si se exporta a CSV con `sql/02_exportar_bcp.ps1` |

### Paquetes Python (versiones probadas)

Las versiones exactas están en `requirements.lock.txt`. Las principales son polars 2.1.0, pyarrow 26.0.0, numpy 2.5.4, scikit-learn 1.9.1, pyyaml 6.0.3, pyodbc 5.3.0 y pytest 9.2.0.

torch se instala aparte porque la rueda depende de CUDA. Se probó con torch 2.15.0 en su versión CPU; la versión CUDA no pudo probarse en desarrollo.

## Opción A: instalación con internet

Desde la raíz del proyecto, en PowerShell:

```powershell
# si PowerShell bloquea scripts en esta sesión:
Set-ExecutionPolicy -Scope Process Bypass

.\scripts\instalar.ps1 -Cuda cu124      # con GPU (ajuste cu124 a su CUDA: cu118, cu121, cu124, cu126...)
.\scripts\instalar.ps1 -Cuda cpu        # sin GPU
```

El script:
1. verifica que el Python sea 3.12;
2. crea `.venv`;
3. instala torch desde `download.pytorch.org`, luego las dependencias fijadas, luego el paquete en modo editable con los extras `sql` y `dev`;
4. ejecuta `d10synth doctor` y `pytest`.

Si hay varias versiones de Python, indique cuál usar con `-Python "py -3.12"` o con la ruta completa.

Para hacerlo a mano:

```powershell
python -m venv .venv
.venv\Scripts\activate
pip install torch --index-url https://download.pytorch.org/whl/cu124
pip install -r requirements.lock.txt
pip install -e ".[sql,dev]"
```

Detrás de un proxy corporativo, configure `pip` antes de instalar:

```powershell
$env:HTTPS_PROXY = "http://proxy.empresa:8080"
# o, de forma permanente:
pip config set global.proxy http://proxy.empresa:8080
```

## Opción B: instalación sin internet (offline)

Use esta opción si el PC de destino no puede descargar paquetes.

**En un PC con internet** que tenga el mismo Python 3.12 de 64 bits en Windows:

```powershell
.\scripts\preparar_offline.ps1 -Cuda cu124      # crea .\wheelhouse (~3 GB con CUDA, ~300 MB con cpu)
```

Copie al destino la carpeta completa del proyecto, incluida `wheelhouse`. No copie `.venv`, `data`, `modelos` ni `reportes`.

**En el PC de destino:**

```powershell
.\scripts\instalar.ps1 -Offline
```

El script instala con `--no-index --find-links wheelhouse`, sin acceder a internet. Si falta alguna rueda, pip lo indica con `No matching distribution found for <paquete>`. En ese caso vuelva a correr `preparar_offline.ps1` en el PC con internet, revisando que tenga la misma versión de Python.

## Driver ODBC (sólo para importar desde SQL)

1. Revise los drivers instalados con `d10synth doctor` (línea `odbc`), o con `Get-OdbcDriver | Where-Object Name -like "*SQL Server*"`.
2. Si no aparece "ODBC Driver 17 for SQL Server" ni "ODBC Driver 18 for SQL Server", instálelo desde el sitio de Microsoft. TI suele tener el instalador `msodbcsql.msi`.
3. Ponga el nombre exacto en `fuente.driver` de `config/d10.yaml`. Con el driver 18, `d10synth` agrega `TrustServerCertificate=yes` automáticamente, porque ese driver cifra por defecto.

El driver antiguo "SQL Server", que viene con Windows, también funciona en la mayoría de los casos, pero no está probado.

## GPU

1. Revise el driver con `nvidia-smi`. La línea "CUDA Version" indica la versión **máxima** que soporta el driver.
2. Elija una rueda de torch con una versión de CUDA igual o menor. Por ejemplo, si el driver dice "CUDA Version: 12.6", sirven `cu124` o `cu126`.
3. Verifique con `d10synth doctor`: la línea `gpu` debe mostrar el nombre de la tarjeta y `prueba gpu` debe decir OK.

No hace falta instalar el CUDA Toolkit: la rueda de torch trae sus propias bibliotecas CUDA.

Para usar el CPU aunque haya GPU, ponga `generacion.dispositivo: cpu` o pase `--dispositivo cpu`.

## Verificación de la instalación

### 1. Diagnóstico

```powershell
.venv\Scripts\activate
d10synth --config config/d10.yaml doctor
```

Salida esperada (ejemplo):

```
OK     python           3.12.10 (Windows 11)
OK     d10synth         0.1.0
OK     polars           2.1.0
...
OK     gpu              NVIDIA RTX A4000, 16.0 GiB, CUDA 12.4
OK     prueba gpu       searchsorted 10M en 0.31s
OK     odbc             ['ODBC Driver 17 for SQL Server', 'SQL Server']; configurado: ODBC Driver 17 for SQL Server
OK     disco E:\        412 GiB libres
```

Cada línea tiene uno de tres estados:
- `ERROR`: impide trabajar y el comando termina con código 1. Por ejemplo, falta un paquete, el Python no es 3.12, o falta el driver ODBC cuando el origen es `sql`.
- `AVISO`: se puede continuar. Por ejemplo, no hay GPU o queda poco disco.
- `OK`.

### 2. Tests

```powershell
pytest -q
```

Deben pasar los 27 tests en unos 30 s. Los tests incluyen el pipeline completo sobre data mock y los tres importadores de archivos. Si alguno falla, **no continúe**: revise [Problemas frecuentes](08_problemas_frecuentes.md).

### 3. Prueba con mock en la GPU (opcional)

Esta prueba valida que el muestreo en GPU funciona, cosa que no se pudo probar en desarrollo:

```powershell
d10synth --config config/d10_mock.yaml mock --deudores 50000 --meses 12
d10synth --config config/d10_mock.yaml fit
d10synth --config config/d10_mock.yaml generate --dispositivo cuda --deudores 1000000
d10synth --config config/d10_mock.yaml validate data/sintetico_mock
d10synth --config config/d10_mock.yaml evaluate
```

Todas las reglas deben salir con 0 violaciones. Para comparar velocidades, repita `generate` con `--dispositivo cpu`. En el CPU de desarrollo, 1 millón de deudores × 12 meses tomó unos 40 s.

## Actualizar o mover la instalación

- **Código nuevo**: reemplace `src/`, `tests/`, `config/` (cuidando su `d10.yaml` local), `docs/` y `scripts/`, y luego corra `pytest`. Con la instalación editable no hace falta reinstalar el paquete, salvo que cambie `pyproject.toml`.
- **Mover de carpeta**: `.venv` no es portable. Bórrelo y vuelva a correr `instalar.ps1`.
