#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Valida un cambio controlado comparando evidencia ANTES contra DESPUES.

Demuestra (a) que el cambio solicitado si ocurrio y (b) que los
elementos criticos no se alteraron. Solo lee archivos, no configura
equipos ni abre SSH.
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

import yaml

# Permite importar auditar_sucursal.py (mismo directorio).
sys.path.insert(0, str(Path(__file__).resolve().parent))
from auditar_sucursal import (  # noqa: E402
    fmt_allowed,
    normalizar_allowed,
    normalizar_ip,
    normalizar_puerto,
    parsear_running_config,
    parsear_vlan_brief,
)

# Rutas base relativas al repo (no al cwd).
BASE = Path(__file__).resolve().parent.parent
RUTA_CAMBIO = BASE / "datos" / "cambio_aprobado.yml"
RUTA_DISENO = BASE / "variables" / "sucursal_01.yml"
DIR_ANTES = BASE / "evidencias" / "antes"
DIR_DESPUES = BASE / "evidencias" / "despues"
RUTA_SALIDA = BASE / "evidencias" / "validacion_cambio.txt"

# Nombres de archivo iguales a los de respaldos/.
ARCH_RUNNING = "running-config"
ARCH_VLAN = "show-vlan-brief"
ARCH_TRUNK = "show-interfaces-trunk"
ARCH_IPBRIEF = "show-ip-interface-brief"
ARCH_ROUTE = "show-ip-route"

OK_CAMBIO = "cambio aplicado correctamente"
OK_CRITICO = "elemento crítico sin cambios"
DIFERENCIA = "diferencia detectada"


# ---------- Carga y validacion de entradas ----------

def cargar_yaml(ruta: Path):
    """Carga un YAML y devuelve su contenido."""
    with open(ruta, encoding="utf-8") as f:
        return yaml.safe_load(f)


def validar_cambio(datos) -> tuple[dict, dict]:
    """Valida la estructura de cambio_aprobado.yml."""
    if not isinstance(datos, dict) or "cambio" not in datos:
        raise ValueError("falta la clave 'cambio' en el archivo de cambio.")
    cambio = datos["cambio"] or {}
    criticos = datos.get("criticos") or {}
    for clave in ("id", "equipo", "interfaz", "descripcion_anterior", "descripcion_nueva"):
        if cambio.get(clave) is None or str(cambio.get(clave)) == "":
            raise ValueError(f"falta 'cambio.{clave}' en el archivo de cambio.")
    if not isinstance(criticos, dict):
        raise ValueError("'criticos' debe ser un diccionario.")
    vlans = criticos.get("vlans", [])
    if not isinstance(vlans, list) or not vlans:
        raise ValueError("'criticos.vlans' debe ser una lista no vacia.")
    try:
        vlans_int = sorted({int(v) for v in vlans})
    except (ValueError, TypeError):
        raise ValueError("'criticos.vlans' debe contener enteros.")
    if 99 not in vlans_int:
        raise ValueError("'criticos.vlans' debe incluir la VLAN 99 (obligatoria).")
    criticos["vlans"] = vlans_int
    for bandera in ("gestion", "troncales", "rutas", "router_subinterfaces",
                    "puertos_acceso_restantes"):
        criticos.setdefault(bandera, True)
    return cambio, criticos


def validar_diseno(diseno: dict) -> tuple[dict, dict]:
    """Valida que el diseno tenga switches y routers."""
    if not isinstance(diseno, dict):
        raise ValueError("el diseno no es un diccionario YAML valido.")
    switches = diseno.get("switches") or {}
    routers = diseno.get("routers") or {}
    if not isinstance(switches, dict) or not switches:
        raise ValueError("el diseno no define 'switches'.")
    if not isinstance(routers, dict) or not routers:
        raise ValueError("el diseno no define 'routers'.")
    return switches, routers


# ---------- Parseo adicional (trunk, ip brief, rutas, gestion) ----------

def extraer_gateway(texto: str) -> str | None:
    """Extrae 'ip default-gateway X' del running-config."""
    m = re.search(r"(?mi)^\s*ip\s+default-gateway\s+(\S+)\s*$", texto)
    return m.group(1).strip() if m else None


def es_vlan99(nombre_if: str) -> bool:
    """Dice si una interfaz es la SVI Vlan99 (tolerante a mayusculas)."""
    return nombre_if.lower().replace(" ", "") == "vlan99"


def extraer_ip_gestion(interfaces: dict) -> str | None:
    """Extrae la IP de Vlan99 desde interfaces parseadas (forma CIDR)."""
    for nombre, cfg in (interfaces or {}).items():
        if es_vlan99(str(nombre)) and isinstance(cfg, dict) and cfg.get("ip"):
            return normalizar_ip(str(cfg["ip"]))
    return None


def parsear_trunk_brief(texto: str) -> dict:
    """Parsea 'show interfaces trunk' en {puerto: {nativa, permitidas, modo}}."""
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
                datos[puerto]["encapsulacion"] = m.group(3)
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
            try:
                puerto = normalizar_puerto(m.group(1))
            except Exception:  # noqa: BLE001 - sigue con el nombre crudo
                puerto = m.group(1)
            datos[puerto] = {"ip": m.group(2), "estado": m.group(3), "protocolo": m.group(4)}
    return datos


def parsear_rutas_conectadas(texto: str) -> list[str]:
    """Extrae rutas conectadas 'C prefijo ... connected, interfaz'."""
    rutas: set[str] = set()
    for linea in texto.splitlines():
        m = re.match(
            r"^[Cc]\s+(\d+\.\d+\.\d+\.\d+/\d+)\s+is directly connected,\s*(\S+?)[,\s]*$",
            linea.strip(),
        )
        if m:
            rutas.add(f"{m.group(1)} via {normalizar_puerto(m.group(2).rstrip(','))}")
    return sorted(rutas)


def leer_texto(ruta: Path) -> str | None:
    """Lee un archivo de evidencia o devuelve None si no existe."""
    try:
        return ruta.read_text(encoding="utf-8", errors="replace")
    except FileNotFoundError:
        return None


def ruta_evidencia(directorio: Path, host: str, archivo: str) -> Path:
    """Construye <dir>/<HOST>-<archivo>.txt."""
    return directorio / f"{host}-{archivo}.txt"


# ---------- Evaluacion ----------

def nueva_fila(categoria: str, elemento: str, chequeo: str, antes: str,
               despues: str, estado: str, detalle: str = "") -> dict:
    """Crea una fila de resultado."""
    return {"categoria": categoria, "elemento": elemento, "chequeo": chequeo,
            "antes": antes, "despues": despues, "estado": estado, "detalle": detalle}


def evaluar_cambio_solicitado(cambio: dict, run_antes: dict | None,
                              run_despues: dict | None, falta_antes: bool,
                              falta_despues: bool) -> dict:
    """Condicion 1: la descripcion era la anterior y ahora es la nueva."""
    interfaz = normalizar_puerto(str(cambio["interfaz"]))
    esp_antes = str(cambio["descripcion_anterior"])
    esp_despues = str(cambio["descripcion_nueva"])
    if falta_antes or run_antes is None:
        return nueva_fila("CAMBIO SOLICITADO", interfaz, "descripcion",
                          "(evidencia faltante)", "(no evaluable)",
                          f"{DIFERENCIA}: evidencia faltante",
                          "falta running-config en ANTES")
    if falta_despues or run_despues is None:
        enc_antes = ((run_antes.get("interfaces", {}) or {}).get(interfaz) or {}).get("description", "(sin descripcion)")
        return nueva_fila("CAMBIO SOLICITADO", interfaz, "descripcion",
                          str(enc_antes), "(evidencia faltante)",
                          f"{DIFERENCIA}: evidencia faltante",
                          "falta running-config en DESPUES")
    enc_antes = ((run_antes.get("interfaces", {}) or {}).get(interfaz) or {}).get("description", "(sin descripcion)")
    enc_despues = ((run_despues.get("interfaces", {}) or {}).get(interfaz) or {}).get("description", "(sin descripcion)")
    if str(enc_antes) != esp_antes:
        return nueva_fila("CAMBIO SOLICITADO", interfaz, "descripcion",
                          str(enc_antes), str(enc_despues),
                          f"{DIFERENCIA} (ANTES no coincide)",
                          f"en ANTES se esperaba '{esp_antes}' y se encontro '{enc_antes}'")
    if str(enc_despues) != esp_despues:
        return nueva_fila("CAMBIO SOLICITADO", interfaz, "descripcion",
                          str(enc_antes), str(enc_despues),
                          f"{DIFERENCIA} (DESPUES no coincide)",
                          f"en DESPUES se esperaba '{esp_despues}' y se encontro '{enc_despues}'")
    return nueva_fila("CAMBIO SOLICITADO", interfaz, "descripcion",
                      str(enc_antes), str(enc_despues), OK_CAMBIO,
                      f"era '{esp_antes}' y ahora es '{esp_despues}'")


def evaluar_otra_diferencia(cambio: dict, run_antes: dict | None,
                            run_despues: dict | None) -> dict | None:
    """Condicion 3: otra diferencia en la interfaz del cambio (ej. VLAN)."""
    interfaz = normalizar_puerto(str(cambio["interfaz"]))
    if run_antes is None or run_despues is None:
        return nueva_fila("OTRA DIFERENCIA", interfaz, "resto de la interfaz",
                          "(no evaluable)", "(no evaluable)",
                          f"{DIFERENCIA}: evidencia faltante",
                          "sin running-config en ANTES o DESPUES")
    cfg_a = (run_antes.get("interfaces", {}) or {}).get(interfaz)
    cfg_d = (run_despues.get("interfaces", {}) or {}).get(interfaz)
    if cfg_a is None or cfg_d is None:
        return nueva_fila("OTRA DIFERENCIA", interfaz, "resto de la interfaz",
                          "(no existe en ANTES)" if cfg_a is None else "(existe)",
                          "(no existe en DESPUES)" if cfg_d is None else "(existe)",
                          f"{DIFERENCIA}: interfaz ausente",
                          "la interfaz debe existir en ambas fases")
    claves = sorted((set(cfg_a) | set(cfg_d)) - {"description"})
    cambios = []
    for k in claves:
        va = cfg_a.get(k, "(sin dato)")
        vd = cfg_d.get(k, "(sin dato)")
        if str(va) != str(vd):
            cambios.append(f"{k}: antes '{va}' vs despues '{vd}'")
    if cambios:
        return nueva_fila("OTRA DIFERENCIA", interfaz, "resto de la interfaz",
                          "; ".join(cambios), "; ".join(cambios),
                          f"{DIFERENCIA}: cambio no aprobado",
                          "ademas de la descripcion cambio: " + "; ".join(cambios))
    return None  # Sin otra diferencia: no se reporta como fallo.


def firma_vlan(vid: int, running_vlans: dict, brief: dict | None) -> str:
    """Firma comparable de una VLAN (running + brief)."""
    nom_run = str(running_vlans.get(vid, "(no definida)"))
    if brief is None:
        return f"running={nom_run} | brief=(sin archivo)"
    if vid not in brief:
        return f"running={nom_run} | brief=(no existe)"
    return (f"running={nom_run} | brief={brief[vid]['nombre']}/"
            f"{brief[vid]['estado']}")


def evaluar_vlans(vlans: list[int], run_a: dict, run_d: dict,
                  brief_a: dict | None, brief_d: dict | None,
                  host: str) -> list[dict]:
    """Compara cada VLAN critica entre ANTES y DESPUES."""
    filas = []
    for vid in vlans:
        fa = firma_vlan(vid, run_a.get("vlans", {}), brief_a)
        fd = firma_vlan(vid, run_d.get("vlans", {}), brief_d)
        if brief_a is None or brief_d is None:
            filas.append(nueva_fila("CRITICO VLAN", f"{host} VLAN {vid}",
                                    "existencia/nombre/estado", fa, fd,
                                    f"{DIFERENCIA}: evidencia faltante",
                                    "falta show-vlan-brief en ANTES o DESPUES"))
        elif fa != fd:
            filas.append(nueva_fila("CRITICO VLAN", f"{host} VLAN {vid}",
                                    "existencia/nombre/estado", fa, fd,
                                    f"{DIFERENCIA}: cambio critico",
                                    f"VLAN {vid}: antes '{fa}' vs despues '{fd}'"))
        else:
            filas.append(nueva_fila("CRITICO VLAN", f"{host} VLAN {vid}",
                                    "existencia/nombre/estado", fa, fd, OK_CRITICO))
    # VLAN 99 obligatoria en especial: debe existir en ambas fases.
    existe_a = 99 in (run_a.get("vlans", {})) or (brief_a is not None and 99 in brief_a)
    existe_d = 99 in (run_d.get("vlans", {})) or (brief_d is not None and 99 in brief_d)
    if not (existe_a and existe_d):
        filas.append(nueva_fila("CRITICO VLAN99", f"{host} VLAN 99",
                                "obligatoria presente",
                                "existe" if existe_a else "no existe/faltante",
                                "existe" if existe_d else "no existe/faltante",
                                f"{DIFERENCIA}: falta VLAN 99",
                                "la VLAN 99 es obligatoria en ANTES y DESPUES"))
    else:
        filas.append(nueva_fila("CRITICO VLAN99", f"{host} VLAN 99",
                                "obligatoria presente", "existe", "existe", OK_CRITICO))
    return filas


def evaluar_gestion(host: str, texto_a: str | None, texto_d: str | None,
                    run_a: dict, run_d: dict,
                    brief_a: str | None, brief_d: str | None) -> dict:
    """Compara IP de Vlan99 y default-gateway de un switch."""
    if texto_a is None or texto_d is None:
        return nueva_fila("CRITICO GESTION", host, "IP Vlan99 + gateway",
                          "(evidencia faltante)", "(evidencia faltante)",
                          f"{DIFERENCIA}: evidencia faltante",
                          "falta running-config en ANTES o DESPUES")
    ip_a = extraer_ip_gestion(run_a.get("interfaces", {}))
    ip_d = extraer_ip_gestion(run_d.get("interfaces", {}))
    gw_a = extraer_gateway(texto_a)
    gw_d = extraer_gateway(texto_d)
    # Complemento con ip brief.
    if brief_a is None or brief_d is None:
        return nueva_fila("CRITICO GESTION", host, "IP Vlan99 + gateway",
                          f"ip={ip_a or '(sin IP)'} gw={gw_a or '(sin gateway)'}",
                          f"ip={ip_d or '(sin IP)'} gw={gw_d or '(sin gateway)'}",
                          f"{DIFERENCIA}: evidencia faltante",
                          "falta show-ip-interface-brief en ANTES o DESPUES")
    ib_a = parsear_ip_brief(brief_a)
    ib_d = parsear_ip_brief(brief_d)
    # Busca Vlan99 en el brief (clave tolerante).
    bip_a = bip_d = None
    for k, v in ib_a.items():
        if es_vlan99(k):
            bip_a = v.get("ip")
    for k, v in ib_d.items():
        if es_vlan99(k):
            bip_d = v.get("ip")
    firma_a = f"running-ip={ip_a or '(sin IP)'} gw={gw_a or '(sin gateway)'} brief-ip={bip_a or '(sin IP)'}"
    firma_d = f"running-ip={ip_d or '(sin IP)'} gw={gw_d or '(sin gateway)'} brief-ip={bip_d or '(sin IP)'}"
    if firma_a != firma_d:
        return nueva_fila("CRITICO GESTION", host, "IP Vlan99 + gateway",
                          firma_a, firma_d, f"{DIFERENCIA}: cambio critico",
                          f"gestion: antes '{firma_a}' vs despues '{firma_d}'")
    return nueva_fila("CRITICO GESTION", host, "IP Vlan99 + gateway",
                      firma_a, firma_d, OK_CRITICO)


def evaluar_troncales(host: str, diseno_trunks: list, run_a: dict,
                      run_d: dict, trunk_a: str | None, trunk_d: str | None) -> list[dict]:
    """Compara troncales (running) y el brief de troncales (por equipo)."""
    filas = []
    for pt in diseno_trunks:
        if not isinstance(pt, dict) or pt.get("puerto") is None:
            continue
        puerto = normalizar_puerto(str(pt["puerto"]))
        if run_a is None or run_d is None:
            filas.append(nueva_fila("CRITICO TRONCAL", f"{host} {puerto}",
                                    "modo/nativa/permitidas/descripcion",
                                    "(evidencia faltante)", "(evidencia faltante)",
                                    f"{DIFERENCIA}: evidencia faltante",
                                    "falta running-config en ANTES o DESPUES"))
            continue
        cfg_a = (run_a.get("interfaces", {}) or {}).get(puerto)
        cfg_d = (run_d.get("interfaces", {}) or {}).get(puerto)
        if cfg_a is None or cfg_d is None:
            filas.append(nueva_fila("CRITICO TRONCAL", f"{host} {puerto}",
                                    "modo/nativa/permitidas/descripcion",
                                    "(ausente en ANTES)" if cfg_a is None else "(presente)",
                                    "(ausente en DESPUES)" if cfg_d is None else "(presente)",
                                    f"{DIFERENCIA}: interfaz ausente",
                                    f"troncal {puerto} debe existir en ambas fases"))
            continue
        fa = (f"modo={cfg_a.get('mode', '(sin modo)')} "
              f"nativa={cfg_a.get('native_vlan', '(sin nativa)')} "
              f"permitidas={fmt_allowed(cfg_a.get('allowed_vlans', [])) if 'allowed_vlans' in cfg_a else '(sin lista)'} "
              f"desc={cfg_a.get('description', '(sin descripcion)')}")
        fd = (f"modo={cfg_d.get('mode', '(sin modo)')} "
              f"nativa={cfg_d.get('native_vlan', '(sin nativa)')} "
              f"permitidas={fmt_allowed(cfg_d.get('allowed_vlans', [])) if 'allowed_vlans' in cfg_d else '(sin lista)'} "
              f"desc={cfg_d.get('description', '(sin descripcion)')}")
        if fa != fd:
            filas.append(nueva_fila("CRITICO TRONCAL", f"{host} {puerto}",
                                    "modo/nativa/permitidas/descripcion", fa, fd,
                                    f"{DIFERENCIA}: cambio critico",
                                    f"troncal: antes '{fa}' vs despues '{fd}'"))
        else:
            filas.append(nueva_fila("CRITICO TRONCAL", f"{host} {puerto}",
                                    "modo/nativa/permitidas/descripcion", fa, fd, OK_CRITICO))
    # Brief de troncales por equipo: debe ser identico.
    if trunk_a is None or trunk_d is None:
        filas.append(nueva_fila("CRITICO TRONCAL", f"{host} (brief)",
                                "show interfaces trunk identico",
                                "(faltante en ANTES)" if trunk_a is None else "(presente)",
                                "(faltante en DESPUES)" if trunk_d is None else "(presente)",
                                f"{DIFERENCIA}: evidencia faltante",
                                "falta show-interfaces-trunk en ANTES o DESPUES"))
    else:
        pa, pd = parsear_trunk_brief(trunk_a), parsear_trunk_brief(trunk_d)
        if pa != pd:
            filas.append(nueva_fila("CRITICO TRONCAL", f"{host} (brief)",
                                    "show interfaces trunk identico",
                                    str(sorted(pa.items())), str(sorted(pd.items())),
                                    f"{DIFERENCIA}: cambio critico",
                                    f"trunk brief: antes {pa} vs despues {pd}"))
        else:
            filas.append(nueva_fila("CRITICO TRONCAL", f"{host} (brief)",
                                    "show interfaces trunk identico",
                                    f"{len(pa)} troncales", f"{len(pd)} troncales",
                                    OK_CRITICO))
    return filas


def evaluar_subinterfaces(router: str, subs: list, run_a: dict | None,
                          run_d: dict | None) -> list[dict]:
    """Compara encapsulacion e IP de cada subinterfaz del router."""
    filas = []
    for sub in subs:
        if not isinstance(sub, dict):
            continue
        nom = normalizar_puerto(str(sub.get("interfaz", sub.get("interface", "?"))))
        try:
            enc = int(str(sub.get("encapsulation", sub.get("encap", sub.get("id")))).split()[0])
        except (ValueError, TypeError, AttributeError):
            filas.append(nueva_fila("CRITICO ROUTER", f"{router} {nom}",
                                    "encapsulacion/IP", "(diseno invalido)",
                                    "(diseno invalido)",
                                    f"{DIFERENCIA}: diseno invalido",
                                    f"subinterfaz sin encapsulacion valida: {sub}"))
            continue
        nativa = bool(sub.get("native", sub.get("nativa", False)))
        esp_ip = normalizar_ip(str(sub.get("ip", "")))
        if run_a is None or run_d is None:
            filas.append(nueva_fila("CRITICO ROUTER", f"{router} {nom}",
                                    "encapsulacion/IP",
                                    "(evidencia faltante)", "(evidencia faltante)",
                                    f"{DIFERENCIA}: evidencia faltante",
                                    "falta running-config en ANTES o DESPUES"))
            continue
        cfg_a = (run_a.get("interfaces", {}) or {}).get(nom)
        cfg_d = (run_d.get("interfaces", {}) or {}).get(nom)
        if cfg_a is None or cfg_d is None:
            filas.append(nueva_fila("CRITICO ROUTER", f"{router} {nom}",
                                    "encapsulacion/IP",
                                    "(ausente en ANTES)" if cfg_a is None else "(presente)",
                                    "(ausente en DESPUES)" if cfg_d is None else "(presente)",
                                    f"{DIFERENCIA}: subinterfaz ausente",
                                    f"{nom} debe existir en ambas fases"))
            continue
        ea = str(cfg_a.get("encap_vlan", "(sin encap)")) + (" native" if cfg_a.get("encap_native") else "")
        ed = str(cfg_d.get("encap_vlan", "(sin encap)")) + (" native" if cfg_d.get("encap_native") else "")
        ipa = normalizar_ip(str(cfg_a.get("ip", "(sin ip)")))
        ipd = normalizar_ip(str(cfg_d.get("ip", "(sin ip)")))
        fa, fd = f"dot1Q {ea} ip={ipa}", f"dot1Q {ed} ip={ipd}"
        if fa != fd:
            filas.append(nueva_fila("CRITICO ROUTER", f"{router} {nom}",
                                    "encapsulacion/IP", fa, fd,
                                    f"{DIFERENCIA}: cambio critico",
                                    f"subinterfaz: antes '{fa}' vs despues '{fd}'"))
        else:
            filas.append(nueva_fila("CRITICO ROUTER", f"{router} {nom}",
                                    "encapsulacion/IP", fa, fd, OK_CRITICO))
    return filas


def evaluar_rutas(router: str, texto_a: str | None, texto_d: str | None) -> dict:
    """Compara rutas conectadas del router."""
    if texto_a is None or texto_d is None:
        return nueva_fila("CRITICO RUTAS", router, "rutas conectadas",
                          "(faltante en ANTES)" if texto_a is None else "(presente)",
                          "(faltante en DESPUES)" if texto_d is None else "(presente)",
                          f"{DIFERENCIA}: evidencia faltante",
                          "falta show-ip-route en ANTES o DESPUES")
    ra, rd = parsear_rutas_conectadas(texto_a), parsear_rutas_conectadas(texto_d)
    if ra != rd:
        return nueva_fila("CRITICO RUTAS", router, "rutas conectadas",
                          ", ".join(ra) or "(sin conectadas)",
                          ", ".join(rd) or "(sin conectadas)",
                          f"{DIFERENCIA}: cambio critico",
                          f"rutas: antes {ra} vs despues {rd}")
    return nueva_fila("CRITICO RUTAS", router, "rutas conectadas",
                      ", ".join(ra) or "(sin conectadas)",
                      ", ".join(rd) or "(sin conectadas)", OK_CRITICO)


def evaluar_accesos_restantes(host: str, accesos: list, cambio: dict,
                              run_a: dict | None, run_d: dict | None) -> list[dict]:
    """Compara VLAN y descripcion de los demas puertos de acceso."""
    filas = []
    cambio_if = normalizar_puerto(str(cambio["interfaz"]))
    cambio_host = str(cambio["equipo"])
    for pa in accesos:
        if not isinstance(pa, dict) or pa.get("puerto") is None:
            continue
        puerto = normalizar_puerto(str(pa["puerto"]))
        if host == cambio_host and puerto == cambio_if:
            continue  # el puerto del cambio no es "restante"
        if run_a is None or run_d is None:
            filas.append(nueva_fila("CRITICO ACCESO", f"{host} {puerto}",
                                    "vlan/descripcion",
                                    "(evidencia faltante)", "(evidencia faltante)",
                                    f"{DIFERENCIA}: evidencia faltante",
                                    "falta running-config en ANTES o DESPUES"))
            continue
        cfg_a = (run_a.get("interfaces", {}) or {}).get(puerto)
        cfg_d = (run_d.get("interfaces", {}) or {}).get(puerto)
        if cfg_a is None or cfg_d is None:
            filas.append(nueva_fila("CRITICO ACCESO", f"{host} {puerto}",
                                    "vlan/descripcion",
                                    "(ausente en ANTES)" if cfg_a is None else "(presente)",
                                    "(ausente en DESPUES)" if cfg_d is None else "(presente)",
                                    f"{DIFERENCIA}: interfaz ausente",
                                    f"acceso {puerto} debe existir en ambas fases"))
            continue
        fa = (f"vlan={cfg_a.get('access_vlan', '1 (por defecto)')} "
              f"desc={cfg_a.get('description', '(sin descripcion)')} "
              f"modo={cfg_a.get('mode', '(sin modo)')}")
        fd = (f"vlan={cfg_d.get('access_vlan', '1 (por defecto)')} "
              f"desc={cfg_d.get('description', '(sin descripcion)')} "
              f"modo={cfg_d.get('mode', '(sin modo)')}")
        if fa != fd:
            filas.append(nueva_fila("CRITICO ACCESO", f"{host} {puerto}",
                                    "vlan/descripcion", fa, fd,
                                    f"{DIFERENCIA}: cambio critico",
                                    f"acceso: antes '{fa}' vs despues '{fd}'"))
        else:
            filas.append(nueva_fila("CRITICO ACCESO", f"{host} {puerto}",
                                    "vlan/descripcion", fa, fd, OK_CRITICO))
    return filas


# ---------- Programa principal ----------

def main(argv: list[str] | None = None) -> int:
    """Compara ANTES contra DESPUES y guarda la evidencia."""
    parser = argparse.ArgumentParser(
        description="Valida un cambio controlado (solo lectura, sin SSH).")
    parser.add_argument("--antes", default=str(DIR_ANTES), help="Carpeta de evidencia ANTES.")
    parser.add_argument("--despues", default=str(DIR_DESPUES), help="Carpeta de evidencia DESPUES.")
    parser.add_argument("--cambio", default=str(RUTA_CAMBIO), help="YAML del cambio aprobado.")
    parser.add_argument("--diseno", default=str(RUTA_DISENO), help="YAML del diseno esperado.")
    parser.add_argument("--salida", default=str(RUTA_SALIDA), help="Archivo de salida.")
    args = parser.parse_args(argv)

    ruta_cambio = Path(args.cambio)
    ruta_diseno = Path(args.diseno)
    dir_antes = Path(args.antes)
    dir_despues = Path(args.despues)
    ruta_salida = Path(args.salida)

    salida: list[str] = []
    salida.append("VALIDACION DE CAMBIO CONTROLADO (Python, solo lectura)")
    salida.append("=" * 100)

    # --- Carga de entradas (errores de formato => salida 2) ---
    try:
        datos_cambio = cargar_yaml(ruta_cambio)
    except FileNotFoundError:
        print(f"ERROR: no existe el archivo de cambio: {ruta_cambio}")
        return 2
    except yaml.YAMLError as e:
        print(f"ERROR: YAML invalido en {ruta_cambio}: {e}")
        return 2
    try:
        cambio, criticos = validar_cambio(datos_cambio)
    except ValueError as e:
        print(f"ERROR: cambio aprobado invalido en {ruta_cambio}: {e}")
        return 2

    try:
        diseno = cargar_yaml(ruta_diseno)
    except FileNotFoundError:
        print(f"ERROR: no existe el diseno esperado: {ruta_diseno}")
        return 2
    except yaml.YAMLError as e:
        print(f"ERROR: YAML invalido en {ruta_diseno}: {e}")
        return 2
    try:
        switches, routers = validar_diseno(diseno)
    except ValueError as e:
        print(f"ERROR: diseno invalido en {ruta_diseno}: {e}")
        return 2

    # --- Lectura de evidencia ---
    equipo = str(cambio["equipo"])
    interfaz_cambio = normalizar_puerto(str(cambio["interfaz"]))
    hosts_sw = list(switches.keys())
    hosts_rt = list(routers.keys())

    # Archivos esperados por fase (para el reporte de datos leidos).
    esperados_antes = len(hosts_sw) * 4 + len(hosts_rt) * 3
    encontrados_antes = sum(1 for h in hosts_sw for a in
                            (ARCH_RUNNING, ARCH_VLAN, ARCH_TRUNK, ARCH_IPBRIEF)
                            if ruta_evidencia(dir_antes, h, a).is_file())
    encontrados_antes += sum(1 for h in hosts_rt for a in
                             (ARCH_RUNNING, ARCH_IPBRIEF, ARCH_ROUTE)
                             if ruta_evidencia(dir_antes, h, a).is_file())
    encontrados_despues = sum(1 for h in hosts_sw for a in
                              (ARCH_RUNNING, ARCH_VLAN, ARCH_TRUNK, ARCH_IPBRIEF)
                              if ruta_evidencia(dir_despues, h, a).is_file())
    encontrados_despues += sum(1 for h in hosts_rt for a in
                               (ARCH_RUNNING, ARCH_IPBRIEF, ARCH_ROUTE)
                               if ruta_evidencia(dir_despues, h, a).is_file())

    salida.append("[1] QUE DATOS LEYO")
    salida.append(f"  - Cambio: {ruta_cambio} (id {cambio['id']}, {equipo} {interfaz_cambio})")
    salida.append(f"    anterior: '{cambio['descripcion_anterior']}' -> nueva: '{cambio['descripcion_nueva']}'")
    salida.append(f"  - Diseno: {ruta_diseno} ({len(hosts_sw)} switches, {len(hosts_rt)} routers)")
    salida.append(f"  - Evidencia ANTES: {dir_antes} ({encontrados_antes} de {esperados_antes} archivos)")
    salida.append(f"  - Evidencia DESPUES: {dir_despues} ({encontrados_despues} de {esperados_antes} archivos)")
    salida.append("")

    # Carga running/brief por equipo y fase.
    datos: dict = {"switches": {}, "routers": {}}
    for host in hosts_sw:
        for fase, directorio in (("antes", dir_antes), ("despues", dir_despues)):
            textos = {}
            for arch in (ARCH_RUNNING, ARCH_VLAN, ARCH_TRUNK, ARCH_IPBRIEF):
                textos[arch] = leer_texto(ruta_evidencia(directorio, host, arch))
            run = parsear_running_config(textos[ARCH_RUNNING]) if textos[ARCH_RUNNING] else None
            brief = parsear_vlan_brief(textos[ARCH_VLAN]) if textos[ARCH_VLAN] else None
            datos["switches"].setdefault(host, {})[fase] = {
                "textos": textos, "run": run, "brief": brief}
    for host in hosts_rt:
        for fase, directorio in (("antes", dir_antes), ("despues", dir_despues)):
            textos = {}
            for arch in (ARCH_RUNNING, ARCH_IPBRIEF, ARCH_ROUTE):
                textos[arch] = leer_texto(ruta_evidencia(directorio, host, arch))
            run = parsear_running_config(textos[ARCH_RUNNING]) if textos[ARCH_RUNNING] else None
            datos["routers"].setdefault(host, {})[fase] = {"textos": textos, "run": run}

    salida.append("[2] QUE CONDICION EVALUO")
    salida.append("  1. CAMBIO SOLICITADO: descripcion era la anterior en ANTES y es la nueva en DESPUES.")
    salida.append("  2. Elementos criticos sin cambios: VLANs, VLAN 99, gestion, troncales,")
    salida.append("     subinterfaces y rutas del router, demas puertos de acceso.")
    salida.append("  3. OTRA DIFERENCIA: ningun otro atributo de la interfaz del cambio.")
    salida.append("  Regla: archivo de evidencia faltante = diferencia detectada.")
    salida.append("")

    # --- Evaluacion ---
    filas: list[dict] = []

    # Condicion 1: cambio solicitado (solo en el equipo del cambio).
    run_a = datos["switches"].get(equipo, {}).get("antes", {}).get("run")
    run_d = datos["switches"].get(equipo, {}).get("despues", {}).get("run")
    falta_a = leer_texto(ruta_evidencia(dir_antes, equipo, ARCH_RUNNING)) is None
    falta_d = leer_texto(ruta_evidencia(dir_despues, equipo, ARCH_RUNNING)) is None
    if equipo not in datos["switches"]:
        filas.append(nueva_fila("CAMBIO SOLICITADO", interfaz_cambio, "descripcion",
                                "(equipo no en diseno)", "(equipo no en diseno)",
                                f"{DIFERENCIA}: diseno invalido",
                                f"el equipo {equipo} no esta en variables/sucursal_01.yml"))
        cambio_ok = False
    else:
        f1 = evaluar_cambio_solicitado(cambio, run_a, run_d, falta_a, falta_d)
        filas.append(f1)
        cambio_ok = (f1["estado"] == OK_CAMBIO)

    # Condicion 2: criticos.
    crit_inicio = len(filas)
    for host in hosts_sw:
        ent_a = datos["switches"][host]["antes"]
        ent_d = datos["switches"][host]["despues"]
        # VLANs (solo si la bandera vlans existe; siempre hay lista validada).
        filas.extend(evaluar_vlans(criticos["vlans"], ent_a["run"] or {"vlans": {}},
                                   ent_d["run"] or {"vlans": {}}, ent_a["brief"],
                                   ent_d["brief"], host))
        # Gestion.
        if criticos.get("gestion", True):
            filas.append(evaluar_gestion(host, ent_a["textos"][ARCH_RUNNING],
                                         ent_d["textos"][ARCH_RUNNING],
                                         ent_a["run"] or {"interfaces": {}},
                                         ent_d["run"] or {"interfaces": {}},
                                         ent_a["textos"][ARCH_IPBRIEF],
                                         ent_d["textos"][ARCH_IPBRIEF]))
        # Troncales.
        if criticos.get("troncales", True):
            dis_trunks = (switches.get(host) or {}).get("puertos_trunk", []) or []
            filas.extend(evaluar_troncales(host, dis_trunks,
                                           ent_a["run"], ent_d["run"],
                                           ent_a["textos"][ARCH_TRUNK],
                                           ent_d["textos"][ARCH_TRUNK]))
        # Accesos restantes.
        if criticos.get("puertos_acceso_restantes", True):
            dis_acc = (switches.get(host) or {}).get("puertos_acceso", []) or []
            filas.extend(evaluar_accesos_restantes(host, dis_acc, cambio,
                                                   ent_a["run"], ent_d["run"]))
    for host in hosts_rt:
        ent_a = datos["routers"][host]["antes"]
        ent_d = datos["routers"][host]["despues"]
        if criticos.get("router_subinterfaces", True):
            subs = (routers.get(host) or {}).get("subinterfaces", []) or []
            filas.extend(evaluar_subinterfaces(host, subs, ent_a["run"], ent_d["run"]))
            # IP brief del router debe ser identico.
            iba, ibd = ent_a["textos"][ARCH_IPBRIEF], ent_d["textos"][ARCH_IPBRIEF]
            if iba is None or ibd is None:
                filas.append(nueva_fila("CRITICO ROUTER", f"{host} (brief)",
                                        "show ip interface brief identico",
                                        "(faltante en ANTES)" if iba is None else "(presente)",
                                        "(faltante en DESPUES)" if ibd is None else "(presente)",
                                        f"{DIFERENCIA}: evidencia faltante",
                                        "falta show-ip-interface-brief en ANTES o DESPUES"))
            elif parsear_ip_brief(iba) != parsear_ip_brief(ibd):
                filas.append(nueva_fila("CRITICO ROUTER", f"{host} (brief)",
                                        "show ip interface brief identico",
                                        str(parsear_ip_brief(iba)), str(parsear_ip_brief(ibd)),
                                        f"{DIFERENCIA}: cambio critico",
                                        "el brief del router cambio entre fases"))
            else:
                filas.append(nueva_fila("CRITICO ROUTER", f"{host} (brief)",
                                        "show ip interface brief identico",
                                        f"{len(parsear_ip_brief(iba))} interfaces",
                                        f"{len(parsear_ip_brief(ibd))} interfaces",
                                        OK_CRITICO))
        if criticos.get("rutas", True):
            filas.append(evaluar_rutas(host, ent_a["textos"][ARCH_ROUTE],
                                       ent_d["textos"][ARCH_ROUTE]))

    # Condicion 3: otra diferencia en la interfaz del cambio.
    n_antes_f3 = len(filas)
    f3 = evaluar_otra_diferencia(cambio, run_a, run_d)
    otra_ok = True
    if f3 is not None:
        filas.append(f3)
        otra_ok = False

    crit_filas = filas[crit_inicio:n_antes_f3]
    total_crit = len(crit_filas)
    ok_crit = sum(1 for r in crit_filas if r["estado"] == OK_CRITICO)
    diferencias = [r for r in filas if r["estado"] != OK_CAMBIO and r["estado"] != OK_CRITICO]

    salida.append("[3] CUAL FUE EL RESULTADO")
    for r in filas:
        marca = "OK " if r["estado"] in (OK_CAMBIO, OK_CRITICO) else "DIF"
        salida.append(f"  [{marca}] {r['categoria']} | {r['elemento']} | {r['chequeo']}")
        salida.append(f"       antes: {r['antes']}")
        salida.append(f"       despues: {r['despues']}")
        salida.append(f"       estado: {r['estado']}" + (f" | {r['detalle']}" if r["detalle"] else ""))
    salida.append("")

    salida.append("RESUMEN")
    salida.append(f"  Cambio aplicado: {'SI' if cambio_ok else 'NO'}")
    salida.append(f"  Criticos sin cambios: {ok_crit} de {total_crit}")
    if diferencias:
        salida.append(f"  Diferencias ({len(diferencias)}):")
        for r in diferencias:
            salida.append(f"    - {r['elemento']} [{r['chequeo']}]: {r['estado']} | {r['detalle']}")
    else:
        salida.append("  Diferencias (0): ninguna.")
    global_ok = cambio_ok and otra_ok and not diferencias
    salida.append("  CUMPLIMIENTO GLOBAL: " + ("CUMPLE" if global_ok else "NO CUMPLE"))
    texto_final = "\n".join(salida) + "\n"
    print(texto_final)

    ruta_salida.parent.mkdir(parents=True, exist_ok=True)
    ruta_salida.write_text(texto_final, encoding="utf-8")
    print(f"Evidencia guardada en: {ruta_salida}")
    return 0 if global_ok else 1


if __name__ == "__main__":
    sys.exit(main())
