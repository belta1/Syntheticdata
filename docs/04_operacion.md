# 4. Operación

Este documento cubre la secuencia completa después de la importación ([doc. 3](03_importacion_datos.md)). Todos los comandos se ejecutan **desde la raíz del proyecto**, con el entorno activado:

```powershell
cd E:\synthdata            # o donde esté el proyecto
.venv\Scripts\activate
```

Opciones comunes a todos los comandos:

| Opción | Efecto |
|---|---|
| `--config <ruta>` | YAML a usar. Por defecto `config/d10.yaml` |
| `-v` / `--verbose` | log en nivel DEBUG |

Las rutas relativas del YAML se resuelven desde la carpeta donde se ejecuta el comando.

## Resumen

| Paso | Comando | Lee | Escribe | Repetible |
|---|---|---|---|---|
| 0 | `import` + `verify` | fuente | `data/real/` | sí, incremental |
| 1 | `profile` | `data/real/` | `reportes/perfil_d10.{md,json}` | sí |
| 2 | `fit` | `data/real/` | `modelos/d10/modelo.{npz,json}` | sí, sobrescribe |
| 3 | `generate` | modelo (+ RUTs de `data/real/`) | `data/sintetico/parte_XXX.parquet` | sí, borra la salida anterior |
| 4 | `evaluate` | `data/real/` + `data/sintetico/` | `reportes/evaluacion.{html,md,json}` | sí, sobrescribe |
| — | `validate <carpeta>` | cualquier parquet con el esquema D10 | consola | sí |

## Paso 1: perfil

```powershell
d10synth --config config/d10.yaml profile
```

Produce `reportes/perfil_d10.md` y `.json`, **sólo con agregados**. Los conteos menores a `k_min` (10) se muestran como `<k`. Tiene estas secciones:

| Sección | Para qué sirve |
|---|---|
| Tipos de columnas en la fuente y origen | Confirmar que la importación interpretó bien los tipos |
| por periodo | Filas, deudores, IF y monto por mes (detectar meses incompletos) |
| deudores por segmento | Validar el umbral persona/empresa: lo normal es una gran mayoría de personas |
| por tipo_deudor / tipo_credito / tipo_mora | Distribución de códigos, con códigos fuera de catálogo si los hay |
| por IF | Tamaño de cada IF |
| tipo_credito × mora | Coherencia (por ejemplo, contingentes sólo en mora 0) |
| cuantiles de monto por tipo de crédito | Órdenes de magnitud de los montos |
| deudores por n_if y por n_celdas | Concentración de relaciones |
| celdas por n códigos de mora | Cuántas celdas tienen más de un tramo de mora |
| transiciones de mora de celdas | Matriz empírica t → t+1 |
| Reglas | Violaciones de las reglas del manual en la data real |

**Revise** que los deudores por segmento sean razonables, que no haya meses anómalos y que las violaciones de reglas sean marginales.

**Compartir:** el perfil está diseñado para salir del PC después de una revisión ([doc. 7](07_seguridad_privacidad.md)), por ejemplo para afinar el modelo con quien lo desarrolló.

## Paso 2: ajuste

```powershell
d10synth --config config/d10.yaml fit
```

Qué hace:
1. Carga `data/real`, **excluye el holdout** (20% de los deudores, por hash del RUT) y descarta las filas inválidas.
2. Construye las celdas (deudor, IF, tipo de deudor, tipo de crédito) por mes.
3. Ajusta la foto, los montos (cuantiles + cópula) y la dinámica (pares de meses consecutivos).
4. Guarda `modelos/d10/modelo.npz` (arreglos) y `modelo.json` (metadatos).

Imprime un resumen como este:

```json
{
  "periodos": ["2023-01-31", "...", "2024-12-31"],
  "n_if": 52,
  "resumen_datos": {"filas": 61234567, "filas_invalidas": 1203, "filas_empresa_producto_persona": 88,
                    "contingentes_con_mora_corregidos": 15, "celdas": 54000000, "deudores_periodo": 23000000},
  "patrones": {"n_patrones": 2850, "cobertura_patrones": {"empresa": 0.991, "persona": 0.998}, "if_con_patron_propio": 90},
  "dinamica": {"pares_periodos": 23, "transiciones_no_permitidas_descartadas": 420,
               "salida_deudores": [0.021, 0.014], "entrada_deudores": [0.022, 0.016], "celdas_nuevas_por_deudor": [0.03, 0.02]},
  "rho": [0.55, 0.62],
  "segundos_ajuste": 310.2
}
```

*(números ilustrativos)*

**Qué mirar:**

| Campo | Esperado | Si no |
|---|---|---|
| `filas_invalidas` / `filas` | ≪ 1% | Revise los códigos en el perfil |
| `cobertura_patrones` | > 0,98 | Si es baja, el vocabulario de patrones no alcanza: suba `ajuste.max_patrones` |
| `pares_periodos` | meses − 1 | Menos significa que hay meses faltantes |
| `transiciones_no_permitidas_descartadas` | bajo en relación con las celdas | Saltos de mora imposibles en la data real: repactaciones, rectificaciones o meses faltantes. Se descartan del ajuste de Markov |
| `salida_deudores`, `entrada_deudores` | [persona, empresa], pocos % mensuales | Si son muy altas, probablemente falta alguna IF en algún mes |
| `rho` | 0,3–0,8 | Correlación entre los montos de un mismo deudor |
| `montos_nivel_fallback` (en `modelo.json`) | la mayoría en nivel 0 | Muchos grupos en niveles altos indican poca data: aumente `muestra_pct` o baje `k_grupo` (con cuidado, ver doc. 7) |

**Versionar corridas.** `fit` sobrescribe `rutas.modelo`. Para conservar varias corridas, cambie la ruta en cada una (por ejemplo `modelos/d10_2026-11_m10`) y guarde una copia del YAML usado junto al modelo.

## Paso 3: generación

```powershell
d10synth --config config/d10.yaml generate
# o, sobrescribiendo el YAML:
d10synth --config config/d10.yaml generate --deudores 1000000 --meses 24 --dispositivo cuda --semilla 7 --chunk-deudores 500000
```

| Opción | YAML | Efecto |
|---|---|---|
| `--deudores` | `generacion.deudores` | Deudores vivos en el **primer** mes. Después la población evoluciona según las tasas de entrada y salida |
| `--meses` | `generacion.meses` | Largo del panel. Puede superar los meses de la data real (se extrapola la dinámica estacionaria) |
| `--dispositivo` | `generacion.dispositivo` | `auto` (CUDA si hay), `cuda` o `cpu` |
| `--semilla` | `generacion.semilla` | Reproducibilidad en el mismo dispositivo |
| `--chunk-deudores` | `generacion.chunk_deudores` | Deudores por bloque. Bájelo si se acaba la memoria de la GPU |
| — | `generacion.periodo_inicio` | Primer mes del panel. Por defecto, el primer periodo de la data de ajuste |
| — | `generacion.rut_salida` | `sin_dv` (entero) o `con_dv` (texto "12345678-5") |
| — | `generacion.excluir_ruts_reales` | `true`: lee los RUT de `data/real` y ninguno se asigna a un deudor sintético |

**Salida:** `data/sintetico/parte_000.parquet`, `parte_001.parquet`, …, con un archivo por chunk. Cada archivo tiene deudores distintos y todos los meses de esos deudores. Las columnas usan **los nombres de la tabla fuente** (`rutis_sbif, periodo, rut_deudor, ...`), con `periodo` como fecha y `monto` como entero. Cada vez que se ejecuta se **borran** los `parte_*.parquet` anteriores de esa carpeta.

**Rendimiento de referencia** (CPU de desarrollo): 1 millón de deudores × 12 meses dio unos 30 millones de filas en 41 s, con 193 MB en disco. En GPU no está medido.

**Memoria de la GPU:** el uso crece con `chunk_deudores`. Si aparece `CUDA out of memory`, divida `chunk_deudores` por 2.

**Validar la salida** sin la data real (rápido):

```powershell
d10synth --config config/d10.yaml validate data/sintetico
```

## Paso 4: evaluación

```powershell
d10synth --config config/d10.yaml evaluate
```

Compara el sintético contra la data real y deja `reportes/evaluacion.html` (para leer), `.md` y `.json` (para procesar). Hace cuatro bloques:

1. **Reglas** sobre el sintético. Todas deben dar 0.
2. **Fidelidad**: el sintético contra la parte de entrenamiento de la data real.
3. **Privacidad**: las distancias sintético → entrenamiento contra holdout → entrenamiento, más RUTs y montos extremos.
4. **Utilidad**: un modelo de deterioro de mora entrenado con sintético contra uno entrenado con real, ambos evaluados en el holdout real.

La interpretación y los criterios de aceptación están en el [doc. 6](06_evaluacion_aceptacion.md).

Para comparar bien, conviene que el sintético tenga un tamaño parecido a la data real importada: mismos meses y una cantidad de deudores similar a la del primer mes real (después del holdout).

`evaluacion.muestra_privacidad` (50.000 por defecto) limita cuántos deudores entran al cálculo de vecinos más cercanos. Súbalo si hay GPU; bájelo si `evaluate` tarda demasiado.

## Corrida tipo, de punta a punta

```powershell
d10synth --config config/d10.yaml doctor
d10synth --config config/d10.yaml import --desde 2023-01-31 --hasta 2024-12-31 --muestra-pct 10
d10synth --config config/d10.yaml verify
d10synth --config config/d10.yaml profile
d10synth --config config/d10.yaml fit
d10synth --config config/d10.yaml generate --meses 24
d10synth --config config/d10.yaml evaluate
```

Para dejar registro de la corrida, redirija la salida a un log. Los logs contienen sólo conteos, rutas y tiempos, nunca RUT:

```powershell
d10synth --config config/d10.yaml fit *>&1 | Tee-Object -FilePath reportes\fit.log
```

## Reprocesos habituales

| Situación | Qué ejecutar |
|---|---|
| Llegó un mes nuevo | `import --hasta <nuevo cierre>` → `verify` → `fit` → `generate` → `evaluate` |
| Se corrigió un mes en la fuente | `import --desde X --hasta X --reemplazar` → `verify` → `fit` → … |
| Cambié parámetros de `ajuste` | `fit` → `generate` → `evaluate` |
| Cambié parámetros de `generacion` | `generate` → `evaluate` |
| Quiero más deudores sintéticos con el mismo modelo | `generate --deudores N` (no hace falta reajustar) |
| Quiero otra muestra de la data real | `import --muestra-pct P --reemplazar` → todo lo demás |

## Mantenimiento de carpetas

| Carpeta | Contiene | Ciclo de vida |
|---|---|---|
| `data/real/` | **data real** (RUT) | mientras dure la PoC; borrado seguro al cerrar ([doc. 7](07_seguridad_privacidad.md#borrado)) |
| `modelos/` | agregados | conservar las versiones aprobadas |
| `data/sintetico/` | data sintética | se regenera; liberar sólo lo aprobado |
| `reportes/` | agregados (perfil, evaluación, logs) | conservar como evidencia |
| `data/real/_tmp_*`, `*.tmp` | temporales de una importación interrumpida | se pueden borrar si no hay una importación en curso |
