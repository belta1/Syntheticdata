"""Utilidad (TSTR): un modelo de deterioro de mora entrenado con sintético, evaluado con real no visto."""

import numpy as np
import polars as pl
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.metrics import roc_auc_score

from d10synth import schema
from d10synth.prep import build_cells, consecutive_periods

FEATURES = ["seg", "if_id", "tipo_deudor", "tc_idx", "mora", "lmonto", "n_celdas", "n_if", "mora_max_deudor", "ldeuda"]
CATEGORICAS = ["if_id", "tc_idx"]


def dataset_deterioro(df: pl.DataFrame) -> pl.DataFrame:
    """Celdas no contingentes presentes en t y t+1; y = 1 si la mora máxima empeora."""
    c, _ = build_cells(df.lazy())
    c = c.filter(~pl.col("tc_idx").is_in(schema.TC_CONTINGENTE_IDX.tolist()))
    deb = c.group_by("periodo", "rut").agg(
        pl.len().alias("n_celdas"), pl.col("if_id").n_unique().alias("n_if"),
        pl.col("mora").max().alias("mora_max_deudor"), pl.col("monto").sum().log().alias("ldeuda"),
    )
    pares = consecutive_periods(c["periodo"].unique().to_list())
    if not pares:
        return pl.DataFrame()
    mapa = pl.DataFrame({"periodo": [p for p, _ in pares], "pn": [q for _, q in pares]})
    t = c.join(mapa, on="periodo").join(
        c.select(pl.col("periodo").alias("pn"), "rut", "if_id", "slot", pl.col("mora").alias("mora1")),
        on=["pn", "rut", "if_id", "slot"],
    ).join(deb, on=["periodo", "rut"])
    return t.with_columns(
        (pl.col("slot") // schema.N_TC + 1).alias("tipo_deudor"),
        pl.col("monto").log().alias("lmonto"),
        (pl.col("mora1") > pl.col("mora")).cast(pl.Int8).alias("y"),
    )


def _xy(d: pl.DataFrame, if_codes: np.ndarray, n: int, seed: int):
    d = d.sample(min(n, d.height), seed=seed) if d.height > n else d
    X = d.select(FEATURES).with_columns(
        pl.col("if_id").replace_strict(dict(zip(if_codes.tolist(), range(len(if_codes)))), default=len(if_codes))
    ).to_numpy().astype(np.float64)
    return X, d["y"].to_numpy()


def utilidad(train: pl.DataFrame, holdout: pl.DataFrame, sint: pl.DataFrame, n: int = 300_000, seed: int = 7) -> dict:
    dt, dh, ds = dataset_deterioro(train), dataset_deterioro(holdout), dataset_deterioro(sint)
    if dt.is_empty() or dh.is_empty() or ds.is_empty():
        return {"nota": "se requieren al menos dos periodos consecutivos"}
    if_codes = np.sort(dt["if_id"].unique().to_numpy())
    Xh, yh = _xy(dh, if_codes, n, seed)
    res = {"tasa_deterioro": {"real_holdout": float(yh.mean()), "sintetico": float(ds["y"].mean())}}
    if yh.min() == yh.max():
        return res | {"nota": "el holdout no tiene ambas clases"}
    cat_mask = [f in CATEGORICAS for f in FEATURES]
    for nombre, d in (("TRTR_real", dt), ("TSTR_sintetico", ds)):
        X, y = _xy(d, if_codes, n, seed)
        if y.min() == y.max():
            res[nombre] = float("nan")
            continue
        clf = HistGradientBoostingClassifier(max_iter=200, categorical_features=cat_mask, random_state=seed)
        clf.fit(X, y)
        res[nombre] = float(roc_auc_score(yh, clf.predict_proba(Xh)[:, 1]))
    res["ratio_TSTR_TRTR"] = res["TSTR_sintetico"] / res["TRTR_real"] if res.get("TRTR_real") else float("nan")
    return res
