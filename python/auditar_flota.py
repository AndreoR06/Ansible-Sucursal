#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Audita una flota pequena (solo lectura de archivos, sin SSH).

Amplia python/auditar_sucursal.py sin modificarlo: importa sus parsers
y utilidades. Evalua version, VLANs, accesos, troncales (running y
brief), gestion, gateway, subinterfaces, brief y rutas del router.
Escribe CSV + tabla legible con el mismo contenido en txt.
"""
from __future__ import annotations

import argparse
import csv
import re
import sys
from pathlib import Path

import yaml

# Reutiliza parsers de la auditoria existente (mismo directorio).
sys.path.insert(0, str(Path(__file__).resolve().parent))
from auditar_sucursal import (  # noqa: E402
    cargar_diseno,
    fmt_allowed,
    normalizar_allowed,
    normalizar_ip,
    normalizar_puerto,
    obtener_vlans_esperadas,
    parsear_running_config,
    parsear_vlan_brief,
)

# Rutas base relativas al repo (no al cwd).
BASE = Path(__file__).resolve().parent.parent
RUTA_DISENO = BASE / "variables" / "sucursal_01.yml"
DIR_RESPALDOS = BASE / "respaldos"
RUTA_CSV = BASE / "evidencias" / "auditoria_flota.csv"
RUTA_TXT = BASE / "evidencias" / "auditoria_flota.txt"

# Nombres de archivo iguales a los de respaldos/.
ARCH_RUNNING = "running-config"
ARCH_VERSION = "show-version"
ARCH_VLAN = "show-vlan-brief"
ARCH_TRUNK = "show-interfaces-trunk"
ARCH_IPBRIEF = "show-ip-interface-brief"
ARCH_ROUTE = "show-ip-route"

GATEWAY_ESPERADO = "192.168.99.1"
TRUNK_NATIVA = 99
TRUNK_PERMITIDAS = [10, 20, 99]
RUTAS_ESPERADAS = ["192.168.10.0/24", "192.168.20.0/24", "192.168.99.0/24"]
EQUIPOS_DEFECTO = ["R-SUC-01", "SW-SUC-01-CORE", "SW-SUC-01-ACC1"]


# ---------- Utilidades minimas (no importables de auditar_sucursal) ----------

def leer_texto(ruta: Path) -> str | None:
    """Lee un archivo o devuelve None si no existe o no se puede leer."""
    try:
        return ruta.read_text(encoding="utf-8", errors="replace")
    except (FileNotFoundError, NotADirectoryError, PermissionError):
        return None


def nombre_archivo(directorio: Path, host: str, archivo: str) -> str:
    """Nombre <HOST>-<archivo>.txt."""
    return f"{host}-{archivo}.txt"


def extraer_gateway(texto: str) -> str | None:
    """Extrae 'ip default-gateway X' del running-config."""
    m = re.search(r"(?mi)^\s*ip\s+default-gateway\s+(\S+)\s*$", texto)
    return m.group(1).strip() if m else None


def extraer_version_ios(texto: str) -> str | None:
    """Identifica la version de IOS en 'show version'."""
    m = re.search(r"(?i)cisco\s+ios\s+software.*?version\s+([^\s,;]+)", texto)
    if m:
        return m.group(1).strip().rstrip(",;")
    m = re.search(r"(?i)\bversion\s+([0-9][\w.()]+)", texto)
    return m.group(1).strip().rstrip(",;") if m else None


def extraer_hostname_version(texto: str) -> str | None:
    """Extrae el hostname desde 'show version' (linea '<HOST> uptime is ...')."""
    m = re.search(r"(?mi)^(\S+)\s+uptime\s+is\b", texto)
    if m:
        return m.group(1).strip()
    m = re.search(r"(?mi)^hostname\s+(\S+)\s*$", texto)
    if m:
        return m.group(1).strip()
    return None


def extraer_hostname_running(texto: str) -> str | None:
    """Extrae 'hostname X' del running-config (referencia cruzada)."""
    m = re.search(r"(?mi)^hostname\s+(\S+)\s*$", texto)
    return m.group(1).strip() if m else None


def parsear_trunk_brief(texto: str) -> dict:
    """Parsea 'show interfaces trunk' en {puerto: {nativa, permitidas, estado}}."""
    datos: dict[str, dict] = {}
    seccion = "inicio"
    for linea in texto.splitlines():
        s = linea.strip()
        if not s or s.startswith("!"):
            continue
        if re.match(r"^Port\s+Mode\s+Encapsulation", s, re.IGNORECASE):
            seccion = "tabla"
            continue
        if re.match(r"^Port\s+Vlans allowed on trunk", s, re.IGNORECASE):
            seccion = "permitidas"
            continue
        if re.match(r"^Port\s+Vlans", s, re.IGNORECASE):
            seccion = "otra"
            continue
        if re.match(r"^Port\b", s, re.IGNORECASE):
            seccion = "otra"
            continue
        if seccion == "tabla":
            m = re.match(r"^(\S+)\s+(\S+)\s+(\S+)\s+(\S+)\s+(\S+)", s)
            if m and re.search(r"\d", m.group(1)):
                puerto = normalizar_puerto(m.group(1))
                datos.setdefault(puerto, {})["modo"] = m.group(2)
                datos[puerto]["estado"] = m.group(4)
                datos[puerto]["nativa"] = m.group(5)
        elif seccion == "permitidas":
            m = re.match(r"^(\S+)\s+(.+)", s)
            if m and re.search(r"\d", m.group(1)):
                puerto = normalizar_puerto(m.group(1))
                datos.setdefault(puerto, {})["permitidas"] = normalizar_allowed(m.group(2))
    return datos


def parsear_ip_brief(texto: str) -> dict:
    """Parsea 'show ip interface brief' en {interfaz: {ip, estado, protocolo}}."""
    datos: dict[str, dict] = {}
    for linea in texto.splitlines():
        s = linea.strip()
        if not s or s.startswith("!") or s.lower().startswith("interface"):
            continue
        m = re.match(r"^(\S+)\s+(\S+)\s+\S+\s+\S+\s+(\S+)\s+(\S+)", s)
        if m:
            datos[normalizar_puerto(m.group(1))] = {
                "ip": m.group(2), "estado": m.group(3), "protocolo": m.group(4)}
    return datos


def parsear_rutas_conectadas(texto: str) -> list[str]:
    """Extrae 'C prefijo ... connected, interfaz' como 'prefijo via interfaz'."""
    rutas: set[str] = set()
    for linea in texto.splitlines():
        m = re.match(
            r"^[Cc]\s+(\d+\.\d+\.\d+\.\d+/\d+)\s+is directly connected,\s*(\S+?)[,\s]*$",
            linea.strip())
        if m:
            rutas.add(f"{m.group(1)} via {normalizar_puerto(m.group(2).rstrip(','))}")
    return sorted(rutas)


def leer_acceso(pa: dict) -> dict | None:
    """Lee un puerto de acceso tolerando puerto/port/name."""
    if not isinstance(pa, dict):
        return None
    puerto = pa.get("puerto", pa.get("port", pa.get("name")))
    vlan = pa.get("vlan", pa.get("vlan_id", pa.get("access_vlan")))
    desc = pa.get("descripcion", pa.get("description", pa.get("desc", "")))
    if puerto is None or vlan is None:
        return None
    try:
        vlan = int(str(vlan).split()[0])
    except (ValueError, TypeError):
        return None
    return {"puerto": str(puerto), "vlan": vlan, "descripcion": str(desc)}


def leer_troncal(pt: dict) -> dict | None:
    """Lee un troncal tolerando puerto/port/name y claves alternativas."""
    if not isinstance(pt, dict):
        return None
    puerto = pt.get("puerto", pt.get("port", pt.get("name")))
    if puerto is None:
        return None
    try:
        nat = int(str(pt.get("native_vlan", pt.get("nativa", TRUNK_NATIVA))).split()[0])
    except (ValueError, TypeError):
        nat = TRUNK_NATIVA
    allow = normalizar_allowed(pt.get("allowed_vlans",
                                      pt.get("permitidas", list(TRUNK_PERMITIDAS))))
    desc = pt.get("descripcion", pt.get("description", pt.get("desc", "")))
    return {"puerto": str(puerto), "native_vlan": nat,
            "allowed_vlans": allow, "descripcion": str(desc)}


# ---------- Filas de resultado ----------

def agregar(filas: list[dict], equipo: str, condicion: str, cumple: bool,
            evidencia: str, esperado: str = "", encontrado: str = "") -> None:
    """Agrega una fila CUMPLE/NO CUMPLE con detalle 'esperado X, encontrado Y'."""
    filas.append({"equipo": equipo, "condicion_evaluada": condicion,
                  "resultado": "CUMPLE" if cumple else "NO CUMPLE",
                  "evidencia_usada": evidencia,
                  "detalle_diferencia": "" if cumple else f"esperado {esperado}, encontrado {encontrado}"})


# ---------- Evaluacion por equipo ----------

def evaluar_version(filas: list[dict], host: str, texto: str | None, nombre: str) -> None:
    """Archivo existe, version identificada y hostname del inventario."""
    if texto is None:
        agregar(filas, host, "show version existe y versión IOS identificada",
                False, nombre, "archivo existe con Version", "archivo faltante o ilegible")
        agregar(filas, host, "hostname coincide con inventario",
                False, nombre, host, "sin evidencia (archivo faltante)")
        return
    ver = extraer_version_ios(texto)
    agregar(filas, host, "show version existe y versión IOS identificada",
            ver is not None, nombre, "archivo existe con Version",
            f"versión {ver}" if ver else "versión no identificada")
    hn = extraer_hostname_version(texto)
    agregar(filas, host, "hostname coincide con inventario",
            hn == host, nombre, host, hn if hn else "hostname no identificado")


def evaluar_vlans(filas: list[dict], host: str, esperadas: list[dict],
                  brief: dict | None, nombre: str) -> None:
    """Existencia, nombre y estado active de cada VLAN."""
    for v in esperadas:
        vid, esp_nombre = int(v["id"]), str(v["nombre"])
        cond = f"VLAN {vid} existencia/nombre/estado"
        if brief is None:
            agregar(filas, host, cond, False, nombre,
                    "existe/nombre/estado active", "sin evidencia (archivo faltante)")
            continue
        if vid not in brief:
            agregar(filas, host, cond, False, nombre,
                    "existe/nombre/estado active", "no existe en show vlan brief")
            continue
        enc = brief[vid]
        ok = (str(enc["nombre"]).lower() == esp_nombre.lower()
              and str(enc["estado"]).lower() == "active")
        agregar(filas, host, cond, ok, nombre,
                f"{esp_nombre}/active",
                f"{enc['nombre']}/{enc['estado']}")


def evaluar_accesos(filas: list[dict], host: str, accesos: list,
                    interfaces: dict | None, nombre: str) -> None:
    """Modo access, VLAN y descripcion de cada puerto de acceso."""
    for pa_crudo in accesos:
        pa = leer_acceso(pa_crudo)
        if pa is None:
            agregar(filas, host, "puerto acceso (definición válida)", False, nombre,
                    "puerto/vlan/descripcion", f"definición inválida: {pa_crudo}")
            continue
        canon = normalizar_puerto(pa["puerto"])
        cond = f"puerto acceso {canon} modo/VLAN/descripción"
        esp = f"modo=access vlan={pa['vlan']} desc={pa['descripcion']}"
        if interfaces is None:
            agregar(filas, host, cond, False, nombre, esp, "sin evidencia (archivo faltante)")
            continue
        cfg = interfaces.get(canon)
        if cfg is None:
            agregar(filas, host, cond, False, nombre, esp, "interfaz no existe en running-config")
            continue
        enc = (f"modo={cfg.get('mode', '(sin modo)')} "
               f"vlan={cfg.get('access_vlan', '1 (por defecto)')} "
               f"desc={cfg.get('description', '(sin descripcion)')}")
        ok = (str(cfg.get("mode", "")).lower() == "access"
              and cfg.get("access_vlan") == pa["vlan"]
              and str(cfg.get("description", "")) == pa["descripcion"])
        agregar(filas, host, cond, ok, nombre, esp, enc)


def evaluar_troncales_running(filas: list[dict], host: str, troncos: list,
                              interfaces: dict | None, nombre: str) -> None:
    """Modo trunk, nativa, permitidas y descripcion (running-config)."""
    for pt_crudo in troncos:
        pt = leer_troncal(pt_crudo)
        if pt is None:
            agregar(filas, host, "troncal (definición válida)", False, nombre,
                    "puerto/native/allowed/descripcion", f"definición inválida: {pt_crudo}")
            continue
        canon = normalizar_puerto(pt["puerto"])
        cond = f"troncal {canon} modo/nativa/permitidas/descripción (running)"
        esp = (f"modo=trunk nativa={pt['native_vlan']} "
               f"permitidas={fmt_allowed(pt['allowed_vlans'])} desc={pt['descripcion']}")
        if interfaces is None:
            agregar(filas, host, cond, False, nombre, esp, "sin evidencia (archivo faltante)")
            continue
        cfg = interfaces.get(canon)
        if cfg is None:
            agregar(filas, host, cond, False, nombre, esp, "interfaz no existe en running-config")
            continue
        enc = (f"modo={cfg.get('mode', '(sin modo)')} "
               f"nativa={cfg.get('native_vlan', '(sin nativa)')} "
               f"permitidas={fmt_allowed(cfg.get('allowed_vlans', [])) if 'allowed_vlans' in cfg else '(sin lista)'} "
               f"desc={cfg.get('description', '(sin descripcion)')}")
        ok = (str(cfg.get("mode", "")).lower() == "trunk"
              and cfg.get("native_vlan") == pt["native_vlan"]
              and cfg.get("allowed_vlans", []) == sorted(pt["allowed_vlans"])
              and str(cfg.get("description", "")) == pt["descripcion"])
        agregar(filas, host, cond, ok, nombre, esp, enc)


def evaluar_troncales_brief(filas: list[dict], host: str, troncos: list,
                            texto: str | None, nombre: str) -> None:
    """Cada troncal con trunking, nativa 99 y permitidas 10,20,99."""
    esperadas = fmt_allowed(TRUNK_PERMITIDAS)
    for pt_crudo in troncos:
        pt = leer_troncal(pt_crudo)
        if pt is None:
            continue  # ya reportado en el bloque de running
        canon = normalizar_puerto(pt["puerto"])
        cond = f"troncal {canon} trunking/nativa/permitidas (interfaces trunk)"
        esp = f"trunking nativa={TRUNK_NATIVA} permitidas={esperadas}"
        if texto is None:
            agregar(filas, host, cond, False, nombre, esp, "sin evidencia (archivo faltante)")
            continue
        datos = parsear_trunk_brief(texto)
        cfg = datos.get(canon)
        if cfg is None:
            agregar(filas, host, cond, False, nombre, esp, "puerto no aparece en show interfaces trunk")
            continue
        enc = (f"{cfg.get('estado', '(sin estado)')} "
               f"nativa={cfg.get('nativa', '(sin nativa)')} "
               f"permitidas={fmt_allowed(cfg.get('permitidas', [])) if 'permitidas' in cfg else '(sin lista)'}")
        ok = ("trunking" in str(cfg.get("estado", "")).lower()
              and str(cfg.get("nativa", "")) == str(TRUNK_NATIVA)
              and (cfg.get("permitidas", []) == sorted(TRUNK_PERMITIDAS)))
        agregar(filas, host, cond, ok, nombre, esp, enc)


def evaluar_gestion(filas: list[dict], host: str, mgmt_ip: str,
                    texto: str | None, nombre: str) -> None:
    """Vlan99 con la IP de mgmt_ip y estado up/up."""
    ip_esp = str(mgmt_ip).split("/")[0]
    cond = "gestión Vlan99 IP y estado up/up"
    if texto is None:
        agregar(filas, host, cond, False, nombre,
                f"Vlan99 {ip_esp} up/up", "sin evidencia (archivo faltante)")
        return
    datos = parsear_ip_brief(texto)
    cfg = None
    for k, v in datos.items():
        if k.lower().replace(" ", "") == "vlan99":
            cfg = v
            break
    if cfg is None:
        agregar(filas, host, cond, False, nombre,
                f"Vlan99 {ip_esp} up/up", "Vlan99 no aparece en show ip interface brief")
        return
    enc = f"Vlan99 {cfg.get('ip')} {cfg.get('estado')}/{cfg.get('protocolo')}"
    ok = (cfg.get("ip") == ip_esp
          and str(cfg.get("estado", "")).lower() == "up"
          and str(cfg.get("protocolo", "")).lower() == "up")
    agregar(filas, host, cond, ok, nombre, f"Vlan99 {ip_esp} up/up", enc)


def evaluar_gateway(filas: list[dict], host: str, texto: str | None, nombre: str) -> None:
    """ip default-gateway 192.168.99.1 en running-config."""
    cond = f"gateway ip default-gateway {GATEWAY_ESPERADO}"
    if texto is None:
        agregar(filas, host, cond, False, nombre,
                GATEWAY_ESPERADO, "sin evidencia (archivo faltante)")
        return
    gw = extraer_gateway(texto)
    agregar(filas, host, cond, gw == GATEWAY_ESPERADO, nombre,
            GATEWAY_ESPERADO, gw if gw else "sin ip default-gateway")


def evaluar_subinterfaces(filas: list[dict], host: str, subs: list,
                          interfaces: dict | None, nombre: str) -> None:
    """Encapsulacion dot1Q e IP normalizada de cada subinterfaz."""
    for sub in subs:
        if not isinstance(sub, dict):
            continue
        nom = normalizar_puerto(str(sub.get("interfaz", sub.get("interface", "?"))))
        try:
            enc = int(str(sub.get("encapsulation", sub.get("encap", sub.get("id")))).split()[0])
        except (ValueError, TypeError, AttributeError):
            agregar(filas, host, f"subinterfaz {nom} encapsulación/IP", False, nombre,
                    "encapsulación válida", f"definición inválida: {sub}")
            continue
        nativa = bool(sub.get("native", sub.get("nativa", False)))
        esp_ip = normalizar_ip(str(sub.get("ip", "")))
        cond = f"subinterfaz {nom} encapsulación/IP"
        esp = f"dot1Q {enc}{' native' if nativa else ''} ip={esp_ip}"
        if interfaces is None:
            agregar(filas, host, cond, False, nombre, esp, "sin evidencia (archivo faltante)")
            continue
        cfg = interfaces.get(nom)
        if cfg is None:
            agregar(filas, host, cond, False, nombre, esp, "subinterfaz no existe en running-config")
            continue
        enc_enc = str(cfg.get("encap_vlan", "(sin encap)"))
        if cfg.get("encap_native"):
            enc_enc += " native"
        enc_ip = normalizar_ip(str(cfg.get("ip", "(sin ip)")))
        enc_txt = f"dot1Q {enc_enc} ip={enc_ip}"
        ok = (cfg.get("encap_vlan") == enc
              and bool(cfg.get("encap_native")) == nativa
              and enc_ip == esp_ip)
        agregar(filas, host, cond, ok, nombre, esp, enc_txt)


def evaluar_brief_router(filas: list[dict], host: str, fisica: str, subs: list,
                         texto: str | None, nombre: str) -> None:
    """Fisica y subinterfaces con su IP y estado up/up."""
    esperado_fis = normalizar_puerto(str(fisica))
    objetivos = [(esperado_fis, None)]
    for sub in subs:
        if isinstance(sub, dict):
            nom = normalizar_puerto(str(sub.get("interfaz", sub.get("interface", "?"))))
            ip_esp = str(sub.get("ip", "")).split("/")[0]
            objetivos.append((nom, ip_esp))
    for nom, ip_esp in objetivos:
        cond = f"interfaz {nom} IP y estado up/up (ip brief)"
        if texto is None:
            agregar(filas, host, cond, False, nombre,
                    f"{ip_esp or 'cualquiera'} up/up", "sin evidencia (archivo faltante)")
            continue
        datos = parsear_ip_brief(texto)
        cfg = datos.get(nom)
        if cfg is None:
            agregar(filas, host, cond, False, nombre,
                    f"{ip_esp or 'cualquiera'} up/up", "interfaz no aparece en show ip interface brief")
            continue
        enc = f"{cfg.get('ip')} {cfg.get('estado')}/{cfg.get('protocolo')}"
        if ip_esp is None:
            ok = (str(cfg.get("estado", "")).lower() == "up"
                  and str(cfg.get("protocolo", "")).lower() == "up")
            agregar(filas, host, cond, ok, nombre, "presente up/up", enc)
        else:
            ok = (cfg.get("ip") == ip_esp
                  and str(cfg.get("estado", "")).lower() == "up"
                  and str(cfg.get("protocolo", "")).lower() == "up")
            agregar(filas, host, cond, ok, nombre, f"{ip_esp} up/up", enc)


def evaluar_rutas(filas: list[dict], host: str, texto: str | None, nombre: str) -> None:
    """Ruta conectada para 192.168.10.0/24, .20.0/24 y .99.0/24."""
    for red in RUTAS_ESPERADAS:
        cond = f"ruta conectada {red}"
        if texto is None:
            agregar(filas, host, cond, False, nombre, red, "sin evidencia (archivo faltante)")
            continue
        conectadas = parsear_rutas_conectadas(texto)
        prefijos = [c.split(" via ")[0] for c in conectadas]
        ok = red in prefijos
        agregar(filas, host, cond, ok, nombre, red,
                ", ".join(conectadas) or "sin rutas conectadas")


# ---------- Programa principal ----------

def resolver_equipos(diseno_sw: dict, diseno_rt: dict, todos: bool,
                     equipos_arg: str | None) -> tuple[list[str], dict]:
    """Devuelve (lista de equipos, rol por equipo)."""
    roles = {h: "switch" for h in diseno_sw}
    roles.update({h: "router" for h in diseno_rt})
    if equipos_arg:
        lista = [e.strip() for e in equipos_arg.split(",") if e.strip()]
        if not lista:
            raise ValueError("--equipos no contiene ningún equipo.")
        for e in lista:
            if e not in roles:
                raise ValueError(f"equipo desconocido en --equipos: '{e}'.")
        return lista, roles
    if todos:
        orden = [h for h in list(diseno_rt) + list(diseno_sw) if h in roles]
        return orden, roles
    return [e for e in EQUIPOS_DEFECTO if e in roles] or list(roles)[:3], roles


def main(argv: list[str] | None = None) -> int:
    """Audita la flota y escribe CSV + tabla legible."""
    parser = argparse.ArgumentParser(description="Audita la flota (solo lectura, sin SSH).")
    parser.add_argument("--diseno", default=str(RUTA_DISENO), help="YAML del diseño esperado.")
    parser.add_argument("--respaldos", default=str(DIR_RESPALDOS), help="Carpeta de evidencia.")
    parser.add_argument("--salida-csv", default=str(RUTA_CSV), help="CSV de resultados.")
    parser.add_argument("--salida-txt", default=str(RUTA_TXT), help="Tabla legible.")
    parser.add_argument("--todos", action="store_true", help="Incluye ACC2 y ACC3.")
    parser.add_argument("--equipos", default=None, help="Lista A,B para limitar equipos.")
    args = parser.parse_args(argv)

    ruta_diseno = Path(args.diseno)
    dir_respaldos = Path(args.respaldos)
    ruta_csv = Path(args.salida_csv)
    ruta_txt = Path(args.salida_txt)

    try:
        diseno = cargar_diseno(ruta_diseno)
    except FileNotFoundError:
        print(f"ERROR: no existe el diseño: {ruta_diseno}")
        return 2
    except yaml.YAMLError as e:
        print(f"ERROR: YAML inválido en {ruta_diseno}: {e}")
        return 2
    if not isinstance(diseno, dict):
        print(f"ERROR: el diseño {ruta_diseno} no es un YAML válido.")
        return 2
    switches = diseno.get("switches") or {}
    routers = diseno.get("routers") or {}
    if not isinstance(switches, dict) or not switches:
        print("ERROR: el diseño no define 'switches'.")
        return 2
    if not isinstance(routers, dict) or not routers:
        print("ERROR: el diseño no define 'routers'.")
        return 2
    try:
        equipos, roles = resolver_equipos(switches, routers, args.todos, args.equipos)
    except ValueError as e:
        print(f"ERROR: {e}")
        return 2
    vlans_esp = obtener_vlans_esperadas(diseno)
    if not vlans_esp:
        print("ERROR: el diseño no define VLANs esperadas.")
        return 2

    filas: list[dict] = []
    leidos = 0
    # Cantidad de archivos esperados para el reporte de datos leídos.
    esperados = sum(5 if roles[h] == "switch" else 4 for h in equipos)
    for host in equipos:
        rol = roles[host]
        archivos = ([ARCH_RUNNING, ARCH_VERSION, ARCH_VLAN, ARCH_TRUNK, ARCH_IPBRIEF]
                    if rol == "switch" else
                    [ARCH_RUNNING, ARCH_VERSION, ARCH_IPBRIEF, ARCH_ROUTE])
        textos = {}
        for a in archivos:
            p = dir_respaldos / nombre_archivo(dir_respaldos, host, a)
            textos[a] = leer_texto(p)
            if textos[a] is not None:
                leidos += 1
        run = parsear_running_config(textos[ARCH_RUNNING]) if textos[ARCH_RUNNING] else None
        interfaces = (run.get("interfaces", {}) or {}) if run else None

        evaluar_version(filas, host, textos[ARCH_VERSION],
                        nombre_archivo(dir_respaldos, host, ARCH_VERSION))
        if rol == "switch":
            brief = parsear_vlan_brief(textos[ARCH_VLAN]) if textos[ARCH_VLAN] else None
            evaluar_vlans(filas, host, vlans_esp, brief,
                          nombre_archivo(dir_respaldos, host, ARCH_VLAN))
            data = switches.get(host) or {}
            evaluar_accesos(filas, host, data.get("puertos_acceso", []) or [],
                            interfaces, nombre_archivo(dir_respaldos, host, ARCH_RUNNING))
            troncos = data.get("puertos_trunk", []) or []
            evaluar_troncales_running(filas, host, troncos, interfaces,
                                      nombre_archivo(dir_respaldos, host, ARCH_RUNNING))
            evaluar_troncales_brief(filas, host, troncos, textos[ARCH_TRUNK],
                                    nombre_archivo(dir_respaldos, host, ARCH_TRUNK))
            evaluar_gestion(filas, host, str((data.get("mgmt_ip", ""))),
                            textos[ARCH_IPBRIEF],
                            nombre_archivo(dir_respaldos, host, ARCH_IPBRIEF))
            evaluar_gateway(filas, host, textos[ARCH_RUNNING],
                            nombre_archivo(dir_respaldos, host, ARCH_RUNNING))
        else:
            data = routers.get(host) or {}
            subs = data.get("subinterfaces", []) or []
            evaluar_subinterfaces(filas, host, subs, interfaces,
                                  nombre_archivo(dir_respaldos, host, ARCH_RUNNING))
            evaluar_brief_router(filas, host, str(data.get("interfaz_fisica", "GigabitEthernet0/1")),
                                 subs, textos[ARCH_IPBRIEF],
                                 nombre_archivo(dir_respaldos, host, ARCH_IPBRIEF))
            evaluar_rutas(filas, host, textos[ARCH_ROUTE],
                          nombre_archivo(dir_respaldos, host, ARCH_ROUTE))

    # --- CSV con columnas exactas ---
    ruta_csv.parent.mkdir(parents=True, exist_ok=True)
    with open(ruta_csv, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["equipo", "condicion_evaluada", "resultado",
                    "evidencia_usada", "detalle_diferencia"])
        for r in filas:
            w.writerow([r["equipo"], r["condicion_evaluada"], r["resultado"],
                        r["evidencia_usada"], r["detalle_diferencia"]])

    # --- Tabla legible (misma salida en pantalla y txt) ---
    salida: list[str] = []
    salida.append("AUDITORIA DE FLOTA (Python, solo lectura)")
    salida.append("=" * 100)
    salida.append("[1] QUÉ DATOS LEYÓ")
    salida.append(f"  - Diseño: {ruta_diseno} ({len(switches)} switches, {len(routers)} routers)")
    salida.append(f"  - Evidencia: {dir_respaldos} ({leidos} de {esperados} archivos para {len(equipos)} equipos)")
    salida.append(f"  - Equipos: {', '.join(equipos)}")
    salida.append("")
    salida.append("[2] QUÉ CONDICIÓN EVALUÓ")
    salida.append("  - Todos: show version (archivo, versión IOS, hostname).")
    salida.append("  - Switches: VLANs, accesos (running), troncales (running y brief),")
    salida.append("    gestión Vlan99 (ip brief) y gateway (running).")
    salida.append("  - Router: subinterfaces (running), brief (física y subifs up/up),")
    salida.append("    rutas conectadas 10/20/99 (ip route).")
    salida.append("  Regla: archivo faltante o ilegible = NO CUMPLE explícito.")
    salida.append("")
    salida.append("[3] CUÁL FUE EL RESULTADO")
    total_ok = total_no = 0
    for host in equipos:
        propias = [r for r in filas if r["equipo"] == host]
        ok = sum(1 for r in propias if r["resultado"] == "CUMPLE")
        no = len(propias) - ok
        total_ok += ok
        total_no += no
        salida.append(f"--- EQUIPO: {host} ({ok} CUMPLE, {no} NO CUMPLE) ---")
        for r in propias:
            marca = "OK " if r["resultado"] == "CUMPLE" else "DIF"
            salida.append(f"  [{marca}] {r['condicion_evaluada']} | {r['resultado']} | {r['evidencia_usada']}")
            if r["detalle_diferencia"]:
                salida.append(f"       detalle: {r['detalle_diferencia']}")
        salida.append("")
    salida.append("RESUMEN")
    salida.append(f"  Total: {total_ok} CUMPLE, {total_no} NO CUMPLE en {len(filas)} condiciones")
    if total_no:
        salida.append(f"  Equipos con fallos: {', '.join(sorted({r['equipo'] for r in filas if r['resultado'] == 'NO CUMPLE'}))}")
    global_ok = bool(filas) and total_no == 0
    salida.append("  CUMPLIMIENTO GLOBAL: " + ("CUMPLE" if global_ok else "NO CUMPLE"))
    texto_final = "\n".join(salida) + "\n"
    print(texto_final)
    ruta_txt.parent.mkdir(parents=True, exist_ok=True)
    ruta_txt.write_text(texto_final, encoding="utf-8")
    print(f"CSV guardado en: {ruta_csv}")
    print(f"Tabla guardada en: {ruta_txt}")
    if not filas:
        return 2
    return 0 if global_ok else 1


if __name__ == "__main__":
    sys.exit(main())
