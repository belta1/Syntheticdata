"""Privacidad: distancia al registro más cercano (DCR), NNDR y chequeos de RUT/extrapolación.

Se compara cuán cerca están los deudores sintéticos de los reales de entrenamiento versus cuán cerca
están deudores reales que el modelo nunca vio (holdout). Si el sintético está sistemáticamente más
cerca del entrenamiento que el holdout, hay indicios de memorización.
"""

import numpy as np
import polars as pl
import torch

from d10synth import schema
from d10synth._torch import get_device


def vectores_deudor(df: pl.DataFrame) -> pl.DataFrame:
    """Un vector por deudor en el primer periodo disponible: log1p(monto) por tipo de crédito, mora máxima, n IFs."""
    p0 = df["periodo"].min()
    d = df.filter(pl.col("periodo") == p0)
    piv = (
        d.group_by("rut", "tipo_credito").agg(pl.col("monto").sum().log1p().alias("lm"))
        .pivot(on="tipo_credito", index="rut", values="lm")
        .fill_null(0)
    )
    for tc in schema.TC_CODES:
        if str(tc) not in piv.columns:
            piv = piv.with_columns(pl.lit(0.0).alias(str(tc)))
    extra = d.group_by("rut").agg(
        (pl.col("tipo_mora").max() / 9).alias("mora_max"), pl.col("if_id").n_unique().log1p().alias("n_if")
    )
    return piv.join(extra, on="rut").select(["rut"] + [str(tc) for tc in schema.TC_CODES] + ["mora_max", "n_if"])


def _nn(a: torch.Tensor, b: torch.Tensor, batch: int = 1024) -> tuple[np.ndarray, np.ndarray]:
    """Distancias al 1er y 2do vecino más cercano de cada fila de a en b."""
    d1, d2 = [], []
    for i in range(0, a.shape[0], batch):
        # cálculo directo (sin el truco de multiplicación de matrices) para no perder precisión en distancias ~0
        d = torch.cdist(a[i : i + batch], b, compute_mode="donot_use_mm_for_euclid_dist")
        v, _ = torch.topk(d, 2, dim=1, largest=False)
        d1.append(v[:, 0].cpu())
        d2.append(v[:, 1].cpu())
    return torch.cat(d1).numpy(), torch.cat(d2).numpy()


def privacidad(train: pl.DataFrame, holdout: pl.DataFrame, sint: pl.DataFrame, n: int, seed: int, device: str = "auto") -> dict:
    dev = get_device(device)
    vt, vh, vs = (vectores_deudor(x) for x in (train, holdout, sint))
    n_eff = min(n, vt.height, vh.height, vs.height)
    vt_s = vt.sample(min(n, vt.height), seed=seed)
    vh_s = vh.sample(n_eff, seed=seed)
    vs_s = vs.sample(n_eff, seed=seed)
    cols = vt.columns[1:]
    mu = vt_s.select(cols).to_numpy().mean(0)
    sd = vt_s.select(cols).to_numpy().std(0) + 1e-6
    T = lambda x: torch.as_tensor((x.select(cols).to_numpy() - mu) / sd, dtype=torch.float32, device=dev)  # noqa: E731
    bt = T(vt_s)
    s1, s2 = _nn(T(vs_s), bt)
    h1, h2 = _nn(T(vh_s), bt)
    q5 = float(np.quantile(h1, 0.05))
    res = {
        "n_deudores_comparados": int(n_eff),
        "dcr_mediana": {"sintetico_a_train": float(np.median(s1)), "holdout_a_train": float(np.median(h1))},
        "dcr_p5": {"sintetico_a_train": float(np.quantile(s1, 0.05)), "holdout_a_train": q5},
        "frac_sint_bajo_p5_holdout": float((s1 < q5).mean()),
        "frac_copias_exactas": {"sintetico": float((s1 < 1e-6).mean()), "holdout": float((h1 < 1e-6).mean())},
        "nndr_mediana": {
            "sintetico": float(np.median(s1 / np.maximum(s2, 1e-9))),
            "holdout": float(np.median(h1 / np.maximum(h2, 1e-9))),
        },
    }
    reales = pl.concat([train.select("rut"), holdout.select("rut")]).unique()
    res["ruts_sinteticos_que_existen_en_real"] = int(sint.select("rut").unique().join(reales, on="rut", how="semi").height)
    # extrapolación: montos sintéticos por sobre el máximo real del mismo tipo de crédito
    mx = pl.concat([train, holdout]).group_by("tipo_credito").agg(pl.col("monto").max().alias("max_real"))
    res["filas_sint_sobre_max_real"] = int(sint.join(mx, on="tipo_credito").filter(pl.col("monto") > pl.col("max_real")).height)
    return res
