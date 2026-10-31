"""Diagnóstico del entorno: versiones, GPU, drivers ODBC, espacio en disco y una prueba corta de muestreo."""

import importlib.metadata as md
import platform
import shutil
import sys
import time
from pathlib import Path

from d10synth.config import Config

PAQUETES = ["d10synth", "polars", "pyarrow", "numpy", "torch", "pyyaml", "scikit-learn", "pyodbc", "pytest"]


def _version(p: str) -> str:
    try:
        return md.version(p)
    except md.PackageNotFoundError:
        return "NO INSTALADO"


def diagnostico(cfg: Config) -> list[tuple[str, str, str]]:
    """Lista de (chequeo, resultado, estado) con estado OK | AVISO | ERROR."""
    out = []
    py = sys.version.split()[0]
    out.append(("python", f"{py} ({platform.system()} {platform.release()})", "OK" if sys.version_info[:2] >= (3, 12) else "ERROR"))
    for p in PAQUETES:
        v = _version(p)
        estado = "OK" if v != "NO INSTALADO" else ("AVISO" if p in ("pyodbc", "pytest") else "ERROR")
        out.append((p, v, estado))

    import torch

    if torch.cuda.is_available():
        props = torch.cuda.get_device_properties(0)
        out.append(("gpu", f"{props.name}, {props.total_memory / 2**30:.1f} GiB, CUDA {torch.version.cuda}", "OK"))
        try:
            t0 = time.time()
            x = torch.rand(10_000_000, device="cuda")
            torch.searchsorted(torch.sort(x).values, torch.rand(10_000_000, device="cuda"))
            torch.cuda.synchronize()
            out.append(("prueba gpu", f"searchsorted 10M en {time.time() - t0:.2f}s", "OK"))
        except Exception as e:  # noqa: BLE001
            out.append(("prueba gpu", repr(e), "ERROR"))
    else:
        cuda = torch.version.cuda
        detalle = "torch sin soporte CUDA (wheel CPU)" if cuda is None else f"torch compilado con CUDA {cuda} pero no ve una GPU (driver)"
        out.append(("gpu", detalle + "; se usará CPU", "AVISO"))

    try:
        import pyodbc

        drivers = [d for d in pyodbc.drivers() if "SQL Server" in d]
        estado = "OK" if cfg.fuente.driver in drivers else ("AVISO" if cfg.importacion.tipo != "sql" else "ERROR")
        out.append(("odbc", f"{drivers or 'ningún driver SQL Server'}; configurado: {cfg.fuente.driver}", estado))
    except ImportError:
        out.append(("odbc", "pyodbc no instalado (sólo necesario para importacion.tipo = sql)", "AVISO" if cfg.importacion.tipo != "sql" else "ERROR"))

    vistos = set()
    for nombre in ("real", "modelo", "sintetico", "reportes"):
        p = Path(getattr(cfg.rutas, nombre)).resolve()
        ancla = p.anchor or str(p)
        if ancla in vistos:
            continue
        vistos.add(ancla)
        p_existente = next((q for q in [p, *p.parents] if q.exists()), Path(ancla))
        libre = shutil.disk_usage(p_existente).free / 2**30
        out.append((f"disco {ancla}", f"{libre:,.0f} GiB libres", "OK" if libre > 50 else "AVISO"))
    return out
