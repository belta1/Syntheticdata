"""Fidelidad estadística: sintético vs real, comparando distribuciones normalizadas (no volúmenes absolutos)."""

import numpy as np
import polars as pl

from d10synth import schema
from d10synth.prep import build_cells, consecutive_periods

QS = np.linspace(0.005, 0.995, 199)


def tvd(real: pl.DataFrame, sint: pl.DataFrame, cols: list[str], peso: str | None = None) -> float:
    """Distancia de variación total entre distribuciones conjuntas de `cols` (0 = idénticas, 1 = disjuntas)."""
    agg = (pl.col(peso).sum() if peso else pl.len()).alias("w")
    a = real.group_by(cols).agg(agg).with_columns(pl.col("w") / pl.col("w").sum())
    b = sint.group_by(cols).agg(agg).with_columns(pl.col("w") / pl.col("w").sum())
    j = a.join(b, on=cols, how="full", coalesce=True).fill_null(0)
    return float(0.5 * (j["w"] - j["w_right"]).abs().sum())


def w1_log(a: np.ndarray, b: np.ndarray) -> float:
    """Wasserstein-1 aproximado entre distribuciones de log(x) vía funciones cuantil."""
    if len(a) < 2 or len(b) < 2:
        return float("nan")
    return float(np.mean(np.abs(np.quantile(np.log(a), QS) - np.quantile(np.log(b), QS))))


def gini(x: np.ndarray) -> float:
    x = np.sort(x)
    n = len(x)
    return float((2 * np.arange(1, n + 1) - n - 1).dot(x) / (n * x.sum())) if n else float("nan")


def top_share(x: np.ndarray, frac: float = 0.01) -> float:
    x = np.sort(x)[::-1]
    k = max(1, int(len(x) * frac))
    return float(x[:k].sum() / x.sum())


def _transiciones(celdas: pl.DataFrame, tc_idx: int | None = None) -> np.ndarray:
    pares = consecutive_periods(celdas["periodo"].unique().to_list())
    m = np.zeros((schema.N_MORA, schema.N_MORA))
    if not pares:
        return m
    c = celdas if tc_idx is None else celdas.filter(pl.col("tc_idx") == tc_idx)
    mapa = pl.DataFrame({"periodo": [p for p, _ in pares], "pn": [q for _, q in pares]})
    t = c.join(mapa, on="periodo").join(
        c.select(pl.col("periodo").alias("pn"), "rut", "if_id", "slot", pl.col("mora").alias("mora1")),
        on=["pn", "rut", "if_id", "slot"],
    ).group_by("mora", "mora1").agg(pl.len())
    m[t["mora"].to_numpy(), t["mora1"].to_numpy()] = t["len"].to_numpy()
    return m


def _dist_trans(a: np.ndarray, b: np.ndarray, k: int) -> float:
    """Distancia media (TVD por fila) entre matrices de transición, en filas con soporte >= k en ambas."""
    ok = (a.sum(1) >= k) & (b.sum(1) >= k)
    if not ok.any():
        return float("nan")
    pa = a[ok] / a[ok].sum(1, keepdims=True)
    pb = b[ok] / b[ok].sum(1, keepdims=True)
    return float(np.mean(0.5 * np.abs(pa - pb).sum(1)))


def _flujos(celdas: pl.DataFrame) -> dict:
    deb = celdas.select("periodo", "rut").unique()
    pares = consecutive_periods(deb["periodo"].unique().to_list())
    if not pares:
        return {"salida_deudores": float("nan"), "entrada_deudores": float("nan")}
    mapa = pl.DataFrame({"periodo": [p for p, _ in pares], "pn": [q for _, q in pares]})
    dt = deb.join(mapa, on="periodo")
    cont = dt.join(deb.select(pl.col("periodo").alias("pn"), "rut"), on=["pn", "rut"], how="semi").height
    nxt = deb.join(mapa.select(pl.col("pn").alias("periodo"), pl.col("periodo").alias("pp")), on="periodo")
    ent = nxt.join(deb.select(pl.col("periodo").alias("pp"), "rut"), on=["pp", "rut"], how="anti").height
    return {"salida_deudores": 1 - cont / dt.height, "entrada_deudores": ent / dt.height}


def fidelidad(real: pl.DataFrame, sint: pl.DataFrame, k: int = 10) -> dict:
    """real y sint en esquema canónico, sólo filas válidas."""
    res: dict = {"filas": {"real": real.height, "sintetico": sint.height}}
    marg = {}
    for cols in (["seg"], ["if_id"], ["tipo_deudor"], ["tipo_credito"], ["tipo_mora"]):
        n = "+".join(cols)
        marg[n] = {"tvd_filas": tvd(real, sint, cols), "tvd_monto": tvd(real, sint, cols, "monto")}
    for cols in (["tipo_credito", "tipo_mora"], ["if_id", "tipo_credito"], ["seg", "tipo_credito"], ["tipo_deudor", "tipo_credito"]):
        n = "+".join(cols)
        marg[n] = {"tvd_filas": tvd(real, sint, cols), "tvd_monto": tvd(real, sint, cols, "monto")}
    res["marginales"] = marg

    w1 = {}
    for tc in schema.TC_CODES:
        a = real.filter(pl.col("tipo_credito") == tc)["monto"].to_numpy()
        b = sint.filter(pl.col("tipo_credito") == tc)["monto"].to_numpy()
        if len(a) >= k and len(b) >= k:
            w1[str(tc)] = w1_log(a, b)
    w1["todos"] = w1_log(real["monto"].to_numpy(), sint["monto"].to_numpy())
    res["w1_log_monto_por_tipo_credito"] = w1

    cr, _ = build_cells(real.lazy())
    cs, _ = build_cells(sint.lazy())
    deudor = {}
    for nombre, c in (("real", cr), ("sintetico", cs)):
        d = c.group_by("periodo", "rut").agg(
            pl.col("if_id").n_unique().alias("n_if"), pl.len().alias("n_celdas"), pl.col("monto").sum().alias("deuda")
        )
        x = d["deuda"].to_numpy()
        deudor[nombre] = {
            "deudores_periodo": d.height,
            "n_if_medio": float(d["n_if"].mean()),
            "celdas_por_deudor": float(d["n_celdas"].mean()),
            "gini_deuda": gini(x),
            "top1pct_deuda": top_share(x, 0.01),
            "celdas_multi_mora": float((c["n_mora"] > 1).mean()),
            **_flujos(c),
        }
        deudor[nombre]["_tablas"] = d
    dr, ds = deudor["real"].pop("_tablas"), deudor["sintetico"].pop("_tablas")
    deudor["tvd_n_if"] = tvd(dr.with_columns(pl.col("n_if").clip(upper_bound=12)), ds.with_columns(pl.col("n_if").clip(upper_bound=12)), ["n_if"])
    deudor["tvd_n_celdas"] = tvd(dr.with_columns(pl.col("n_celdas").clip(upper_bound=30)), ds.with_columns(pl.col("n_celdas").clip(upper_bound=30)), ["n_celdas"])
    deudor["w1_log_deuda_deudor"] = w1_log(dr["deuda"].to_numpy(), ds["deuda"].to_numpy())
    res["deudores"] = deudor

    tr = {"todas": _dist_trans(_transiciones(cr), _transiciones(cs), k)}
    for tc in (1, 2, 3, 7):
        tr[str(tc)] = _dist_trans(_transiciones(cr, schema.TC_INDEX[tc]), _transiciones(cs, schema.TC_INDEX[tc]), k)
    res["transiciones_mora_tvd_media"] = tr
    mr, ms = _transiciones(cr), _transiciones(cs)
    res["_matrices"] = {"real": mr, "sintetico": ms}
    return res
