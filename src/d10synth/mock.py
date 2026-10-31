"""Generador de un D10 falso con parámetros conocidos, para desarrollar y testear sin la data real.

El proceso es deliberadamente distinto del modelo de d10synth (productos independientes por IF, montos
lognormales con factor de deudor, una cadena de Markov de mora conocida), así el ajuste se puede evaluar
contra la verdad.
"""

import calendar
import json
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import polars as pl

from d10synth import schema
from d10synth.config import Columnas

IF_CODES = np.array([1, 9, 12, 14, 16, 28, 37, 39, 49, 51, 53, 55, 59, 504, 672])

# (tipo_deudor, tipo_credito, prob por relación deudor-IF, mu log-monto, sigma)
PRODUCTOS = {
    schema.SEG_PERSONA: [
        (1, 2, 0.65, 14.5, 1.1), (1, 3, 0.12, 17.5, 0.7), (1, 7, 0.45, 13.8, 1.0), (1, 8, 0.04, 15.0, 0.6),
        (1, 1, 0.06, 15.5, 1.3), (1, 6, 0.03, 15.0, 1.2), (1, 12, 0.01, 13.0, 0.8), (2, 1, 0.03, 16.0, 1.2),
        (1, 22, 0.01, 14.0, 0.9),
    ],
    schema.SEG_EMPRESA: [
        (1, 1, 0.92, 18.5, 2.0), (1, 6, 0.30, 17.5, 1.8), (1, 7, 0.40, 16.0, 1.5), (1, 4, 0.04, 20.0, 1.5),
        (1, 5, 0.03, 20.5, 1.5), (1, 25, 0.05, 16.5, 1.2), (1, 21, 0.01, 18.0, 1.2), (2, 1, 0.12, 18.0, 1.8),
    ],
}

# Cadena de Markov "verdadera" de la mora máxima de una celda (respeta schema.MORA_PERMITIDA)
T_TRUE = np.array([
    [0.970, 0.020, 0.010, 0, 0, 0, 0, 0, 0, 0],
    [0.550, 0.250, 0.150, 0.050, 0, 0, 0, 0, 0, 0],
    [0.250, 0.100, 0.250, 0.300, 0.100, 0, 0, 0, 0, 0],
    [0.150, 0.050, 0.050, 0.200, 0.550, 0, 0, 0, 0, 0],
    [0.080, 0, 0, 0.020, 0.600, 0.300, 0, 0, 0, 0],
    [0.040, 0, 0, 0, 0.030, 0.680, 0.250, 0, 0, 0],
    [0.020, 0, 0, 0, 0, 0, 0.830, 0.150, 0, 0],
    [0.010, 0, 0, 0, 0, 0, 0, 0.840, 0.150, 0],
    [0.010, 0, 0, 0, 0, 0, 0, 0, 0.840, 0.150],
    [0.010, 0, 0, 0, 0, 0, 0, 0, 0, 0.990],
])
MORA_INICIAL = np.array([0.90, 0.03, 0.015, 0.01, 0.01, 0.01, 0.01, 0.005, 0.005, 0.005])
P_SALIDA_DEUDOR = {schema.SEG_PERSONA: 0.015, schema.SEG_EMPRESA: 0.010}
P_PERSONA = 0.85
RHO = 0.6
LAMBDA_NUEVAS = 0.03
P_MISMA_IF = 0.6
DERIVA = {3: (-0.004, 0.01), 2: (-0.03, 0.05), 7: (0.0, 0.15), 1: (0.0, 0.10)}  # tc -> (media, sd) de log-ratio


def _p_salida_celda(tc: np.ndarray, mora: np.ndarray) -> np.ndarray:
    p = np.where(tc == 3, 0.005, 0.02)
    return np.where(mora == 9, 0.03, p)


class _Estado:
    def __init__(self, rng: np.random.Generator):
        self.rng = rng
        self.seg = np.empty(0, np.int8)
        self.z = np.empty(0)
        self.rut = np.empty(0, np.int64)
        self.vivo = np.empty(0, bool)
        self.c = {k: np.empty(0, np.int64) for k in ("deb", "ifx", "td", "tc", "mora")} | {"lm": np.empty(0)}
        self.share_if = 1 / np.arange(1, len(IF_CODES) + 1) ** 0.9
        self.share_if /= self.share_if.sum()
        self._ruts_usados: set[int] = set()

    def _ruts(self, seg: np.ndarray) -> np.ndarray:
        out = np.empty(len(seg), np.int64)
        for s, (lo, hi) in {0: (5_000_000, 27_000_000), 1: (50_000_000, 99_000_000)}.items():
            idx = np.nonzero(seg == s)[0]
            cand = np.unique(self.rng.integers(lo, hi, size=len(idx) * 2 + 10))
            cand = np.array([r for r in self.rng.permutation(cand) if r not in self._ruts_usados][: len(idx)], np.int64)
            self._ruts_usados.update(cand.tolist())
            out[idx] = cand
        return out

    def agregar_deudores(self, n: int):
        rng = self.rng
        seg = (rng.random(n) > P_PERSONA).astype(np.int8)
        z = rng.standard_normal(n)
        base = len(self.seg)
        n_if = np.minimum(1 + rng.poisson(np.where(seg == 0, 0.5, 1.2)), 8)
        cel = {k: [] for k in self.c}
        for i in range(n):
            ifs = rng.choice(len(IF_CODES), size=n_if[i], replace=False, p=self.share_if)
            prods = PRODUCTOS[int(seg[i])]
            for ifx in ifs:
                elegidos = [p for p in prods if rng.random() < p[2]] or [prods[0]]
                for td, tc, _, mu, sd in elegidos:
                    cel["deb"].append(base + i)
                    cel["ifx"].append(ifx)
                    cel["td"].append(td)
                    cel["tc"].append(tc)
                    cel["mora"].append(0 if tc in schema.CONTINGENTES else rng.choice(10, p=MORA_INICIAL))
                    cel["lm"].append(mu + sd * (RHO * z[i] + np.sqrt(1 - RHO**2) * rng.standard_normal()))
        self.seg = np.concatenate([self.seg, seg])
        self.z = np.concatenate([self.z, z])
        self.rut = np.concatenate([self.rut, self._ruts(seg)])
        self.vivo = np.concatenate([self.vivo, np.ones(n, bool)])
        for k in self.c:
            self.c[k] = np.concatenate([self.c[k], np.asarray(cel[k], dtype=self.c[k].dtype)])

    def _filtrar(self, keep):
        self.c = {k: v[keep] for k, v in self.c.items()}

    def paso(self):
        rng = self.rng
        n_vivos = self.vivo.sum()
        sale = rng.random(len(self.seg)) < np.where(self.seg == 0, P_SALIDA_DEUDOR[0], P_SALIDA_DEUDOR[1])
        self.vivo &= ~sale
        self._filtrar(self.vivo[self.c["deb"]])
        self._filtrar(rng.random(len(self.c["deb"])) >= _p_salida_celda(self.c["tc"], self.c["mora"]))
        # mora
        cont = np.isin(self.c["tc"], list(schema.CONTINGENTES))
        u = rng.random(len(self.c["mora"]))
        cdf = np.cumsum(T_TRUE, axis=1)[self.c["mora"]]
        nueva = (u[:, None] > cdf).sum(1)
        self.c["mora"] = np.where(cont, 0, np.minimum(nueva, 9))
        # montos
        mu = np.zeros(len(self.c["tc"]))
        sd = np.full(len(self.c["tc"]), 0.05)
        for tc, (m, s) in DERIVA.items():
            mu[self.c["tc"] == tc], sd[self.c["tc"] == tc] = m, s
        self.c["lm"] = self.c["lm"] + mu + sd * rng.standard_normal(len(mu))
        # celdas nuevas
        vivos = np.nonzero(self.vivo)[0]
        n_new = rng.poisson(LAMBDA_NUEVAS, size=len(vivos))
        nuevos = []
        for d in np.repeat(vivos, n_new):
            prods = PRODUCTOS[int(self.seg[d])]
            td, tc, _, m, s = prods[rng.choice(len(prods), p=np.array([p[2] for p in prods]) / sum(p[2] for p in prods))]
            propias = self.c["ifx"][self.c["deb"] == d]
            ifx = rng.choice(propias) if len(propias) and rng.random() < P_MISMA_IF else rng.choice(len(IF_CODES), p=self.share_if)
            nuevos.append((d, ifx, td, tc, 0, m + s * (RHO * self.z[d] + np.sqrt(1 - RHO**2) * rng.standard_normal())))
        if nuevos:
            arr = list(zip(*nuevos))
            for k, v in zip(("deb", "ifx", "td", "tc", "mora", "lm"), arr):
                self.c[k] = np.concatenate([self.c[k], np.asarray(v, dtype=self.c[k].dtype)])
            clave = ((self.c["deb"] * 100 + self.c["ifx"]) * 3 + self.c["td"]) * 100 + self.c["tc"]
            _, primera = np.unique(clave, return_index=True)
            self._filtrar(np.sort(primera))
        # entrantes (población estable)
        self.agregar_deudores(int(rng.poisson(n_vivos * (P_PERSONA * P_SALIDA_DEUDOR[0] + (1 - P_PERSONA) * P_SALIDA_DEUDOR[1]))))
        self.vivo &= np.bincount(self.c["deb"], minlength=len(self.seg)) > 0

    def filas(self) -> dict[str, np.ndarray]:
        """Expande celdas a filas por código de mora."""
        rng = self.rng
        c = self.c
        n = len(c["deb"])
        monto = np.exp(c["lm"])
        cuotas = np.isin(c["tc"], [1, 2, 3, 8]) & (c["mora"] >= 1)
        con_cero = cuotas & (rng.random(n) < 0.7)
        con_medio = cuotas & (c["mora"] >= 2) & (rng.random(n) < 0.3)
        p_max = np.where(con_cero | con_medio, rng.beta(2, 5, n), 1.0)
        p_medio = np.where(con_medio, (1 - p_max) * np.where(con_cero, 0.5, 1.0), 0.0)
        p_cero = 1 - p_max - p_medio
        partes = [(np.ones(n, bool), c["mora"], p_max), (con_medio, c["mora"] - 1, p_medio), (con_cero, np.zeros(n, np.int64), p_cero)]
        out = {k: [] for k in ("deb", "ifx", "td", "tc", "mora", "monto")}
        for m, cod, p in partes:
            for k in ("deb", "ifx", "td", "tc"):
                out[k].append(c[k][m])
            out["mora"].append(cod[m])
            out["monto"].append(np.maximum(1, np.round(monto[m] * p[m])).astype(np.int64))
        return {k: np.concatenate(v) for k, v in out.items()}


def _viernes_del_mes(fin: date) -> list[date]:
    d = date(fin.year, fin.month, 1)
    d += timedelta(days=(4 - d.weekday()) % 7)
    out = []
    while d < fin:
        out.append(d)
        d += timedelta(days=7)
    return out


def _escribir_d10(salida: Path, fecha: date, ifs: np.ndarray, rut: np.ndarray, f: dict, rng) -> None:
    """Un archivo por IF con el layout del manual (CRLF, latin-1). El nombre es relleno ficticio.

    Usa su propio rng para que el panel sea idéntico en los tres formatos.
    """
    nombres = np.array([n.ljust(50)[:50] for n in ("DEUDOR FICTICIO", "PEÑA Y CÍA LTDA", "MUÑOZ ROJAS")])
    dv = schema.rut_dv(rut)
    for if_code in np.unique(ifs):
        m = ifs == if_code
        nm = nombres[rng.integers(0, len(nombres), m.sum())]
        lineas = [f"{if_code:04d}D10{fecha:%Y%m%d}".ljust(78)]
        lineas += [
            f"{r:09d}{d}{n}{td:01d}{tc:02d}{mo:01d}{int(mt):014d}"
            for r, d, n, td, tc, mo, mt in zip(rut[m], dv[m], nm, f["td"][m], f["tc"][m], f["mora"][m], f["monto"][m])
        ]
        (salida / f"D10_{if_code:04d}_{fecha:%Y%m%d}.txt").write_bytes(("\r\n".join(lineas) + "\r\n").encode("latin-1"))


def generar_mock(
    salida: str | Path,
    deudores: int = 50_000,
    meses: int = 12,
    inicio: date = date(2024, 1, 31),
    semilla: int = 0,
    columnas: Columnas | None = None,
    rut_con_dv: bool = False,
    semanales: bool = False,
    formato: str = "parquet",
) -> Path:
    """Escribe el D10 falso, un archivo por fecha de corte.

    formato:
      parquet  como la tabla fuente, periodo int aaaammdd (lo que lee el pipeline sin importar)
      csv      como un export de la tabla (separador ';', periodo 'aaaa-mm-dd 00:00:00.000')
      d10      como los archivos que envían las IF: ancho fijo de 78 bytes, uno por IF y fecha
    """
    col = columnas or Columnas()
    salida = Path(salida)
    salida.mkdir(parents=True, exist_ok=True)
    for patron in ("*.parquet", "*.csv", "D10_*.txt"):
        for viejo in salida.glob(patron):
            viejo.unlink()
    rng = np.random.default_rng(semilla)
    est = _Estado(rng)
    est.agregar_deudores(deudores)
    for t in range(meses):
        if t > 0:
            est.paso()
        y, m = divmod(inicio.month - 1 + t, 12)
        y, m = inicio.year + y, m + 1
        fin = date(y, m, calendar.monthrange(y, m)[1])
        f = est.filas()
        rut = est.rut[f["deb"]]
        base = {
            col.if_id: IF_CODES[f["ifx"]],
            col.rut: schema.formatear_rut(rut) if rut_con_dv else rut,
            col.tipo_deudor: f["td"].astype(np.int16),
            col.tipo_credito: f["tc"].astype(np.int16),
            col.tipo_mora: f["mora"].astype(np.int16),
            col.monto: f["monto"],
        }
        fechas = ([*_viernes_del_mes(fin)] if semanales else []) + [fin]
        for fecha in fechas:
            df = pl.DataFrame({col.periodo: np.full(len(rut), int(fecha.strftime("%Y%m%d")))} | base)
            if formato == "parquet":
                df.write_parquet(salida / f"periodo={fecha:%Y%m%d}.parquet")
            elif formato == "csv":
                df.with_columns(pl.lit(f"{fecha:%Y-%m-%d} 00:00:00.000").alias(col.periodo)).write_csv(
                    salida / f"D10C_{fecha:%Y%m%d}.csv", separator=";"
                )
            elif formato == "d10":
                _escribir_d10(salida, fecha, IF_CODES[f["ifx"]], rut, f, np.random.default_rng(semilla + t))
            else:
                raise ValueError(f"formato desconocido: {formato}")
    params = {
        "T_true": T_TRUE.tolist(),
        "p_salida_deudor": {schema.SEGMENTOS[k]: v for k, v in P_SALIDA_DEUDOR.items()},
        "p_persona": P_PERSONA,
        "rho": RHO,
        "lambda_nuevas": LAMBDA_NUEVAS,
        "p_misma_if": P_MISMA_IF,
    }
    (salida / "mock_parametros.json").write_text(json.dumps(params, indent=2), encoding="utf-8")
    return salida
