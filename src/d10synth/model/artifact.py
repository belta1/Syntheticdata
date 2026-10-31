"""Persistencia del modelo: arreglos en .npz y metadatos en .json (sólo agregados)."""

import json
from dataclasses import dataclass
from pathlib import Path

import numpy as np


@dataclass
class Modelo:
    arrays: dict[str, np.ndarray]
    meta: dict

    def __getitem__(self, k: str) -> np.ndarray:
        return self.arrays[k]


def save(modelo: Modelo, path: str | Path) -> Path:
    path = Path(path)
    path.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(path / "modelo.npz", **modelo.arrays)
    (path / "modelo.json").write_text(json.dumps(modelo.meta, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    return path


def load(path: str | Path) -> Modelo:
    path = Path(path)
    with np.load(path / "modelo.npz", allow_pickle=False) as z:
        arrays = {k: z[k] for k in z.files}
    meta = json.loads((path / "modelo.json").read_text(encoding="utf-8"))
    return Modelo(arrays, meta)
