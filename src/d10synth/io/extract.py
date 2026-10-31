"""Importación desde SQL Server: cierres de mes de la tabla fuente al staging canónico (un parquet por periodo).

Se ejecuta en el PC con acceso a la base. Es reanudable: los periodos ya importados se saltan.
Sólo hace SELECT; no crea objetos ni escribe en la base.
"""

import logging
import os

import polars as pl
import pyarrow as pa

from d10synth.config import Config
from d10synth.io.staging import Staging, a_canonico, filtro_periodos
from d10synth.prep import periodo_expr

log = logging.getLogger(__name__)


def connection_string(cfg: Config) -> str:
    f = cfg.fuente
    cs = f"DRIVER={{{f.driver}}};SERVER={f.servidor};DATABASE={f.base};"
    if f.trusted_connection:
        cs += "Trusted_Connection=yes;"
    else:
        cs += f"UID={os.environ['D10_USER']};PWD={os.environ['D10_PASSWORD']};"
    if "18" in f.driver:
        # el driver 18 cifra por defecto y exige un certificado de confianza
        cs += "TrustServerCertificate=yes;"
    return cs


def _conectar(cfg: Config):
    import pyodbc

    conn = pyodbc.connect(connection_string(cfg), readonly=True)
    conn.timeout = 0
    return conn


def periodos_cierre_mes(cfg: Config, conn) -> list[tuple]:
    """Pares (valor original de periodo en la tabla, fecha) de los cierres de mes dentro de [desde, hasta]."""
    cur = conn.cursor()
    cur.execute(f"SELECT DISTINCT {cfg.columnas.periodo} FROM {cfg.fuente.tabla}")
    s = pl.Series("periodo", [r[0] for r in cur.fetchall()])
    fechas = pl.DataFrame({"periodo": s}).select(
        pl.col("periodo").alias("original"), periodo_expr(s.dtype, cfg.formato.periodo).alias("fecha")
    ).filter(pl.col("fecha").is_not_null())
    validas = set(filtro_periodos(fechas["fecha"].to_list(), cfg))
    return sorted((o, f) for o, f in fechas.rows() if f in validas)


def consulta(cfg: Config) -> str:
    c = cfg.columnas
    cols = ", ".join([c.if_id, c.periodo, c.rut, c.tipo_deudor, c.tipo_credito, c.tipo_mora, c.monto])
    q = f"SELECT {cols} FROM {cfg.fuente.tabla} WHERE {c.periodo} = ?"
    if cfg.importacion.muestra_pct < 100:
        # muestra estable por deudor: el mismo RUT queda dentro o fuera en todos los periodos
        q += f" AND ABS(CHECKSUM({c.rut})) % 10000 < {int(round(cfg.importacion.muestra_pct * 100))}"
    return q


def _lotes(cur, cfg: Config):
    nombres = [d[0] for d in cur.description]
    while filas := cur.fetchmany(cfg.importacion.batch_filas):
        tabla = pa.Table.from_pydict({nm: [f[i] for f in filas] for i, nm in enumerate(nombres)})
        yield a_canonico(pl.from_arrow(tabla), cfg).collect()


def extraer(cfg: Config, reemplazar: bool = False) -> list:
    f = cfg.fuente
    st = Staging(cfg, {"tipo": "sql", "servidor": f.servidor, "base": f.base, "tabla": f.tabla,
                       "muestra_pct": cfg.importacion.muestra_pct}, reemplazar)
    conn = _conectar(cfg)
    try:
        periodos = periodos_cierre_mes(cfg, conn)
        log.info("%s cierres de mes a importar", len(periodos))
        q = consulta(cfg)
        archivos = []
        for original, fecha in periodos:
            if st.existe(fecha):
                log.info("%s ya importado, se salta", fecha)
                continue
            cur = conn.cursor()
            cur.execute(q, original)
            archivos.append(st.escribir(fecha, _lotes(cur, cfg)))
    finally:
        conn.close()
    return archivos
