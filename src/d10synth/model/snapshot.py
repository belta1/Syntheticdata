"""Modelo de la foto de un cierre de mes: deudores, IFs, patrones de productos, mora y perfiles de mora."""

import numpy as np
import polars as pl

from d10synth import schema
from d10synth.config import Ajuste
from d10synth.model.tables import prob_table


def _bincount_k(values: np.ndarray, size: int, k_min: int, weights=None) -> np.ndarray:
    c = np.bincount(values, weights=weights, minlength=size)[:size].astype(np.float64)
    c[c < k_min] = 0
    return c


def fit_debtors(celdas: pl.DataFrame, n_if: int, a: Ajuste) -> dict:
    deb = celdas.group_by("periodo", "rut", "seg").agg(pl.col("if_idx").n_unique().alias("n_if"))
    seg = deb["seg"].to_numpy()
    cap = min(a.max_if_por_deudor, n_if)
    nif = np.minimum(deb["n_if"].to_numpy(), cap) - 1
    p_nif = np.stack([_bincount_k(nif[seg == s], cap, a.k_min) for s in (0, 1)])
    return {"p_seg": np.bincount(seg, minlength=2)[:2].astype(np.float64), "p_nif": p_nif}


def fit_institutions(pairs: pl.DataFrame, n_if: int, a: Ajuste) -> dict:
    """Participación de cada IF por segmento y co-ocurrencia (deudores que operan con ambas)."""
    share = np.zeros((2, n_if))
    for s in (0, 1):
        share[s] = _bincount_k(pairs.filter(pl.col("seg") == s)["if_idx"].to_numpy(), n_if, a.k_min)
    p = pairs.select("periodo", "rut", "seg", "if_idx")
    multi = p.filter(pl.len().over("periodo", "rut") >= 2)
    co = (
        multi.join(multi.select("periodo", "rut", pl.col("if_idx").alias("if_b")), on=["periodo", "rut"])
        .filter(pl.col("if_idx") != pl.col("if_b"))
        .group_by("seg", "if_idx", "if_b")
        .agg(pl.len().alias("n"))
        .filter(pl.col("n") >= a.k_min)
    )
    cooc = np.zeros((2, n_if, n_if))
    cooc[co["seg"].to_numpy(), co["if_idx"].to_numpy(), co["if_b"].to_numpy()] = co["n"].to_numpy()
    return {"if_share": share, "if_cooc": cooc}


def fit_patterns(pairs: pl.DataFrame, n_if: int, a: Ajuste) -> tuple[dict, dict]:
    """Patrón de productos por (deudor, IF) como bitmask de slots, sobre un vocabulario de patrones frecuentes."""
    por_seg = pairs.group_by("seg", "pattern").agg(pl.len().alias("n")).filter(pl.col("n") >= a.k_min)
    vocab = (
        por_seg.sort("n", descending=True)
        .group_by("seg", maintain_order=True)
        .head(a.max_patrones)["pattern"]
        .unique()
        .sort()
        .to_numpy()
    )
    voc = pl.DataFrame({"pattern": vocab, "pat_idx": np.arange(len(vocab), dtype=np.int32)})
    counts = (
        pairs.join(voc, on="pattern")
        .group_by("seg", "if_idx", "pat_idx")
        .agg(pl.len().alias("n"))
    )
    p_pattern, nivel = prob_table(
        counts, ["seg", "if_idx"], (2, n_if), "pat_idx", len(vocab),
        [["seg", "if_idx"], ["seg"]], a.k_grupo, a.k_min,
    )
    cob = (
        pairs.join(voc, on="pattern", how="left")
        .group_by("seg")
        .agg(pl.col("pat_idx").is_not_null().mean().alias("c"))
    )
    cobertura = {schema.SEGMENTOS[s]: float(c) for s, c in cob.iter_rows()}
    meta = {"n_patrones": int(len(vocab)), "cobertura_patrones": cobertura, "if_con_patron_propio": int((nivel == 0).sum())}
    return {"patterns": vocab.astype(np.int64), "p_pattern": p_pattern}, meta


def fit_mora(celdas: pl.DataFrame, a: Ajuste) -> dict:
    counts = celdas.group_by("seg", "slot", "mora").agg(pl.len().alias("n"))
    p_mora, _ = prob_table(
        counts, ["seg", "slot"], (2, schema.N_SLOTS), "mora", schema.N_MORA,
        [["seg", "slot"], ["slot"], []], a.k_grupo, a.k_min,
    )
    for slot in range(schema.N_SLOTS):
        if schema.slot_tc_idx(slot) in schema.TC_CONTINGENTE_IDX:
            p_mora[:, slot] = 0
            p_mora[:, slot, 0] = 1
    return {"p_mora": p_mora}


def fit_profiles(celdas: pl.DataFrame, por_mora: pl.DataFrame, a: Ajuste) -> dict:
    """Perfil de mora de una celda: qué códigos de mora coexisten dado (tipo_credito, mora máxima),
    y cómo se reparte el monto entre ellos (Dirichlet por momentos)."""
    nprof = a.max_perfiles_mora
    masks = np.zeros((schema.N_TC, schema.N_MORA, nprof), dtype=np.int32)
    probs = np.zeros((schema.N_TC, schema.N_MORA, nprof))
    alpha = np.ones((schema.N_TC, schema.N_MORA, nprof, schema.N_MORA))

    counts = (
        celdas.group_by("tc_idx", "mora", "pmask").agg(pl.len().alias("n"))
        .filter(pl.col("n") >= a.k_min)
        .sort("n", descending=True)
    )
    top: dict[tuple, list] = {}
    for tc, m, pm, n in counts.iter_rows():
        lst = top.setdefault((tc, m), [])
        if len(lst) < nprof:
            lst.append((pm, n))

    keys = ["periodo", "rut", "seg", "if_id", "slot"]
    multi = celdas.filter(pl.col("n_mora") > 1).select(keys + ["pmask", pl.col("monto").alias("total")])
    shares = (
        por_mora.join(multi, on=keys)
        .with_columns((pl.col("monto") / pl.col("total")).alias("share"))
        .group_by("tc_idx", "pmask", "tipo_mora")
        .agg(pl.col("share").mean().alias("mu"), pl.col("share").var().alias("var"), pl.len().alias("n"))
        .filter(pl.col("n") >= a.k_min)
    )
    stats: dict[tuple, dict] = {}
    for tc, pm, cod, mu, var, n in shares.iter_rows():
        stats.setdefault((tc, pm), {})[cod] = (mu, var)

    for tc in range(schema.N_TC):
        contingente = tc in schema.TC_CONTINGENTE_IDX
        for m in range(schema.N_MORA):
            unico = 1 << m
            lst = [(1, 1)] if contingente else list(top.get((tc, m), []))
            if not contingente and unico not in [pm for pm, _ in lst]:
                if len(lst) == nprof:
                    lst = lst[:-1]
                lst.append((unico, max(1, sum(n for _, n in lst)) * 1e-3))
            for j, (pm, n) in enumerate(lst):
                masks[tc, m, j] = pm
                probs[tc, m, j] = n
                st = stats.get((tc, pm))
                if st and len(st) == bin(pm).count("1"):
                    mu = np.array([st.get(c, (0.0, 0.0))[0] for c in range(schema.N_MORA)])
                    s_i = [mu_c * (1 - mu_c) / v - 1 for mu_c, v in st.values() if v and v > 0]
                    s = float(np.clip(np.median(s_i), 0.5, 500)) if s_i else 2.0
                    alpha[tc, m, j] = np.where(mu > 0, np.maximum(mu * s, 1e-2), 1.0)
    return {"prof_mask": masks, "prof_p": probs, "prof_alpha": alpha}
