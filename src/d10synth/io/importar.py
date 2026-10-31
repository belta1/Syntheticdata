"""Importación desde archivos al staging canónico: CSV/TXT delimitado, parquet o archivos D10 originales.

- csv / parquet: export de la tabla fuente (bcp, SSMS, SSIS, Spark...). Columnas según cfg.columnas y
  formato según cfg.formato, igual que en SQL.
- d10: los archivos tal como los envían las IF (ancho fijo, 78 bytes; un registro de cabecera con el
  código de la IF y la fecha, y luego un registro por deudor/tipo/mora). El nombre del deudor
  (bytes 11 a 60) nunca se decodifica ni se guarda.
"""

import glob
import gzip
import logging
import tempfile
from collections import defaultdict
from datetime import date, datetime
from pathlib import Path

import numpy as np
import polars as pl

from d10synth.config import Config
from d10synth.io.staging import Staging, a_canonico, filtro_periodos, muestra_expr

log = logging.getLogger(__name__)

TIPOS = ("sql", "csv", "parquet", "d10")
LARGO_D10 = 78
# posiciones (0-based, fin exclusivo) del registro de detalle; las del final se cuentan desde el final de la línea
D10_RUT, D10_DV = slice(0, 9), 9
D10_FIN = 18  # tipo deudor 9(01) + tipo crédito 9(02) + morosidad 9(01) + monto 9(14)


def expandir(entrada: str | Path, extensiones: tuple[str, ...]) -> list[Path]:
    """Archivo, carpeta (recursiva, por extensión) o patrón glob -> lista ordenada de archivos."""
    p = Path(entrada)
    if p.is_file():
        return [p]
    if p.is_dir():
        return sorted(a for a in p.rglob("*") if a.is_file() and a.name.lower().endswith(extensiones))
    archivos = sorted(Path(a) for a in glob.glob(str(entrada), recursive=True) if Path(a).is_file())
    if not archivos:
        raise FileNotFoundError(f"No se encontraron archivos en {entrada}")
    return archivos


def importar(cfg: Config, tipo: str | None = None, entrada: str | None = None, reemplazar: bool = False) -> list[Path]:
    tipo = tipo or cfg.importacion.tipo
    if tipo not in TIPOS:
        raise ValueError(f"importacion.tipo debe ser uno de {TIPOS}, no '{tipo}'")
    if tipo == "sql":
        from d10synth.io.extract import extraer

        return extraer(cfg, reemplazar)
    entrada = entrada or cfg.importacion.entrada
    if not entrada:
        raise ValueError("Falta importacion.entrada (o --entrada): archivo, carpeta o patrón con los datos")
    if tipo == "d10":
        return importar_d10(cfg, expandir(entrada, (".txt", ".dat", ".d10", ".gz")), reemplazar)
    ext = (".parquet",) if tipo == "parquet" else (".csv", ".txt", ".csv.gz", ".txt.gz")
    return importar_tabular(cfg, tipo, expandir(entrada, ext), reemplazar)


# ---------------------------------------------------------------- csv / parquet

def _scan_tabular(cfg: Config, tipo: str, archivos: list[Path]) -> pl.LazyFrame:
    if tipo == "parquet":
        return pl.scan_parquet([str(a) for a in archivos])
    c = cfg.importacion.csv
    lf = pl.scan_csv(
        [str(a) for a in archivos], separator=c.separador, has_header=c.encabezado,
        new_columns=c.nombres, encoding=c.encoding, infer_schema=False, truncate_ragged_lines=True,
    )
    nombres = lf.collect_schema().names()
    monto = cfg.columnas.monto
    if monto in nombres:
        m = pl.col(monto).str.strip_chars()
        if c.decimal == ",":
            m = m.str.replace_all(".", "", literal=True).str.replace(",", ".", literal=True)
        lf = lf.with_columns(m.alias(monto))
    return lf


def importar_tabular(cfg: Config, tipo: str, archivos: list[Path], reemplazar: bool) -> list[Path]:
    st = Staging(cfg, {"tipo": tipo, "entrada": str(cfg.importacion.entrada), "n_archivos": len(archivos),
                       "muestra_pct": cfg.importacion.muestra_pct}, reemplazar)
    lf = a_canonico(_scan_tabular(cfg, tipo, archivos), cfg)
    if cfg.importacion.muestra_pct < 100:
        lf = lf.filter(muestra_expr(cfg.importacion.muestra_pct))
    log.info("leyendo los periodos presentes en %s archivo(s)...", len(archivos))
    presentes = lf.select(pl.col("periodo").unique()).collect()["periodo"].drop_nulls().to_list()
    periodos = [f for f in filtro_periodos(presentes, cfg) if not st.existe(f)]
    descartados = len(presentes) - len(filtro_periodos(presentes, cfg))
    log.info("%s cierres de mes por importar (%s fechas que no son cierre de mes o están fuera de rango se omiten)",
             len(periodos), descartados)
    if not periodos:
        return []
    archivos_out = []
    with tempfile.TemporaryDirectory(dir=st.carpeta, prefix="_tmp_") as tmpdir:
        fuente = lf
        if tipo == "csv":
            # una sola pasada sobre los CSV: se dejan los cierres pedidos en un parquet temporal
            tmp = Path(tmpdir) / "cierres.parquet"
            lf.filter(pl.col("periodo").is_in(periodos)).sink_parquet(tmp)
            fuente = pl.scan_parquet(tmp)
        for fecha in periodos:
            df = fuente.filter(pl.col("periodo") == fecha).collect()
            archivos_out.append(st.escribir(fecha, [df]))
    return archivos_out


# ---------------------------------------------------------------- archivos D10 originales

def _abrir(path: Path) -> bytes:
    return gzip.decompress(path.read_bytes()) if path.name.lower().endswith(".gz") else path.read_bytes()


def _primera_linea(path: Path) -> bytes:
    if path.name.lower().endswith(".gz"):
        with gzip.open(path, "rb") as f:
            return f.readline()
    with open(path, "rb") as f:
        return f.readline()


def cabecera_d10(linea: bytes) -> tuple[int, date]:
    """Primer registro: código IF 9(04), 'D10' X(03), fecha aaaammdd F(08)."""
    linea = linea.lstrip(b"\xef\xbb\xbf").rstrip(b"\r\n")
    if len(linea) < 15 or linea[4:7].upper() != b"D10":
        raise ValueError(f"cabecera D10 inválida: {linea[:15]!r}")
    return int(linea[:4]), datetime.strptime(linea[7:15].decode("ascii"), "%Y%m%d").date()


def _digitos(b: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Bytes ASCII (n, w) -> (entero, ok). Los espacios cuentan como ceros (relleno a la izquierda)."""
    d = b.astype(np.int64) - 48
    ok = ((d >= 0) & (d <= 9)) | (b == 32)
    d = np.where(b == 32, 0, d)
    return d @ (10 ** np.arange(b.shape[1] - 1, -1, -1, dtype=np.int64)), ok.all(1)


def _dv_num(rut: np.ndarray) -> np.ndarray:
    """Dígito verificador módulo 11 como número (10 = 'K')."""
    r = rut.astype(np.int64).copy()
    total = np.zeros_like(r)
    factor = 2
    while np.any(r > 0):
        total += (r % 10) * factor
        r //= 10
        factor = 2 if factor == 7 else factor + 1
    return (11 - total % 11) % 11


def _campos_fijos(data: bytes) -> tuple[bytes, np.ndarray, np.ndarray, np.ndarray]:
    """Separa la cabecera y devuelve los 10 primeros y 18 últimos bytes de cada registro de detalle.

    Vía rápida (todo numpy) si todas las líneas tienen el mismo largo; si no, línea por línea.
    Devuelve (cabecera, ini (n,10), fin (n,18), largos de cada línea leída).
    """
    data = data.lstrip(b"\xef\xbb\xbf")
    corte = data.find(b"\n")
    cabecera, cuerpo = (data, b"") if corte < 0 else (data[: corte + 1], data[corte + 1 :])
    term = b"\r\n" if cabecera.endswith(b"\r\n") else b"\n"
    cuerpo = cuerpo.rstrip(b"\x1a\r\n") + term if cuerpo.strip(b"\x1a\r\n ") else b""
    largo = cuerpo.find(term) + len(term)
    if largo > len(term) and len(cuerpo) % largo == 0:
        arr = np.frombuffer(cuerpo, dtype=np.uint8).reshape(-1, largo)
        if (arr[:, largo - len(term) :] == np.frombuffer(term, np.uint8)).all() and largo - len(term) >= 10 + D10_FIN:
            neto = largo - len(term)
            return cabecera, arr[:, :10], arr[:, neto - D10_FIN : neto], np.full(len(arr), neto)
    lineas = [ln.rstrip(b"\r") for ln in cuerpo.split(b"\n")]
    lineas = [ln for ln in lineas if ln.strip(b" \x1a")]  # líneas vacías y EOF de DOS
    largos = np.fromiter(map(len, lineas), dtype=np.int64, count=len(lineas))
    lineas = [ln if len(ln) >= 10 + D10_FIN else b" " * (10 + D10_FIN) for ln in lineas]  # cortas: quedan ilegibles
    n = len(lineas)
    ini = np.frombuffer(b"".join(ln[:10] for ln in lineas), dtype=np.uint8).reshape(n, 10)
    fin = np.frombuffer(b"".join(ln[-D10_FIN:] for ln in lineas), dtype=np.uint8).reshape(n, D10_FIN)
    return cabecera, ini, fin, largos


def leer_d10(path: Path) -> tuple[int, date, pl.DataFrame, dict]:
    """Lee un archivo D10 (una IF, una fecha). Devuelve (código IF, fecha, filas canónicas, conteos).

    Sólo se toman los primeros 10 bytes (RUT + DV) y los últimos 18 de cada registro; el nombre queda sin leer.
    """
    cabecera, ini, fin, largos = _campos_fijos(_abrir(path))
    if_id, fecha = cabecera_d10(cabecera)
    cortas = largos < 10 + D10_FIN
    rut, ok_rut = _digitos(ini[:, D10_RUT])
    td, ok_td = _digitos(fin[:, 0:1])
    tc, ok_tc = _digitos(fin[:, 1:3])
    mora, ok_mora = _digitos(fin[:, 3:4])
    monto, ok_monto = _digitos(fin[:, 4:18])
    ok = ok_rut & ok_td & ok_tc & ok_mora & ok_monto & ~cortas
    dv_byte = ini[:, D10_DV]
    dv_leido = np.where((dv_byte == ord("K")) | (dv_byte == ord("k")), 10, dv_byte.astype(np.int64) - 48)
    dv_ok = dv_leido == _dv_num(np.where(ok_rut, rut, 0))
    df = pl.DataFrame({
        "rut": rut[ok], "tipo_deudor": td[ok].astype(np.int16), "tipo_credito": tc[ok].astype(np.int16),
        "tipo_mora": mora[ok].astype(np.int16), "monto": monto[ok].astype(np.float64),
    }).with_columns(pl.lit(if_id, pl.Int64).alias("if_id"), pl.lit(fecha, pl.Date).alias("periodo"))
    conteos = {
        "registros": int(len(largos)),
        "registros_ilegibles": int((~ok).sum()),
        "registros_largo_distinto_78": int((largos[~cortas] != LARGO_D10).sum()),
        "rut_dv_invalido": int((~dv_ok & ok).sum()),
    }
    return if_id, fecha, df, conteos


def importar_d10(cfg: Config, archivos: list[Path], reemplazar: bool) -> list[Path]:
    st = Staging(cfg, {"tipo": "d10", "entrada": str(cfg.importacion.entrada), "n_archivos": len(archivos),
                       "muestra_pct": cfg.importacion.muestra_pct}, reemplazar)
    # 1) cabeceras: qué IF y qué fecha trae cada archivo
    por_fecha: dict[date, dict[int, list[Path]]] = defaultdict(lambda: defaultdict(list))
    ilegibles = []
    for a in archivos:
        try:
            if_id, fecha = cabecera_d10(_primera_linea(a))
        except ValueError as e:
            ilegibles.append(a.name)
            log.warning("%s: %s (se omite)", a.name, e)
            continue
        por_fecha[fecha][if_id].append(a)
    periodos = [f for f in filtro_periodos(por_fecha, cfg) if not st.existe(f)]
    log.info("%s archivos, %s fechas distintas, %s cierres de mes por importar, %s archivos sin cabecera válida",
             len(archivos), len(por_fecha), len(periodos), len(ilegibles))
    # 2) un periodo a la vez; si una IF envió más de un archivo para la fecha, vale el más reciente
    salida = []
    for fecha in periodos:
        dfs, total = [], defaultdict(int)
        for if_id, lista in sorted(por_fecha[fecha].items()):
            elegido = max(lista, key=lambda p: (p.stat().st_mtime, p.name))
            total["archivos_reemplazados"] += len(lista) - 1
            _, _, df, conteos = leer_d10(elegido)
            for k, v in conteos.items():
                total[k] += v
            if cfg.importacion.muestra_pct < 100:
                df = df.filter(muestra_expr(cfg.importacion.muestra_pct))
            dfs.append(df)
        total["archivos"] = len(por_fecha[fecha])
        salida.append(st.escribir(fecha, dfs, {"lectura_d10": dict(total)}))
    return salida
