# 8. Problemas frecuentes

Para obtener más detalle de cualquier error, repita el comando con `-v` (log DEBUG). Los mensajes de error de `d10synth` están en español e indican la clave del YAML que hay que revisar.

## Instalación y entorno

| Síntoma | Causa | Solución |
|---|---|---|
| `instalar.ps1 no se puede cargar porque la ejecución de scripts está deshabilitada` | política de ejecución de PowerShell | `Set-ExecutionPolicy -Scope Process Bypass` y vuelva a ejecutar |
| `Se requiere Python 3.12` | otra versión en el PATH | `.\scripts\instalar.ps1 -Python "py -3.12"` o la ruta completa al `python.exe` 3.12 |
| `No matching distribution found for torch` | sin internet, proxy, o versión de Python distinta | configure el proxy ([doc. 2](02_instalacion.md)) o use la instalación offline con el mismo Python |
| La descarga de torch se corta (`connection reset`, `timeout`) | red lenta: la rueda CUDA pesa ~2,5 GB | `pip install --timeout 600 --retries 10 torch --index-url ...`, o prepare el wheelhouse en otro PC |
| `doctor`: `gpu ... torch sin soporte CUDA (wheel CPU)` | se instaló la rueda CPU | `pip uninstall torch` y luego `pip install torch --index-url https://download.pytorch.org/whl/cu124` |
| `doctor`: `torch compilado con CUDA X pero no ve una GPU` | driver NVIDIA ausente o antiguo | actualice el driver (`nvidia-smi` debe funcionar) o use una rueda con CUDA más antiguo (`cu118`) |
| Caracteres raros (`est�n`) en la consola | consola en cp1252 | `d10synth` ya fuerza UTF-8. Si persiste, ejecute `chcp 65001` antes |
| `d10synth` no se reconoce como comando | entorno no activado | `.venv\Scripts\activate`, o use `.venv\Scripts\d10synth.exe` |
| Falla `pytest` | instalación incompleta o versiones distintas | revise `doctor` y reinstale con `requirements.lock.txt`. Si persiste, guarde la salida completa de `pytest -q` |

## Importación desde SQL

| Síntoma | Causa | Solución |
|---|---|---|
| `Data source name not found and no default driver specified` | `fuente.driver` no coincide con un driver instalado | `d10synth doctor` lista los drivers. Copie el nombre exacto |
| `SSL Provider: The certificate chain was issued by an authority that is not trusted` | driver 18 con cifrado obligatorio | use el nombre exacto "ODBC Driver 18 for SQL Server" (d10synth agrega `TrustServerCertificate=yes`) o use el driver 17 |
| `Login failed for user` | permisos o modo de autenticación | con `trusted_connection: true`, el usuario de Windows necesita acceso a `cmfdat`. Con login SQL, use `trusted_connection: false` y las variables `D10_USER` y `D10_PASSWORD` |
| `KeyError: 'D10_USER'` | `trusted_connection: false` sin variables de entorno | defina `$env:D10_USER` y `$env:D10_PASSWORD` en la misma sesión |
| `Invalid object name 'dbo.D10C'` | tabla o base distintas | revise `fuente.base` y `fuente.tabla` |
| `Invalid column name ...` | nombres de columna distintos | ajuste la sección `columnas` (consulta 1 del prechequeo) |
| `0 cierres de mes a importar` | el periodo no se interpretó, el rango no calza, o la tabla no tiene fechas de último día del mes | revise `formato.periodo`, `desde` y `hasta`, y la consulta 4 del prechequeo. Si los cierres están fechados el último día **hábil** (por ejemplo 2024-03-29), el filtro de cierre de mes los descarta: avise a quien mantiene el código |
| La importación es muy lenta | no hay índice por `periodo`, la red es lenta o la base está cargada | consulta 3 del prechequeo. Importe fuera de horario, use `muestra_pct` o exporte con bcp |
| `Query timeout expired` | timeout del servidor | d10synth no pone un timeout de consulta. Pida al DBA revisar la configuración del servidor o importe por rangos (`--desde/--hasta`) |
| Se cortó a mitad | red, sesión o Ctrl+C | vuelva a ejecutar el mismo comando: es reanudable |

## Importación desde archivos

| Síntoma | Causa | Solución |
|---|---|---|
| `Falta importacion.entrada` | origen de archivos sin ruta | defina `importacion.entrada` o use `--entrada` |
| `No se encontraron archivos en ...` | ruta o patrón incorrecto | use barras `/` o comillas. Con una carpeta, la búsqueda es recursiva por extensión (`.csv/.txt/.gz`, `.parquet`, `.txt/.dat/.d10/.gz`) |
| `Faltan columnas en la data: [...]` | los nombres del CSV o parquet no calzan con `columnas`, o el CSV no tiene encabezado | ajuste `columnas`, o use `csv.encabezado: false` con `csv.nombres` |
| Todas las filas quedan en una columna | separador incorrecto | `csv.separador` |
| CSV: `0 cierres de mes por importar` | periodo en formato dd/mm/aaaa o con otro separador | exporte el periodo en ISO (`CONVERT(CHAR(10), periodo, 23)`) o como `aaaammdd` |
| CSV: montos nulos, o filas inválidas en `verify` | decimal coma o separador de miles | `csv.decimal: ","` |
| CSV: `invalid utf-8 sequence` | archivo en latin-1/ANSI | `csv.encoding: utf8-lossy` |
| D10: `cabecera D10 inválida: b'...'` (archivo omitido) | no es un archivo D10, o tiene otro layout | revise los primeros 15 bytes: 4 dígitos + `D10` + aaaammdd |
| D10: `registros_ilegibles` alto | registros de cola o totales, otro layout, o campos con letras | abra el archivo y compare con el layout ([doc. 3 §D](03_importacion_datos.md#d-desde-los-archivos-d10-originales-tipo-d10)) |
| D10: `registros_largo_distinto_78` > 0 | nombres en UTF-8 o espacios recortados en el nombre | no afecta la lectura, porque se usan los primeros 10 y los últimos 18 bytes. Sólo es informativo |
| D10: `rut_dv_invalido` > 0 | RUT mal informados por la IF | se importan igual; anótelo para el informe de calidad |

## Staging

| Síntoma | Causa | Solución |
|---|---|---|
| `... tiene parquet sin manifiesto (¿data mock o de otra versión?)` | `rutas.real` tiene parquet de otra procedencia | use otra carpeta o `--reemplazar` (borra esos parquet) |
| `... ya tiene data importada desde 'sql'. No se mezclan orígenes` | se intentó importar otro origen en la misma carpeta | cambie `rutas.real` o use `--reemplazar` |
| `verify`: `el SHA-256 ... no coincide` | un archivo se modificó después de importarlo | re-importe ese mes: `import --desde X --hasta X --reemplazar` |
| `verify`: `archivos parquet fuera del manifiesto` | se copiaron archivos a mano | sáquelos de la carpeta |
| `verify`: `meses faltantes` | huecos en la fuente o en el rango | importe los meses faltantes o acepte el hueco (doc. 3 §3.1) |
| Quedan carpetas `_tmp_*` o archivos `.tmp` | importación interrumpida | bórrelos si no hay una importación corriendo |

## Ajuste, generación y evaluación

| Síntoma | Causa | Solución |
|---|---|---|
| `MemoryError`, el proceso se cierra o Windows se pone muy lento en `fit`, `profile` o `evaluate` | la data no cabe en RAM | menos filas: `import --muestra-pct P --reemplazar` o menos meses. Ver [dimensionamiento](03_importacion_datos.md#38-dimensionamiento) |
| `No hay archivos parquet en data/real` | no se importó, o `rutas.real` apunta a otra carpeta | ejecute `import`, revise `rutas.real` y la carpeta de trabajo |
| `CUDA out of memory` en `generate` | chunk demasiado grande para la GPU | `--chunk-deudores` a la mitad, o `--dispositivo cpu` |
| `CUDA out of memory` en `evaluate` | la muestra de privacidad es grande | baje `evaluacion.muestra_privacidad`, o `--dispositivo cpu` |
| El sintético tiene muchos menos o más deudores al final que al inicio | tasas de entrada y salida reales (estacionarias) | es esperable. Para un tamaño final dado, ajuste `--deudores` |
| `evaluate`: `utilidad: se requieren al menos dos periodos consecutivos` | sólo hay un mes, o los meses no son consecutivos | importe al menos dos cierres consecutivos |
| `evaluate` tarda mucho | data grande, sin GPU | baje `evaluacion.muestra_privacidad`. La parte de utilidad toma hasta 300 mil celdas por modelo |
| `filas_invalidas` alto en `fit` | códigos fuera del catálogo del manual | revise la sección *Reglas* del perfil. Si hay códigos nuevos de la CMF (un tipo de crédito nuevo), hay que agregarlos en `schema.py` |
| `Claves desconocidas en sección ...` al cargar el YAML | error de tipeo o clave antigua (por ejemplo, `extraccion`, que ahora es `importacion`) | corrija según el [doc. 5](05_configuracion.md) |

## Si nada de esto aplica

Reúna lo siguiente **sin incluir data real**:
- la salida de `d10synth doctor`;
- el comando exacto y el mensaje de error completo (traceback);
- la salida de `verify`;
- si corresponde, `reportes/perfil_d10.md` revisado.

Con eso se puede diagnosticar sin acceso a la data.
