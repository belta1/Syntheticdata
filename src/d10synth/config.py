"""Carga de la configuración YAML en dataclasses."""

from dataclasses import dataclass, field, fields, is_dataclass
from pathlib import Path

import yaml


@dataclass
class Fuente:
    driver: str = "ODBC Driver 17 for SQL Server"
    servidor: str = "srvdatscience02"
    base: str = "cmfdat"
    tabla: str = "dbo.D10C"
    trusted_connection: bool = True


@dataclass
class Columnas:
    if_id: str = "rutis_sbif"
    periodo: str = "periodo"
    rut: str = "rut_deudor"
    tipo_deudor: str = "tipo_deudor"
    tipo_credito: str = "tipo_credito"
    tipo_mora: str = "tipo_mora"
    monto: str = "monto"

    def mapa(self) -> dict[str, str]:
        """Nombre en la fuente -> nombre canónico."""
        return {getattr(self, f.name): f.name for f in fields(self)}


@dataclass
class Formato:
    periodo: str = "auto"
    rut: str = "auto"
    umbral_rut_empresa: int = 50_000_000


@dataclass
class Csv:
    separador: str = ";"
    encabezado: bool = True
    nombres: list[str] | None = None  # nombres de columnas cuando el archivo no trae encabezado
    encoding: str = "utf8"  # utf8 | utf8-lossy
    decimal: str = "."


@dataclass
class Importacion:
    tipo: str = "sql"  # sql | csv | parquet | d10
    entrada: str | None = None  # archivo, carpeta o patrón glob (csv, parquet, d10)
    desde: str | None = None
    hasta: str | None = None
    muestra_pct: float = 100
    batch_filas: int = 500_000
    csv: Csv = field(default_factory=Csv)


@dataclass
class Rutas:
    real: str = "data/real"
    modelo: str = "modelos/d10"
    sintetico: str = "data/sintetico"
    reportes: str = "reportes"


@dataclass
class Ajuste:
    holdout_pct: float = 20
    k_min: int = 10
    k_grupo: int = 200
    max_patrones: int = 3000
    max_perfiles_mora: int = 8
    n_cuantiles: int = 201
    cuantil_min: float = 0.001
    cuantil_max: float = 0.999
    max_if_por_deudor: int = 12


@dataclass
class Generacion:
    deudores: int = 200_000
    meses: int = 12
    periodo_inicio: str | None = None
    chunk_deudores: int = 250_000
    semilla: int = 42
    dispositivo: str = "auto"
    excluir_ruts_reales: bool = True
    rut_salida: str = "sin_dv"


@dataclass
class Evaluacion:
    muestra_privacidad: int = 50_000
    semilla: int = 7


@dataclass
class Config:
    fuente: Fuente = field(default_factory=Fuente)
    columnas: Columnas = field(default_factory=Columnas)
    formato: Formato = field(default_factory=Formato)
    importacion: Importacion = field(default_factory=Importacion)
    rutas: Rutas = field(default_factory=Rutas)
    ajuste: Ajuste = field(default_factory=Ajuste)
    generacion: Generacion = field(default_factory=Generacion)
    evaluacion: Evaluacion = field(default_factory=Evaluacion)


def _build(cls, data: dict | None):
    data = data or {}
    validos = {f.name: f for f in fields(cls)}
    desconocidos = set(data) - set(validos)
    if desconocidos:
        raise ValueError(f"Claves desconocidas en sección {cls.__name__}: {sorted(desconocidos)}")
    kwargs = {}
    for nombre, valor in data.items():
        tipo = validos[nombre].default_factory if validos[nombre].default_factory is not None else None
        kwargs[nombre] = _build(tipo, valor) if tipo is not None and is_dataclass(tipo) else valor
    return cls(**kwargs)


def load_config(path: str | Path | None = None) -> Config:
    """Carga la configuración. Las rutas relativas se interpretan desde el directorio de trabajo."""
    if path is None:
        return Config()
    with open(path, encoding="utf-8") as f:
        return _build(Config, yaml.safe_load(f))


def ruta(p: str | Path) -> Path:
    return Path(p).expanduser()
