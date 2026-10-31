"""Helpers de muestreo vectorizado en torch (GPU si está disponible)."""

import numpy as np
import torch


def get_device(pref: str = "auto") -> torch.device:
    if pref == "auto":
        return torch.device("cuda" if torch.cuda.is_available() else "cpu")
    if pref == "cuda" and not torch.cuda.is_available():
        raise RuntimeError("Se pidió dispositivo cuda pero torch no detecta GPU")
    return torch.device(pref)


def ndtri(p: np.ndarray) -> np.ndarray:
    """Inversa de la normal estándar."""
    return torch.special.ndtri(torch.tensor(np.asarray(p, dtype=np.float64))).numpy()


def ndtr(x: np.ndarray) -> np.ndarray:
    """CDF de la normal estándar."""
    return torch.special.ndtr(torch.tensor(np.asarray(x, dtype=np.float64))).numpy()


def normal_grid(n: int, p_min: float, p_max: float) -> tuple[np.ndarray, np.ndarray]:
    """Grilla equiespaciada en escala normal (más densa en las colas). Devuelve (scores, probabilidades)."""
    g = np.linspace(ndtri(np.array(p_min)), ndtri(np.array(p_max)), n)
    return g, ndtr(g)


def normalize_rows(p: np.ndarray) -> np.ndarray:
    """Normaliza la última dimensión; filas vacías quedan con toda la masa en la categoría 0."""
    p = np.asarray(p, dtype=np.float64).copy()
    s = p.sum(axis=-1, keepdims=True)
    vacias = s[..., 0] <= 0
    p[vacias] = 0.0
    p[vacias, 0] = 1.0
    s = p.sum(axis=-1, keepdims=True)
    return p / s


class RowSampler:
    """Muestreo categórico condicional: cada fila de `probs` es una distribución.

    Usa una única CDF aplanada con offsets (fila r ocupa (r, r+1]) y `searchsorted`,
    así millones de muestras con filas distintas se resuelven en una sola llamada.
    """

    def __init__(self, probs: np.ndarray, device: torch.device):
        p = normalize_rows(probs).reshape(-1, probs.shape[-1])
        self.n_rows, self.k = p.shape
        cdf = np.cumsum(p, axis=1)
        cdf[:, -1] = 1.0
        cdf += np.arange(self.n_rows)[:, None]
        self.flat = torch.as_tensor(cdf.reshape(-1), dtype=torch.float64, device=device)
        self.device = device

    def sample(self, row: torch.Tensor) -> torch.Tensor:
        row = row.long()
        u = torch.rand(row.shape[0], dtype=torch.float64, device=self.device)
        idx = torch.searchsorted(self.flat, row.double() + u, right=True)
        return (idx - row * self.k).clamp_(0, self.k - 1)


class QuantileTable:
    """Funciones cuantil por fila sobre una grilla normal; se evalúan en scores normales (interpolación lineal)."""

    def __init__(self, q: np.ndarray, g_min: float, g_max: float, device: torch.device):
        q = np.asarray(q, dtype=np.float64)
        self.nq = q.shape[-1]
        self.flat = torch.as_tensor(q.reshape(-1), dtype=torch.float32, device=device)
        self.g_min, self.g_max = float(g_min), float(g_max)

    def __call__(self, row: torch.Tensor, g: torch.Tensor) -> torch.Tensor:
        pos = ((g - self.g_min) / (self.g_max - self.g_min) * (self.nq - 1)).clamp_(0, self.nq - 1)
        lo = pos.floor().long().clamp_(max=self.nq - 2)
        w = (pos - lo).float()
        base = row.long() * self.nq + lo
        return self.flat[base] * (1 - w) + self.flat[base + 1] * w


def bits_to_matrix(mask: torch.Tensor, n_bits: int) -> torch.Tensor:
    """(n,) int64 de bitmasks -> (n, n_bits) bool."""
    shifts = torch.arange(n_bits, device=mask.device, dtype=torch.int64)
    return ((mask.unsqueeze(1) >> shifts) & 1).bool()
