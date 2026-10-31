"""Validación de reglas del D10 sobre data canónica (real o sintética)."""

import polars as pl

from d10synth import schema
from d10synth.prep import consecutive_periods, regla_persona_expr, valid_expr

ROW_KEYS = ["periodo", "rut", "if_id", "tipo_deudor", "tipo_credito", "tipo_mora"]
CELL_KEYS = ["rut", "if_id", "tipo_deudor", "tipo_credito"]


def _transiciones(df: pl.DataFrame) -> tuple[int, int]:
    pares = consecutive_periods(df["periodo"].unique().to_list())
    if not pares:
        return 0, 0
    mapa = pl.DataFrame({"periodo": [p for p, _ in pares], "pn": [q for _, q in pares]})
    cel = df.group_by(["periodo"] + CELL_KEYS).agg(pl.col("tipo_mora").max().alias("m"))
    t = cel.join(mapa, on="periodo").join(
        cel.select(pl.col("periodo").alias("pn"), *CELL_KEYS, pl.col("m").alias("m1")), on=["pn"] + CELL_KEYS
    )
    ok = schema.MORA_PERMITIDA[t["m"].to_numpy(), t["m1"].to_numpy()]
    return int((~ok).sum()), t.height


def validar(df: pl.DataFrame, rut_original: pl.Series | None = None) -> dict[str, dict[str, int]]:
    """df en esquema canónico (prep.normalize). rut_original: columna RUT cruda para validar DV, si viene con DV."""
    n = df.height
    validas = df.filter(valid_expr())
    contingente = pl.col("tipo_credito").is_in(sorted(schema.CONTINGENTES))
    out = {
        "codigos_o_monto_invalidos": {"violaciones": n - validas.height, "revisadas": n},
        "contingente_con_mora": {
            "violaciones": validas.filter(contingente & (pl.col("tipo_mora") > 0)).height,
            "revisadas": validas.filter(contingente).height,
        },
        "empresa_con_producto_persona": {
            "violaciones": validas.filter(regla_persona_expr()).height,
            "revisadas": validas.filter(pl.col("seg") == schema.SEG_EMPRESA).height,
        },
        "filas_duplicadas": {"violaciones": n - df.select(ROW_KEYS).n_unique(), "revisadas": n},
        "monto_no_entero": {"violaciones": validas.filter(pl.col("monto") != pl.col("monto").round()).height, "revisadas": validas.height},
    }
    v, r = _transiciones(validas)
    out["transicion_mora_no_permitida"] = {"violaciones": v, "revisadas": r}
    if rut_original is not None and rut_original.dtype == pl.Utf8:
        s = rut_original.str.replace_all(r"[.\s]", "").str.to_uppercase()
        partes = s.str.split("-")
        con_dv = partes.list.len() == 2
        num = partes.list.first().cast(pl.Int64, strict=False).filter(con_dv)
        dv = partes.list.last().filter(con_dv).to_numpy()
        out["rut_dv_invalido"] = {"violaciones": int((schema.rut_dv(num.fill_null(0).to_numpy()) != dv).sum()), "revisadas": len(dv)}
    return out


def todas_ok(resultado: dict) -> bool:
    return all(v["violaciones"] == 0 for v in resultado.values())


def resumen_texto(resultado: dict) -> str:
    lineas = []
    for regla, v in resultado.items():
        pct = 100 * v["violaciones"] / v["revisadas"] if v["revisadas"] else 0.0
        estado = "OK " if v["violaciones"] == 0 else "!! "
        lineas.append(f"{estado}{regla:32s} {v['violaciones']:>12,} / {v['revisadas']:>12,}  ({pct:.3f}%)")
    return "\n".join(lineas)
