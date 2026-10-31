"""Generación del panel sintético con muestreo vectorizado en torch (GPU si está disponible)."""

import calendar
import logging
import math
import time
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import numpy as np
import polars as pl
import torch

from d10synth import schema
from d10synth._torch import QuantileTable, RowSampler, bits_to_matrix, get_device
from d10synth.config import Config
from d10synth.model.artifact import Modelo, load

log = logging.getLogger(__name__)

RUT_MIN_PERSONA = 1_000_000
RUT_MAX = 99_999_999


class RutAllocator:
    """Asigna RUTs únicos por segmento con una permutación afín del rango (i -> lo + (a*i + b) mod N).

    Opcionalmente excluye RUTs reales para que ningún registro sintético comparta RUT con un deudor real.
    """

    def __init__(self, umbral: int, excluir: np.ndarray | None, seed: int):
        rng = np.random.default_rng(seed)
        self.rangos = [(RUT_MIN_PERSONA, umbral - 1), (umbral, RUT_MAX)]
        self.params = []
        for lo, hi in self.rangos:
            n = hi - lo + 1
            a = int(rng.integers(n // 3, n))
            while math.gcd(a, n) != 1:
                a += 1
            self.params.append((lo, n, a, int(rng.integers(0, n))))
        self.next = [0, 0]
        self.excluir = np.unique(excluir) if excluir is not None else np.array([], dtype=np.int64)

    def take(self, seg: int, k: int) -> np.ndarray:
        lo, n, a, b = self.params[seg]
        out = []
        falta = k
        while falta > 0:
            if self.next[seg] >= n:
                raise RuntimeError(f"Se agotaron los RUTs del segmento {schema.SEGMENTOS[seg]}")
            i = np.arange(self.next[seg], min(n, self.next[seg] + int(falta * 1.1) + 16), dtype=np.int64)
            ruts = lo + (a * i + b) % n
            ok = np.nonzero(~np.isin(ruts, self.excluir))[0] if len(self.excluir) else np.arange(len(ruts))
            ok = ok[:falta]
            self.next[seg] += int(ok[-1]) + 1 if len(ok) == falta else len(i)
            out.append(ruts[ok])
            falta -= len(ok)
        return np.concatenate(out) if out else np.array([], dtype=np.int64)


@dataclass
class Celdas:
    """Estado de las celdas vivas de un chunk (ordenadas por deudor)."""

    deb: torch.Tensor     # índice local del deudor
    ifx: torch.Tensor     # índice de IF
    slot: torch.Tensor
    mora: torch.Tensor
    lmonto: torch.Tensor  # log(monto total de la celda)

    def filter(self, keep: torch.Tensor) -> "Celdas":
        return Celdas(self.deb[keep], self.ifx[keep], self.slot[keep], self.mora[keep], self.lmonto[keep])

    @staticmethod
    def cat(a: "Celdas", b: "Celdas") -> "Celdas":
        return Celdas(*[torch.cat([getattr(a, f), getattr(b, f)]) for f in ("deb", "ifx", "slot", "mora", "lmonto")])

    def sort(self) -> "Celdas":
        orden = torch.argsort(self.deb, stable=True)
        return self.filter(orden)


class Generador:
    def __init__(self, modelo: Modelo, device: torch.device):
        self.m = modelo
        self.dev = device
        A = modelo.arrays
        self.n_if = int(modelo.meta["n_if"])
        self.cap_if = A["p_nif"].shape[1]
        t = lambda x, dt=torch.float32: torch.as_tensor(np.asarray(x), dtype=dt, device=device)  # noqa: E731

        self.p_seg = t(A["p_seg"] / A["p_seg"].sum())
        self.s_nif = RowSampler(A["p_nif"], device)
        self.s_if = RowSampler(A["if_share"], device)
        cooc = A["if_cooc"].copy()
        sin_cooc = cooc.sum(-1) == 0
        cooc[sin_cooc] = A["if_share"][np.nonzero(sin_cooc)[0]]
        self.cooc = t(cooc)
        self.patterns = t(A["patterns"], torch.int64)
        self.s_pattern = RowSampler(A["p_pattern"], device)
        self.s_mora = RowSampler(A["p_mora"], device)
        self.prof_mask = t(A["prof_mask"], torch.int64).reshape(-1)
        self.nprof = A["prof_mask"].shape[-1]
        self.s_prof = RowSampler(A["prof_p"], device)
        self.prof_alpha = t(A["prof_alpha"]).reshape(-1, schema.N_MORA)
        g_min, g_max = modelo.meta["g_min"], modelo.meta["g_max"]
        self.q_amount = QuantileTable(A["q_amount"], g_min, g_max, device)
        self.q_growth = QuantileTable(A["q_growth"], g_min, g_max, device)
        self.rho = t(A["rho"])
        self.p_exit_debtor = t(A["p_exit_debtor"])
        self.entry_rate = A["entry_rate"]
        self.p_exit_cell = t(A["p_exit_cell"]).reshape(-1)
        self.s_trans = RowSampler(A["trans"], device)
        self.lam_new = t(A["lam_new"])
        self.p_same_if = t(A["p_new_same_if"])
        self.s_new_slot = RowSampler(A["p_new_slot"], device)
        self.s_new_mora = RowSampler(A["p_new_mora"], device)
        self.tc_codes = torch.as_tensor(schema.TC_CODES, device=device)

    # --- foto inicial -------------------------------------------------------------------------
    def nuevos_deudores(self, n: int, seg: torch.Tensor | None = None):
        """Crea n deudores con sus celdas. Devuelve (seg, z, Celdas con deb en 0..n-1)."""
        dev = self.dev
        if seg is None:
            seg = torch.multinomial(self.p_seg, n, replacement=True)
        z = torch.randn(n, device=dev)
        n_ifs = self.s_nif.sample(seg) + 1
        primera = self.s_if.sample(seg)

        # IFs adicionales: Gumbel-top-k sobre la co-ocurrencia con la primera IF
        k_extra = self.cap_if - 1
        if k_extra > 0 and int(n_ifs.max()) > 1:
            w = self.cooc[seg, primera]
            w[torch.arange(n, device=dev), primera] = 0
            claves = torch.log(w) - torch.log(-torch.log(torch.rand_like(w).clamp_min(1e-12)))
            top_v, top_i = torch.topk(claves, k_extra, dim=1)
            usar = (torch.arange(k_extra, device=dev)[None, :] < (n_ifs - 1)[:, None]) & torch.isfinite(top_v)
            deb_extra = torch.arange(n, device=dev)[:, None].expand(-1, k_extra)[usar]
            if_extra = top_i[usar]
        else:
            deb_extra = if_extra = torch.empty(0, dtype=torch.long, device=dev)
        p_deb = torch.cat([torch.arange(n, device=dev), deb_extra])
        p_if = torch.cat([primera, if_extra])

        # Patrón de productos por (deudor, IF) -> celdas
        pat_idx = self.s_pattern.sample(seg[p_deb] * self.n_if + p_if)
        bits = bits_to_matrix(self.patterns[pat_idx], schema.N_SLOTS)
        par, slot = torch.nonzero(bits, as_tuple=True)
        deb, ifx = p_deb[par], p_if[par]
        s = seg[deb]
        mora = self.s_mora.sample(s * schema.N_SLOTS + slot)
        lmonto = self.monto_inicial(s, slot, mora, z[deb])
        return seg, z, Celdas(deb, ifx, slot, mora, lmonto).sort()

    def monto_inicial(self, seg, slot, mora, z_deb):
        r = self.rho[seg]
        g = r * z_deb + torch.sqrt(1 - r * r) * torch.randn_like(z_deb)
        return self.q_amount((seg * schema.N_SLOTS + slot) * schema.N_MORA + mora, g)

    # --- paso mensual ----------------------------------------------------------------------------
    def paso(self, seg, z, vivo, c: Celdas) -> tuple[torch.Tensor, Celdas]:
        dev = self.dev
        # salida de deudores
        sale = torch.rand(seg.shape[0], device=dev) < self.p_exit_debtor[seg]
        vivo = vivo & ~sale
        c = c.filter(vivo[c.deb])
        # salida de celdas
        s = seg[c.deb]
        tc = c.slot % schema.N_TC
        p_sal = self.p_exit_cell[(s * schema.N_TC + tc) * schema.N_MORA + c.mora]
        c = c.filter(torch.rand_like(p_sal) >= p_sal)
        tc = c.slot % schema.N_TC
        # transición de mora y evolución del monto
        mora1 = self.s_trans.sample(tc * schema.N_MORA + c.mora)
        r = self.q_growth((tc * schema.N_MORA + c.mora) * schema.N_MORA + mora1, torch.randn_like(c.lmonto))
        c = Celdas(c.deb, c.ifx, c.slot, mora1, c.lmonto + r)
        # celdas nuevas en deudores vivos. Un deudor que continúa tiene al menos una celda en t+1
        # (así se definió al ajustar la salida), por lo que si perdió todas recibe al menos una nueva.
        idx_vivos = torch.nonzero(vivo).squeeze(1)
        sin_celdas = torch.bincount(c.deb, minlength=seg.shape[0])[idx_vivos] == 0
        n_new = torch.maximum(torch.poisson(self.lam_new[seg[idx_vivos]]).long(), sin_celdas.long())
        if int(n_new.sum()) > 0:
            c = Celdas.cat(c, self.celdas_nuevas(seg, z, idx_vivos.repeat_interleave(n_new), c))
            c = self.dedup(c)
        return vivo, c.sort()

    def celdas_nuevas(self, seg, z, deb, c: Celdas) -> Celdas:
        """c debe venir ordenado por deudor."""
        dev = self.dev
        s = seg[deb]
        # IF: con prob p_same_if, una IF donde el deudor ya opera; si no (o no tiene celdas), según participación
        ifx = self.s_if.sample(s)
        if c.deb.shape[0] > 0:
            cuenta = torch.bincount(c.deb, minlength=seg.shape[0])
            inicio = torch.cumsum(cuenta, 0) - cuenta
            k = cuenta[deb]
            misma = (torch.rand(deb.shape[0], device=dev) < self.p_same_if[s]) & (k > 0)
            pos = (inicio[deb] + (torch.rand(deb.shape[0], device=dev) * k).long()).clamp(0, c.deb.shape[0] - 1)
            ifx = torch.where(misma, c.ifx[pos], ifx)
        slot = self.s_new_slot.sample(s)
        mora = self.s_new_mora.sample(slot % schema.N_TC)
        lmonto = self.monto_inicial(s, slot, mora, z[deb])
        return Celdas(deb, ifx, slot, mora, lmonto)

    def dedup(self, c: Celdas) -> Celdas:
        """Elimina celdas nuevas que repiten (deudor, IF, slot); prevalece la primera (la existente)."""
        clave = (c.deb * self.n_if + c.ifx) * schema.N_SLOTS + c.slot
        _, inv = torch.unique(clave, return_inverse=True)
        primera = torch.full((int(inv.max()) + 1,), c.deb.shape[0], device=self.dev, dtype=torch.long)
        primera.scatter_reduce_(0, inv, torch.arange(c.deb.shape[0], device=self.dev), reduce="amin")
        keep = torch.zeros(c.deb.shape[0], dtype=torch.bool, device=self.dev)
        keep[primera] = True
        return c.filter(keep)

    # --- salida -----------------------------------------------------------------------------------
    def filas(self, c: Celdas) -> dict[str, torch.Tensor]:
        """Expande celdas a filas por código de mora (perfil de mora + reparto Dirichlet del monto)."""
        tc = c.slot % schema.N_TC
        fila_perfil = tc * schema.N_MORA + c.mora
        j = self.s_prof.sample(fila_perfil)
        k = fila_perfil * self.nprof + j
        bit_max = torch.bitwise_left_shift(torch.ones_like(c.mora), c.mora)
        mask = self.prof_mask[k] | bit_max  # la mora máxima siempre está presente
        bits = bits_to_matrix(mask, schema.N_MORA)
        alpha = torch.where(bits, self.prof_alpha[k], torch.ones_like(self.prof_alpha[k]))
        gam = torch._standard_gamma(alpha) * bits
        share = gam / gam.sum(1, keepdim=True).clamp_min(1e-30)
        cel, cod = torch.nonzero(bits, as_tuple=True)
        monto = torch.clamp(torch.round(torch.exp(c.lmonto[cel].double()) * share[cel, cod].double()), min=1).long()
        return {
            "deb": c.deb[cel], "ifx": c.ifx[cel], "slot": c.slot[cel], "mora": cod, "monto": monto,
        }


def _meses(inicio: date, n: int) -> list[date]:
    out = []
    for i in range(n):
        y, m = divmod(inicio.month - 1 + i, 12)
        y, m = inicio.year + y, m + 1
        out.append(date(y, m, calendar.monthrange(y, m)[1]))
    return out


def generar(cfg: Config, modelo: Modelo | None = None, excluir_ruts: np.ndarray | None = None) -> list[Path]:
    g = cfg.generacion
    modelo = modelo or load(cfg.rutas.modelo)
    dev = get_device(g.dispositivo)
    gen = Generador(modelo, dev)
    inicio = date.fromisoformat(g.periodo_inicio or modelo.meta["periodos"][0])
    meses = _meses(inicio, g.meses)
    salida = Path(cfg.rutas.sintetico)
    salida.mkdir(parents=True, exist_ok=True)
    for viejo in salida.glob("parte_*.parquet"):
        viejo.unlink()
    alloc = RutAllocator(int(modelo.meta["umbral_rut_empresa"]), excluir_ruts, g.semilla)
    if_codes = modelo["if_codes"]
    col = cfg.columnas
    archivos = []
    n_chunks = max(1, math.ceil(g.deudores / g.chunk_deudores))
    log.info("generando %s deudores x %s meses en %s chunk(s) sobre %s", f"{g.deudores:,}", g.meses, n_chunks, dev)
    for ch in range(n_chunks):
        t0 = time.time()
        torch.manual_seed(g.semilla * 1000 + ch)
        n0 = min(g.chunk_deudores, g.deudores - ch * g.chunk_deudores)
        seg, z, c = gen.nuevos_deudores(n0)
        vivo = torch.ones(n0, dtype=torch.bool, device=dev)
        partes = []
        for t, mes in enumerate(meses):
            if t > 0:
                n_seg = torch.bincount(seg[vivo], minlength=2).cpu().numpy()
                vivo, c = gen.paso(seg, z, vivo, c)
                n_ent = np.random.default_rng(g.semilla * 7919 + ch * 101 + t).poisson(gen.entry_rate * n_seg)
                for s_ in (0, 1):
                    if n_ent[s_] == 0:
                        continue
                    s_t = torch.full((int(n_ent[s_]),), s_, device=dev, dtype=torch.long)
                    seg_n, z_n, c_n = gen.nuevos_deudores(int(n_ent[s_]), s_t)
                    off = seg.shape[0]
                    c_n.deb += off
                    seg, z = torch.cat([seg, seg_n]), torch.cat([z, z_n])
                    vivo = torch.cat([vivo, torch.ones_like(seg_n, dtype=torch.bool)])
                    c = Celdas.cat(c, c_n).sort()
                # deudores sin celdas dejan de existir
                vivo &= torch.bincount(c.deb, minlength=seg.shape[0]) > 0
            f = gen.filas(c)
            partes.append({k: v.cpu().numpy() for k, v in f.items()} | {"t": np.full(f["deb"].shape[0], t, dtype=np.int16)})
        # RUTs por deudor (únicos globalmente)
        seg_np = seg.cpu().numpy()
        rut = np.zeros(seg_np.shape[0], dtype=np.int64)
        for s_ in (0, 1):
            idx = np.nonzero(seg_np == s_)[0]
            rut[idx] = alloc.take(s_, len(idx))
        df = _a_dataframe(partes, rut, if_codes, meses, col, g.rut_salida)
        archivo = salida / f"parte_{ch:03d}.parquet"
        df.write_parquet(archivo)
        archivos.append(archivo)
        log.info("chunk %s: %s filas, %s deudores (%.1fs)", ch, f"{df.height:,}", f"{seg_np.shape[0]:,}", time.time() - t0)
    return archivos


def _a_dataframe(partes, rut, if_codes, meses, col, rut_salida) -> pl.DataFrame:
    p = {k: np.concatenate([x[k] for x in partes]) for k in partes[0]}
    ruts = rut[p["deb"]]
    rut_col = schema.formatear_rut(ruts) if rut_salida == "con_dv" else ruts
    tc_codes = np.asarray(schema.TC_CODES, dtype=np.int16)
    return pl.DataFrame({
        col.if_id: if_codes[p["ifx"]],
        col.periodo: pl.Series(meses, dtype=pl.Date).gather(p["t"].astype(np.int64)),
        col.rut: rut_col,
        col.tipo_deudor: (p["slot"] // schema.N_TC + 1).astype(np.int16),
        col.tipo_credito: tc_codes[p["slot"] % schema.N_TC],
        col.tipo_mora: p["mora"].astype(np.int16),
        col.monto: p["monto"].astype(np.int64),
    }).sort(col.periodo, col.rut, col.if_id)
