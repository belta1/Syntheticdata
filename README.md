# d10synth: datos sintéticos D10 (PoC)

Este paquete genera un panel sintético multi-mes del archivo **D10 (Información de deudores, Art. 14 LGB)**, usando solo los cierres de mes. El resultado tiene el mismo esquema que `cmfdat.dbo.D10C`:

`rutis_sbif, periodo, rut_deudor, tipo_deudor, tipo_credito, tipo_mora, monto`

Ningún registro sintético corresponde a un deudor real, y el modelo guardado contiene solo agregados (conteos con supresión k y cuantiles recortados).

## Documentación

La guía completa para implantarlo en el entorno real está en [docs/](docs/README.md). Cubre arquitectura, instalación (también offline), importación de datos, operación, configuración, evaluación, seguridad, problemas frecuentes y un checklist.

## Cómo funciona

El generador es **jerárquico por deudor**. Una *celda* es la combinación (deudor, IF, tipo_deudor, tipo_crédito).

1. **Foto inicial**
   - Segmento (persona o empresa, según el RUT) y número de IF del deudor.
   - IFs, elegidas por participación y co-ocurrencia.
   - Patrón de productos por IF.
   - Mora máxima por celda.
   - Montos, a partir de funciones cuantil condicionales más una cópula gaussiana del "tamaño" del deudor.
2. **Dinámica mensual**
   - Salidas y entradas de deudores.
   - Bajas y altas de celdas.
   - Cadena de Markov de la mora por tipo de crédito, con las transiciones imposibles fijadas en cero.
   - Evolución de los montos según la transición de mora.
3. **Salida**
   - Cada celda se expande a filas por código de mora (perfil de mora más reparto Dirichlet del monto).
   - Se asignan RUTs ficticios con DV válido, excluyendo los RUTs reales.

El muestreo está vectorizado en PyTorch y corre en **GPU** si hay CUDA; si no, en CPU.

## Instalación (Python 3.12)

El script crea `.venv`, instala torch con CUDA y las dependencias fijadas, y luego corre `doctor` y los tests:

```powershell
.\scripts\instalar.ps1 -Cuda cu124
```

O a mano:

```bash
python -m venv .venv
.venv\Scripts\activate
pip install torch --index-url https://download.pytorch.org/whl/cu124
pip install -r requirements.lock.txt   # versiones probadas (torch se instala aparte)
pip install -e ".[sql,dev]"
d10synth doctor
```

Se probó con torch 2.15 (CPU), polars 2.1, numpy 2.5 y Python 3.12. Si el destino no tiene internet, use `scripts/preparar_offline.ps1` (ver [docs/02_instalacion.md](docs/02_instalacion.md)).

## Uso con la data real

Todos los comandos se ejecutan desde la raíz del proyecto. Las rutas del YAML son relativas a esa carpeta.

```bash
d10synth --config config/d10.yaml doctor      # entorno: versiones, GPU, ODBC, disco
d10synth --config config/d10.yaml import      # fuente -> data/real (staging canónico, reanudable)
d10synth --config config/d10.yaml verify      # integridad del staging (hash, esquema, meses faltantes)
d10synth --config config/d10.yaml profile     # reportes/perfil_d10.md (sólo agregados)
d10synth --config config/d10.yaml fit         # modelos/d10/modelo.npz + modelo.json
d10synth --config config/d10.yaml generate --deudores 1000000 --meses 12
d10synth --config config/d10.yaml evaluate    # reportes/evaluacion.html
```

`import` lee desde cuatro orígenes, según `importacion.tipo`:
- `sql`: la tabla de SQL Server;
- `csv`: un export CSV;
- `parquet`: archivos parquet;
- `d10`: los archivos originales de ancho fijo que envían las IF.

Ver [docs/03_importacion_datos.md](docs/03_importacion_datos.md).

En `config/d10.yaml` se configuran:
- conexión, nombres de columnas y formato de `periodo` y `rut` (`auto` los detecta);
- origen de la importación, rango de periodos y `importacion.muestra_pct` (muestra estable de deudores);
- umbrales de supresión y parámetros de generación.

## Desarrollo local con data mock

```bash
d10synth --config config/d10_mock.yaml mock --deudores 50000 --meses 12
d10synth --config config/d10_mock.yaml fit
d10synth --config config/d10_mock.yaml generate
d10synth --config config/d10_mock.yaml evaluate
pytest
```

Para probar los importadores, genere el mock en otro formato con `mock --formato csv|d10 --salida <carpeta>` y luego corra `import` sobre esa carpeta.

## Evaluación

| Eje | Qué mide |
|---|---|
| Reglas | Códigos válidos, contingentes con mora 0, empresas sin productos de persona, sin duplicados, transiciones de mora posibles, DV válido |
| Fidelidad | TVD de marginales y pares, W1 de log(monto) por tipo de crédito, concentración (Gini, top 1%), IFs y celdas por deudor, matrices de transición, tasas de entrada y salida |
| Privacidad | DCR y NNDR (sintético→train vs holdout→train, kNN en GPU), copias exactas, RUTs que existen en real, montos sobre el máximo real |
| Utilidad | TSTR: AUC de un modelo de deterioro de mora entrenado con sintético y evaluado en real holdout, comparado con el entrenado con real |

## Limitaciones de la PoC

- Los deudores que entran se generan con el modelo de la foto, que fue ajustado sobre el stock. Los entrantes reales suelen tener menos productos y mora 0.
- En cada mes, el perfil de mora de una celda (qué códigos coexisten) se vuelve a muestrear según su mora máxima, sin memoria del mes anterior.
- No ofrece privacidad diferencial formal. La protección viene de la supresión k, el recorte de colas y el modelo de solo agregados, y se verifica con las métricas de privacidad.
- Se asume estacionariedad: los parámetros se ajustan agrupando todos los periodos.
