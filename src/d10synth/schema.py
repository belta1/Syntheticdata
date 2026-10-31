"""Catálogos y reglas del archivo D10 (ver d10-manual.txt)."""

import numpy as np

COLS = ["if_id", "periodo", "rut", "tipo_deudor", "tipo_credito", "tipo_mora", "monto"]

TIPOS_DEUDOR = {1: "Deudor directo", 2: "Deudor indirecto"}

TIPOS_CREDITO = {
    1: "Créditos comerciales",
    2: "Créditos de consumo",
    3: "Créditos para vivienda",
    4: "Operaciones financieras",
    5: "Instrumentos de deuda adquiridos",
    6: "Créditos contingentes",
    7: "Cupos de líneas de crédito de libre disposición",
    8: "Créditos para estudios superiores Ley 20.027",
    9: "Créditos educacionales con garantía CORFO",
    10: "Otros créditos para estudios superiores",
    11: "Créditos contingentes para estudios superiores",
    12: "Créditos para la salud",
    21: "Créditos comerciales securitizados",
    22: "Créditos de consumo securitizados",
    23: "Créditos para vivienda securitizados",
    24: "Otros créditos securitizados",
    25: "Créditos comerciales subrogados (garantía estatal)",
    26: "Créditos de consumo subrogados (garantía estatal)",
    27: "Créditos para vivienda subrogados (garantía estatal)",
    28: "Otros créditos subrogados (garantía estatal)",
}
TC_CODES = list(TIPOS_CREDITO)
N_TC = len(TC_CODES)
TC_INDEX = {c: i for i, c in enumerate(TC_CODES)}

# Un "slot" de producto es (tipo_deudor, tipo_credito): slot = td_idx * N_TC + tc_idx
N_TD = 2
N_SLOTS = N_TD * N_TC

MORA = {
    0: "Al día",
    1: "< 30 días",
    2: "30-59 días",
    3: "60-89 días",
    4: "90-179 días",
    5: "180 días - 1 año",
    6: "1-2 años",
    7: "2-3 años",
    8: "3-4 años",
    9: "4 años o más",
}
N_MORA = len(MORA)

# Contingentes y cupos se informan siempre con mora 0
CONTINGENTES = {6, 7, 11}
# Productos que por definición sólo tienen personas naturales
SOLO_PERSONA = {2, 3, 8, 9, 10, 11, 12, 22, 23, 26, 27}

SEG_PERSONA, SEG_EMPRESA = 0, 1
SEGMENTOS = {SEG_PERSONA: "persona", SEG_EMPRESA: "empresa"}

# Días de atraso de cada tramo de mora: [desde, hasta]
_MORA_DESDE = np.array([0, 1, 30, 60, 90, 180, 365, 730, 1095, 1460])
_MORA_HASTA = np.array([0, 29, 59, 89, 179, 364, 729, 1094, 1459, np.inf])
DIAS_ENTRE_CIERRES = 31


def mora_permitida() -> np.ndarray:
    """Matriz booleana (10x10) de transiciones de mora posibles entre dos cierres de mes consecutivos.

    En un mes el atraso crece a lo más ~31 días; bajar de tramo (pago, repactación) siempre es posible.
    """
    m = np.arange(N_MORA)
    sube_ok = _MORA_DESDE[None, :] <= _MORA_HASTA[:, None] + DIAS_ENTRE_CIERRES
    return (m[None, :] <= m[:, None]) | sube_ok


MORA_PERMITIDA = mora_permitida()

TC_CONTINGENTE_IDX = np.array([TC_INDEX[c] for c in sorted(CONTINGENTES)])
TC_SOLO_PERSONA_IDX = np.array([TC_INDEX[c] for c in sorted(SOLO_PERSONA)])


def slot_tc_idx(slot):
    return slot % N_TC


def slot_td_idx(slot):
    return slot // N_TC


def rut_dv(rut) -> np.ndarray:
    """Dígito verificador módulo 11 (vectorizado). Devuelve strings '0'-'9' o 'K'."""
    r = np.asarray(rut, dtype=np.int64).copy()
    total = np.zeros_like(r)
    factor = 2
    while np.any(r > 0):
        total += (r % 10) * factor
        r //= 10
        factor = 2 if factor == 7 else factor + 1
    dv = 11 - total % 11
    out = dv.astype(str).astype(object)
    out[dv == 11] = "0"
    out[dv == 10] = "K"
    return out.astype(str)


def formatear_rut(rut) -> np.ndarray:
    rut = np.asarray(rut, dtype=np.int64)
    return np.char.add(np.char.add(rut.astype(str), "-"), rut_dv(rut))
