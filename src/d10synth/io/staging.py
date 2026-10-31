"""Staging: la data real importada, en esquema canónico, con un parquet por cierre de mes.

Todas las vías de importación (SQL, CSV, parquet y archivos D10 originales) escriben lo mismo:

    if_id Int64 | periodo Date | rut Int64 (sin DV) | tipo_deudor, tipo_credito, tipo_mora Int16 | monto Float64

Además dejan un manifiesto `_manifiesto.json` con el origen, las filas por periodo y el SHA-256 de cada archivo.
Las filas se copian tal cual, incluidas las que no cumplen el manual: el ajuste las descarta y el perfil las cuenta.
"""

import hashlib
import json
import logging
import time
from collections.abc import Iterable
from datetime import date, datetime
from pathlib import Path

import polars as pl
import pyarrow.parquet as pq

from d10synth.config import Config

log = logging.getLogger(__name__)

ESQUEMA = "canonico-v1"
MANIFIESTO = "_manifiesto.json"
DTYPES = {
    "if_id": pl.Int64,
    "periodo": pl.Date,
    "rut": pl.Int64,
    "tipo_deudor": pl.Int16,
    "tipo_credito": pl.Int16,
    "tipo_mora": pl.Int16,
    "monto": pl.Float64,
}


def archivo_periodo(carpeta: str | Path, fecha: date) -> Path:
    return Path(carpeta) / f"periodo={fecha:%Y%m%d}.parquet"


def leer_manifiesto(carpeta: str | Path) -> dict | None:
    p = Path(carpeta) / MANIFIESTO
    if not p.exists():
        return None
    return json.loads(p.read_text(encoding="utf-8"))


def es_canonico(carpeta: str | Path) -> bool:
    m = leer_manifiesto(carpeta) if Path(carpeta).is_dir() else None
    return bool(m) and m.get("esquema") == ESQUEMA


def sha256(path: Path, bloque: int = 1 << 24) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(bloque):
            h.update(chunk)
    return h.hexdigest()


def a_canonico(df: pl.DataFrame | pl.LazyFrame, cfg: Config) -> pl.LazyFrame:
    """Columnas de la fuente (según cfg.columnas y cfg.formato) -> esquema canónico de staging."""
    from d10synth.prep import normalize

    return normalize(df.lazy(), cfg, solo_cierre_mes=False).select([pl.col(c).cast(t) for c, t in DTYPES.items()])


def muestra_expr(pct: float) -> pl.Expr:
    """Muestra estable de deudores por hash del RUT, independiente del hash del holdout (prep.holdout_expr)."""
    h = (pl.col("rut") * 2246822519 + 3266489917) % 4294967296
    return (h % 10000) < int(round(pct * 100))


def filtro_periodos(fechas: Iterable[date], cfg: Config) -> list[date]:
    """Cierres de mes dentro de [desde, hasta]."""
    imp = cfg.importacion
    desde = date.fromisoformat(imp.desde) if imp.desde else date.min
    hasta = date.fromisoformat(imp.hasta) if imp.hasta else date.max
    s = pl.Series("f", sorted(set(fechas)), dtype=pl.Date)
    return [f for f, fin in zip(s.to_list(), s.dt.month_end().to_list()) if f == fin and desde <= f <= hasta]


class Staging:
    """Carpeta de staging (rutas.real). Reanudable: un periodo ya escrito se salta, salvo con reemplazar=True."""

    def __init__(self, cfg: Config, origen: dict, reemplazar: bool = False):
        self.carpeta = Path(cfg.rutas.real)
        self.carpeta.mkdir(parents=True, exist_ok=True)
        self.reemplazar = reemplazar
        previo = leer_manifiesto(self.carpeta)
        sueltos = [p for p in self.carpeta.glob("*.parquet")]
        if previo is None and sueltos and not reemplazar:
            raise ValueError(
                f"{self.carpeta} tiene parquet sin manifiesto (¿data mock o de otra versión?). "
                "Use otra carpeta en rutas.real o --reemplazar para borrarlos."
            )
        if previo is not None and previo.get("origen", {}).get("tipo") != origen["tipo"] and not reemplazar:
            raise ValueError(
                f"{self.carpeta} ya tiene data importada desde '{previo['origen']['tipo']}'. "
                f"No se mezclan orígenes: use otra carpeta en rutas.real o --reemplazar."
            )
        if reemplazar and (previo is None or previo.get("origen", {}).get("tipo") != origen["tipo"]):
            for p in sueltos:
                p.unlink()
            previo = None
        self.manifiesto = previo or {"esquema": ESQUEMA, "periodos": {}}
        self.manifiesto["origen"] = origen
        self.manifiesto["esquema"] = ESQUEMA
        self._guardar()

    def existe(self, fecha: date) -> bool:
        return not self.reemplazar and f"{fecha:%Y-%m-%d}" in self.manifiesto["periodos"] and archivo_periodo(self.carpeta, fecha).exists()

    def escribir(self, fecha: date, lotes: Iterable[pl.DataFrame], extra: dict | None = None) -> Path:
        """Escribe un periodo a partir de lotes en esquema canónico (escritura atómica: .tmp y luego rename)."""
        t0 = time.time()
        archivo = archivo_periodo(self.carpeta, fecha)
        tmp = archivo.with_suffix(".tmp")
        writer = None
        try:
            for lote in lotes:
                if lote.height == 0:
                    continue
                tabla = lote.select([pl.col(c).cast(t) for c, t in DTYPES.items()]).to_arrow()
                if writer is None:
                    writer = pq.ParquetWriter(tmp, tabla.schema, compression="zstd")
                writer.write_table(tabla)
        finally:
            if writer is not None:
                writer.close()
        if writer is None:
            log.warning("%s: sin filas, no se escribe", archivo.name)
            return archivo
        stats = self._estadisticas(tmp, fecha)
        tmp.replace(archivo)
        stats.update(archivo=archivo.name, sha256=sha256(archivo), segundos=round(time.time() - t0, 1), **(extra or {}))
        self.manifiesto["periodos"][f"{fecha:%Y-%m-%d}"] = stats
        self._guardar()
        log.info("%s: %s filas, %s deudores (%.0fs)", archivo.name, f"{stats['filas']:,}", f"{stats['deudores']:,}", time.time() - t0)
        return archivo

    @staticmethod
    def _estadisticas(path: Path, fecha: date) -> dict:
        from d10synth.prep import valid_expr

        s = pl.scan_parquet(path).select(
            pl.len().alias("filas"),
            pl.col("rut").n_unique().alias("deudores"),
            pl.col("if_id").n_unique().alias("ifs"),
            pl.col("monto").sum().alias("monto_total"),
            (~valid_expr()).fill_null(True).sum().alias("filas_invalidas"),
            (pl.col("periodo") != fecha).fill_null(True).sum().alias("filas_otro_periodo"),
        ).collect().row(0, named=True)
        return {k: (float(v) if k == "monto_total" else int(v)) for k, v in s.items()}

    def _guardar(self):
        self.manifiesto["actualizado"] = datetime.now().isoformat(timespec="seconds")
        self.manifiesto["periodos"] = dict(sorted(self.manifiesto["periodos"].items()))
        p = self.carpeta / MANIFIESTO
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.manifiesto, indent=2, ensure_ascii=False), encoding="utf-8")
        tmp.replace(p)


def verificar(carpeta: str | Path) -> dict:
    """Revisa la integridad del staging: hash, tipos y filas de cada archivo, y meses faltantes entre el primero y el último."""
    carpeta = Path(carpeta)
    m = leer_manifiesto(carpeta)
    if m is None:
        raise FileNotFoundError(f"No hay {MANIFIESTO} en {carpeta}: la carpeta no fue escrita por 'd10synth import'")
    problemas, filas = [], []
    for clave, st in m["periodos"].items():
        p = carpeta / st["archivo"]
        if not p.exists():
            problemas.append(f"{clave}: falta el archivo {p.name}")
            continue
        if sha256(p) != st["sha256"]:
            problemas.append(f"{clave}: el SHA-256 de {p.name} no coincide con el manifiesto (archivo modificado)")
        esquema = pl.read_parquet_schema(p)
        if dict(esquema) != DTYPES:
            problemas.append(f"{clave}: esquema inesperado {dict(esquema)}")
        n = pq.ParquetFile(p).metadata.num_rows
        if n != st["filas"]:
            problemas.append(f"{clave}: {n} filas en disco vs {st['filas']} en el manifiesto")
        filas.append({"periodo": clave, **{k: st[k] for k in ("filas", "deudores", "ifs", "monto_total", "filas_invalidas")}})
    extra = sorted({p.name for p in carpeta.glob("*.parquet")} - {st["archivo"] for st in m["periodos"].values()})
    if extra:
        problemas.append(f"archivos parquet fuera del manifiesto: {extra}")
    faltantes = []
    if m["periodos"]:
        fechas = sorted(date.fromisoformat(k) for k in m["periodos"])
        s = pl.date_range(fechas[0].replace(day=1), fechas[-1], "1mo", eager=True).dt.month_end().to_list()
        faltantes = [f"{f:%Y-%m-%d}" for f in s if f not in set(fechas)]
    return {
        "origen": m.get("origen"),
        "esquema": m.get("esquema"),
        "periodos": filas,
        "meses_faltantes": faltantes,
        "problemas": problemas,
        "ok": not problemas,
    }
