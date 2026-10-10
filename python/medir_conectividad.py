#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Mide conectividad de gestion con ping del sistema (medicion, no configura).

Lee los destinos de inventario/hosts.yml, ejecuta el ping del sistema
con subprocess (sin shell) y clasifica cada destino. No abre SSH.
"""
from __future__ import annotations

import argparse
import csv
import ipaddress
import platform
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path

import yaml

# Umbrales de clasificacion (constantes al inicio).
UMBRAL_LATENCIA_MS = 50
PERDIDA_MAXIMA = 0  # en porcentaje

# Parametros por defecto.
PAQUETES_DEFECTO = 10
DESTINOS_DEFECTO = ["R-SUC-01", "SW-SUC-01-CORE", "SW-SUC-01-ACC1"]

# Rutas base relativas al repo (no al cwd).
BASE = Path(__file__).resolve().parent.parent
RUTA_HOSTS = BASE / "inventario" / "hosts.yml"
RUTA_CSV = BASE / "evidencias" / "mediciones_conectividad.csv"
RUTA_TXT = BASE / "evidencias" / "mediciones_conectividad.txt"


# ---------- Destinos ----------

def cargar_hosts(ruta: Path) -> dict:
    """Lee inventario/hosts.yml y devuelve {nombre: ansible_host}."""
    with open(ruta, encoding="utf-8") as f:
        datos = yaml.safe_load(f)
    hosts: dict[str, str] = {}
    children = ((datos.get("all") or {}).get("children") or {})
    for _grupo, gdata in children.items():
        for nombre, hdata in ((gdata or {}).get("hosts") or {}).items():
            hosts[str(nombre)] = str((hdata or {}).get("ansible_host", ""))
    # Router primero para un orden estable.
    orden = sorted(hosts, key=lambda h: (0 if h.startswith("R-") else 1, h))
    return {h: hosts[h] for h in orden}


def validar_ip(ip: str) -> str:
    """Valida la IP con ipaddress y la devuelve normalizada."""
    return str(ipaddress.ip_address(str(ip).strip()))


def resolver_destinos(todos: bool, extras: list[str] | None,
                      tabla: dict) -> list[tuple[str, str]]:
    """Destinos por defecto, con --todos o manuales NOMBRE=IP."""
    if extras:
        lista = []
        for e in extras:
            if "=" not in e:
                raise ValueError(f"--destino debe ser NOMBRE=IP (recibido '{e}').")
            nombre, ip = e.split("=", 1)
            nombre, ip = nombre.strip(), validar_ip(ip)
            if not nombre:
                raise ValueError(f"--destino sin nombre (recibido '{e}').")
            lista.append((nombre, ip))
        return lista
    if todos:
        nombres = list(tabla)
    else:
        nombres = [n for n in DESTINOS_DEFECTO if n in tabla]
        if len(nombres) < len(DESTINOS_DEFECTO):
            faltan = [n for n in DESTINOS_DEFECTO if n not in tabla]
            raise ValueError(f"faltan en el inventario: {', '.join(faltan)}.")
    return [(n, validar_ip(tabla[n])) for n in nombres]


# ---------- Medicion ----------

def construir_comando(ip: str, paquetes: int) -> list[str]:
    """Comando ping segun el sistema (lista de argumentos, sin shell)."""
    if platform.system().lower() == "windows":
        return ["ping", "-n", str(paquetes), "-w", "2000", ip]
    return ["ping", "-c", str(paquetes), "-W", "2", ip]


def parsear_salida(salida: str) -> dict:
    """Extrae enviados/recibidos/perdida/latencias (ingles y español).

    Soporta 'rtt min/avg/max/mdev' de Linux y 'Minimum/Maximum/Average'
    o 'Mínimo/Máximo/Media' de Windows. Lanza ValueError si no se puede
    interpretar.
    """
    enviados = recibidos = perdida = None
    m = re.search(r"(\d+)\s+(?:packets\s+transmitted|paquetes\s+transmitidos)\s*,\s*"
                  r"(\d+)\s+(?:received|recibidos?)", salida, re.IGNORECASE)
    if m:
        enviados, recibidos = int(m.group(1)), int(m.group(2))
    else:
        m = re.search(r"[Pp]ackets:\s*[Ss]ent\s*=\s*(\d+)\s*,\s*[Rr]eceived\s*=\s*(\d+)", salida)
        if not m:
            m = re.search(r"[Pp]aquetes:\s*enviados\s*=\s*(\d+)\s*,\s*recibidos\s*=\s*(\d+)", salida)
        if m:
            enviados, recibidos = int(m.group(1)), int(m.group(2))
    m = re.search(r"(\d+(?:[.,]\d+)?)\s*%\s*(?:(?:paquetes?\s+)?perdidos?|p[ée]rdida|(?:packet\s+)?loss)", salida, re.IGNORECASE)
    if m:
        perdida = float(m.group(1).replace(",", "."))
    if enviados is None or recibidos is None or perdida is None:
        raise ValueError("no se pudo interpretar el resumen de paquetes del ping.")
    lat_min = lat_prom = lat_max = None
    m = re.search(r"rtt\s+min/avg/max/mdev\s*=\s*([\d.]+)/([\d.]+)/([\d.]+)/[\d.]+\s*ms", salida)
    if m:
        lat_min, lat_prom, lat_max = float(m.group(1)), float(m.group(2)), float(m.group(3))
    else:
        # Windows: el orden es mínimo, máximo, promedio.
        m = re.search(r"[Mm]inimum\s*=\s*(\d+)\s*ms\s*,\s*[Mm]aximum\s*=\s*(\d+)\s*ms\s*,\s*"
                      r"[Aa]verage\s*=\s*(\d+)\s*ms", salida)
        if not m:
            m = re.search(r"M[íi]nimo\s*=\s*(\d+)\s*ms\s*,\s*M[áa]ximo\s*=\s*(\d+)\s*ms\s*,\s*"
                          r"(?:Media|Promedio)\s*=\s*(\d+)\s*ms", salida)
        if m:
            lat_min, lat_max, lat_prom = float(m.group(1)), float(m.group(2)), float(m.group(3))
    if recibidos == 0:
        lat_min = lat_prom = lat_max = None  # pérdida 100 %: latencias vacías
    return {"enviados": enviados, "recibidos": recibidos, "perdida": perdida,
            "min": lat_min, "prom": lat_prom, "max": lat_max}


def medir(ip: str, paquetes: int) -> dict:
    """Ejecuta el ping y devuelve la medición con su detalle."""
    validar_ip(ip)  # nunca se ejecuta con una IP inválida
    cmd = construir_comando(ip, paquetes)
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True,
                              timeout=paquetes * 3 + 15)
        salida = (proc.stdout or "") + (proc.stderr or "")
    except FileNotFoundError:
        return {"error": "no se encontró el comando ping del sistema."}
    except subprocess.TimeoutExpired:
        return {"error": f"timeout tras {paquetes * 3 + 15} s esperando al ping."}
    try:
        return parsear_salida(salida)
    except ValueError as e:
        return {"error": f"{e} Salida: {salida.strip()[:300]}"}


def clasificar(med: dict) -> tuple[str, str]:
    """'cumple' si pérdida = 0 % y promedio <= 50 ms; si no, 'no cumple'."""
    if "error" in med:
        return "no cumple", f"sin medición: {med['error']}"
    if med["recibidos"] == 0:
        return "no cumple", f"pérdida 100 % (0 de {med['enviados']} recibidos)."
    if med["perdida"] > PERDIDA_MAXIMA:
        return "no cumple", (f"pérdida {med['perdida']} % mayor que la máxima "
                             f"{PERDIDA_MAXIMA} % ({med['recibidos']} de {med['enviados']}).")
    if med["prom"] is None:
        return "no cumple", "latencia promedio no disponible en la salida del ping."
    if med["prom"] > UMBRAL_LATENCIA_MS:
        return "no cumple", (f"latencia promedio {med['prom']} ms mayor que "
                             f"el umbral {UMBRAL_LATENCIA_MS} ms.")
    return "cumple", (f"pérdida {med['perdida']} % y promedio {med['prom']} ms "
                      f"dentro del umbral {UMBRAL_LATENCIA_MS} ms.")


# ---------- Programa principal ----------

def main(argv: list[str] | None = None) -> int:
    """Mide, clasifica y guarda CSV (historial) + tabla legible."""
    parser = argparse.ArgumentParser(description="Mide conectividad de gestión con ping (sin SSH).")
    parser.add_argument("--hosts", default=str(RUTA_HOSTS), help="Inventario con ansible_host.")
    parser.add_argument("--paquetes", type=int, default=PAQUETES_DEFECTO, help="Pruebas ICMP por destino.")
    parser.add_argument("--todos", action="store_true", help="Suma ACC2 y ACC3.")
    parser.add_argument("--destino", action="append", default=None, help="Manual NOMBRE=IP (repetible).")
    parser.add_argument("--salida-csv", default=str(RUTA_CSV), help="CSV con historial.")
    parser.add_argument("--salida-txt", default=str(RUTA_TXT), help="Tabla legible.")
    args = parser.parse_args(argv)

    if args.paquetes <= 0:
        print("ERROR: --paquetes debe ser mayor que 0.")
        return 2
    try:
        tabla = cargar_hosts(Path(args.hosts))
    except FileNotFoundError:
        print(f"ERROR: no existe el inventario: {args.hosts}")
        return 2
    except yaml.YAMLError as e:
        print(f"ERROR: YAML inválido en {args.hosts}: {e}")
        return 2
    try:
        destinos = resolver_destinos(args.todos, args.destino, tabla)
    except ValueError as e:
        print(f"ERROR: {e}")
        return 2

    sistema = platform.system()
    filas = []
    for nombre, ip in destinos:
        med = medir(ip, args.paquetes)
        clase, detalle = clasificar(med)
        filas.append({"destino": nombre, "ip": ip, "med": med,
                      "clasificacion": clase, "detalle": detalle})

    # --- CSV con historial (encabezado solo si se crea) ---
    ruta_csv = Path(args.salida_csv)
    ruta_csv.parent.mkdir(parents=True, exist_ok=True)
    nuevo = not ruta_csv.is_file()
    ahora = datetime.now().astimezone().isoformat()
    with open(ruta_csv, "a", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        if nuevo:
            w.writerow(["fecha", "destino", "ip", "paquetes_enviados", "paquetes_recibidos",
                        "perdida_pct", "latencia_min_ms", "latencia_prom_ms",
                        "latencia_max_ms", "clasificacion", "detalle"])
        for r in filas:
            m = r["med"]
            w.writerow([ahora, r["destino"], r["ip"],
                        m.get("enviados", ""), m.get("recibidos", ""), m.get("perdida", ""),
                        m.get("min", "") if m.get("min") is not None else "",
                        m.get("prom", "") if m.get("prom") is not None else "",
                        m.get("max", "") if m.get("max") is not None else "",
                        r["clasificacion"], r["detalle"]])

    # --- Tabla legible (misma salida en pantalla y txt) ---
    salida = []
    salida.append("MEDICION DE CONECTIVIDAD DE GESTION (ping del sistema, sin SSH)")
    salida.append("=" * 100)
    salida.append("QUÉ DATOS LEYÓ")
    salida.append(f"  - Inventario: {args.hosts} ({len(destinos)} destinos)")
    for n, ip in destinos:
        salida.append(f"    {n}: {ip}")
    salida.append(f"  - Parámetros: {args.paquetes} paquetes por destino, sistema {sistema}")
    salida.append("")
    salida.append("QUÉ CONDICIÓN EVALUÓ")
    salida.append(f"  - 'cumple' si pérdida = {PERDIDA_MAXIMA} % Y promedio <= {UMBRAL_LATENCIA_MS} ms.")
    salida.append("")
    salida.append("CUÁL FUE EL RESULTADO")
    ok = no = 0
    for r in filas:
        m = r["med"]
        marca = "OK " if r["clasificacion"] == "cumple" else "DIF"
        if "error" in m:
            linea = f"  [{marca}] {r['destino']} ({r['ip']}): {r['clasificacion']} | {r['detalle']}"
        else:
            lat = (f"min={m['min']}ms prom={m['prom']}ms max={m['max']}ms"
                   if m["prom"] is not None else "sin latencias")
            linea = (f"  [{marca}] {r['destino']} ({r['ip']}): {r['clasificacion']} | "
                     f"enviados={m['enviados']} recibidos={m['recibidos']} "
                     f"pérdida={m['perdida']} % {lat} | {r['detalle']}")
        salida.append(linea)
        ok, no = (ok + 1, no) if r["clasificacion"] == "cumple" else (ok, no + 1)
    salida.append("")
    salida.append("RESUMEN")
    salida.append(f"  Cumplen: {ok}, no cumplen: {no} de {len(filas)} destinos")
    salida.append("  Resultado global: " + ("CUMPLE" if no == 0 else "NO CUMPLE"))
    texto = "\n".join(salida) + "\n"
    print(texto)
    ruta_txt = Path(args.salida_txt)
    ruta_txt.parent.mkdir(parents=True, exist_ok=True)
    ruta_txt.write_text(texto, encoding="utf-8")
    print(f"CSV guardado en: {ruta_csv}")
    print(f"Tabla guardada en: {ruta_txt}")
    return 0 if no == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
