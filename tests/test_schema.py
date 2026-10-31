import numpy as np

from d10synth import schema


def test_rut_dv_conocidos():
    assert list(schema.rut_dv([12345678, 11111111, 22222222])) == ["5", "1", "2"]


def test_rut_dv_k_y_cero():
    # busca ejemplos de DV 'K' y '0' y verifica por definición
    ruts = np.arange(1_000_000, 1_000_200)
    dvs = schema.rut_dv(ruts)
    assert "K" in dvs and "0" in dvs
    for r, dv in zip(ruts, dvs):
        s = sum(int(d) * f for d, f in zip(str(r)[::-1], [2, 3, 4, 5, 6, 7] * 2))
        esperado = {11: "0", 10: "K"}.get(11 - s % 11, str(11 - s % 11))
        assert dv == esperado


def test_formatear_rut():
    assert schema.formatear_rut([12345678])[0] == "12345678-5"


def test_mora_permitida():
    P = schema.MORA_PERMITIDA
    assert P[0, 1] and P[0, 2] and not P[0, 3]
    assert P[1, 3] and not P[1, 4]
    assert P[2, 4] and not P[2, 5]
    assert P[3, 4] and not P[3, 5]
    assert P[4, 5] and P[5, 6] and P[8, 9]
    assert all(P[i, j] for i in range(10) for j in range(i + 1))  # bajar siempre es posible


def test_slots():
    assert schema.N_TC == 20 and schema.N_SLOTS == 40
    assert schema.slot_tc_idx(schema.N_TC + 3) == 3 and schema.slot_td_idx(schema.N_TC + 3) == 1
