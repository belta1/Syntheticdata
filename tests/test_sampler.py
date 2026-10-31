import numpy as np
import torch

from d10synth._torch import QuantileTable, RowSampler, bits_to_matrix, normal_grid

DEV = torch.device("cpu")


def test_row_sampler_frecuencias():
    torch.manual_seed(0)
    probs = np.array([[0.2, 0.0, 0.8], [0.0, 1.0, 0.0], [0.0, 0.0, 0.0]])
    s = RowSampler(probs, DEV)
    n = 200_000
    for r, esperado in enumerate([probs[0], probs[1], [1.0, 0.0, 0.0]]):
        x = s.sample(torch.full((n,), r))
        frec = np.bincount(x.numpy(), minlength=3) / n
        assert np.allclose(frec, esperado, atol=0.01)


def test_row_sampler_filas_mezcladas():
    torch.manual_seed(1)
    probs = np.random.default_rng(0).dirichlet(np.ones(5), size=50)
    s = RowSampler(probs, DEV)
    rows = torch.randint(0, 50, (500_000,))
    x = s.sample(rows)
    assert int(x.min()) >= 0 and int(x.max()) <= 4
    r0 = x[rows == 7].numpy()
    assert np.allclose(np.bincount(r0, minlength=5) / len(r0), probs[7], atol=0.02)


def test_quantile_table_interpola():
    g, p = normal_grid(101, 0.001, 0.999)
    q = np.stack([g, 2 * g + 1])  # fila 0: identidad en score normal; fila 1: lineal
    t = QuantileTable(q, g[0], g[-1], DEV)
    z = torch.tensor([-1.0, 0.0, 0.5, 10.0])
    assert torch.allclose(t(torch.zeros(4, dtype=torch.long), z), torch.tensor([-1.0, 0.0, 0.5, float(g[-1])]), atol=1e-4)
    assert torch.allclose(t(torch.ones(4, dtype=torch.long), z)[:3], 2 * z[:3] + 1, atol=1e-4)


def test_bits_to_matrix():
    m = bits_to_matrix(torch.tensor([0b101, 0b10]), 3)
    assert m.tolist() == [[True, False, True], [False, True, False]]
