# 3. Importación de los datos reales

Este documento explica **qué datos necesita la síntesis**, **de dónde se pueden importar** y **cómo verificar** que quedaron bien. La importación es el único paso que toca la fuente. Todo lo demás (perfil, ajuste, generación y evaluación) trabaja sobre la copia local que deja `d10synth import`.

## 3.1 Qué datos necesita la síntesis

### Contenido

Una fila por (IF, fecha de corte, deudor, tipo de deudor, tipo de crédito, morosidad), con su monto, tal como lo define el manual del D10 ([d10-manual.txt](../d10-manual.txt)):

| Campo canónico | En `D10C` | En el archivo D10 | Valores válidos |
|---|---|---|---|
| `if_id` | `rutis_sbif` | código IF de la cabecera, 9(04) | cualquier entero; sólo se usa como identificador |
| `periodo` | `periodo` | fecha de la cabecera, aaaammdd | **sólo cierres de mes** (último día calendario) |
| `rut` | `rut_deudor` | RUT 9(09) + DV X(01) | entero sin DV; ≥ `umbral_rut_empresa` (50.000.000) = empresa |
| `tipo_deudor` | `tipo_deudor` | 9(01) | 1 directo, 2 indirecto |
| `tipo_credito` | `tipo_credito` | 9(02) | 1–12, 21–28 |
| `tipo_mora` | `tipo_mora` | 9(01) | 0–9 (contingentes 6, 7 y 11 siempre 0) |
| `monto` | `monto` | 9(14) | pesos, entero > 0 |

El **nombre o razón social** del deudor no se usa y nunca se importa. En los archivos D10 originales esos bytes ni siquiera se decodifican.

### Cobertura temporal

| Requisito | Mínimo | Recomendado | Por qué |
|---|---|---|---|
| Cierres de mes | 2 consecutivos | 12–24 consecutivos | La dinámica (Markov de mora, entradas y salidas, crecimiento de montos) se ajusta con pares (t, t+1) de meses **consecutivos**. Con más meses, las tablas tienen más datos y caen menos al fallback |
| Meses faltantes | se toleran | ninguno | Un mes faltante elimina dos pares (t−1, t) y (t, t+1), pero no rompe nada |
| IF por periodo | todas | todas | Si una IF falta un mes, sus deudores "salen" y "vuelven", lo que infla las tasas de salida y entrada |
| Deudores | todos o una muestra estable | muestra del 5–20% en la primera corrida | Ver [muestra](#36-muestra-de-deudores) y [dimensionamiento](#38-dimensionamiento) |

### Las filas inválidas no se corrigen al importar

La importación copia las filas **tal cual**, también las que no cumplen el manual: códigos fuera de catálogo, monto ≤ 0, contingentes con mora o empresas con productos de persona. Después:
- `profile` y `verify` las **cuentan**;
- `fit` **descarta** las inválidas y fuerza mora 0 en los contingentes;
- el resumen queda en `modelo.json` → `resumen_datos`.

Así el staging es una copia fiel y auditable de la fuente.

## 3.2 Elegir el origen

`d10synth import` acepta cuatro orígenes (`importacion.tipo`). Todos producen **exactamente el mismo resultado** en `data/real`; hay un test que lo verifica sobre el mock.

| Origen | Cuándo usarlo | Requisitos | Ventajas | Desventajas |
|---|---|---|---|---|
| **`sql`** (preferido) | Python puede conectarse a `srvdatscience02` | driver ODBC, permiso `SELECT` sobre la tabla | Directo, reanudable, sin archivos intermedios | Depende de la red y de la carga de la base |
| **`csv`** | Python no puede conectarse (política, driver o firewall), pero alguien puede exportar con bcp, SSMS o SSIS | export en un disco accesible | Separa quién exporta de quién procesa | Archivos grandes en texto plano con RUT. Hay que borrarlos al terminar |
| **`parquet`** | La data ya existe en parquet (plataforma de datos, Spark, export previo) | archivos con las columnas de la tabla | El más rápido | |
| **`d10`** | Se quiere trabajar desde los archivos que envían las IF, o validar la tabla contra ellos | archivos D10 de ancho fijo | No depende de la tabla. Valida el DV | El código IF es el de 4 dígitos de la CMF, no `rutis_sbif`. Un archivo por IF y fecha |

No se pueden mezclar orígenes en una misma carpeta de staging. Para comparar, por ejemplo, la tabla SQL contra los archivos D10, use dos carpetas (`rutas.real`) distintas.

## 3.3 Prechequeo de la fuente (antes de importar)

Antes de la primera importación, una persona autorizada ejecuta [sql/01_prechequeo_d10.sql](../sql/01_prechequeo_d10.sql) en SSMS o Azure Data Studio. **Todas sus consultas devuelven agregados**: tipos de columna, conteos y tamaños, nunca un RUT. Con el resultado se completa `config/d10.yaml`:

| Consulta | Qué mirar | Qué ajustar |
|---|---|---|
| 1. Tipos de columna | Tipo de `periodo` y de `rut_deudor` | `formato.periodo` y `formato.rut` (tabla siguiente) |
| 1. Nombres | ¿Las columnas se llaman como en `columnas:`? | sección `columnas` |
| 2. `sp_spaceused` | Filas y tamaño de la tabla | `importacion.muestra_pct`, `desde`/`hasta` ([dimensionamiento](#38-dimensionamiento)) |
| 3. Índices | ¿Hay un índice que **empiece** por `periodo`? | Si no hay, cada periodo es un scan completo de la tabla: importe fuera de horario o pida el índice al DBA |
| 4. Periodos | ¿Existen fechas de cierre de mes (último día calendario)? ¿Filas y deudores estables mes a mes? | `importacion.desde` / `hasta` |
| 5. Códigos | Valores fuera del catálogo | Nada: se descartan en `fit`. Anótelos para el informe |
| 6. Nulos y reglas | Montos ≤ 0, contingentes con mora, decimales | Nada: se descartan o corrigen en `fit` |
| 7. Forma del RUT | Largo, guion, "K" final | `formato.rut` |
| 8. Personas y empresas | ¿El umbral de 50M separa bien? | `formato.umbral_rut_empresa` |

### Cómo fijar `formato.periodo` y `formato.rut`

| Tipo en la tabla | Ejemplo | `formato.periodo` |
|---|---|---|
| `date`, `datetime`, `datetime2` | 2024-01-31 | `auto` (o `date`) |
| `int` / `char(8)` aaaammdd | 20240131 | `auto` (o `yyyymmdd`) |
| `int` / `char(6)` aaaamm | 202401 | `auto` (o `yyyymm`): cada valor se toma como el cierre de ese mes |
| `varchar` "2024-01-31 00:00:00.000" | | `auto`: se toman los primeros 8 dígitos |
| dd/mm/aaaa | 31/01/2024 | **no soportado**: convierta en el export (`CONVERT(CHAR(10), periodo, 23)`) |

| Forma del RUT en la tabla | Ejemplo | `formato.rut` |
|---|---|---|
| entero sin DV | 12345678 | `auto` (o `sin_dv`) |
| texto con guion | "12.345.678-5" o "12345678-5" | `auto`: se corta en el guion |
| entero o texto **con** DV pegado, sin guion | 123456785 | **`con_dv`** (obligatorio: `auto` lo tomaría como un RUT de 9 dígitos) |
| texto con DV "K" pegado | "1234567K" | `con_dv` |

Para comprobar si el último dígito es DV sin mirar ningún RUT, use la consulta 7: largo de 8–9 caracteres sin guion sugiere que no trae DV; 9–10 sugiere que sí. Si hay dudas, importe un mes con `--hasta` y revise `verify`: con el formato equivocado, el número de deudores con RUT ≥ 50M se dispara (sale en `profile`, en *deudores por segmento*).

## 3.4 Importar desde cada origen

En todos los casos:

```powershell
.venv\Scripts\activate
d10synth --config config/d10.yaml import        # usa importacion.tipo y importacion.entrada del YAML
# o, sobrescribiendo el YAML:
d10synth --config config/d10.yaml import --tipo csv --entrada "E:\export_d10\*.csv" --desde 2023-01-31 --hasta 2024-12-31 --muestra-pct 10
```

El resultado queda en `rutas.real` (por defecto `data/real`). Al final, el comando indica cuántos periodos importó y sugiere correr `verify`.

### A. Desde SQL Server (`tipo: sql`)

**Permisos.** Basta un login con `SELECT` sobre la tabla (por ejemplo, el rol `db_datareader` en `cmfdat`). `d10synth` abre la conexión en modo sólo lectura (`readonly=True`) y no crea tablas temporales ni objetos.

**Configuración:**

```yaml
fuente:
  driver: "ODBC Driver 17 for SQL Server"   # nombre exacto, ver 'd10synth doctor'
  servidor: srvdatscience02
  base: cmfdat
  tabla: dbo.D10C
  trusted_connection: true                  # autenticación Windows del usuario que ejecuta
importacion:
  tipo: sql
  desde: "2023-01-31"
  hasta: "2024-12-31"
  muestra_pct: 10
  batch_filas: 500000
```

Para un **login SQL** en vez de la autenticación Windows, ponga `trusted_connection: false` y defina las credenciales **sólo en la sesión**, nunca en el YAML:

```powershell
$env:D10_USER = "usuario_lectura"
$env:D10_PASSWORD = Read-Host "Clave" -AsSecureString | ForEach-Object { [Runtime.InteropServices.Marshal]::PtrToStringAuto([Runtime.InteropServices.Marshal]::SecureStringToBSTR($_)) }
d10synth --config config/d10.yaml import
Remove-Item Env:D10_PASSWORD
```

**Consultas que ejecuta**, para revisión del DBA:

```sql
-- 1) una vez: qué periodos hay
SELECT DISTINCT periodo FROM dbo.D10C;
-- 2) por cada cierre de mes dentro de [desde, hasta]:
SELECT rutis_sbif, periodo, rut_deudor, tipo_deudor, tipo_credito, tipo_mora, monto
FROM dbo.D10C WHERE periodo = ?
  -- con muestra_pct < 100 se agrega:
  AND ABS(CHECKSUM(rut_deudor)) % 10000 < <muestra_pct*100>
```

El filtro de cierre de mes se aplica en Python sobre la lista de periodos distintos, así que funciona con cualquier tipo de columna. Las filas se leen en lotes de `batch_filas` y se escriben directo a parquet, sin cargar el mes completo en memoria.

**Tiempos.** Dependen de la red y de la base. Como estimación (no medida en este entorno), pyodbc lee del orden de 100–300 mil filas por segundo en una red local. El log informa los segundos de cada periodo, y el manifiesto también. Con muestra, `CHECKSUM` obliga a recorrer todo el periodo aunque haya índice.

**Reanudar.** Si se corta (red, timeout, Ctrl+C), vuelva a ejecutar el mismo comando: los periodos ya escritos se saltan y el periodo a medio escribir se rehace, porque la escritura es atómica (`.tmp` y luego rename).

### B. Desde CSV (`tipo: csv`)

Use esta vía cuando Python no puede conectarse a la base. El export lo hace alguien con acceso: con el script incluido, con SSMS o con SSIS.

**Export con el script incluido** (requiere `bcp`, que viene con las *SQL Server Command Line Utilities*):

```powershell
.\sql\02_exportar_bcp.ps1 -Salida E:\export_d10 -Desde 2023-01-31 -Hasta 2024-12-31
# si periodo es INT aaaammdd:
.\sql\02_exportar_bcp.ps1 -Salida E:\export_d10 -Desde 2023-01-31 -Hasta 2024-12-31 -PeriodoEntero
```

El script hace lo siguiente:
- deja un archivo `D10C_AAAAMMDD.csv` por cierre de mes, en UTF-8, con separador `;` y encabezado;
- convierte el periodo a ISO (`aaaa-mm-dd`);
- se salta los archivos que ya existen.

**Export con SSMS o SSIS.** Se puede usar el asistente *Tasks → Export Data → Flat File Destination* (no use "Save results as" para volúmenes grandes). Requisitos del archivo:

| Aspecto | Requisito | Configuración |
|---|---|---|
| Separador | un carácter que no aparezca en los datos (`;`, `\|`, tab) | `importacion.csv.separador` |
| Encabezado | recomendado, con los nombres de la tabla | `csv.encabezado: true`. Sin encabezado: `csv.encabezado: false` y `csv.nombres: [rutis_sbif, periodo, ...]` en el orden del archivo |
| Periodo | ISO `aaaa-mm-dd`, `aaaammdd` o `aaaamm` (también ISO con hora) | `formato.periodo: auto` |
| Monto | sin separador de miles, o con decimal coma | `csv.decimal: "."` o `","` (con coma se eliminan los puntos de miles) |
| Codificación | UTF-8. Con latin-1/ANSI use `utf8-lossy`: sólo afectaría texto, y no se importan columnas de texto libre | `csv.encoding` |
| Comillas | permitidas | |
| Archivos | uno o varios. Se aceptan carpeta, patrón glob o `.csv.gz` | `importacion.entrada` |

**Configuración:**

```yaml
importacion:
  tipo: csv
  entrada: "E:/export_d10"          # carpeta (se buscan *.csv, *.txt, *.csv.gz) o patrón "E:/export_d10/D10C_2024*.csv"
  csv:
    separador: ";"
    encabezado: true
    decimal: "."
```

**Cómo procesa.** Una pasada sobre los CSV deja los cierres de mes pedidos en un parquet temporal (`data/real/_tmp_*`, que se borra solo al terminar). Desde ahí escribe un archivo por periodo. Se necesita espacio libre temporal equivalente a la data importada en parquet.

**Al terminar**, borre los CSV de forma segura: contienen RUT en texto plano (ver [doc. 7](07_seguridad_privacidad.md#borrado)).

### C. Desde parquet (`tipo: parquet`)

```yaml
importacion:
  tipo: parquet
  entrada: "E:/lake/d10c/*.parquet"   # o carpeta (recursiva)
```

Las columnas se buscan con los nombres de la sección `columnas` y se interpretan según `formato`, igual que con SQL. Se puede usar, por ejemplo, para un export hecho con Spark o Databricks.

> **No apunte `entrada` a la carpeta de staging** (`data/real`) ni a una carpeta de mock. El staging no se importa: ya está importado.

### D. Desde los archivos D10 originales (`tipo: d10`)

Son los archivos que envía cada IF según el manual: texto de **ancho fijo**, 78 bytes por registro, uno por IF y fecha de corte (viernes o cierre de mes).

**Layout que lee `d10synth`** (posiciones en bytes, base 1):

| Registro | Bytes | Campo | Formato | Uso |
|---|---|---|---|---|
| Cabecera (1ª línea) | 1–4 | Código de la IF | 9(04) | → `if_id` |
| | 5–7 | Identificación del archivo | X(03) = "D10" | se valida |
| | 8–15 | Fecha | aaaammdd | → `periodo`; si no es cierre de mes, el archivo se omite |
| | 16–78 | Filler | | se ignora |
| Detalle | 1–9 | RUT del deudor | 9(09) | → `rut` |
| | 10 | DV | X(01) | se valida (módulo 11) y se cuenta |
| | 11–60 | Nombre o razón social | X(50) | **nunca se lee** |
| | 61 | Tipo de deudor | 9(01) | → `tipo_deudor` |
| | 62–63 | Tipo de crédito | 9(02) | → `tipo_credito` |
| | 64 | Morosidad | 9(01) | → `tipo_mora` |
| | 65–78 | Monto | 9(14) | → `monto` |

**Detalles de lectura:**
- Los campos numéricos se leen como bytes ASCII. Los espacios a la izquierda cuentan como ceros.
- RUT y DV se toman de los **primeros 10 bytes** y el resto de los **últimos 18** de cada línea. Por eso un nombre con caracteres UTF-8 multibyte (una línea de más de 78 bytes) no corre las columnas. Esos casos se cuentan en `registros_largo_distinto_78`.
- Se aceptan finales de línea CRLF o LF, un BOM UTF-8 al inicio, el carácter EOF de DOS (`0x1A`) al final y líneas vacías.
- Un registro con caracteres no numéricos en un campo numérico, o de menos de 28 bytes, se descarta y se cuenta en `registros_ilegibles`.
- Un DV que no calza **no descarta** el registro: se importa y se cuenta en `rut_dv_invalido`.
- Si una IF envió **más de un archivo para la misma fecha** (reenvíos o rectificaciones), vale el de modificación más reciente. Los demás se cuentan en `archivos_reemplazados`.
- Se aceptan archivos comprimidos `.gz`.
- Velocidad: unos **1,2 millones de registros por segundo** (un archivo de 240 MB en unos 2,5 s).

**Configuración:**

```yaml
importacion:
  tipo: d10
  entrada: "E:/archivos_d10"          # carpeta (recursiva: *.txt, *.dat, *.d10, *.gz) o patrón glob
```

Los nombres de archivo no importan: la IF y la fecha se leen de la cabecera. Puede apuntar a una carpeta con todo el histórico, incluidos los semanales, porque sólo se procesan los cierres de mes.

> **El código de IF es distinto.** Los archivos D10 traen el código de 4 dígitos de la CMF, mientras que la tabla `D10C` trae `rutis_sbif`. La data sintética usará los códigos del origen importado. No mezcle un modelo ajustado con un origen y comparaciones contra el otro.

## 3.5 El staging: qué queda en `data/real`

Todas las vías escriben el mismo formato canónico:

```
data/real/
  periodo=20230131.parquet
  periodo=20230228.parquet
  ...
  _manifiesto.json
```

**Esquema de cada parquet** (compresión zstd):

| Columna | Tipo | Nota |
|---|---|---|
| `if_id` | Int64 | |
| `periodo` | Date | siempre un cierre de mes |
| `rut` | Int64 | **sin DV**, ya normalizado |
| `tipo_deudor`, `tipo_credito`, `tipo_mora` | Int16 | |
| `monto` | Float64 | pesos |

**Manifiesto** (`_manifiesto.json`), ejemplo para un origen `d10`:

```json
{
  "esquema": "canonico-v1",
  "origen": {"tipo": "d10", "entrada": "E:/archivos_d10", "n_archivos": 1240, "muestra_pct": 100},
  "periodos": {
    "2024-01-31": {
      "filas": 31544120, "deudores": 11893001, "ifs": 52, "monto_total": 2.9e14,
      "filas_invalidas": 1203, "filas_otro_periodo": 0,
      "archivo": "periodo=20240131.parquet",
      "sha256": "60c040b8...",
      "segundos": 41.3,
      "lectura_d10": {"archivos": 52, "archivos_reemplazados": 1, "registros": 31544310,
                      "registros_ilegibles": 190, "registros_largo_distinto_78": 0, "rut_dv_invalido": 12}
    }
  },
  "actualizado": "2026-11-04T10:12:44"
}
```

*(los números son ilustrativos)*

**Reglas del staging:**

- **Reanudable e incremental.** Volver a ejecutar `import` sólo agrega los periodos que faltan. Para sumar meses nuevos, amplíe `hasta` y vuelva a ejecutar.
- **`--reemplazar`** vuelve a escribir los periodos del rango, por ejemplo si cambió la fuente o la muestra. Si el origen guardado es distinto, borra antes los parquet de la carpeta.
- **Un solo origen por carpeta.** Si `rutas.real` ya tiene data de otro origen, `import` se detiene con un mensaje. Lo mismo pasa si hay parquet sin manifiesto, como un mock. En ambos casos use otra carpeta o `--reemplazar`.
- **Inmutable.** No edite los parquet a mano: `verify` lo detecta por el SHA-256.
- El resto del pipeline reconoce el staging por el manifiesto. Con él, ignora `formato.periodo` y `formato.rut`, que ya se aplicaron al importar.

## 3.6 Muestra de deudores

`importacion.muestra_pct` toma una **muestra estable por deudor**: un RUT queda dentro o fuera en *todos* los periodos. Así se conserva la dinámica completa de cada deudor de la muestra.

- **Con SQL**, se usa `ABS(CHECKSUM(rut_deudor)) % 10000 < pct×100` en el servidor, lo que reduce el tráfico.
- **Con archivos**, se usa un hash multiplicativo del RUT. Es **independiente** del hash del holdout de 20%, de modo que el holdout sigue siendo el 20% de la muestra (está testeado).

Las dos vías no eligen los mismos deudores. Para la síntesis da lo mismo, porque ambas son muestras aleatorias estables.

**Recomendación:**
1. Primera corrida con el 5–10% y 12 meses, para validar el flujo completo en poco tiempo.
2. Subir al 20–100% según la RAM disponible ([dimensionamiento](#38-dimensionamiento)).

El modelo es invariante a la escala de la muestra, porque modela proporciones. Lo que mejora con más data es la cola: más grupos superan `k_grupo` y hay menos fallback.

## 3.7 Verificar la importación

```powershell
d10synth --config config/d10.yaml verify
```

```
origen: {"tipo": "sql", "servidor": "srvdatscience02", "base": "cmfdat", "tabla": "dbo.D10C", "muestra_pct": 10}
periodo              filas    deudores  IFs     monto (MM$)  inválidas
2024-01-31       3,154,412   1,189,300   52     290,112,442        120
2024-02-29       3,160,877   1,191,054   52     291,887,001        131
...
meses faltantes: ninguno
OK
```

*(números ilustrativos)*

`verify` hace cuatro comprobaciones:
1. recalcula el SHA-256 de cada archivo y lo compara con el manifiesto;
2. revisa el esquema y el número de filas;
3. busca archivos sobrantes o faltantes;
4. lista los meses faltantes entre el primero y el último.

Termina con código 1 si hay problemas. Su salida contiene sólo agregados por mes, y puede compartirse como evidencia de la carga (ver [doc. 7](07_seguridad_privacidad.md)).

**Qué revisar:**

| Señal | Esperado | Si no |
|---|---|---|
| filas y deudores mes a mes | estables (variación de pocos %) | Un mes con mucho menos puede estar incompleto en la fuente: re-importe ese mes con `--desde X --hasta X --reemplazar` cuando esté completo |
| IFs | constante | Una IF que falta un mes distorsiona entradas y salidas. Excluya ese mes del rango |
| inválidas | ≪ 1% | Revise los códigos en `profile` (sección *Reglas*) |
| `filas_otro_periodo` (manifiesto) | 0 | Indica un problema de formato de periodo |
| `lectura_d10.registros_ilegibles` | ≈ 0 | Revise si los archivos traen registros de cola, otro layout u otra codificación |
| meses faltantes | ninguno | Ver [3.1](#cobertura-temporal) |

Luego ejecute `profile` ([doc. 4](04_operacion.md#paso-1-perfil)) para revisar distribuciones y reglas con más detalle.

## 3.8 Dimensionamiento

Mediciones sobre mock en el PC de desarrollo (CPU, 6,3 millones de filas: 200 mil deudores × 12 meses):

| Paso | Tiempo | Pico de RAM | RAM por millón de filas reales |
|---|---|---|---|
| `profile` | 8 s | 3,6 GiB | ~0,55 GiB |
| `fit` | 16 s | 3,3 GiB | ~0,5 GiB |
| `generate` (200 mil deudores, 12 meses, CPU) | 9 s (ajuste incluido) | 3,2 GiB | depende de `chunk_deudores`, no de la data real |
| `evaluate` (sintético de tamaño similar) | 70 s | 4,2 GiB | **~0,7 GiB** (el paso más exigente) |

**Regla práctica:** RAM necesaria ≈ **0,7 GiB × millones de filas importadas + 2 GiB**.

| Filas importadas | RAM aproximada | Ejemplo |
|---|---|---|
| 10 millones | 9 GiB | 12 meses al 3% de una tabla de 30 millones de filas por mes |
| 30 millones | 23 GiB | 12 meses al 10% |
| 60 millones | 44 GiB | 24 meses al 10%, o 12 meses al 20% |
| 120 millones | 86 GiB | 12 meses al 40% |

Para estimar sus filas: filas por mes (consulta 4 del prechequeo) × meses × `muestra_pct` / 100.

El disco por fila importada es de unos 10–15 bytes en parquet: 30 millones de filas ocupan unos 0,4 GB. Los tiempos crecen de forma aproximadamente lineal. `evaluate` puede acotarse con `evaluacion.muestra_privacidad`.

## 3.9 Desarrollo: cómo agregar otro origen

Si la data llega por una vía no soportada (otra base, una API, otro layout), basta producir DataFrames canónicos por periodo y entregarlos a `Staging`:

```python
from d10synth.config import load_config
from d10synth.io.staging import Staging, a_canonico, filtro_periodos

cfg = load_config("config/d10.yaml")
st = Staging(cfg, origen={"tipo": "mi_origen", "detalle": "..."})   # 'tipo' identifica el origen en el manifiesto
for fecha in filtro_periodos(fechas_disponibles, cfg):              # cierres de mes dentro de [desde, hasta]
    if st.existe(fecha):
        continue
    lotes = (a_canonico(df_crudo, cfg).collect() for df_crudo in leer_por_lotes(fecha))
    st.escribir(fecha, lotes, extra={"mi_origen": {"filas_leidas": ...}})
```

- `a_canonico` renombra según `cfg.columnas`, interpreta `periodo` y `rut` según `cfg.formato` y castea al esquema canónico.
- `escribir` acepta un iterable de lotes, para no cargar el mes entero en memoria. Además:
  - escribe de forma atómica;
  - calcula las estadísticas;
  - actualiza el manifiesto.
- Agregue un test en `tests/test_importar.py`. El patrón es generar el mock en el nuevo formato, importarlo y comparar con el mock parquet (ver `test_formatos_equivalentes`).

Para registrar el origen en la CLI, agréguelo a `TIPOS` y a `importar()` en [src/d10synth/io/importar.py](../src/d10synth/io/importar.py), y a las opciones de `--tipo` en [src/d10synth/cli.py](../src/d10synth/cli.py).
