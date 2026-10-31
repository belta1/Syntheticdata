"""Dinámica mes a mes: salidas/entradas de deudores y celdas, transiciones de mora y evolución de montos."""

import numpy as np
import polars as pl

from d10synth import schema
from d10synth.config import Ajuste
from d10synth.model.tables import prob_table, quantile_table
from d10synth.prep import consecutive_periods

CELL_JOIN = ["pn", "rut", "if_id", "slot"]


def _por_seg(df: pl.DataFrame) -> np.ndarray:
    out = np.zeros(2)
    for s, n in df.group_by("seg").agg(pl.len()).iter_rows():
        out[s] = n
    return out


def default_dynamics(n_q: int) -> dict:
    trans = np.broadcast_to(np.eye(schema.N_MORA), (schema.N_TC, schema.N_MORA, schema.N_MORA)).copy()
    return {
        "p_exit_debtor": np.zeros(2),
        "entry_rate": np.zeros(2),
        "p_exit_cell": np.zeros((2, schema.N_TC, schema.N_MORA)),
        "trans": trans,
        "q_growth": np.zeros((schema.N_TC, schema.N_MORA, schema.N_MORA, n_q)),
        "lam_new": np.zeros(2),
        "p_new_same_if": np.zeros(2),
        "p_new_slot": np.zeros((2, schema.N_SLOTS)),
        "p_new_mora": np.eye(schema.N_MORA)[[0] * schema.N_TC],
    }


def fit_dynamics(celdas: pl.DataFrame, a: Ajuste, probs: np.ndarray) -> tuple[dict, dict]:
    pares = consecutive_periods(celdas["periodo"].unique().to_list())
    out = default_dynamics(len(probs))
    if not pares:
        return out, {"pares_periodos": 0}
    mapa = pl.DataFrame({"periodo": [p for p, _ in pares], "pn": [q for _, q in pares]})

    # Deudores
    deb = celdas.select("periodo", "rut", "seg").unique()
    deb_t = deb.join(mapa, on="periodo")
    cont = deb_t.join(deb.select(pl.col("periodo").alias("pn"), "rut"), on=["pn", "rut"], how="semi")
    n_t, n_cont = _por_seg(deb_t), _por_seg(cont)
    entrantes = (
        deb.join(mapa.select(pl.col("pn").alias("periodo"), pl.col("periodo").alias("pprev")), on="periodo")
        .join(deb.select(pl.col("periodo").alias("pprev"), "rut"), on=["pprev", "rut"], how="anti")
    )
    with np.errstate(invalid="ignore", divide="ignore"):
        out["p_exit_debtor"] = np.nan_to_num(1 - n_cont / n_t)
        out["entry_rate"] = np.nan_to_num(_por_seg(entrantes) / n_t)

    # Celdas de deudores que continúan
    cells_t = celdas.join(mapa, on="periodo").join(cont.select("periodo", "rut"), on=["periodo", "rut"], how="semi")
    cells_t1 = celdas.select(
        pl.col("periodo").alias("pn"), "rut", "if_id", "slot",
        pl.col("mora").alias("mora1"), pl.col("monto").alias("monto1"),
    )
    m = cells_t.join(cells_t1, on=CELL_JOIN, how="left")

    salida = m.group_by("seg", "tc_idx", "mora", pl.col("mora1").is_null().cast(pl.Int8).alias("sale")).agg(pl.len().alias("n"))
    p_sal, _ = prob_table(
        salida, ["seg", "tc_idx", "mora"], (2, schema.N_TC, schema.N_MORA), "sale", 2,
        [["seg", "tc_idx", "mora"], ["tc_idx", "mora"], ["tc_idx"], []], a.k_grupo, a.k_min,
    )
    out["p_exit_cell"] = p_sal[..., 1] / np.maximum(p_sal.sum(-1), 1)

    surv = m.filter(pl.col("mora1").is_not_null())
    tr = surv.group_by("tc_idx", "mora", "mora1").agg(pl.len().alias("n"))
    permitida = pl.Series([bool(schema.MORA_PERMITIDA[i, j]) for i, j in zip(tr["mora"], tr["mora1"])])
    n_viol = int(tr.filter(~permitida)["n"].sum())
    trans, _ = prob_table(
        tr.filter(permitida), ["tc_idx", "mora"], (schema.N_TC, schema.N_MORA), "mora1", schema.N_MORA,
        [["tc_idx", "mora"], ["mora"]], a.k_grupo, a.k_min,
    )
    vacias = trans.sum(-1) == 0
    trans[vacias] = np.eye(schema.N_MORA)[np.nonzero(vacias)[1]]
    trans[schema.TC_CONTINGENTE_IDX] = np.eye(schema.N_MORA)[0]
    out["trans"] = trans

    growth = surv.select("tc_idx", "mora", "mora1", (pl.col("monto1") / pl.col("monto")).log().alias("r"))
    out["q_growth"], _ = quantile_table(
        growth, ["tc_idx", "mora", "mora1"], (schema.N_TC, schema.N_MORA, schema.N_MORA), "r", probs,
        [["tc_idx", "mora", "mora1"], ["tc_idx", "mora"], ["tc_idx"], []], a.k_grupo, a.k_min,
    )

    # Celdas nuevas en deudores que continúan
    nc = celdas.join(cont.select(pl.col("pn").alias("periodo"), "rut"), on=["periodo", "rut"], how="semi")
    previas = cells_t.select(pl.col("pn").alias("periodo"), "rut", "if_id", "slot")
    nuevas = nc.join(previas, on=["periodo", "rut", "if_id", "slot"], how="anti")
    misma_if = nuevas.join(previas.select("periodo", "rut", "if_id").unique(), on=["periodo", "rut", "if_id"], how="semi")
    n_nuevas = _por_seg(nuevas)
    with np.errstate(invalid="ignore", divide="ignore"):
        out["lam_new"] = np.nan_to_num(n_nuevas / n_cont)
        out["p_new_same_if"] = np.nan_to_num(_por_seg(misma_if) / n_nuevas)
    ns = nuevas.group_by("seg", "slot").agg(pl.len().alias("n")).filter(pl.col("n") >= a.k_min)
    p_new_slot = np.zeros((2, schema.N_SLOTS))
    p_new_slot[ns["seg"].to_numpy(), ns["slot"].to_numpy()] = ns["n"].to_numpy()
    out["p_new_slot"] = p_new_slot
    nm = nuevas.group_by("tc_idx", "mora").agg(pl.len().alias("n"))
    p_new_mora, _ = prob_table(
        nm, ["tc_idx"], (schema.N_TC,), "mora", schema.N_MORA, [["tc_idx"], []], a.k_grupo, a.k_min,
    )
    p_new_mora[schema.TC_CONTINGENTE_IDX] = np.eye(schema.N_MORA)[0]
    out["p_new_mora"] = p_new_mora

    meta = {
        "pares_periodos": len(pares),
        "transiciones_no_permitidas_descartadas": n_viol,
        "salida_deudores": out["p_exit_debtor"].round(4).tolist(),
        "entrada_deudores": out["entry_rate"].round(4).tolist(),
        "celdas_nuevas_por_deudor": out["lam_new"].round(4).tolist(),
    }
    return out, meta
