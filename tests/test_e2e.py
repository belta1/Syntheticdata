"""Pipeline completo sobre data mock: mock -> fit -> generate -> reglas, y recuperación de parámetros."""

import json
from dataclasses import replace

import numpy as np
import polars as pl
import pytest

from d10synth import schema
from d10synth.config import Config
from d10synth.evaluate.fidelity import _flujos
from d10synth.prep import build_cells
from d10synth.generate import generar
from d10synth.mock import P_SALIDA_DEUDOR, RHO, T_TRUE, generar_mock
from d10synth.model.artifact import load, save
from d10synth.model.fit import fit
from d10synth.prep import load_real, normalize, scan_parquet_dir
from d10synth.rules import resumen_texto, todas_ok, validar


@pytest.fixture(scope="module")
def entorno(tmp_path_factory):
    base = tmp_path_factory.mktemp("d10")
    cfg = Config()
    cfg.rutas = replace(cfg.rutas, real=str(base / "real"), modelo=str(base / "modelo"), sintetico=str(base / "sint"), reportes=str(base / "rep"))
    cfg.generacion = replace(cfg.generacion, deudores=8000, meses=6, chunk_deudores=3000, dispositivo="cpu")
    cfg.ajuste = replace(cfg.ajuste, k_grupo=100)
    generar_mock(cfg.rutas.real, deudores=8000, meses=6, semilla=3, rut_con_dv=True, semanales=True)
    modelo = fit(cfg)
    save(modelo, cfg.rutas.modelo)
    excluir = load_real(cfg).select("rut").unique().collect()["rut"].to_numpy()
    generar(cfg, load(cfg.rutas.modelo), excluir_ruts=excluir)
    return cfg, modelo, excluir


def test_prep_filtra_cierres_y_parsea_rut(entorno):
    cfg, _, _ = entorno
    df = load_real(cfg).collect()
    assert df["periodo"].n_unique() == 6
    assert (df["periodo"] == df["periodo"].dt.month_end()).all()
    assert df["rut"].null_count() == 0 and df["rut"].min() >= 5_000_000


def test_mock_cumple_reglas(entorno):
    cfg, _, _ = entorno
    r = validar(load_real(cfg).collect())
    assert todas_ok(r), resumen_texto(r)


def test_sintetico_cumple_reglas(entorno):
    cfg, _, _ = entorno
    raw = scan_parquet_dir(cfg.rutas.sintetico)
    df = normalize(raw, cfg).collect()
    r = validar(df, raw.select("rut_deudor").collect().to_series())
    assert todas_ok(r), resumen_texto(r)
    assert df["periodo"].n_unique() == 6


def test_ruts_sinteticos_no_existen_en_real(entorno):
    cfg, _, excluir = entorno
    df = normalize(scan_parquet_dir(cfg.rutas.sintetico), cfg).collect()
    assert not np.isin(df["rut"].unique().to_numpy(), excluir).any()
    # RUT único por deudor y segmento coherente con el rango
    assert ((df["rut"] >= cfg.formato.umbral_rut_empresa).cast(pl.Int8) == df["seg"]).all()


def test_modelo_solo_agregados(entorno):
    _, modelo, _ = entorno
    assert "rut" not in json.dumps(modelo.meta).lower().replace("umbral_rut_empresa", "")
    for nombre, arr in modelo.arrays.items():
        assert arr.dtype != object, nombre


def test_recupera_markov_de_mora(entorno):
    _, modelo, _ = entorno
    tr = modelo["trans"][schema.TC_INDEX[2]]  # consumo
    tr = tr / tr.sum(1, keepdims=True)
    for fila in (0, 1, 2):
        assert np.abs(tr[fila] - T_TRUE[fila]).max() < 0.08, (fila, tr[fila].round(3), T_TRUE[fila])
    assert not (modelo["trans"][:, ~schema.MORA_PERMITIDA] > 0).any()


def test_recupera_dinamica(entorno):
    cfg, modelo, _ = entorno
    # la salida observada incluye a quienes pierden todas sus celdas, así que es >= la del parámetro
    assert P_SALIDA_DEUDOR[0] - 0.005 < modelo["p_exit_debtor"][0] < P_SALIDA_DEUDOR[0] + 0.02
    assert abs(modelo["rho"][0] - RHO) < 0.2
    real = load_real(cfg).collect()
    sint = normalize(scan_parquet_dir(cfg.rutas.sintetico), cfg).collect()
    fr, fs = _flujos(build_cells(real.lazy())[0]), _flujos(build_cells(sint.lazy())[0])
    assert abs(fr["salida_deudores"] - fs["salida_deudores"]) < 0.006, (fr, fs)
    assert abs(fr["entrada_deudores"] - fs["entrada_deudores"]) < 0.006, (fr, fs)


def test_volumen_sintetico_razonable(entorno):
    cfg, _, _ = entorno
    real = load_real(cfg).collect()
    sint = normalize(scan_parquet_dir(cfg.rutas.sintetico), cfg).collect()
    filas_por_deudor = lambda d: d.height / d.select("periodo", "rut").n_unique()  # noqa: E731
    assert abs(filas_por_deudor(sint) / filas_por_deudor(real) - 1) < 0.15
    tendencia = lambda d: (lambda x: x[-1] / x[0])(d.group_by("periodo").agg(pl.col("rut").n_unique()).sort("periodo")["rut"].to_numpy())  # noqa: E731
    assert abs(tendencia(sint) - tendencia(real)) < 0.05
