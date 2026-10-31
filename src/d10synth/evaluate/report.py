"""Evaluación completa (reglas, fidelidad, privacidad, utilidad) y reporte en markdown/HTML/JSON."""

import html
import json
import logging
from dataclasses import replace
from datetime import datetime
from pathlib import Path

import numpy as np
import polars as pl

from d10synth import rules, schema
from d10synth.config import Config
from d10synth.evaluate.fidelity import fidelidad
from d10synth.evaluate.privacy import privacidad
from d10synth.evaluate.utility import utilidad
from d10synth.prep import holdout_expr, load_real, normalize, scan_parquet_dir, valid_expr

log = logging.getLogger(__name__)


def cargar(cfg: Config) -> tuple[pl.DataFrame, pl.DataFrame, pl.DataFrame, pl.Series]:
    real = load_real(cfg).filter(valid_expr()).collect()
    ho = real.select(holdout_expr(cfg.ajuste.holdout_pct)).to_series()
    raw = scan_parquet_dir(cfg.rutas.sintetico)
    rut_raw = raw.select(cfg.columnas.rut).collect().to_series()
    cfg_sint = replace(cfg, formato=replace(cfg.formato, rut="auto", periodo="auto"))
    sint = normalize(raw, cfg_sint).collect()
    return real.filter(~ho), real.filter(ho), sint, rut_raw


def evaluar(cfg: Config) -> dict:
    train, holdout, sint_all, rut_raw = cargar(cfg)
    log.info("real train %s, holdout %s, sintético %s filas", f"{train.height:,}", f"{holdout.height:,}", f"{sint_all.height:,}")
    reglas = rules.validar(sint_all, rut_raw)
    sint = sint_all.filter(valid_expr())
    log.info("reglas evaluadas")
    fid = fidelidad(train, sint, cfg.ajuste.k_min)
    log.info("fidelidad evaluada")
    priv = privacidad(train, holdout, sint, cfg.evaluacion.muestra_privacidad, cfg.evaluacion.semilla, cfg.generacion.dispositivo)
    log.info("privacidad evaluada")
    util = utilidad(train, holdout, sint, seed=cfg.evaluacion.semilla)
    log.info("utilidad evaluada")
    res = {"fecha": datetime.now().isoformat(timespec="seconds"), "reglas": reglas, "fidelidad": fid, "privacidad": priv, "utilidad": util}
    escribir_reporte(res, Path(cfg.rutas.reportes))
    return res


# ---------------------------------------------------------------------------------------------------
def _fmt(v) -> str:
    if isinstance(v, float):
        return "nan" if np.isnan(v) else f"{v:.4f}"
    if isinstance(v, int):
        return f"{v:,}"
    return str(v)


def _plano(d: dict, pref: str = "") -> list[tuple[str, str]]:
    out = []
    for k, v in d.items():
        if k.startswith("_"):
            continue
        if isinstance(v, dict):
            out += _plano(v, f"{pref}{k}.")
        else:
            out.append((f"{pref}{k}", _fmt(v)))
    return out


def _matriz_md(m: np.ndarray) -> str:
    p = m / np.maximum(m.sum(1, keepdims=True), 1)
    filas = ["| desde \\ hacia | " + " | ".join(str(j) for j in range(schema.N_MORA)) + " |", "|---" * (schema.N_MORA + 1) + "|"]
    for i in range(schema.N_MORA):
        filas.append(f"| {i} | " + " | ".join(f"{p[i, j]:.3f}" if m[i, j] else "·" for j in range(schema.N_MORA)) + " |")
    return "\n".join(filas)


def _secciones(res: dict) -> list[tuple[str, list[tuple[str, str]] | str]]:
    fid = res["fidelidad"]
    return [
        ("Reglas del D10 en el sintético", rules.resumen_texto(res["reglas"])),
        ("Fidelidad: marginales (TVD; 0 = idénticas)", _plano(fid["marginales"])),
        ("Fidelidad: W1 de log(monto) por tipo de crédito", _plano(fid["w1_log_monto_por_tipo_credito"])),
        ("Fidelidad: nivel deudor", _plano(fid["deudores"])),
        ("Fidelidad: transiciones de mora (TVD media por fila)", _plano(fid["transiciones_mora_tvd_media"])),
        ("Matriz de transición real", _matriz_md(fid["_matrices"]["real"])),
        ("Matriz de transición sintética", _matriz_md(fid["_matrices"]["sintetico"])),
        ("Privacidad", _plano(res["privacidad"])),
        ("Utilidad (AUC deterioro de mora, evaluado en real holdout)", _plano(res["utilidad"])),
    ]


def escribir_reporte(res: dict, carpeta: Path) -> None:
    carpeta.mkdir(parents=True, exist_ok=True)
    limpio = json.loads(json.dumps(res, default=lambda o: o.tolist() if isinstance(o, np.ndarray) else str(o)))
    (carpeta / "evaluacion.json").write_text(json.dumps(limpio, indent=2, ensure_ascii=False), encoding="utf-8")

    md = ["# Evaluación de datos sintéticos D10", "", f"Generado: {res['fecha']}", ""]
    cuerpo = []
    for titulo, cont in _secciones(res):
        md += [f"## {titulo}", ""]
        cuerpo.append(f"<h2>{html.escape(titulo)}</h2>")
        if isinstance(cont, str) and cont.startswith("|"):
            md += [cont, ""]
            cuerpo.append(_md_tabla_html(cont))
        elif isinstance(cont, str):
            md += ["```", cont, "```", ""]
            cuerpo.append(f"<pre>{html.escape(cont)}</pre>")
        else:
            md += ["| métrica | valor |", "|---|---|", *[f"| {k} | {v} |" for k, v in cont], ""]
            cuerpo.append("<table><tr><th>métrica</th><th>valor</th></tr>" + "".join(
                f"<tr><td>{html.escape(k)}</td><td class='n'>{html.escape(v)}</td></tr>" for k, v in cont) + "</table>")
    (carpeta / "evaluacion.md").write_text("\n".join(md), encoding="utf-8")
    (carpeta / "evaluacion.html").write_text(_HTML.format(fecha=res["fecha"], cuerpo="\n".join(cuerpo)), encoding="utf-8")


def _md_tabla_html(md: str) -> str:
    filas = [f.strip("|").split("|") for f in md.splitlines() if not f.startswith("|---")]
    cab, *resto = filas
    return "<table><tr>" + "".join(f"<th>{html.escape(c.strip())}</th>" for c in cab) + "</tr>" + "".join(
        "<tr>" + "".join(f"<td class='n'>{html.escape(c.strip())}</td>" for c in f) + "</tr>" for f in resto) + "</table>"


_HTML = """<!doctype html><html lang="es"><head><meta charset="utf-8"><title>Evaluación D10 sintético</title>
<style>
body{{font-family:system-ui,sans-serif;max-width:960px;margin:2rem auto;padding:0 1rem;color:#1d1d1f;background:#fff}}
h1{{font-size:1.5rem}} h2{{font-size:1.1rem;margin-top:2rem;border-bottom:1px solid #ddd;padding-bottom:.3rem}}
table{{border-collapse:collapse;font-size:.85rem;margin:.5rem 0}} td,th{{border:1px solid #e3e3e3;padding:.25rem .5rem;text-align:left}}
td.n{{font-variant-numeric:tabular-nums}} th{{background:#f5f5f7}} pre{{background:#f5f5f7;padding:.75rem;overflow-x:auto;font-size:.8rem}}
</style></head><body><h1>Evaluación de datos sintéticos D10</h1><p>Generado: {fecha}</p>
{cuerpo}
</body></html>"""
