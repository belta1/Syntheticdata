"""Normalización de la data D10 a un esquema canónico y tablas derivadas (celdas)."""

from dataclasses import replace
from pathlib import Path

import numpy as np
import polars as pl

from d10synth import schema
from d10synth.config import Config

INT_TYPES = (pl.Int8, pl.Int16, pl.Int32, pl.Int64, pl.UInt8, pl.UInt16, pl.UInt32, pl.UInt64)
CELL_KEYS = ["periodo", "rut", "seg", "if_id", "slot"]


def scan_parquet_dir(path: str | Path) -> pl.LazyFrame:
    path = Path(path)
    archivos = sorted(path.glob("*.parquet")) if path.is_dir() else [path]
    if not archivos:
        raise FileNotFoundError(f"No hay archivos parquet en {path}")
    return pl.scan_parquet([str(a) for a in archivos])


def periodo_expr(dtype: pl.DataType, fmt: str) -> pl.Expr:
    c = pl.col("periodo")
    if dtype == pl.Date:
        return c
    if isinstance(dtype, pl.Datetime):
        return c.dt.date()
    s = c.cast(pl.Utf8).str.replace_all(r"[^0-9]", "")
    # "2024-01-31 00:00:00.000" -> "20240131000000000": la fecha son los primeros 8 dígitos
    s = pl.when(s.str.len_chars() > 8).then(s.str.slice(0, 8)).otherwise(s)
    ymd = s.str.strptime(pl.Date, "%Y%m%d", strict=False)
    ym = (s + "01").str.strptime(pl.Date, "%Y%m%d", strict=False).dt.month_end()
    if fmt == "yyyymmdd":
        return ymd
    if fmt == "yyyymm":
        return ym
    return pl.when(s.str.len_chars() == 6).then(ym).otherwise(ymd)


def rut_expr(dtype: pl.DataType, fmt: str) -> pl.Expr:
    c = pl.col("rut")
    if dtype in INT_TYPES:
        return (c // 10 if fmt == "con_dv" else c).cast(pl.Int64)
    s = c.cast(pl.Utf8).str.replace_all(r"[.\s]", "")
    con_guion = s.str.contains("-")
    antes_guion = s.str.split("-").list.first()
    if fmt == "con_dv":
        num = pl.when(con_guion).then(antes_guion).otherwise(s.str.slice(0, s.str.len_chars() - 1))
    else:
        num = pl.when(con_guion).then(antes_guion).otherwise(s)
    return num.cast(pl.Int64, strict=False)


def normalize(lf: pl.LazyFrame, cfg: Config, solo_cierre_mes: bool = True) -> pl.LazyFrame:
    """Renombra a columnas canónicas, parsea periodo/RUT, deriva segmento y filtra cierres de mes."""
    mapa = cfg.columnas.mapa()
    lf = lf.rename({k: v for k, v in mapa.items() if k != v and k in lf.collect_schema().names()})
    faltan = set(schema.COLS) - set(lf.collect_schema().names())
    if faltan:
        raise ValueError(f"Faltan columnas en la data: {sorted(faltan)} (revise la sección 'columnas' del YAML)")
    sch = lf.collect_schema()
    lf = lf.select(
        pl.col("if_id").cast(pl.Int64, strict=False),
        periodo_expr(sch["periodo"], cfg.formato.periodo).alias("periodo"),
        rut_expr(sch["rut"], cfg.formato.rut).alias("rut"),
        pl.col("tipo_deudor").cast(pl.Int16, strict=False),
        pl.col("tipo_credito").cast(pl.Int16, strict=False),
        pl.col("tipo_mora").cast(pl.Int16, strict=False),
        pl.col("monto").cast(pl.Float64, strict=False),
    )
    if solo_cierre_mes:
        lf = lf.filter(pl.col("periodo") == pl.col("periodo").dt.month_end())
    return lf.with_columns(
        (pl.col("rut") >= cfg.formato.umbral_rut_empresa).cast(pl.Int8).alias("seg")
    )


def cfg_real(cfg: Config, path: str | Path | None = None) -> Config:
    """La data importada (staging canónico, ver io/staging.py) tiene formato fijo: periodo date y RUT sin DV."""
    from d10synth.io.staging import es_canonico

    if es_canonico(path or cfg.rutas.real):
        return replace(cfg, formato=replace(cfg.formato, periodo="date", rut="sin_dv"))
    return cfg


def load_real(cfg: Config, path: str | Path | None = None) -> pl.LazyFrame:
    return normalize(scan_parquet_dir(path or cfg.rutas.real), cfg_real(cfg, path))


def holdout_expr(pct: float) -> pl.Expr:
    """Asignación determinística y estable de deudores a holdout (hash multiplicativo del RUT)."""
    h = (pl.col("rut") * 2654435761) % 4294967296
    return (h % 10000) < int(round(pct * 100))


def valid_expr() -> pl.Expr:
    return (
        pl.col("rut").is_not_null()
        & pl.col("periodo").is_not_null()
        & pl.col("if_id").is_not_null()
        & pl.col("tipo_deudor").is_in(list(schema.TIPOS_DEUDOR))
        & pl.col("tipo_credito").is_in(schema.TC_CODES)
        & pl.col("tipo_mora").is_between(0, schema.N_MORA - 1)
        & (pl.col("monto") > 0)
    )


def regla_persona_expr() -> pl.Expr:
    """True si la fila viola la regla 'empresa no puede tener productos de persona natural'."""
    return (pl.col("seg") == schema.SEG_EMPRESA) & pl.col("tipo_credito").is_in(sorted(schema.SOLO_PERSONA))


def with_slot(lf: pl.LazyFrame) -> pl.LazyFrame:
    tc_idx = pl.col("tipo_credito").replace_strict(schema.TC_INDEX, return_dtype=pl.Int16)
    return lf.with_columns(
        tc_idx.alias("tc_idx"),
        ((pl.col("tipo_deudor") - 1) * schema.N_TC + tc_idx).cast(pl.Int16).alias("slot"),
    )


def build_cells(rows: pl.LazyFrame) -> tuple[pl.DataFrame, pl.DataFrame]:
    """Agrega filas válidas a celdas (deudor, IF, tipo_deudor, tipo_credito) por periodo.

    Devuelve (celdas, filas_por_mora). En celdas: mora = mora máxima, monto = total,
    pmask = bitmask de los códigos de mora presentes, n_mora = cantidad de códigos.
    """
    rows = with_slot(rows)
    por_mora = (
        rows.group_by(CELL_KEYS + ["tc_idx", "tipo_mora"])
        .agg(pl.col("monto").sum())
        .collect()
    )
    celdas = por_mora.group_by(CELL_KEYS + ["tc_idx"]).agg(
        pl.col("tipo_mora").max().alias("mora"),
        pl.col("monto").sum(),
        pl.lit(2).pow(pl.col("tipo_mora")).sum().cast(pl.Int32).alias("pmask"),
        pl.len().cast(pl.Int16).alias("n_mora"),
    )
    return celdas, por_mora


def month_next(p: pl.Expr) -> pl.Expr:
    return p.dt.offset_by("1d").dt.month_end()


def consecutive_periods(periodos: list) -> list[tuple]:
    """Pares (t, t+1) de cierres de mes consecutivos presentes en la data."""
    ps = sorted(periodos)
    s = pl.Series(ps, dtype=pl.Date)
    sig = s.dt.offset_by("1d").dt.month_end().to_list()
    presentes = set(ps)
    return [(a, b) for a, b in zip(ps, sig) if b in presentes]


def index_if(codigos: np.ndarray) -> pl.DataFrame:
    return pl.DataFrame({"if_id": np.asarray(codigos, dtype=np.int64), "if_idx": np.arange(len(codigos), dtype=np.int32)})
