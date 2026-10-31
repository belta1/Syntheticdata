"""Importación al staging: los tres formatos de archivo dan la misma data, y los casos sucios del D10 original."""

import gzip
import os
from dataclasses import replace
from datetime import date

import numpy as np
import polars as pl
import pytest

from d10synth import schema
from d10synth.config import Config, Csv, Importacion
from d10synth.io.importar import importar, leer_d10
from d10synth.io.staging import DTYPES, es_canonico, verificar
from d10synth.mock import generar_mock
from d10synth.prep import holdout_expr, load_real

CIERRES = [date(2024, 1, 31), date(2024, 2, 29), date(2024, 3, 31)]


def _cfg(base, nombre, tipo, entrada, **imp) -> Config:
    cfg = Config()
    cfg.rutas = replace(cfg.rutas, real=str(base / nombre))
    cfg.importacion = Importacion(tipo=tipo, entrada=str(entrada), **imp)
    return cfg


def _ordenar(df: pl.DataFrame) -> pl.DataFrame:
    return df.select(list(DTYPES)).sort(list(DTYPES))


@pytest.fixture(scope="module")
def fuentes(tmp_path_factory):
    base = tmp_path_factory.mktemp("imp")
    kw = dict(deudores=2000, meses=3, semilla=5, semanales=True)
    generar_mock(base / "pq", **kw)
    generar_mock(base / "csv", formato="csv", rut_con_dv=True, **kw)
    generar_mock(base / "d10", formato="d10", **kw)
    return base


@pytest.fixture(scope="module")
def staging(fuentes):
    out = {}
    for tipo, carpeta in (("parquet", "pq"), ("csv", "csv"), ("d10", "d10")):
        cfg = _cfg(fuentes, f"stg_{tipo}", tipo, fuentes / carpeta)
        importar(cfg)
        out[tipo] = cfg
    return out


def test_formatos_equivalentes(fuentes, staging):
    # referencia: el mock parquet leído directo, sin importar (sólo cierres de mes)
    ref = _ordenar(load_real(Config(), fuentes / "pq").collect())
    for tipo, cfg in staging.items():
        assert es_canonico(cfg.rutas.real), tipo
        df = load_real(cfg).collect()
        assert sorted(df["periodo"].unique().to_list()) == CIERRES, tipo
        assert _ordenar(df).equals(ref), tipo


def test_verify_ok_y_detecta_cambios(staging, tmp_path):
    cfg = staging["d10"]
    r = verificar(cfg.rutas.real)
    assert r["ok"] and not r["meses_faltantes"] and len(r["periodos"]) == 3
    # una copia alterada debe fallar
    import shutil

    copia = tmp_path / "copia"
    shutil.copytree(cfg.rutas.real, copia)
    p = copia / "periodo=20240229.parquet"
    pl.read_parquet(p).head(10).write_parquet(p)
    (copia / "periodo=20240331.parquet").unlink()
    r = verificar(copia)
    assert not r["ok"]
    assert any("SHA-256" in x for x in r["problemas"]) and any("falta" in x for x in r["problemas"])


def test_no_mezcla_origenes_y_reanuda(fuentes, staging):
    cfg = replace(staging["csv"], importacion=Importacion(tipo="d10", entrada=str(fuentes / "d10")))
    with pytest.raises(ValueError, match="No se mezclan"):
        importar(cfg)
    # mismo origen: los periodos ya importados se saltan
    assert importar(staging["csv"]) == []
    assert len(importar(staging["csv"], reemplazar=True)) == 3


def test_desde_hasta_y_muestra(fuentes):
    cfg = _cfg(fuentes, "stg_muestra", "parquet", fuentes / "pq", desde="2024-02-01", muestra_pct=40)
    importar(cfg)
    df = load_real(cfg).collect()
    assert sorted(df["periodo"].unique().to_list()) == CIERRES[1:]
    total = load_real(Config(), fuentes / "pq").filter(pl.col("periodo") >= date(2024, 2, 1)).collect()
    frac = df["rut"].n_unique() / total["rut"].n_unique()
    assert 0.33 < frac < 0.47
    # la muestra es por deudor (estable entre meses) e independiente del holdout del 20%
    ruts = df.select("rut").unique()
    assert 0.14 < ruts.select(holdout_expr(20)).to_series().mean() < 0.26


def test_csv_sin_encabezado_y_decimal_coma(tmp_path):
    src = tmp_path / "x.csv"
    src.write_text(
        "15;31/01/2024;12.345.678-5;1;2;0;1.500,00\n"
        "15;31/01/2024;12.345.678-5;1;7;0;200,00\n"
        "15;15/01/2024;12.345.678-5;1;2;0;1.400,00\n",  # no es cierre de mes
        encoding="utf-8",
    )
    cfg = _cfg(tmp_path, "stg", "csv", src)
    cfg.importacion.csv = Csv(encabezado=False, decimal=",", nombres=["rutis_sbif", "periodo", "rut_deudor", "tipo_deudor", "tipo_credito", "tipo_mora", "monto"])
    # dd/mm/aaaa no se reconoce (el periodo debe venir como fecha ISO, aaaammdd o aaaamm): no se importa nada
    assert importar(cfg) == []

    src.write_text(
        "15;2024-01-31;12.345.678-5;1;2;0;1.500,00\n15;20240131;12345678-5;1;7;0;200,00\n15;20240115;12345678-5;1;2;0;1.400,00\n",
        encoding="utf-8",
    )
    importar(cfg, reemplazar=True)
    df = load_real(cfg).collect().sort("tipo_credito")
    assert df["rut"].to_list() == [12345678, 12345678]
    assert df["monto"].to_list() == [1500.0, 200.0]
    assert df["periodo"].to_list() == [date(2024, 1, 31)] * 2


def _linea(rut, dv, nombre, td, tc, mora, monto, enc="latin-1") -> bytes:
    return f"{rut:09d}{dv}".encode() + nombre.ljust(50)[:50].encode(enc) + f"{td}{tc:02d}{mora}{monto:014d}".encode()


def test_d10_casos_sucios(tmp_path):
    d = tmp_path / "d10"
    d.mkdir()
    dv = schema.rut_dv([12345678, 7654321])
    cab = b"0037D1020240131".ljust(78)
    filas = [
        _linea(12345678, dv[0], "PEÑA", 1, 2, 0, 1000),
        _linea(7654321, dv[1], "MUÑOZ ÁLVAREZ", 1, 7, 0, 500, enc="utf-8"),  # nombre en UTF-8: línea de 79+ bytes
        _linea(7654321, "0" if dv[1] != "0" else "1", "X", 1, 1, 3, 2500),  # DV malo: se importa y se cuenta
        b"00000ABC9" + b"X" * 69,  # ilegible
        b"corta",
    ]
    (d / "a.txt").write_bytes(b"\r\n".join([cab, *filas]) + b"\r\n\x1a")
    _, fecha, df, c = leer_d10(d / "a.txt")
    assert fecha == date(2024, 1, 31) and df.height == 3
    assert c == {"registros": 5, "registros_ilegibles": 2, "registros_largo_distinto_78": 1, "rut_dv_invalido": 1}
    assert df["monto"].to_list() == [1000.0, 500.0, 2500.0] and df["if_id"].unique().to_list() == [37]

    # reenvío de la misma IF y fecha: vale el más reciente; también se aceptan .gz
    (d / "a_v2.txt.gz").write_bytes(gzip.compress(b"\r\n".join([cab, filas[0]]) + b"\r\n"))
    os.utime(d / "a_v2.txt.gz", (2e9, 2e9))
    cfg = _cfg(tmp_path, "stg", "d10", d)
    importar(cfg)
    df = load_real(cfg).collect()
    assert df.height == 1
    assert verificar(cfg.rutas.real)["ok"]


def test_staging_tipos(staging):
    p = next(iter(sorted(__import__("pathlib").Path(staging["csv"].rutas.real).glob("*.parquet"))))
    assert dict(pl.read_parquet_schema(p)) == DTYPES
    assert np.issubdtype(pl.read_parquet(p)["rut"].to_numpy().dtype, np.integer)
