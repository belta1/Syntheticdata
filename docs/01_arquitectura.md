# 1. Arquitectura

## Objetivo

Generar un **panel sintético multi-mes** del archivo D10 (deuda por deudor, Art. 14 LGB) que:

- tenga **el mismo esquema** que la tabla `cmfdat.dbo.D10C`: `rutis_sbif, periodo, rut_deudor, tipo_deudor, tipo_credito, tipo_mora, monto`;
- cumpla las **reglas del manual** (códigos válidos, contingentes con mora 0, sin productos de persona en empresas, transiciones de mora posibles, DV válido);
- reproduzca las **distribuciones y la dinámica** de la data real (montos, productos, concentración, migración de mora, entradas y salidas);
- **no contenga ningún deudor real** ni permita reconstruirlo.

Es una prueba de concepto: sólo usa cierres de mes y un único modelo, el estadístico jerárquico (no hay modelos neuronales de comparación).

## Flujo de datos

```
                ZONA SEGURA (PC con acceso a la data real)
 ┌───────────────────────────────────────────────────────────────────────────┐
 │                                                                           │
 │  Fuente                   import            data/real/ (staging)          │
 │  ─────────                ───────           ──────────────────────        │
 │  SQL Server D10C  ──┐                       periodo=AAAAMMDD.parquet      │
 │  CSV (bcp/SSMS)   ──┼──►  normaliza   ──►   (esquema canónico, 1 por mes) │
 │  parquet          ──┤     filtra cierres    _manifiesto.json              │
 │  archivos D10 IF  ──┘     muestra opcional  (filas, hash SHA-256)         │
 │                                                  │                        │
 │                     verify ◄─────────────────────┤                        │
 │                     profile ──► reportes/perfil_d10.md (agregados, k)     │
 │                                                  │                        │
 │                     fit (excluye holdout 20%) ◄──┘                        │
 │                       │                                                   │
 │                       ▼                                                   │
 │               modelos/d10/modelo.npz + modelo.json  (sólo agregados)      │
 │                       │                                                   │
 │                     generate (GPU/CPU) ◄── RUTs reales (para excluirlos)  │
 │                       │                                                   │
 │                       ▼                                                   │
 │               data/sintetico/parte_XXX.parquet                            │
 │                       │                                                   │
 │                     evaluate ◄── data/real (train + holdout)              │
 │                       │                                                   │
 │                       ▼                                                   │
 │               reportes/evaluacion.{html,md,json}                          │
 │                                                                           │
 └───────────────────────────────────────────────────────────────────────────┘
          │ sólo tras la revisión del responsable (ver doc. 7)
          ▼
   data sintética liberada / perfil agregado
```

Cada paso lee lo que dejó el anterior en disco, así que se puede repetir cualquiera sin rehacer los otros. La única conexión a la base es la de `import` con origen `sql`, y sólo hace `SELECT`.

## Componentes

| Paso | Módulo | Entrada | Salida |
|---|---|---|---|
| `doctor` | `doctor.py` | entorno | diagnóstico por consola |
| `import` | `io/extract.py` (sql), `io/importar.py` (csv, parquet, d10), `io/staging.py` | fuente | `data/real/*.parquet` + `_manifiesto.json` |
| `verify` | `io/staging.py` | `data/real` | integridad por consola |
| `profile` | `profile.py` | `data/real` | `reportes/perfil_d10.{md,json}` |
| `fit` | `model/fit.py`, `snapshot.py`, `amounts.py`, `dynamics.py`, `tables.py` | `data/real` | `modelos/d10/modelo.{npz,json}` |
| `generate` | `generate.py`, `_torch.py` | modelo (+ RUTs reales) | `data/sintetico/parte_XXX.parquet` |
| `evaluate` | `evaluate/*.py`, `rules.py` | `data/real` + `data/sintetico` | `reportes/evaluacion.{html,md,json}` |
| `validate` | `rules.py` | cualquier carpeta parquet | reglas por consola |

## El modelo

### Unidad: el deudor y sus celdas

La unidad es el **deudor**, no la fila. Una **celda** es la combinación (deudor, IF, tipo_deudor, tipo_crédito). Su estado es la **mora máxima** entre sus filas y su monto es la suma. Una celda puede tener varias filas, una por cada código de mora presente (por ejemplo, un crédito en cuotas con parte al día y parte en mora 2).

### Foto inicial (primer mes)

1. **Segmento**: persona o empresa, según el RUT (≥ `umbral_rut_empresa` = empresa).
2. **Número de IF** del deudor, según su segmento.
3. **Cuáles IF**: la primera según la participación de cada IF en el segmento; las siguientes según su co-ocurrencia con las ya elegidas (muestreo sin reemplazo, Gumbel-top-k).
4. **Patrón de productos** por (deudor, IF): un bitmask sobre los 40 *slots* (2 tipos de deudor × 20 tipos de crédito), elegido entre los patrones frecuentes (con al menos `k_min` deudores) del segmento y la IF.
5. **Mora máxima** de cada celda, según (segmento, slot). Los contingentes (6, 7, 11) quedan siempre en 0.
6. **Perfil de mora**: qué códigos coexisten en la celda, dado (tipo de crédito, mora máxima), y cómo se reparte el monto entre ellos (Dirichlet ajustada por momentos).
7. **Monto**: función cuantil empírica de log(monto) según (segmento, slot, mora), con colas recortadas. Los montos de un mismo deudor se correlacionan mediante una **cópula gaussiana** con un factor latente de "tamaño" del deudor (ρ estimado por segmento).

### Dinámica (mes t → t+1)

- **Deudores**: tasa de salida y de entrada por segmento. Los que entran se generan con el modelo de la foto.
- **Celdas**: probabilidad de baja según (segmento, tipo de crédito, mora); altas según una tasa de Poisson, con probabilidad de caer en una IF que el deudor ya tiene.
- **Mora**: cadena de Markov 10×10 por tipo de crédito, con **ceros estructurales**. En un mes el atraso sólo puede crecer unos 31 días, así que saltos como 0 → 4 son imposibles.
- **Montos**: cuantiles de log(monto_t+1 / monto_t) según (tipo de crédito, mora_t, mora_t+1).

### Salida

Cada celda se expande a filas (una por código de mora del perfil) y cada deudor recibe un **RUT ficticio**:

- se obtiene con una permutación afín dentro del rango de su segmento;
- tiene DV módulo 11 válido;
- excluye todos los RUT que aparecen en la data real.

### Protección incorporada en el ajuste

- **Holdout**: el 20% de los deudores (por hash del RUT) no entra al ajuste. Sirve para medir privacidad y utilidad contra deudores que el modelo nunca vio.
- **Supresión k**: los conteos menores a `k_min` (10) no entran a ninguna tabla.
- **Fallback jerárquico**: una distribución condicional con menos de `k_grupo` (200) observaciones se reemplaza por la de un nivel más agregado. Por ejemplo, (segmento, slot, mora) pasa a (segmento, slot), luego a (slot) y así hasta la marginal.
- **Recorte de colas**: los cuantiles extremos se acotan al k-ésimo valor más chico o más grande del grupo, así que el máximo de un individuo nunca queda en el modelo.

### Qué contiene el artefacto del modelo

`modelo.npz` tiene sólo arreglos numéricos de probabilidades y cuantiles; en el mock pesa unos 200 KB:

| Arreglo | Forma | Contenido |
|---|---|---|
| `if_codes` | (n_if,) | códigos de las IF |
| `p_seg`, `p_nif` | (2,), (2, 12) | segmento y número de IF por deudor |
| `if_share`, `if_cooc` | (2, n_if), (2, n_if, n_if) | participación y co-ocurrencia de IF |
| `patterns`, `p_pattern` | (n_pat,), (2, n_if, n_pat) | vocabulario de patrones de productos y su probabilidad |
| `p_mora` | (2, 40, 10) | mora máxima inicial por segmento y slot |
| `prof_mask`, `prof_p`, `prof_alpha` | (20, 10, 8[,10]) | perfiles de mora y parámetros Dirichlet |
| `q_amount` | (2, 40, 10, 201) | cuantiles de log(monto) |
| `rho` | (2,) | correlación del factor de tamaño |
| `p_exit_debtor`, `entry_rate` | (2,) | salida y entrada de deudores |
| `p_exit_cell` | (2, 20, 10) | baja de celdas |
| `trans` | (20, 10, 10) | conteos de transición de mora por tipo de crédito |
| `q_growth` | (20, 10, 10, 201) | cuantiles de crecimiento de montos |
| `lam_new`, `p_new_same_if`, `p_new_slot`, `p_new_mora` | | altas de celdas |

`modelo.json` guarda los metadatos del ajuste: periodos usados, parámetros, resumen de la data (filas, filas inválidas descartadas), cobertura de patrones, tasas de la dinámica y cuántas tablas de montos usaron fallback. **No contiene RUT, filas ni hashes de RUT.**

## GPU

El muestreo de `generate` está vectorizado en PyTorch. Sus operaciones son búsquedas en CDF (`searchsorted`), interpolación de cuantiles, Dirichlet vía gamma y bincount. Corre por *chunks* de deudores. La semilla de cada chunk es fija (`semilla*1000 + chunk`), así que el resultado es reproducible en un mismo dispositivo. CPU y GPU pueden diferir en los últimos decimales, y por eso en las filas exactas.

`evaluate` también usa la GPU para la búsqueda de vecinos más cercanos de las métricas de privacidad. Sin CUDA, todo corre en CPU: 1 millón de deudores × 12 meses (unos 30 millones de filas) toma unos 40 s en el CPU de desarrollo.

## Limitaciones conocidas

- Los deudores que entran se generan con el modelo de la foto, ajustado sobre el stock. Los entrantes reales probablemente tienen menos productos y mora 0.
- El perfil de mora de una celda (qué códigos coexisten) se vuelve a sortear cada mes, sin memoria del mes anterior. La mora máxima sí tiene memoria (Markov).
- **No hay privacidad diferencial formal.** La protección viene de la supresión k, el recorte de colas y el modelo de sólo agregados, y se verifica empíricamente (doc. 6 y 7).
- Se asume estacionariedad: todos los periodos se agrupan para ajustar los parámetros. No hay estacionalidad ni tendencia macro.
- La IF y el producto de una celda nueva se eligen sin mirar la historia del deudor, salvo por la preferencia por una IF que ya tiene.
