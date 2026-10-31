"""Ajuste del modelo jerárquico completo sobre la data real (sólo deudores de entrenamiento)."""

import logging
import time
from dataclasses import asdict

import numpy as np
import polars as pl

from d10synth import schema
from d10synth._torch import normal_grid
from d10synth.config import Config
from d10synth.model import amounts, dynamics, snapshot
from d10synth.model.artifact import Modelo, save
from d10synth.prep import build_cells, holdout_expr, index_if, load_real, regla_persona_expr, valid_expr

log = logging.getLogger(__name__)


def prepare_training(cfg: Config, lf: pl.LazyFrame | None = None) -> tuple[pl.DataFrame, pl.DataFrame, dict]:
    """Filtra holdout y filas inválidas, corrige mora de contingentes y arma celdas."""
    lf = load_real(cfg) if lf is None else lf
    lf = lf.filter(~holdout_expr(cfg.ajuste.holdout_pct))
    contingente = pl.col("tipo_credito").is_in(sorted(schema.CONTINGENTES))
    resumen = lf.select(
        pl.len().alias("filas"),
        (~valid_expr()).sum().alias("filas_invalidas"),
        (valid_expr() & regla_persona_expr()).sum().alias("filas_empresa_producto_persona"),
        (valid_expr() & contingente & (pl.col("tipo_mora") > 0)).sum().alias("contingentes_con_mora_corregidos"),
    ).collect().to_dicts()[0]
    rows = lf.filter(valid_expr() & ~regla_persona_expr()).with_columns(
        pl.when(contingente).then(0).otherwise(pl.col("tipo_mora")).cast(pl.Int16).alias("tipo_mora")
    )
    celdas, por_mora = build_cells(rows)
    return celdas, por_mora, resumen


def fit(cfg: Config, lf: pl.LazyFrame | None = None) -> Modelo:
    a = cfg.ajuste
    t0 = time.time()
    celdas, por_mora, resumen = prepare_training(cfg, lf)
    if celdas.is_empty():
        raise ValueError("No quedan celdas válidas para ajustar")
    if_codes = np.sort(celdas["if_id"].unique().to_numpy())
    celdas = celdas.join(index_if(if_codes), on="if_id")
    n_if = len(if_codes)
    log.info("celdas: %s, IFs: %s (%.1fs)", f"{celdas.height:,}", n_if, time.time() - t0)

    g, probs = normal_grid(a.n_cuantiles, a.cuantil_min, a.cuantil_max)
    pairs = celdas.group_by("periodo", "rut", "seg", "if_idx").agg(
        pl.lit(2.0).pow(pl.col("slot")).sum().cast(pl.Int64).alias("pattern")
    )

    arrays: dict[str, np.ndarray] = {"if_codes": if_codes.astype(np.int64)}
    arrays |= snapshot.fit_debtors(celdas, n_if, a)
    arrays |= snapshot.fit_institutions(pairs, n_if, a)
    pat, meta_pat = snapshot.fit_patterns(pairs, n_if, a)
    arrays |= pat
    arrays |= snapshot.fit_mora(celdas, a)
    arrays |= snapshot.fit_profiles(celdas, por_mora, a)
    log.info("foto ajustada (%.1fs)", time.time() - t0)
    arrays["q_amount"], nivel_amt = amounts.fit_amount_quantiles(celdas, probs, a.k_grupo, a.k_min)
    arrays["rho"] = amounts.fit_rho(celdas)
    log.info("montos ajustados (%.1fs)", time.time() - t0)
    dyn, meta_dyn = dynamics.fit_dynamics(celdas, a, probs)
    arrays |= dyn
    log.info("dinámica ajustada (%.1fs)", time.time() - t0)

    periodos = sorted(celdas["periodo"].unique().to_list())
    meta = {
        "version": 1,
        "periodos": [str(p) for p in periodos],
        "n_if": n_if,
        "g_min": float(g[0]),
        "g_max": float(g[-1]),
        "umbral_rut_empresa": cfg.formato.umbral_rut_empresa,
        "ajuste": asdict(a),
        "resumen_datos": resumen | {
            "celdas": celdas.height,
            "deudores_periodo": celdas.select("periodo", "rut").n_unique(),
        },
        "patrones": meta_pat,
        "dinamica": meta_dyn,
        "rho": arrays["rho"].round(4).tolist(),
        "montos_nivel_fallback": {str(k): int(v) for k, v in zip(*np.unique(nivel_amt, return_counts=True))},
        "segundos_ajuste": round(time.time() - t0, 1),
    }
    return Modelo(arrays, meta)


def fit_and_save(cfg: Config) -> Modelo:
    modelo = fit(cfg)
    save(modelo, cfg.rutas.modelo)
    return modelo
