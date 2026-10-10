#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Relaciona el IPAM simulado con YAML, inventario y evidencia (solo lectura).

Compara VLAN, red/gateway, IP de gestion y nombre/funcion entre las
fuentes y clasifica en COINCIDENCIAS, FALTANTES y DIFERENCIAS.
No configura equipos ni abre SSH. Compara IP con ipaddress.
"""
from __future__ import annotations

import argparse
import ipaddress
import json
import re
import sys
from pathlib import Path

import yaml

# Reutiliza el parseo de running-config (mismo directorio).
sys.path.insert(0, str(Path(__file__).resolve().parent))
from auditar_sucursal import parsear_running_config  # noqa: E402

# Rutas base relativas al repo (no al cwd).
BASE = Path(__file__).resolve().parent.parent
RUTA_IPAM = BASE / "datos" / "ipam_sucursal_01.json"
RUTA_DISENO = BASE / "variables" / "sucursal_01.yml"
RUTA_GV_SW = BASE / "inventario" / "group_vars" / "switches.yml"
RUTA_GV_RT = BASE / "inventario" / "group_vars" / "routers.yml"
RUTA_HOSTS = BASE / "inventario" / "hosts.yml"
DIR_RESPALDOS = BASE / "respaldos"
RUTA_SALIDA = BASE / "evidencias" / "validacion_inventario.txt"


# ---------- Carga ----------

def cargar_json(ruta: Path):
    """Carga un JSON local (IPAM simulado)."""
    with open(ruta, encoding="utf-8") as f:
        return json.load(f)


def cargar_yaml(ruta: Path):
    """Carga un YAML."""
    with open(ruta, encoding="utf-8") as f:
        return yaml.safe_load(f)


def leer_texto(ruta: Path) -> str | None:
    """Lee un archivo de evidencia o devuelve None si falta."""
    try:
        return ruta.read_text(encoding="utf-8", errors="replace")
    except (FileNotFoundError, NotADirectoryError, PermissionError):
        return None


# ---------- Normalizacion con ipaddress ----------

def norm_ip(valor: str) -> str:
    """Normaliza una IP ('.14', '.14/24' o 'IP mascara') a solo direccion."""
    s = str(valor).strip()
    if "/" in s:
        return str(ipaddress.ip_interface(s).ip)
    partes = s.split()
    if len(partes) == 2:
        return str(ipaddress.ip_interface(f"{partes[0]}/{partes[1]}").ip)
    return str(ipaddress.ip_address(s))


def norm_iface(valor: str) -> str:
    """Normaliza a 'ip/prefijo'."""
    s = str(valor).strip()
    if "/" in s:
        i = ipaddress.ip_interface(s)
        return f"{i.ip}/{i.network.prefixlen}"
    partes = s.split()
    if len(partes) == 2:
        i = ipaddress.ip_interface(f"{partes[0]}/{partes[1]}")
        return f"{i.ip}/{i.network.prefixlen}"
    return str(ipaddress.ip_address(s))


def norm_red(valor: str) -> str:
    """Normaliza una red ('A/M' o 'A M') a 'red/prefijo'."""
    s = str(valor).strip()
    if "/" in s:
        return str(ipaddress.ip_network(s, strict=False))
    partes = s.split()
    if len(partes) == 2:
        return str(ipaddress.ip_network((partes[0], partes[1]), strict=False))
    return str(ipaddress.ip_network(s, strict=False))


# ---------- Parseo de evidencia del router y switches ----------

def parsear_pools_running(texto: str) -> dict:
    """Extrae pools DHCP {NOMBRE: {network, router}} del running-config."""
    pools: dict[str, dict] = {}
    actual: str | None = None
    for linea in texto.splitlines():
        s = linea.strip()
        m = re.match(r"^ip\s+dhcp\s+pool\s+(\S+)", s, re.IGNORECASE)
        if m:
            actual = m.group(1)
            pools[actual] = {}
            continue
        if actual is None:
            continue
        if s.startswith("!") or s.lower() == "end":
            actual = None
            continue
        m = re.match(r"^network\s+(\S+)\s+(\S+)", s, re.IGNORECASE)
        if m:
            pools[actual]["network"] = f"{m.group(1)} {m.group(2)}"
            continue
        m = re.match(r"^default-router\s+(\S+)", s, re.IGNORECASE)
        if m:
            pools[actual]["router"] = m.group(1).strip()
            continue
    return pools


def hostname_running(texto: str) -> str | None:
    """Extrae 'hostname X' del running-config."""
    m = re.search(r"(?mi)^hostname\s+(\S+)\s*$", texto)
    return m.group(1).strip() if m else None


def ip_vlan99_running(interfaces: dict) -> str | None:
    """IP de Vlan99 desde interfaces parseadas (forma 'ip/prefijo')."""
    for nombre, cfg in (interfaces or {}).items():
        if str(nombre).lower().replace(" ", "") == "vlan99" and cfg.get("ip"):
            return norm_iface(str(cfg["ip"]))
    return None


def ip_vlan99_brief(texto: str) -> str | None:
    """IP de Vlan99 desde 'show ip interface brief' (solo direccion)."""
    for linea in texto.splitlines():
        s = linea.strip()
        if not s or s.startswith("!") or s.lower().startswith("interface"):
            continue
        m = re.match(r"^(\S+)\s+(\S+)\s+\S+\s+\S+\s+\S+\s+\S+", s)
        if m and m.group(1).lower().replace(" ", "") == "vlan99":
            return m.group(2).strip()
    return None


def vid_de_pool(nombre_pool: str) -> int | None:
    """Deduce la VLAN del nombre del pool ('POOL_VLAN10' -> 10)."""
    m = re.search(r"VLAN\s*0*(\d+)", str(nombre_pool), re.IGNORECASE)
    return int(m.group(1)) if m else None


# ---------- Programa principal ----------

def main(argv: list[str] | None = None) -> int:
    """Relaciona las fuentes y escribe la clasificacion."""
    parser = argparse.ArgumentParser(description="Valida el inventario contra el IPAM (solo lectura).")
    parser.add_argument("--ipam", default=str(RUTA_IPAM), help="JSON del IPAM simulado.")
    parser.add_argument("--respaldos", default=str(DIR_RESPALDOS), help="Carpeta de evidencia.")
    parser.add_argument("--diseno", default=str(RUTA_DISENO), help="YAML del diseño.")
    parser.add_argument("--salida", default=str(RUTA_SALIDA), help="Archivo de salida.")
    args = parser.parse_args(argv)

    ruta_ipam, ruta_diseno = Path(args.ipam), Path(args.diseno)
    dir_respaldos, ruta_salida = Path(args.respaldos), Path(args.salida)

    try:
        ipam = cargar_json(ruta_ipam)
        diseno = cargar_yaml(ruta_diseno)
        gv_sw = cargar_yaml(RUTA_GV_SW)
        gv_rt = cargar_yaml(RUTA_GV_RT)
        hosts = cargar_yaml(RUTA_HOSTS)
    except FileNotFoundError as e:
        print(f"ERROR: falta una entrada: {e.filename}")
        return 2
    except (json.JSONDecodeError, yaml.YAMLError) as e:
        print(f"ERROR: formato inválido: {e}")
        return 2
    if not isinstance(ipam, dict) or "vlans" not in ipam or "dispositivos" not in ipam:
        print(f"ERROR: el IPAM {ruta_ipam} debe tener 'vlans' y 'dispositivos'.")
        return 2
    if not isinstance(diseno, dict) or not diseno.get("switches") or not diseno.get("routers"):
        print(f"ERROR: diseño incompleto en {ruta_diseno}.")
        return 2

    # --- Fuente 1: IPAM ---
    ipam_vlans: dict[int, dict] = {}
    for v in ipam.get("vlans", []):
        try:
            ipam_vlans[int(v["vlan_id"])] = {"nombre": str(v["nombre"]),
                                             "red": norm_red(str(v["red"])),
                                             "gateway": norm_ip(str(v["gateway"]))}
        except (KeyError, ValueError, TypeError, ipaddress.AddressValueError) as e:
            print(f"ERROR: VLAN inválida en IPAM {v}: {e}")
            return 2
    ipam_devs: dict[str, dict] = {}
    for d in ipam.get("dispositivos", []):
        try:
            ipam_devs[str(d["hostname"])] = {"funcion": str(d.get("funcion", "")),
                                             "ip_gestion": norm_iface(str(d["ip_gestion"]))}
        except (KeyError, ValueError, TypeError, ipaddress.AddressValueError) as e:
            print(f"ERROR: dispositivo inválido en IPAM {d}: {e}")
            return 2

    # --- Fuente 2: diseño YAML (red/gateway derivados de la subinterfaz) ---
    yaml_nombres: dict[int, str] = {}
    for v in diseno.get("vlans_esperadas", []):
        yaml_nombres[int(v.get("id", v.get("vlan_id")))] = str(v.get("nombre", v.get("name")))
    yaml_mgmt: dict[str, str] = {}
    for h, data in (diseno.get("switches") or {}).items():
        yaml_mgmt[h] = norm_iface(str((data or {})["mgmt_ip"]))
    yaml_red: dict[int, str] = {}
    yaml_gw: dict[int, str] = {}
    for _rh, rdata in (diseno.get("routers") or {}).items():
        for sub in (rdata or {}).get("subinterfaces", []):
            enc = int(str(sub.get("encapsulation", sub.get("id"))).split()[0])
            iface = ipaddress.ip_interface(norm_iface(str(sub["ip"])))
            yaml_red[enc] = str(iface.network)
            yaml_gw[enc] = str(iface.ip)

    # --- Fuente 3: group_vars (VLANs y pools DHCP) ---
    gv_nombres: dict[int, str] = {}
    for v in (gv_sw.get("vlans_sucursal") or []):
        gv_nombres[int(v.get("vlan_id"))] = str(v.get("name"))
    gv_red: dict[int, str] = {}
    gv_gw: dict[int, str] = {}
    for p in (gv_rt.get("dhcp_pools") or []):
        vid = vid_de_pool(str(p.get("name", "")))
        if vid is not None:
            gv_red[vid] = norm_red(str(p["network"]))
            gv_gw[vid] = norm_ip(str(p["router"]))

    # --- Fuente 4: inventario hosts.yml (ansible_host por equipo) ---
    inv: dict[str, dict] = {}
    for grupo, gdata in ((hosts.get("all", {}) or {}).get("children", {}) or {}).items():
        for h, hdata in ((gdata or {}).get("hosts", {}) or {}).items():
            inv[h] = {"grupo": grupo, "ansible_host": str((hdata or {}).get("ansible_host", ""))}

    # --- Fuente 5: evidencia en respaldos/ ---
    ev_subif_ip: dict[int, str] = {}
    ev_pool_red: dict[int, str] = {}
    ev_pool_gw: dict[int, str] = {}
    ev_hostnames: dict[str, str | None] = {}
    ev_sw_running_ip: dict[str, str | None] = {}
    ev_sw_brief_ip: dict[str, str | None] = {}
    ev_leidos = 0
    for host in list((diseno.get("routers") or {})) + list((diseno.get("switches") or {})):
        p_run = dir_respaldos / f"{host}-running-config.txt"
        t_run = leer_texto(p_run)
        if t_run is not None:
            ev_leidos += 1
            ev_hostnames[host] = hostname_running(t_run)
            parsed = parsear_running_config(t_run)
            if host in (diseno.get("routers") or {}):
                for nombre, cfg in (parsed.get("interfaces", {}) or {}).items():
                    m = re.match(r"(?i)^gigabitethernet\d+/\d+\.(\d+)$", str(nombre).replace(" ", ""))
                    if m and cfg.get("ip"):
                        ev_subif_ip[int(m.group(1))] = norm_iface(str(cfg["ip"]))
                for pname, pdata in parsear_pools_running(t_run).items():
                    vid = vid_de_pool(pname)
                    if vid is not None:
                        if pdata.get("network"):
                            ev_pool_red[vid] = norm_red(str(pdata["network"]))
                        if pdata.get("router"):
                            ev_pool_gw[vid] = norm_ip(str(pdata["router"]))
            else:
                ev_sw_running_ip[host] = ip_vlan99_running(parsed.get("interfaces", {}))
        else:
            ev_hostnames[host] = None
            if host in (diseno.get("switches") or {}):
                ev_sw_running_ip[host] = None
        p_brief = dir_respaldos / f"{host}-show-ip-interface-brief.txt"
        t_brief = leer_texto(p_brief)
        if t_brief is not None:
            ev_leidos += 1
            if host in (diseno.get("switches") or {}):
                ev_sw_brief_ip[host] = ip_vlan99_brief(t_brief)

    coincidencias, faltantes, diferencias = [], [], []

    # --- 1. VLAN: id y nombre (IPAM vs YAML vs group_vars) ---
    for vid in sorted(set(ipam_vlans) | set(yaml_nombres) | set(gv_nombres)):
        presentes = {s: n for s, n in
                     (("IPAM", ipam_vlans.get(vid, {}).get("nombre")),
                      ("YAML", yaml_nombres.get(vid)), ("group_vars", gv_nombres.get(vid)))
                     if n is not None}
        if len(presentes) < 3:
            faltan = [s for s in ("IPAM", "YAML", "group_vars") if s not in presentes]
            estan = ", ".join(f"{s} '{presentes[s]}'" for s in presentes)
            faltantes.append(f"VLAN {vid}: falta en {', '.join(faltan)}; está en {estan or 'ninguna'}.")
        elif len({n.lower() for n in presentes.values()}) == 1:
            coincidencias.append(f"VLAN {vid} ({presentes['IPAM']}): nombre igual en IPAM, YAML y group_vars.")
        else:
            diferencias.append(f"VLAN {vid}: IPAM '{presentes['IPAM']}', "
                               f"YAML '{presentes['YAML']}', group_vars '{presentes['group_vars']}'.")

    # --- 2. Red y gateway (IPAM vs YAML/router vs running del router) ---
    for vid in sorted(set(ipam_vlans) | set(yaml_red) | set(gv_red) | set(ev_subif_ip) | set(ev_pool_red)):
        for concepto, fuentes in (
                (f"Red VLAN {vid}",
                 (("IPAM", (ipam_vlans.get(vid) or {}).get("red")),
                  ("YAML", yaml_red.get(vid)), ("group_vars", gv_red.get(vid)),
                  ("evidencia-subinterfaz", (lambda v: str(ipaddress.ip_network(v, strict=False)) if v else None)(ev_subif_ip.get(vid))),
                  ("evidencia-dhcp", ev_pool_red.get(vid)))),
                (f"Gateway VLAN {vid}",
                 (("IPAM", (ipam_vlans.get(vid) or {}).get("gateway")),
                  ("YAML", yaml_gw.get(vid)), ("group_vars", gv_gw.get(vid)),
                  ("evidencia-subinterfaz", (lambda v: str(ipaddress.ip_interface(v).ip) if v else None)(ev_subif_ip.get(vid))),
                  ("evidencia-dhcp", ev_pool_gw.get(vid))))):
            vals = {s: v for s, v in fuentes if v is not None}
            if len(vals) <= 1 and not vals:
                faltantes.append(f"{concepto}: sin datos en ninguna fuente.")
            elif len(vals) < 5:
                faltan = [s for s, _v in fuentes if _v is None]
                estan = ", ".join(f"{s} {vals[s]}" for s in vals)
                faltantes.append(f"{concepto}: falta en {', '.join(faltan)}; está en {estan}.")
                if len(set(vals.values())) > 1:
                    diferencias.append(f"{concepto}: " + ", ".join(f"{s} {vals[s]}" for s, _v in fuentes if _v is not None))
            elif len(set(vals.values())) == 1:
                coincidencias.append(f"{concepto}: {next(iter(vals.values()))} igual en IPAM, YAML, group_vars y evidencia.")
            else:
                diferencias.append(f"{concepto}: " + ", ".join(f"{s} {vals[s]}" for s, _v in fuentes))

    # --- 3. IP de gestion (IPAM vs YAML mgmt_ip vs ansible_host vs evidencia) ---
    for host in sorted(set(ipam_devs) | set(yaml_mgmt) | set(inv)):
        es_router = host in (diseno.get("routers") or {})
        if es_router and host not in yaml_mgmt and 99 in yaml_gw:
            yaml_mgmt[host] = norm_iface(f"{yaml_gw[99]}/24")
        fuentes = [("IPAM", (lambda v: str(ipaddress.ip_interface(v).ip) if v else None)(ipam_devs.get(host, {}).get("ip_gestion"))),
                   ("YAML", (lambda v: str(ipaddress.ip_interface(v).ip) if v else None)(yaml_mgmt.get(host))),
                   ("inventario", inv.get(host, {}).get("ansible_host") or None),
                   ("evidencia-running", (lambda v: str(ipaddress.ip_interface(v).ip) if v else None)(
                       ev_subif_ip.get(99) if es_router else ev_sw_running_ip.get(host)))]
        if not es_router:
            fuentes.append(("evidencia-brief", ev_sw_brief_ip.get(host)))
        n_esperadas = len(fuentes)
        vals = {s: v for s, v in fuentes if v}
        if host not in ipam_devs or host not in inv:
            falta = "IPAM" if host not in ipam_devs else "inventario"
            esta = ", ".join(f"{s} {vals[s]}" for s in vals)
            faltantes.append(f"Dirección de gestión {host}: falta en {falta}; está en {esta or 'ninguna'}.")
            continue
        if len(vals) < n_esperadas:
            faltan = [s for s, _v in fuentes if not _v]
            estan = ", ".join(f"{s} {vals[s]}" for s in vals)
            faltantes.append(f"Dirección de gestión {host}: falta en {', '.join(faltan)}; está en {estan}.")
            if len(set(vals.values())) > 1:
                diferencias.append(f"Dirección de gestión {host}: " + ", ".join(f"{s} {vals[s]}" for s, _v in fuentes if _v))
        elif len(set(vals.values())) == 1:
            coincidencias.append(f"Dirección de gestión {host}: {next(iter(vals.values()))} igual en IPAM, YAML, inventario y evidencia.")
        else:
            diferencias.append(f"Dirección de gestión {host}: " + ", ".join(f"{s} {vals[s]}" for s, _v in fuentes))

    # --- 4. Nombre y funcion (IPAM vs inventario vs hostname del respaldo) ---
    for host in sorted(set(ipam_devs) | set(inv)):
        en_ipam, en_inv = host in ipam_devs, host in inv
        run_hn = ev_hostnames.get(host)
        if en_ipam and en_inv:
            if run_hn is None:
                faltantes.append(f"Equipo {host} ({ipam_devs[host]['funcion']}): falta respaldo running-config; está en IPAM e inventario.")
            elif run_hn != host:
                diferencias.append(f"Hostname {host}: inventario '{host}', respaldo '{run_hn}'.")
            else:
                coincidencias.append(f"Equipo {host} ({ipam_devs[host]['funcion']}): en IPAM, inventario y respaldo.")
        elif en_ipam:
            faltantes.append(f"Equipo {host} ({ipam_devs[host]['funcion']}): falta en inventario"
                             + (" y en respaldos" if run_hn is None else f"; respaldo trae '{run_hn}'")
                             + "; está en IPAM.")
        else:
            faltantes.append(f"Equipo {host}: falta en IPAM; está en inventario (grupo {inv[host]['grupo']})"
                             + (f" y respaldo '{run_hn}'" if run_hn else " (sin respaldo running-config)") + ".")

    # --- Salida ---
    salida = []
    salida.append("VALIDACION DE INVENTARIO CONTRA IPAM (Python, solo lectura)")
    salida.append("=" * 100)
    salida.append("QUÉ FUENTES LEYÓ")
    salida.append(f"  - IPAM: {ruta_ipam} ({len(ipam_vlans)} VLANs, {len(ipam_devs)} dispositivos)")
    salida.append(f"  - Diseño: {ruta_diseno} ({len(yaml_nombres)} VLANs; red/gateway derivados de la subinterfaz del router)")
    salida.append(f"  - group_vars: {RUTA_GV_SW} ({len(gv_nombres)} VLANs), {RUTA_GV_RT} ({len(gv_red)} pools DHCP)")
    salida.append(f"  - Inventario: {RUTA_HOSTS} ({len(inv)} equipos con ansible_host)")
    salida.append(f"  - Evidencia: {dir_respaldos} ({ev_leidos} archivos leídos: running-config + ip brief)")
    salida.append("")
    salida.append("QUÉ CONDICIÓN EVALUÓ")
    salida.append("  1. VLAN id/nombre (IPAM vs YAML vs group_vars).")
    salida.append("  2. Red/gateway por VLAN (IPAM vs YAML/router vs running del router).")
    salida.append("  3. IP de gestión por equipo (IPAM vs YAML vs ansible_host vs evidencia).")
    salida.append("  4. Nombre/función (IPAM vs inventario vs hostname del respaldo). IP con ipaddress.")
    salida.append("")
    for titulo, items in (("COINCIDENCIAS", coincidencias), ("FALTANTES", faltantes), ("DIFERENCIAS", diferencias)):
        salida.append(f"{titulo} ({len(items)})")
        salida.append("-" * 100)
        salida.extend(f"  - {e}" for e in items) if items else salida.append("  (ninguna)")
        salida.append("")
    salida.append("RESUMEN")
    salida.append(f"  Coincidencias: {len(coincidencias)}, faltantes: {len(faltantes)}, diferencias: {len(diferencias)}")
    ok = not faltantes and not diferencias
    salida.append("  Resultado global: " + ("CUMPLE" if ok else "NO CUMPLE"))
    texto = "\n".join(salida) + "\n"
    print(texto)
    ruta_salida.parent.mkdir(parents=True, exist_ok=True)
    ruta_salida.write_text(texto, encoding="utf-8")
    print(f"Evidencia guardada en: {ruta_salida}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
