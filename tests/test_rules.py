from datetime import date

import polars as pl

from d10synth.rules import todas_ok, validar


def _df(filas):
    df = pl.DataFrame(filas, schema=["if_id", "periodo", "rut", "tipo_deudor", "tipo_credito", "tipo_mora", "monto"], orient="row")
    return df.with_columns((pl.col("rut") >= 50_000_000).cast(pl.Int8).alias("seg"))


ENE, FEB = date(2024, 1, 31), date(2024, 2, 29)


def test_data_limpia_pasa():
    df = _df([
        (1, ENE, 10_000_000, 1, 2, 0, 1000.0),
        (1, FEB, 10_000_000, 1, 2, 1, 990.0),
        (1, ENE, 60_000_000, 1, 1, 0, 5e6),
        (1, ENE, 60_000_000, 1, 6, 0, 1e6),
    ])
    assert todas_ok(validar(df))


def test_detecta_violaciones():
    df = _df([
        (1, ENE, 10_000_000, 1, 6, 2, 100.0),      # contingente con mora
        (1, ENE, 60_000_000, 1, 3, 0, 100.0),      # empresa con vivienda
        (1, ENE, 10_000_000, 1, 2, 0, 100.0),
        (1, ENE, 10_000_000, 1, 2, 0, 100.0),      # duplicada
        (1, FEB, 10_000_000, 1, 2, 4, 100.0),      # 0 -> 4 en un mes
        (1, ENE, 11_000_000, 1, 13, 0, 100.0),     # tipo de crédito inexistente
        (1, ENE, 12_000_000, 1, 2, 0, 10.5),       # monto no entero
    ])
    r = validar(df)
    assert r["contingente_con_mora"]["violaciones"] == 1
    assert r["empresa_con_producto_persona"]["violaciones"] == 1
    assert r["filas_duplicadas"]["violaciones"] == 1
    assert r["transicion_mora_no_permitida"]["violaciones"] == 1
    assert r["codigos_o_monto_invalidos"]["violaciones"] == 1
    assert r["monto_no_entero"]["violaciones"] == 1


def test_dv_invalido():
    df = _df([(1, ENE, 12345678, 1, 2, 0, 1.0), (1, ENE, 11111111, 1, 2, 0, 1.0)])
    r = validar(df, pl.Series(["12345678-5", "11111111-2"]))
    assert r["rut_dv_invalido"] == {"violaciones": 1, "revisadas": 2}
