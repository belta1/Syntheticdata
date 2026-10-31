# Documentación de implantación: d10synth (PoC de datos sintéticos D10)

Esta carpeta explica cómo instalar, alimentar con datos reales, operar y evaluar `d10synth` en el PC que tiene acceso a la data del D10. Está escrita para el equipo que hará la implantación: científicos de datos, soporte TI y el responsable de la información.

## Antes de empezar

`d10synth` se desarrolló **sin acceso a la data real**, contra un generador mock con parámetros conocidos. Todo el pipeline está probado de punta a punta sobre ese mock, pero hay cuatro cosas que **sólo se pueden confirmar en el entorno real**:

1. **La conexión a SQL Server.** El driver, los permisos y el tipo real de las columnas `periodo` y `rut_deudor`.
2. **La GPU.** El código usa CUDA si la encuentra, pero nunca se ejecutó sobre una GPU.
3. **El volumen.** Cuántas filas tiene la tabla y cuánta RAM hace falta (la [guía de importación](03_importacion_datos.md#38-dimensionamiento) trae la fórmula).
4. **La calidad del modelo con la data real.** Los números del mock son optimistas, porque el mock sigue supuestos simples.

## Orden de lectura

| # | Documento | Para quién | Contenido |
|---|---|---|---|
| 1 | [Arquitectura](01_arquitectura.md) | todos | Qué hace el sistema, componentes, flujo de datos, qué contiene el modelo |
| 2 | [Instalación](02_instalacion.md) | TI / quien instala | Requisitos, instalación online y offline, GPU, verificación con `doctor` |
| 3 | [Importación de datos](03_importacion_datos.md) | quien carga la data | Los cuatro orígenes (SQL, CSV, parquet, archivos D10), el staging, verificación, dimensionamiento |
| 4 | [Operación](04_operacion.md) | quien ejecuta | Perfil, ajuste, generación y evaluación paso a paso, con entradas, salidas y tiempos |
| 5 | [Configuración](05_configuracion.md) | quien ejecuta | Referencia de cada clave de `config/d10.yaml` |
| 6 | [Evaluación y aceptación](06_evaluacion_aceptacion.md) | todos | Cómo leer el reporte y criterios propuestos para dar la PoC por aprobada |
| 7 | [Seguridad y privacidad](07_seguridad_privacidad.md) | responsable de la información | Qué datos se tocan, qué puede salir del PC, liberación y borrado |
| 8 | [Problemas frecuentes](08_problemas_frecuentes.md) | quien ejecuta | Errores conocidos y su solución |
| 9 | [Checklist de implantación](09_checklist_implantacion.md) | coordinador | Lista de pasos con casillas, de la aprobación al cierre |

## Ruta rápida

Para quien ya conoce el sistema, desde la raíz del proyecto en el PC de destino:

```powershell
.\scripts\instalar.ps1 -Cuda cu124                    # 1. entorno (+ doctor + tests)
# 2. ejecutar sql\01_prechequeo_d10.sql en SSMS y ajustar config\d10.yaml
d10synth --config config/d10.yaml import             # 3. data real -> data/real
d10synth --config config/d10.yaml verify
d10synth --config config/d10.yaml profile            # 4. reportes/perfil_d10.md
d10synth --config config/d10.yaml fit                # 5. modelos/d10
d10synth --config config/d10.yaml generate           # 6. data/sintetico
d10synth --config config/d10.yaml evaluate           # 7. reportes/evaluacion.html
```

## Estructura del proyecto

```
config/
  d10.yaml                 configuración para la data real
  d10_mock.yaml            configuración para desarrollo con mock
docs/                      esta documentación
scripts/
  instalar.ps1             instalación (online u offline) + doctor + tests
  preparar_offline.ps1     arma un wheelhouse en un PC con internet
sql/
  01_prechequeo_d10.sql    consultas agregadas sobre la tabla fuente (sólo lectura)
  02_exportar_bcp.ps1      export a CSV con bcp, por si Python no puede conectarse
src/d10synth/
  cli.py                   comandos: doctor, mock, import, verify, profile, fit, generate, evaluate, validate
  config.py                carga y valida el YAML
  schema.py                catálogos y reglas del manual D10
  doctor.py                diagnóstico del entorno
  io/staging.py            formato canónico de la data importada + manifiesto + verify
  io/extract.py            importación desde SQL Server
  io/importar.py           importación desde CSV, parquet y archivos D10 de ancho fijo
  prep.py                  normalización, celdas, holdout
  profile.py               perfil agregado con supresión k
  model/                   ajuste (foto, montos, dinámica) y artefacto
  generate.py              muestreo en GPU/CPU
  rules.py                 validación de reglas del manual
  evaluate/                fidelidad, privacidad, utilidad y reporte
  mock.py                  D10 falso para desarrollo y tests
tests/                     tests unitarios y de punta a punta sobre mock
d10-manual.txt             especificación del archivo D10 (CMF)
requirements.lock.txt      versiones probadas (sin torch)
```

Las carpetas `data/`, `modelos/` y `reportes/` se crean al ejecutar y **no deben copiarse ni versionarse** (ya están en `.gitignore`).
