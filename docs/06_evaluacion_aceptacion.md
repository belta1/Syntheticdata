# 6. Evaluación y criterios de aceptación

`d10synth evaluate` deja `reportes/evaluacion.html`, `.md` y `.json`. El reporte tiene cuatro bloques. Este documento explica cómo leer cada uno y propone umbrales para dar la PoC por aprobada.

> **Los umbrales son una propuesta.** Salen de lo observado con el mock y de criterios usuales en la literatura de datos sintéticos. El responsable de la información y los usuarios del sintético deben validarlos antes de la evaluación con la data real.

## Conceptos

- **Train**: la parte de la data real con la que se ajustó el modelo. Es el 80% de los deudores.
- **Holdout**: el 20% de los deudores reales que el modelo **nunca vio**. Se asignan por hash del RUT, el mismo en todos los meses.
- **TVD** (*total variation distance*): la fracción de masa que habría que mover para que dos distribuciones sean iguales. 0 = idénticas, 1 = disjuntas. Se calcula tanto por filas como ponderado por monto.
- **W1** (Wasserstein-1) sobre log(monto): cuánto hay que "desplazar" una distribución para que coincida con la otra, en unidades de log. Por ejemplo, W1 = 0,1 equivale a una diferencia típica de ~10% en los montos.

## Bloque 1: reglas (bloqueante)

Se valida el sintético completo contra las reglas del manual.

| Regla | Qué revisa |
|---|---|
| `codigos_o_monto_invalidos` | tipo_deudor ∈ {1, 2}, tipo_crédito del catálogo, mora 0–9, monto > 0, sin nulos |
| `contingente_con_mora` | tipos 6, 7 y 11 con mora ≠ 0 |
| `empresa_con_producto_persona` | RUT de empresa con consumo, vivienda, estudios o salud (2, 3, 8, 9, 10, 11, 12, 22, 23, 26, 27) |
| `filas_duplicadas` | más de una fila por (periodo, RUT, IF, tipo de deudor, tipo de crédito, mora) |
| `monto_no_entero` | montos con decimales |
| `transicion_mora_no_permitida` | celdas cuya mora máxima salta más de lo posible en un mes |
| `rut_dv_invalido` | sólo si la salida es `con_dv` |

**Criterio: 0 violaciones en todas.** Así salió en todas las pruebas con mock, incluida una de 30 millones de filas.

## Bloque 2: fidelidad

Compara el sintético con el **train** real.

| Métrica (`evaluacion.json`) | Qué mide | Mock (referencia) | Umbral propuesto |
|---|---|---|---|
| `marginales.<campo>.tvd_filas` para seg, if_id, tipo_deudor, tipo_credito, tipo_mora | distribución de cada campo por filas | 0,003–0,02 | ≤ 0,05 |
| `marginales.<campo>.tvd_monto` | ídem ponderado por monto | 0,002–0,07 | ≤ 0,10 |
| `marginales.<par>.tvd_filas` (pares como tipo_credito+tipo_mora, if_id+tipo_credito) | relación entre dos campos | 0,015–0,03 | ≤ 0,10 |
| `marginales.if_id+tipo_credito.tvd_monto` | mezcla de productos por IF en monto (la más difícil) | ~0,11 | ≤ 0,20 |
| `w1_log_monto_por_tipo_credito.todos` | distribución de montos | 0,06–0,08 | ≤ 0,15 |
| `w1_log_monto_por_tipo_credito.<tc>` | montos por tipo de crédito | 0,02–0,29 (los tipos con poca data dan valores altos) | ≤ 0,25 en los tipos con más del 1% de las filas |
| `deudores.tvd_n_if` | número de IF por deudor | 0,005 | ≤ 0,05 |
| `deudores.tvd_n_celdas` | número de productos por deudor | 0,02 | ≤ 0,05 |
| `deudores.w1_log_deuda_deudor` | deuda total por deudor | 0,08 | ≤ 0,15 |
| `deudores.{real,sintetico}.gini_deuda` | concentración de la deuda | 0,943 contra 0,943 | diferencia ≤ 0,02 |
| `deudores.{real,sintetico}.top1pct_deuda` | participación del 1% más endeudado | 0,53 contra 0,53 | diferencia ≤ 0,05 |
| `deudores.{real,sintetico}.celdas_multi_mora` | celdas con más de un tramo de mora | 0,056 contra 0,062 | diferencia relativa ≤ 25% |
| `transiciones_mora_tvd_media.todas` | matriz de migración de mora (TVD media por fila) | 0,005–0,013 | ≤ 0,03 |
| `deudores.{real,sintetico}.salida_deudores` y `entrada_deudores` | flujos mensuales de deudores | 2,05% contra 2,06% | diferencia ≤ 0,5 puntos porcentuales |

El reporte HTML también muestra las **matrices de transición** real y sintética, para compararlas a ojo. Las celdas vacías (·) son transiciones que no ocurrieron. Las transiciones estructuralmente imposibles nunca aparecen en el sintético.

**Cómo leer un mal resultado:**
- **TVD alta en `if_id`**: hay IF pequeñas, por debajo de `k_min`, que se suprimieron. Es esperable y aceptable.
- **W1 alto en un tipo de crédito con poca data**: hubo fallback a un nivel más agregado. Revise `montos_nivel_fallback` en `modelo.json`.
- **Flujos de entrada o salida muy distintos**: suele indicar que en la data real falta una IF algún mes (ver [doc. 3 §3.7](03_importacion_datos.md#37-verificar-la-importación)).

## Bloque 3: privacidad

Mide si el sintético está "demasiado cerca" de deudores reales concretos, lo que indicaría memorización. Cada deudor se representa, en su primer mes, con un vector:
- log(1 + monto) por cada uno de los 20 tipos de crédito;
- mora máxima;
- log(1 + número de IF).

La comparación clave es **sintético → train** contra **holdout → train**. El holdout son deudores reales que el modelo no vio, así que es la vara de lo que significa "parecerse por casualidad".

| Métrica | Qué mide | Mock | Criterio propuesto |
|---|---|---|---|
| `dcr_mediana` (sint vs holdout) | distancia mediana al deudor real más parecido del train | 0,0103 contra 0,0111 | sintético ≥ 0,9 × holdout |
| `dcr_p5` | distancia en el 5% más cercano | similar | sintético ≥ 0,8 × holdout |
| `frac_sint_bajo_p5_holdout` | fracción de sintéticos más cerca del train que el 5% más cercano del holdout | 0,058 | ≤ 0,07 (se espera ~0,05) |
| `frac_copias_exactas` (sint vs holdout) | vectores idénticos a uno del train | 0,27% contra 0,17% | sintético ≤ holdout + 0,5 puntos porcentuales |
| `nndr_mediana` (sint vs holdout) | cociente entre la distancia al 1er y al 2º vecino. Cerca de 0 = un vecino "único" muy cercano | 0,666 contra 0,666 | sintético ≥ 0,9 × holdout |
| `ruts_sinteticos_que_existen_en_real` | RUT sintéticos presentes en la data real | 0 | **= 0 (bloqueante)** |
| `filas_sint_sobre_max_real` | montos por sobre el máximo real del tipo de crédito | 0 | **= 0 (bloqueante)** |

**Sobre las "copias exactas":** muchos deudores reales tienen perfiles idénticos y comunes, por ejemplo una sola tarjeta de consumo al día con un monto redondo. Que un sintético coincida con uno de esos perfiles no expone a nadie, y por eso se compara contra la misma tasa en el holdout. **Lo preocupante sería que el sintético coincidiera mucho más que el holdout.**

**Límites de estas métricas:**
- Son empíricas y no son una garantía formal (no hay privacidad diferencial).
- Se calculan sobre el primer mes y una muestra de `evaluacion.muestra_privacidad` deudores.
- No evalúan ataques sobre la trayectoria multi-mes completa.

El [doc. 7](07_seguridad_privacidad.md) explica las salvaguardas adicionales.

## Bloque 4: utilidad (TSTR)

Pregunta si un modelo entrenado con el sintético sirve sobre data real.

- **Tarea**: predecir si la mora máxima de una celda empeora del mes t al t+1. Se excluyen los contingentes.
- **Features**: segmento, tipo de deudor, tipo de crédito, IF, mora actual, log(monto), número de celdas e IF del deudor, mora máxima del deudor y log(deuda total).
- **Modelo**: `HistGradientBoostingClassifier` de scikit-learn.
- **TRTR**: se entrena con el train real y se evalúa en el holdout real.
- **TSTR**: se entrena con el sintético y se evalúa en el **mismo** holdout real.

| Métrica | Mock | Criterio propuesto |
|---|---|---|
| `tasa_deterioro` real_holdout contra sintético | 5,4% contra 5,6% | diferencia relativa ≤ 20% |
| `TRTR_real` (AUC) | 0,713–0,727 | referencia |
| `TSTR_sintetico` (AUC) | 0,711–0,727 | |
| `ratio_TSTR_TRTR` | 0,997–1,00 | **≥ 0,90** |

Un ratio de 0,90 o más significa que el sintético conserva casi toda la señal predictiva para esta tarea. Si el sintético se va a usar para otras tareas (por ejemplo, modelos de provisiones), conviene agregar el TSTR correspondiente; [src/d10synth/evaluate/utility.py](../src/d10synth/evaluate/utility.py) sirve de plantilla.

## Resumen de criterios propuestos

| Bloque | Bloqueantes | Deseables |
|---|---|---|
| Reglas | 0 violaciones en todas | — |
| Fidelidad | — | marginales ≤ 0,05; pares ≤ 0,10; W1 global ≤ 0,15; transiciones ≤ 0,03; flujos ± 0,5 pp; Gini ± 0,02 |
| Privacidad | 0 RUT reales; 0 montos sobre el máximo real | DCR y NNDR del sintético ≥ 0,9 × holdout; `frac_sint_bajo_p5_holdout` ≤ 0,07 |
| Utilidad | — | ratio TSTR/TRTR ≥ 0,90 |

**Propuesta de dictamen:**
- **Aprobada**: se cumplen todos los bloqueantes y al menos el 80% de los deseables.
- **Aprobada con observaciones**: se cumplen los bloqueantes y las brechas en los deseables están explicadas (por ejemplo, tipos de crédito con poca data).
- **No aprobada**: falla cualquier bloqueante.

## Si los resultados no alcanzan

| Síntoma | Ajuste sugerido |
|---|---|
| Mucho fallback, W1 alto en varios tipos | más data (`muestra_pct`, más meses), o `ajuste.k_grupo` 100 |
| Cobertura de patrones < 0,98 | `ajuste.max_patrones` 5000–10000 |
| Flujos distintos | revise la completitud de IF por mes y excluya los meses incompletos |
| Privacidad: sintético más cerca que el holdout | suba `ajuste.k_min` (20–50) y `ajuste.k_grupo`, y vuelva a evaluar |
| Matrices de transición distintas en tipos específicos | revise `transiciones_no_permitidas_descartadas`, que pueden ser repactaciones |
