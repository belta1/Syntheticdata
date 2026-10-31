"""Tablas condicionales (categóricas y de cuantiles) con supresión k y fallback jerárquico.

Cada tabla se define sobre una grilla de claves enteras (p.ej. seg x slot x mora). Para cada celda
de la grilla se usa el primer nivel de la jerarquía cuyo grupo tenga al menos `k_grupo` observaciones.
"""

import itertools

import numpy as np
import polars as pl


def _level_lookup(dims: list[str], level: list[str]):
    pos = [dims.index(k) for k in level]
    return lambda idx: tuple(idx[p] for p in pos)


def prob_table(
    counts: pl.DataFrame,
    dims: list[str],
    shape: tuple[int, ...],
    cat: str,
    n_cat: int,
    levels: list[list[str]],
    k_grupo: int,
    k_min: int,
) -> tuple[np.ndarray, np.ndarray]:
    """counts: columnas dims + [cat, 'n']. Devuelve (probs con shape+(n_cat,), nivel usado por celda)."""
    counts = counts.filter(pl.col("n") >= k_min)
    out = np.zeros(shape + (n_cat,))
    nivel = np.full(shape, -1, dtype=np.int8)
    tablas = []
    for level in levels:
        agg = counts.group_by(level + [cat]).agg(pl.col("n").sum()) if level else counts.group_by(cat).agg(pl.col("n").sum())
        d: dict[tuple, np.ndarray] = {}
        for row in agg.iter_rows(named=True):
            key = tuple(row[k] for k in level)
            d.setdefault(key, np.zeros(n_cat))[row[cat]] += row["n"]
        tablas.append((_level_lookup(dims, level), d))
    for idx in itertools.product(*[range(s) for s in shape]):
        for li, (lookup, d) in enumerate(tablas):
            v = d.get(lookup(idx))
            if v is not None and (v.sum() >= k_grupo or li == len(tablas) - 1):
                out[idx] = v
                nivel[idx] = li
                break
    return out, nivel


def _sorted_groups(df: pl.DataFrame, keys: list[str], col: str):
    d = df.select(keys + [col]).sort(keys + [col]) if keys else df.select(col).sort(col)
    vals = d[col].to_numpy().astype(np.float64)
    if keys:
        g = d.group_by(keys, maintain_order=True).agg(pl.len().alias("n"))
        ns = g["n"].to_numpy().astype(np.int64)
        claves = list(g.select(keys).iter_rows())
    else:
        ns = np.array([len(vals)], dtype=np.int64)
        claves = [()]
    starts = np.concatenate([[0], np.cumsum(ns)[:-1]]).astype(np.int64)
    return vals, claves, starts, ns


def group_quantiles(df: pl.DataFrame, keys: list[str], col: str, probs: np.ndarray, k_clip: int) -> dict:
    """Cuantiles por grupo. Para no revelar valores individuales, se recortan al k-ésimo menor/mayor valor."""
    vals, claves, starts, ns = _sorted_groups(df, keys, col)
    out = {}
    for key, s, n in zip(claves, starts, ns):
        if n < max(2 * k_clip, 2):
            continue
        sl = vals[s : s + n]
        q = np.interp(probs * (n - 1), np.arange(n), sl)
        out[tuple(key)] = (int(n), np.clip(q, sl[k_clip - 1], sl[n - k_clip]))
    return out


def quantile_table(
    df: pl.DataFrame,
    dims: list[str],
    shape: tuple[int, ...],
    col: str,
    probs: np.ndarray,
    levels: list[list[str]],
    k_grupo: int,
    k_clip: int,
) -> tuple[np.ndarray, np.ndarray]:
    """Devuelve (Q con shape+(len(probs),), nivel usado por celda). Si ningún nivel alcanza, queda en 0."""
    out = np.zeros(shape + (len(probs),))
    nivel = np.full(shape, -1, dtype=np.int8)
    tablas = [(_level_lookup(dims, lv), group_quantiles(df, lv, col, probs, k_clip)) for lv in levels]
    for idx in itertools.product(*[range(s) for s in shape]):
        for li, (lookup, d) in enumerate(tablas):
            v = d.get(lookup(idx))
            if v is not None and (v[0] >= k_grupo or li == len(tablas) - 1):
                out[idx] = v[1]
                nivel[idx] = li
                break
    return out, nivel
