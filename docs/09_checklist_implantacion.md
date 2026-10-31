# 9. Checklist de implantación

Imprima o copie este checklist para la implantación. Cada fase indica qué debe quedar como evidencia. Ninguna evidencia contiene data real.

**Proyecto:** PoC de datos sintéticos D10 · **Versión del código:** d10synth 0.1.0
**Responsable de la información:** ____________ · **Ejecutor:** ____________ · **Fecha de inicio:** ____________

---

## Fase 0: autorizaciones

- [ ] Uso de la data del D10 aprobado por el responsable de la información, con propósito, alcance y plazo.
- [ ] PC de procesamiento definido. Disco cifrado, acceso restringido, y una carpeta local que no esté sincronizada con la nube.
- [ ] Login de base de datos de **sólo lectura** sobre `cmfdat.dbo.D10C`, o acceso a los archivos D10.
- [ ] Criterios de aceptación del [doc. 6](06_evaluacion_aceptacion.md) revisados y aprobados (umbrales finales: ____________).
- [ ] Alcance acordado: meses `desde` ______ `hasta` ______; muestra ______ %.

*Evidencia:* registro de la aprobación.

## Fase 1: preparación del entorno

- [ ] Python 3.12 de 64 bits instalado.
- [ ] (con GPU) Driver NVIDIA instalado; `nvidia-smi` muestra la tarjeta y la versión CUDA ______.
- [ ] (origen `sql`) ODBC Driver 17 u 18 for SQL Server instalado.
- [ ] (origen `csv` vía bcp) SQL Server Command Line Utilities instaladas.
- [ ] Espacio en disco suficiente ([doc. 2](02_instalacion.md)).
- [ ] RAM suficiente para el volumen previsto: ______ millones de filas × 0,7 GiB + 2 GiB = ______ GiB ([doc. 3 §3.8](03_importacion_datos.md#38-dimensionamiento)).

## Fase 2: instalación

- [ ] Proyecto copiado sin `.venv`, `data`, `modelos` ni `reportes`.
- [ ] Instalación: `.\scripts\instalar.ps1 -Cuda ____` o `-Offline`.
- [ ] `d10synth --config config/d10.yaml doctor` sin `ERROR`.
- [ ] `pytest -q`: 27 tests pasados.
- [ ] (con GPU) Prueba con mock en GPU ([doc. 2](02_instalacion.md#3-prueba-con-mock-en-la-gpu-opcional)): `validate` sin violaciones. Tiempo GPU ______ s contra CPU ______ s.

*Evidencia:* salida de `doctor` y de `pytest -q`.

## Fase 3: prechequeo de la fuente

- [ ] `sql/01_prechequeo_d10.sql` ejecutado (o, si el origen son archivos, revisados el layout y la cabecera de una muestra de archivos).
- [ ] Tipo de `periodo`: ______ → `formato.periodo: ______`.
- [ ] Forma del RUT: ______ → `formato.rut: ______`.
- [ ] Nombres de columnas verificados en la sección `columnas`.
- [ ] Existen cierres de mes (último día calendario) en el rango acordado.
- [ ] Índice por `periodo`: sí / no (si no, importar fuera de horario).
- [ ] Filas por mes ≈ ______ → filas a importar ≈ ______ (cuadra con la RAM de la fase 1).
- [ ] `config/d10.yaml` completo ([doc. 5](05_configuracion.md)).

## Fase 4: importación

- [ ] `d10synth --config config/d10.yaml import` terminó sin errores. ______ periodos, ______ minutos.
- [ ] `d10synth --config config/d10.yaml verify` → `OK`.
- [ ] Filas, deudores e IF estables mes a mes; sin meses faltantes (o justificados).
- [ ] Filas inválidas ≪ 1%. (Origen `d10`: `registros_ilegibles` ≈ 0.)
- [ ] (origen `csv` o `d10`) Archivos intermedios borrados de forma segura.

*Evidencia:* salida de `verify` y copia de `data/real/_manifiesto.json`.

## Fase 5: perfil

- [ ] `d10synth --config config/d10.yaml profile`.
- [ ] Deudores por segmento razonables (el umbral de 50M separa bien personas y empresas).
- [ ] Violaciones de reglas en la data real: anotadas.
- [ ] (opcional) `perfil_d10.md` revisado por el responsable y compartido para afinar el modelo.

*Evidencia:* `reportes/perfil_d10.md`.

## Fase 6: ajuste

- [ ] `d10synth --config config/d10.yaml fit`, en ______ s.
- [ ] `cobertura_patrones` > 0,98.
- [ ] `pares_periodos` = meses − 1 (o justificado).
- [ ] Tasas de entrada y salida plausibles.
- [ ] Modelo guardado en una ruta versionada, junto con una copia del YAML usado.

*Evidencia:* resumen impreso por `fit` (o `modelo.json`).

## Fase 7: generación

- [ ] `d10synth --config config/d10.yaml generate --deudores ______ --meses ______`, en ______ s sobre ______ (cuda/cpu).
- [ ] `d10synth --config config/d10.yaml validate data/sintetico` → todas las reglas OK.

## Fase 8: evaluación

- [ ] `d10synth --config config/d10.yaml evaluate`.
- [ ] Reglas: 0 violaciones (bloqueante).
- [ ] Privacidad: 0 RUT reales y 0 montos sobre el máximo real (bloqueante).
- [ ] Privacidad: DCR y NNDR del sintético ≥ 0,9 × holdout; `frac_sint_bajo_p5_holdout` ≤ 0,07.
- [ ] Fidelidad: criterios deseables cumplidos: ______ de ______.
- [ ] Utilidad: ratio TSTR/TRTR = ______ (≥ 0,90).
- [ ] Dictamen: aprobada / aprobada con observaciones / no aprobada.

*Evidencia:* `reportes/evaluacion.html` y `evaluacion.json`.

## Fase 9: liberación (si corresponde)

- [ ] Revisión de outliers del sintético hecha ([doc. 7](07_seguridad_privacidad.md#proceso-de-liberación-de-la-data-sintética)).
- [ ] Destinatario, uso y plazo declarados.
- [ ] Aprobación del responsable registrada (fecha, versión del modelo, `evaluacion.json`).
- [ ] Entregado sólo `data/sintetico/*.parquet`.

## Fase 10: cierre

- [ ] Evidencias archivadas: `doctor`, `pytest`, `verify`, manifiesto, perfil, resumen de `fit`, evaluación y aprobaciones.
- [ ] Modelo aprobado conservado en el entorno seguro (o borrado, según la decisión).
- [ ] `data/real` y todo export intermedio borrados de forma segura ([doc. 7](07_seguridad_privacidad.md#borrado)).
- [ ] Informe de resultados de la PoC: hallazgos de calidad de la data, métricas, limitaciones y próximos pasos.

**Cierre:** ____________ (responsable) · ____________ (ejecutor) · Fecha: ____________
