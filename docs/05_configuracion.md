# 5. Referencia de configuración

Toda la configuración está en un YAML; por defecto, `config/d10.yaml`. Funciona así:
- una clave desconocida produce un error al cargar, para que no pasen errores de tipeo;
- una clave omitida toma el valor por defecto de esta tabla;
- las rutas relativas se resuelven desde la carpeta donde se ejecuta el comando.

Algunos parámetros de `generacion` e `importacion` también se pueden pasar por línea de comandos. Esos valores tienen prioridad sobre el YAML (ver [doc. 4](04_operacion.md)).

## `fuente`: conexión a SQL Server (sólo para `importacion.tipo: sql`)

| Clave | Tipo | Defecto | Descripción |
|---|---|---|---|
| `driver` | texto | `ODBC Driver 17 for SQL Server` | Nombre exacto del driver ODBC (ver `d10synth doctor`). Con "18" se agrega `TrustServerCertificate=yes` |
| `servidor` | texto | `srvdatscience02` | Servidor o `servidor\instancia` o `servidor,puerto` |
| `base` | texto | `cmfdat` | Base de datos |
| `tabla` | texto | `dbo.D10C` | Tabla o vista con el D10 |
| `trusted_connection` | bool | `true` | `true`: autenticación Windows. `false`: usa las variables de entorno `D10_USER` y `D10_PASSWORD` |

No existe una clave para la contraseña, a propósito: nunca debe quedar escrita en un archivo.

## `columnas`: nombre de cada campo en la fuente

Se aplica a `sql`, `csv` y `parquet`. El origen `d10` no la usa, porque el layout es fijo.

| Clave (campo canónico) | Defecto |
|---|---|
| `if_id` | `rutis_sbif` |
| `periodo` | `periodo` |
| `rut` | `rut_deudor` |
| `tipo_deudor` | `tipo_deudor` |
| `tipo_credito` | `tipo_credito` |
| `tipo_mora` | `tipo_mora` |
| `monto` | `monto` |

Estos nombres también se usan para las **columnas de la data sintética**, así que el sintético queda con el mismo esquema que la fuente.

## `formato`: cómo interpretar periodo y RUT

| Clave | Valores | Defecto | Descripción |
|---|---|---|---|
| `periodo` | `auto`, `date`, `yyyymmdd`, `yyyymm` | `auto` | `auto` reconoce fecha, `aaaammdd`, `aaaamm` y texto ISO con hora. Ver [doc. 3 §3.3](03_importacion_datos.md#cómo-fijar-formatoperiodo-y-formatorut) |
| `rut` | `auto`, `sin_dv`, `con_dv` | `auto` | `auto` corta en el guion si lo hay y si no toma el número completo. **Use `con_dv` si el DV viene pegado sin guion** |
| `umbral_rut_empresa` | entero | `50000000` | RUT ≥ umbral = empresa. Define el segmento, que condiciona todo el modelo |

`periodo` y `rut` se aplican **al importar**. La data ya importada (con manifiesto) tiene un formato fijo y no los usa. `umbral_rut_empresa` se usa en todos los pasos.

## `importacion`: origen y alcance de la data real

| Clave | Tipo | Defecto | Descripción |
|---|---|---|---|
| `tipo` | `sql`, `csv`, `parquet`, `d10` | `sql` | Origen. CLI: `--tipo` |
| `entrada` | texto | `null` | Para `csv`, `parquet` y `d10`: archivo, carpeta (recursiva) o patrón glob. CLI: `--entrada` |
| `desde` | `"aaaa-mm-dd"` | `null` | Primer cierre de mes, inclusive. `null` = sin límite. CLI: `--desde` |
| `hasta` | `"aaaa-mm-dd"` | `null` | Último cierre de mes, inclusive. CLI: `--hasta` |
| `muestra_pct` | 0–100 | `100` | Porcentaje de deudores, con muestra estable por RUT. CLI: `--muestra-pct` |
| `batch_filas` | entero | `500000` | Para `sql`: filas por lote al leer. Bájelo si la memoria es escasa |

### `importacion.csv`: sólo para `tipo: csv`

| Clave | Tipo | Defecto | Descripción |
|---|---|---|---|
| `separador` | 1 carácter | `";"` | Separador de campos |
| `encabezado` | bool | `true` | Si la primera línea tiene los nombres de columna |
| `nombres` | lista | `null` | Nombres en el orden del archivo cuando `encabezado: false`. Deben calzar con `columnas` |
| `encoding` | `utf8`, `utf8-lossy` | `utf8` | `utf8-lossy` tolera latin-1 o ANSI (los caracteres inválidos se reemplazan) |
| `decimal` | `"."` o `","` | `"."` | Con `","` se eliminan los puntos de miles del monto y la coma pasa a ser el punto decimal |

## `rutas`

| Clave | Defecto | Contenido |
|---|---|---|
| `real` | `data/real` | Staging de la data real importada (**sensible**) |
| `modelo` | `modelos/d10` | `modelo.npz` y `modelo.json` |
| `sintetico` | `data/sintetico` | `parte_XXX.parquet` |
| `reportes` | `reportes` | perfil y evaluación |

## `ajuste`: parámetros del modelo

| Clave | Defecto | Descripción | Cuándo cambiarlo |
|---|---|---|---|
| `holdout_pct` | `20` | % de deudores que no entran al ajuste. Se usan para medir privacidad y utilidad | Rara vez. Bajarlo da más data al ajuste pero mediciones más ruidosas |
| `k_min` | `10` | **Supresión**: un conteo menor no entra a ninguna tabla. También es el orden estadístico donde se recortan los cuantiles extremos | Subirlo da más privacidad y menos detalle. **No lo baje** sin aprobación ([doc. 7](07_seguridad_privacidad.md)) |
| `k_grupo` | `200` | Mínimo de observaciones para que una distribución condicional (de montos, salidas o transiciones) use su propio grupo. Con menos, usa un nivel más agregado | Bájelo (≥ 50) si hay mucho fallback y poca data. Súbalo para un modelo más suave |
| `max_patrones` | `3000` | Tamaño del vocabulario de patrones de productos por segmento | Súbalo si la cobertura de patrones (`modelo.json`) es < 0,98 |
| `max_perfiles_mora` | `8` | Perfiles de mora distintos que se guardan por (tipo de crédito, mora máxima) | Rara vez |
| `n_cuantiles` | `201` | Puntos de cada función cuantil | Rara vez |
| `cuantil_min`, `cuantil_max` | `0.001`, `0.999` | Rango de probabilidades de la grilla de cuantiles. Fuera de él no se extrapola | Rara vez |
| `max_if_por_deudor` | `12` | Tope del número de IF por deudor (los que tienen más se cuentan en el tope) | Si el perfil muestra muchos deudores con más de 12 IF |

## `generacion`

| Clave | Defecto | Descripción |
|---|---|---|
| `deudores` | `200000` | Deudores en el primer mes. CLI: `--deudores` |
| `meses` | `12` | Largo del panel. CLI: `--meses` |
| `periodo_inicio` | `null` | Primer cierre del panel (`"aaaa-mm-dd"`). `null` = primer periodo de la data de ajuste |
| `chunk_deudores` | `250000` | Deudores por bloque de muestreo (memoria de la GPU). CLI: `--chunk-deudores` |
| `semilla` | `42` | Semilla. CLI: `--semilla` |
| `dispositivo` | `auto` | `auto`, `cuda` o `cpu`. También aplica a la parte de privacidad de `evaluate`. CLI: `--dispositivo` |
| `excluir_ruts_reales` | `true` | Ningún RUT sintético coincide con un RUT de `rutas.real`. **Déjelo en `true`** |
| `rut_salida` | `sin_dv` | `sin_dv`: entero. `con_dv`: texto `"12345678-5"` |

## `evaluacion`

| Clave | Defecto | Descripción |
|---|---|---|
| `muestra_privacidad` | `50000` | Deudores por grupo en el cálculo de vecinos más cercanos (DCR/NNDR) |
| `semilla` | `7` | Semilla de las muestras de evaluación |

## Ejemplo completo para la data real

```yaml
fuente:
  driver: "ODBC Driver 17 for SQL Server"
  servidor: srvdatscience02
  base: cmfdat
  tabla: dbo.D10C
  trusted_connection: true

columnas:
  if_id: rutis_sbif
  periodo: periodo
  rut: rut_deudor
  tipo_deudor: tipo_deudor
  tipo_credito: tipo_credito
  tipo_mora: tipo_mora
  monto: monto

formato:
  periodo: auto
  rut: auto                 # con_dv si el DV viene pegado sin guion
  umbral_rut_empresa: 50000000

importacion:
  tipo: sql
  desde: "2023-01-31"
  hasta: "2024-12-31"
  muestra_pct: 10
  batch_filas: 500000

rutas:
  real: data/real
  modelo: modelos/d10
  sintetico: data/sintetico
  reportes: reportes

ajuste:
  holdout_pct: 20
  k_min: 10
  k_grupo: 200
  max_patrones: 3000

generacion:
  deudores: 1000000
  meses: 24
  dispositivo: auto
  excluir_ruts_reales: true
  rut_salida: sin_dv

evaluacion:
  muestra_privacidad: 50000
```
