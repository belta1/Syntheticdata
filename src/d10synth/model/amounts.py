"""Montos: funciones cuantil de log(monto) y cópula gaussiana de 'tamaño' del deudor."""

import numpy as np
import polars as pl

from d10synth import schema
from d10synth._torch import ndtri
from d10synth.model.tables import quantile_table

AMT_DIMS = ["seg", "slot", "mora"]
AMT_SHAPE = (2, schema.N_SLOTS, schema.N_MORA)
AMT_LEVELS = [["seg", "slot", "mora"], ["seg", "slot"], ["slot"], ["seg"], []]


def fit_amount_quantiles(celdas: pl.DataFrame, probs: np.ndarray, k_grupo: int, k_clip: int):
    df = celdas.select("seg", "slot", "mora", pl.col("monto").log().alias("lmonto"))
    return quantile_table(df, AMT_DIMS, AMT_SHAPE, "lmonto", probs, AMT_LEVELS, k_grupo, k_clip)


def fit_rho(celdas: pl.DataFrame, max_celdas: int = 30) -> np.ndarray:
    """Correlación latente entre montos de un mismo deudor, por segmento.

    Cada monto se lleva a score normal dentro de su grupo (seg, slot, mora). Si g = rho*z + sqrt(1-rho^2)*e,
    entonces para un deudor con m celdas E[(sum g)^2] = m + m(m-1) rho^2, de donde sale el estimador de momentos.
    """
    grupo = ["seg", "slot", "mora"]
    df = celdas.select(
        "periodo", "rut", "seg",
        ((pl.col("monto").rank("ordinal").over(grupo) - 0.5) / pl.len().over(grupo)).alias("u"),
    )
    df = df.with_columns(pl.Series("g", ndtri(df["u"].to_numpy())))
    s = (
        df.group_by("periodo", "rut", "seg")
        .agg(pl.col("g").sum().alias("S"), pl.len().alias("m"))
        .filter(pl.col("m").is_between(2, max_celdas))
        .group_by("seg")
        .agg(
            (pl.col("S") ** 2 - pl.col("m")).sum().alias("num"),
            (pl.col("m") * (pl.col("m") - 1)).sum().alias("den"),
        )
    )
    rho = np.zeros(2)
    for row in s.iter_rows(named=True):
        if row["den"] > 0:
            rho[row["seg"]] = np.sqrt(np.clip(row["num"] / row["den"], 0.0, 0.98))
    return rho
