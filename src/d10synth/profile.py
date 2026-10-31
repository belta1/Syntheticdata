"""Perfil de la data real con SÓLO agregados (supresión de conteos < k), apto para compartir."""

import json
from pathlib import Path

import numpy as np
import polars as pl

from d10synth import rules, schema
from d10synth.config import Config
from d10synth.model.tables import group_quantiles
from d10synth.io.staging import leer_manifiesto
from d10synth.prep import build_cells, cfg_real, consecutive_periods, normalize, scan_parquet_dir, valid_expr

QUANTILES = [0.01, 0.05, 0.25, 0.5, 0.75, 0.95, 0.99]


def _suprimir(df: pl.DataFrame, k: int, cols=("n",)) -> pl.DataFrame:
    return df.with_columns([pl.when(pl.col(c) < k).then(None).otherwise(pl.col(c)).alias(c) for c in cols])


def _md_tabla(df: pl.DataFrame) -> str:
    cols = df.columns
    filas = ["| " + " | ".join(cols) + " |", "|" + "---|" * len(cols)]
    for r in df.iter_rows():
        filas.append("| " + " | ".join(f"<k" if v is None else (f"{v:,.4g}" if isinstance(v, float) else f"{v:,}" if isinstance(v, int) else str(v)) for v in r) + " |")
    return "\n".join(filas)


def perfilar(cfg: Config) -> dict:
    k = cfg.ajuste.k_min
    raw = scan_parquet_dir(cfg.rutas.real)
    sch = raw.collect_schema()
    lf = normalize(raw, cfg_real(cfg))
    df = lf.collect()
    validas = df.filter(valid_expr())
    secciones: dict[str, pl.DataFrame] = {}

    secciones["por_periodo"] = df.group_by("periodo").agg(
        pl.len().alias("filas"),
        pl.col("rut").n_unique().alias("deudores"),
        pl.col("if_id").n_unique().alias("ifs"),
        (pl.col("monto").sum() / 1e6).alias("monto_MM"),
    ).sort("periodo")
    secciones["deudores_por_segmento"] = df.group_by("periodo", "seg").agg(pl.col("rut").n_unique().alias("n")).group_by("seg").agg(
        pl.col("n").mean().round(0).alias("deudores_promedio_mes")
    ).sort("seg")
    for c in ("tipo_deudor", "tipo_credito", "tipo_mora"):
        secciones[f"por_{c}"] = _suprimir(
            df.group_by(c).agg(pl.len().alias("n"), (pl.col("monto").sum() / 1e6).alias("monto_MM")).sort(c), k
        )
    secciones["por_if"] = _suprimir(
        df.group_by("if_id").agg(pl.len().alias("n"), pl.col("rut").n_unique().alias("deudores"), (pl.col("monto").sum() / 1e6).alias("monto_MM")).sort("if_id"),
        k, ("n", "deudores"),
    )
    secciones["tipo_credito_x_mora"] = _suprimir(
        df.group_by("tipo_credito", "tipo_mora").agg(pl.len().alias("n")), k
    ).pivot(on="tipo_mora", index="tipo_credito", values="n", sort_columns=True).sort("tipo_credito")

    q = group_quantiles(validas.with_columns(pl.col("monto").log().alias("lm")), ["tipo_credito"], "lm", np.array(QUANTILES), k)
    secciones["cuantiles_monto_por_tipo_credito"] = pl.DataFrame(
        [{"tipo_credito": kk[0], "n": n, **{f"p{int(p*100)}": float(np.exp(v)) for p, v in zip(QUANTILES, qs)}} for kk, (n, qs) in sorted(q.items())]
    ) if q else pl.DataFrame()

    celdas, _ = build_cells(validas.lazy())
    deb = celdas.group_by("periodo", "rut", "seg").agg(
        pl.col("if_id").n_unique().alias("n_if"), pl.len().alias("n_celdas")
    )
    for c, cap in (("n_if", 12), ("n_celdas", 30)):
        secciones[f"deudores_por_{c}"] = _suprimir(
            deb.group_by("seg", pl.col(c).clip(upper_bound=cap)).agg(pl.len().alias("n")).sort("seg", c), k
        )
    secciones["celdas_por_n_codigos_mora"] = _suprimir(celdas.group_by("n_mora").agg(pl.len().alias("n")).sort("n_mora"), k)

    pares = consecutive_periods(celdas["periodo"].unique().to_list())
    if pares:
        mapa = pl.DataFrame({"periodo": [p for p, _ in pares], "pn": [q_ for _, q_ in pares]})
        t = celdas.join(mapa, on="periodo").join(
            celdas.select(pl.col("periodo").alias("pn"), "rut", "if_id", "slot", pl.col("mora").alias("mora1")),
            on=["pn", "rut", "if_id", "slot"],
        )
        secciones["transiciones_mora_celdas"] = _suprimir(
            t.group_by("mora", "mora1").agg(pl.len().alias("n")), k
        ).pivot(on="mora1", index="mora", values="n", sort_columns=True).sort("mora")

    validacion = rules.validar(df)
    resultado = {
        "tipos_columnas_fuente": {c: str(t) for c, t in sch.items()},
        "origen": (leer_manifiesto(cfg.rutas.real) or {}).get("origen"),
        "k_supresion": k,
        "secciones": {n: s.to_dicts() for n, s in secciones.items()},
        "reglas": validacion,
    }
    out = Path(cfg.rutas.reportes)
    out.mkdir(parents=True, exist_ok=True)
    (out / "perfil_d10.json").write_text(json.dumps(resultado, indent=2, default=str, ensure_ascii=False), encoding="utf-8")
    md = ["# Perfil D10 (sólo agregados)", "", f"Conteos menores a {k} se muestran como `<k`.", "",
          "## Tipos de columnas en la fuente", "", *[f"- `{c}`: {t}" for c, t in resultado["tipos_columnas_fuente"].items()], "",
          f"Origen de la importación: `{json.dumps(resultado['origen'], ensure_ascii=False)}`", ""]
    for nombre, s in secciones.items():
        md += [f"## {nombre.replace('_', ' ')}", "", _md_tabla(s) if s.height else "(vacío)", ""]
    md += ["## Reglas", "", "```", rules.resumen_texto(validacion), "```", ""]
    (out / "perfil_d10.md").write_text("\n".join(md), encoding="utf-8")
    return resultado
