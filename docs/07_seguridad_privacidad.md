# 7. Seguridad y privacidad

El D10 está protegido por **secreto bancario**: contiene RUT, montos y morosidad de cada deudor. Este documento define qué datos toca `d10synth`, dónde quedan, qué puede salir del PC y cómo se borra todo al terminar.

## Principios

1. **La data real no sale del PC autorizado.** Todo el procesamiento (importación, ajuste, generación y evaluación) ocurre ahí.
2. **Quien desarrolló el código no tiene acceso a la data.** El código se escribió y probó con data ficticia (mock). La comunicación posterior se limita a agregados revisados, como el perfil y la salida de `verify`.
3. **Mínimo necesario.** No se importa el nombre del deudor. Se puede trabajar con una muestra (`muestra_pct`) y con un rango acotado de meses.
4. **Ninguna salida contiene identificadores reales.** El modelo guarda sólo agregados, los RUT sintéticos excluyen todos los reales y los logs no imprimen RUT.

## Inventario de datos

| Artefacto | Ubicación | Contiene datos personales | Clasificación | Puede salir del PC |
|---|---|---|---|---|
| Tabla fuente / archivos D10 / CSV exportados | fuente, `importacion.entrada` | **sí** (RUT, montos; nombre en los archivos D10) | secreto bancario | no |
| Staging | `data/real/*.parquet` | **sí** (RUT sin DV, montos) | secreto bancario | no |
| Manifiesto | `data/real/_manifiesto.json` | no (conteos por mes, hashes de archivos, rutas) | interno | sí, como evidencia |
| Temporales de importación | `data/real/_tmp_*`, `*.tmp` | **sí** | secreto bancario | no (se borran solos; si quedan, bórrelos) |
| Perfil | `reportes/perfil_d10.{md,json}` | no (agregados con supresión de conteos < k) | interno | **sí, tras revisión** |
| Salida de `verify` y `doctor` | consola | no | interno | sí |
| Modelo | `modelos/*/modelo.{npz,json}` | no (probabilidades y cuantiles agregados, colas recortadas) | interno reservado | sólo con aprobación (ver abajo) |
| Data sintética | `data/sintetico/*.parquet` | no (deudores ficticios) | interno | **sólo tras el proceso de liberación** |
| Evaluación | `reportes/evaluacion.{html,md,json}` | no (métricas agregadas) | interno | sí, tras revisión (ver nota) |
| Logs | consola o `reportes/*.log` | no (conteos, tiempos, rutas) | interno | sí |

**Nota sobre la evaluación:** las matrices de transición del reporte (`_matrices` en el JSON) son conteos agregados **sin supresión k**. Una celda poco frecuente de la matriz (por ejemplo, la transición 8 → 9 en un tipo de crédito pequeño) puede tener conteos bajos. Revise antes de compartir el JSON fuera del equipo. El `.md` y el `.html` muestran proporciones.

## Controles incorporados en el software

| Control | Dónde |
|---|---|
| Conexión SQL en modo sólo lectura (`readonly=True`), sólo `SELECT` | `io/extract.py` |
| Contraseña sólo por variable de entorno, nunca en el YAML (no existe la clave) | `config.py`, `io/extract.py` |
| El nombre del deudor de los archivos D10 nunca se decodifica (sólo se leen los bytes 1–10 y los 18 finales) | `io/importar.py` |
| Escritura atómica y verificación de integridad por SHA-256 | `io/staging.py` |
| Holdout del 20% de los deudores, nunca usado en el ajuste | `prep.holdout_expr`, `model/fit.py` |
| Supresión: conteos < `k_min` no entran a ninguna tabla del modelo | `model/tables.py` |
| Fallback jerárquico: grupos con < `k_grupo` observaciones usan una distribución más agregada | `model/tables.py` |
| Recorte de cuantiles extremos al k-ésimo valor más chico o más grande (el máximo de un individuo nunca queda en el modelo) | `model/tables.group_quantiles` |
| El modelo no guarda RUT, filas ni hashes de RUT (hay un test que lo verifica) | `model/artifact.py`, `tests/test_e2e.py` |
| RUT sintéticos: permutación dentro del rango del segmento, con DV válido y excluyendo todos los RUT reales | `generate.RutAllocator` |
| El perfil suprime los conteos < k | `profile.py` |
| `.gitignore` excluye `data/`, `modelos/` y `reportes/` | raíz |

## Controles operativos (responsabilidad del equipo)

- [ ] El PC de procesamiento tiene el disco **cifrado** (BitLocker) y acceso restringido a las personas autorizadas.
- [ ] La carpeta del proyecto está en un disco local, **no** en una carpeta sincronizada (OneDrive, Dropbox) ni en un share de red ampliamente accesible.
- [ ] El login de base de datos es de **sólo lectura** (`db_datareader` o `SELECT` sobre la tabla).
- [ ] Los CSV o archivos D10 copiados para importar se borran de forma segura al terminar la importación.
- [ ] Nadie envía por correo, chat o tickets ningún archivo de `data/real` ni capturas de pantalla con filas reales.
- [ ] Lo que se comparte fuera del equipo (perfil, evaluación) lo revisa antes el responsable de la información.

## Compartir el perfil para afinar el modelo

`reportes/perfil_d10.md` existe para poder pedir ajustes del modelo sin dar acceso a la data. Antes de enviarlo:

1. Ábralo y revise que no haya valores que identifiquen a alguien. El diseño suprime conteos < k, pero conviene una mirada humana.
2. Si alguna IF o algún tipo de crédito son muy pequeños, revise que la combinación de las secciones *por IF* y *cuantiles de monto* no permita inferir montos de un deudor concreto. En ese caso, suba `ajuste.k_min` y vuelva a ejecutar `profile`.
3. Comparta sólo el `.md`. El `.json` tiene lo mismo, más los tipos de columna y el origen.

## Proceso de liberación de la data sintética

La data sintética no contiene deudores reales, pero **imita** a la población real. Antes de usarla fuera del entorno seguro, por ejemplo para entregarla a otro equipo o a un proveedor:

1. **Evaluación aprobada**: se cumplen los criterios bloqueantes de privacidad del [doc. 6](06_evaluacion_aceptacion.md), con 0 RUT reales y 0 montos sobre el máximo real, y las métricas de distancia no son peores que las del holdout.
2. **Revisión de outliers**: revise que el sintético no tenga deudores con combinaciones únicas y reconocibles, como una empresa con un monto excepcional en un producto raro de una IF pequeña. El recorte de colas lo mitiga, pero la revisión es humana.
3. **Uso declarado**: quién la recibe, para qué y por cuánto tiempo.
4. **Aprobación** del responsable de la información, registrada (fecha, versión del modelo, `evaluacion.json` adjunto).
5. **Entrega** sólo de `data/sintetico/*.parquet`, sin el modelo ni los reportes con matrices.

Esta PoC **no ofrece garantías formales de privacidad diferencial**. Si se busca liberar la data públicamente o a terceros externos, se recomienda una evaluación adicional, por ejemplo ataques de inferencia de membresía sobre trayectorias completas, o bien agregar ruido calibrado al modelo.

## El modelo

`modelo.npz` contiene sólo agregados, pero describe la población con bastante detalle: participación de cada IF, distribución de montos por producto y transiciones de mora. **Trátelo como información interna reservada** y no lo saque del entorno seguro sin aprobación. Con el modelo se puede generar cualquier cantidad de data sintética.

## Borrado

Al cerrar la PoC, o cuando ya no se necesite la data real:

```powershell
# 1. borrar staging y temporales
Remove-Item -Recurse -Force data\real
# 2. borrar los exports intermedios, si los hubo
Remove-Item -Recurse -Force E:\export_d10
# 3. sobrescribir el espacio libre del disco (borra los restos recuperables)
cipher /w:E:\
```

Con SSD y BitLocker, `cipher /w` es menos relevante: el cifrado protege los bloques liberados. Igual conviene ejecutarlo si la política lo pide. También puede usarse `sdelete` (Sysinternals) si está autorizado.

Conserve como evidencia `reportes/` (perfil, evaluación, logs), `data/real/_manifiesto.json` (cópielo **antes** del paso 1) y el modelo aprobado.

## Incidentes

Si se sospecha que data real salió del entorno (un archivo de `data/real` enviado por error, una captura con filas reales, un disco extraviado), siga el procedimiento institucional de incidentes de seguridad de la información y notifique al responsable de la información. No intente "corregirlo" borrando evidencia.
