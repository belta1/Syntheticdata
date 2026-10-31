"""CLI: d10synth doctor | mock | import | verify | profile | fit | generate | evaluate | validate."""

import argparse
import json
import logging
import sys
import time
from datetime import date


def _cfg(args):
    from d10synth.config import load_config

    cfg = load_config(args.config)
    g = cfg.generacion
    for attr in ("deudores", "meses", "dispositivo", "semilla", "chunk_deudores"):
        v = getattr(args, attr, None)
        if v is not None:
            setattr(g, attr, v)
    return cfg


def cmd_doctor(args):
    from d10synth.doctor import diagnostico

    filas = diagnostico(_cfg(args))
    for chequeo, resultado, estado in filas:
        print(f"{estado:<6} {chequeo:<16} {resultado}")
    if any(e == "ERROR" for _, _, e in filas):
        sys.exit(1)


def cmd_mock(args):
    from d10synth.mock import generar_mock

    cfg = _cfg(args)
    out = generar_mock(
        args.salida or cfg.rutas.real, deudores=args.deudores_mock, meses=args.meses_mock,
        inicio=date.fromisoformat(args.inicio), semilla=args.semilla_mock, columnas=cfg.columnas,
        rut_con_dv=args.rut_con_dv, semanales=args.semanales, formato=args.formato,
    )
    print(f"mock escrito en {out}")


def cmd_import(args):
    from d10synth.io.importar import importar

    cfg = _cfg(args)
    if args.desde:
        cfg.importacion.desde = args.desde
    if args.hasta:
        cfg.importacion.hasta = args.hasta
    if args.muestra_pct is not None:
        cfg.importacion.muestra_pct = args.muestra_pct
    t0 = time.time()
    archivos = importar(cfg, args.tipo, args.entrada, args.reemplazar)
    print(f"{len(archivos)} periodo(s) importado(s) a {cfg.rutas.real} ({time.time() - t0:.0f}s). "
          f"Revise con: d10synth --config {args.config} verify")


def cmd_verify(args):
    from d10synth.io.staging import verificar

    cfg = _cfg(args)
    r = verificar(args.ruta or cfg.rutas.real)
    print(f"origen: {json.dumps(r['origen'], ensure_ascii=False)}")
    print(f"{'periodo':<12}{'filas':>14}{'deudores':>12}{'IFs':>5}{'monto (MM$)':>16}{'inválidas':>11}")
    for f in r["periodos"]:
        print(f"{f['periodo']:<12}{f['filas']:>14,}{f['deudores']:>12,}{f['ifs']:>5}{f['monto_total'] / 1e6:>16,.0f}{f['filas_invalidas']:>11,}")
    print(f"meses faltantes: {', '.join(r['meses_faltantes']) or 'ninguno'}")
    for p in r["problemas"]:
        print(f"PROBLEMA: {p}")
    print("OK" if r["ok"] else "CON PROBLEMAS")
    if not r["ok"]:
        sys.exit(1)


def cmd_profile(args):
    from d10synth.profile import perfilar

    cfg = _cfg(args)
    perfilar(cfg)
    print(f"perfil escrito en {cfg.rutas.reportes}/perfil_d10.md (sólo agregados)")


def cmd_fit(args):
    from d10synth.model.fit import fit_and_save

    cfg = _cfg(args)
    m = fit_and_save(cfg)
    print(json.dumps({k: m.meta[k] for k in ("periodos", "n_if", "resumen_datos", "patrones", "dinamica", "rho", "segundos_ajuste")}, indent=2, default=str, ensure_ascii=False))
    print(f"modelo guardado en {cfg.rutas.modelo}")


def cmd_generate(args):
    from d10synth.generate import generar
    from d10synth.prep import load_real

    cfg = _cfg(args)
    excluir = None
    if cfg.generacion.excluir_ruts_reales:
        try:
            excluir = load_real(cfg).select("rut").unique().collect()["rut"].drop_nulls().to_numpy()
        except FileNotFoundError:
            logging.warning("no se encontró data real en %s; no se excluyen RUTs reales", cfg.rutas.real)
    t0 = time.time()
    archivos = generar(cfg, excluir_ruts=excluir)
    print(f"{len(archivos)} archivo(s) en {cfg.rutas.sintetico} ({time.time() - t0:.1f}s)")


def cmd_evaluate(args):
    from d10synth.evaluate.report import evaluar

    cfg = _cfg(args)
    res = evaluar(cfg)
    from d10synth.rules import resumen_texto

    print(resumen_texto(res["reglas"]))
    print(f"reporte en {cfg.rutas.reportes}/evaluacion.html")


def cmd_validate(args):
    from dataclasses import replace

    from d10synth.prep import normalize, scan_parquet_dir
    from d10synth.rules import resumen_texto, validar

    cfg = _cfg(args)
    raw = scan_parquet_dir(args.ruta)
    df = normalize(raw, replace(cfg, formato=replace(cfg.formato, rut="auto"))).collect()
    print(resumen_texto(validar(df, raw.select(cfg.columnas.rut).collect().to_series())))


def main(argv=None):
    p = argparse.ArgumentParser(prog="d10synth", description="Datos sintéticos D10 (PoC)")
    p.add_argument("--config", default="config/d10.yaml")
    p.add_argument("-v", "--verbose", action="store_true")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("doctor", help="diagnóstico del entorno (versiones, GPU, ODBC, disco)").set_defaults(f=cmd_doctor)

    s = sub.add_parser("mock", help="genera un D10 falso para desarrollo")
    s.add_argument("--salida", help="carpeta de salida (por defecto rutas.real)")
    s.add_argument("--deudores", dest="deudores_mock", type=int, default=50_000)
    s.add_argument("--meses", dest="meses_mock", type=int, default=12)
    s.add_argument("--inicio", default="2024-01-31")
    s.add_argument("--semilla", dest="semilla_mock", type=int, default=0)
    s.add_argument("--rut-con-dv", action="store_true")
    s.add_argument("--semanales", action="store_true", help="agrega cortes de viernes (para probar el filtro de cierre de mes)")
    s.add_argument("--formato", choices=["parquet", "csv", "d10"], default="parquet",
                   help="parquet (listo para usar), csv o d10 (para probar 'import')")
    s.set_defaults(f=cmd_mock)

    for nombre in ("import", "extract"):
        s = sub.add_parser(nombre, help="importa la data real al staging (rutas.real)" if nombre == "import" else "alias de 'import'")
        s.add_argument("--tipo", choices=["sql", "csv", "parquet", "d10"], help="por defecto importacion.tipo")
        s.add_argument("--entrada", help="archivo, carpeta o patrón glob (csv, parquet, d10)")
        s.add_argument("--desde", help="primer cierre de mes, aaaa-mm-dd")
        s.add_argument("--hasta", help="último cierre de mes, aaaa-mm-dd")
        s.add_argument("--muestra-pct", dest="muestra_pct", type=float)
        s.add_argument("--reemplazar", action="store_true", help="vuelve a escribir periodos ya importados")
        s.set_defaults(f=cmd_import)

    s = sub.add_parser("verify", help="verifica integridad del staging (hash, esquema, meses faltantes)")
    s.add_argument("ruta", nargs="?", help="por defecto rutas.real")
    s.set_defaults(f=cmd_verify)
    sub.add_parser("profile", help="perfil agregado de la data real").set_defaults(f=cmd_profile)
    sub.add_parser("fit", help="ajusta el modelo").set_defaults(f=cmd_fit)

    s = sub.add_parser("generate", help="genera el panel sintético")
    s.add_argument("--deudores", type=int)
    s.add_argument("--meses", type=int)
    s.add_argument("--dispositivo", choices=["auto", "cuda", "cpu"])
    s.add_argument("--semilla", type=int)
    s.add_argument("--chunk-deudores", dest="chunk_deudores", type=int)
    s.set_defaults(f=cmd_generate)

    s = sub.add_parser("evaluate", help="evalúa el sintético contra la data real")
    s.add_argument("--dispositivo", choices=["auto", "cuda", "cpu"])
    s.set_defaults(f=cmd_evaluate)

    s = sub.add_parser("validate", help="valida reglas del D10 sobre una carpeta de parquet")
    s.add_argument("ruta")
    s.set_defaults(f=cmd_validate)

    args = p.parse_args(argv)
    for flujo in (sys.stdout, sys.stderr):
        if hasattr(flujo, "reconfigure"):
            flujo.reconfigure(encoding="utf-8")
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    args.f(args)

