#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Auditoria de sucursal 01 (solo lectura de archivos, sin SSH).

Lee el diseno esperado desde variables/sucursal_01.yml y la evidencia
desde respaldos/ (running-config y show vlan brief). Compara y muestra
una tabla con CUMPLE / NO CUMPLE, guardando la misma salida en
evidencias/auditoria_python.txt.
"""
from __future__ import annotations

import argparse
import ipaddress
import os
import re
import sys
from pathlib import Path

import yaml

# Rutas base (relativas al repo, no al cwd).
# Se pueden sobreescribir con argumentos --diseno/--respaldos/--salida
# o con la variable de entorno AUDITORIA_RESPALDOS (solo para pruebas
# en carpeta temporal, sin tocar respaldos/ reales).
BASE = Path(__file__).resolve().parent.parent
RUTA_DISENO = BASE / "variables" / "sucursal_01.yml"
DIR_RESPALDOS = BASE / "respaldos"
RUTA_SALIDA = BASE / "evidencias" / "auditoria_python.txt"


# ---------- Carga del diseño ----------

def cargar_diseno(ruta: Path) -> dict:
    """Carga el YAML con el diseño esperado."""
    with open(ruta, encoding="utf-8") as f:
        return yaml.safe_load(f)


# ---------- Utilidades de normalización ----------

def normalizar_puerto(nombre: str) -> str:
    """Normaliza Gi/Gig/GigabitEthernet a forma canónica."""
    n = nombre.strip()
    # Separa subinterfaz (.10) para no romperla.
    sufijo = ""
    if "." in n:
        base, suf = n.split(".", 1)
        sufijo = "." + suf.strip()
        n = base.strip()
    bajo = n.lower().replace(" ", "")
    # Extrae la parte numérica (0/1, 1/0, etc.).
    m = re.search(r"(\d.*/.*|\d+)$", bajo)
    resto = m.group(1) if m else bajo
    if bajo.startswith("gigabitethernet") or bajo.startswith("gig") or bajo.startswith("gi"):
        canon = f"GigabitEthernet{resto}"
    elif bajo.startswith("fastethernet") or bajo.startswith("fa"):
        canon = f"FastEthernet{resto}"
    elif bajo.startswith("ethernet") or bajo.startswith("et"):
        canon = f"Ethernet{resto}"
    else:
        canon = n.strip()
    return canon + sufijo


def normalizar_allowed(valor) -> list[int]:
    """Convierte '10,20,99' o [10,20,99] en lista ordenada de int."""
    if valor is None:
        return []
    if isinstance(valor, (list, tuple)):
        partes: list[str] = []
        for v in valor:
            partes.extend(str(v).split(","))
    else:
        partes = str(valor).replace(" ", "").split(",")
    nums: list[int] = []
    for p in partes:
        p = p.strip()
        if not p or p.lower() in ("none", "no", "-"):
            continue
        # Soporta rangos tipo 10-12 (por si aparece).
        if "-" in p and "," not in p:
            try:
                a, b = p.split("-", 1)
                nums.extend(range(int(a), int(b) + 1))
                continue
            except ValueError:
                pass
        try:
            nums.append(int(p))
        except ValueError:
            continue
    return sorted(set(nums))


def fmt_allowed(lista: list[int]) -> str:
    """Formatea lista de VLANs como '10,20,99'."""
    return ",".join(str(x) for x in sorted(lista))


def normalizar_ip(valor: str) -> str:
    """Normaliza una IP a forma 'addr/mask' para comparar.

    Acepta '192.168.10.1 255.255.255.0' y '192.168.10.1/24' y devuelve
    siempre '192.168.10.1/24'. Si no se puede parsear, devuelve el
    texto recortado original.
    """
    s = str(valor).strip()
    try:
        if "/" in s:
            # Ya es CIDR: valida y normaliza.
            iface = ipaddress.ip_interface(s)
            return f"{iface.ip}/{iface.network.prefixlen}"
        partes = s.split()
        if len(partes) == 2:
            # Forma 'IP MASCARA'.
            iface = ipaddress.ip_interface(f"{partes[0]}/{partes[1]}")
            return f"{iface.ip}/{iface.network.prefixlen}"
    except ValueError:
        pass
    return s


def _leer_vlan_esperada(v: dict) -> dict | None:
    """Tolera claves id/vlan_id, nombre/name y estado/state."""
    if not isinstance(v, dict):
        return None
    vid = v.get("id", v.get("vlan_id", v.get("vlan")))
    nombre = v.get("nombre", v.get("name"))
    estado = v.get("estado", v.get("state", "active"))
    if vid is None or nombre is None:
        return None
    try:
        vid = int(vid)
    except (ValueError, TypeError):
        return None
    return {"id": vid, "nombre": str(nombre), "estado": str(estado)}


def obtener_vlans_esperadas(diseno: dict) -> list[dict]:
    """Lee vlans_esperadas tolerando vlans/vlans_sucursal."""
    crudo = diseno.get("vlans_esperadas", diseno.get("vlans", diseno.get("vlans_sucursal", [])))
    if not isinstance(crudo, list):
        return []
    buenas: list[dict] = []
    for v in crudo:
        n = _leer_vlan_esperada(v)
        if n is not None:
            buenas.append(n)
    return buenas


def _leer_puerto_acceso(pa: dict) -> dict | None:
    """Tolera puerto/port/name, vlan/vlan_id y descripcion/description."""
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


def _leer_puerto_trunk(pt: dict) -> dict | None:
    """Tolera puerto/port/name, native_vlan/nativa y allowed_vlans/permitidas."""
    if not isinstance(pt, dict):
        return None
    puerto = pt.get("puerto", pt.get("port", pt.get("name")))
    nat = pt.get("native_vlan", pt.get("nativa", pt.get("native")))
    allow = pt.get("allowed_vlans", pt.get("permitidas", pt.get("allowed")))
    desc = pt.get("descripcion", pt.get("description", pt.get("desc", "")))
    if puerto is None:
        return None
    return {"puerto": str(puerto), "native_vlan": nat, "allowed_vlans": allow, "descripcion": str(desc)}


def _leer_subinterfaz(sub: dict) -> dict | None:
    """Tolera interfaz/interface/name, encapsulation/encap/vlan y native/nativa."""
    if not isinstance(sub, dict):
        return None
    iface = sub.get("interfaz", sub.get("interface", sub.get("name")))
    enc = sub.get("encapsulation", sub.get("encap", sub.get("vlan", sub.get("id"))))
    if iface is None or enc is None:
        return None
    try:
        enc = int(str(enc).split()[0])
    except (ValueError, TypeError):
        return None
    return {
        "interfaz": str(iface),
        "encapsulation": enc,
        "native": bool(sub.get("native", sub.get("nativa", False))),
        "ip": str(sub.get("ip", "")),
    }


# ---------- Parseo de evidencia ----------

def parsear_running_config(texto: str) -> dict:
    """Parsea running-config en vlans e interfaces."""
    vlans: dict[int, str] = {}
    interfaces: dict[str, dict] = {}
    actual: str | None = None
    # VLANs tipo: 'vlan 10' seguido de 'name Datos'.
    lineas = texto.splitlines()
    vlan_actual: int | None = None
    for linea in lineas:
        s = linea.strip()
        m_vlan = re.match(r"^vlan\s+(\d+)\s*$", s, re.IGNORECASE)
        if m_vlan:
            vlan_actual = int(m_vlan.group(1))
            continue
        if vlan_actual is not None:
            m_name = re.match(r"^name\s+(.+?)\s*$", s, re.IGNORECASE)
            if m_name:
                vlans[vlan_actual] = m_name.group(1).strip()
                vlan_actual = None
                continue
            # Si llega otra sección, se cierra la vlan sin nombre.
            if s.startswith("!") or s.startswith("interface ") or s.startswith("line "):
                vlan_actual = None
        m_if = re.match(r"^interface\s+(\S+)\s*$", s, re.IGNORECASE)
        if m_if:
            actual = normalizar_puerto(m_if.group(1))
            interfaces[actual] = {}
            continue
        if actual is None:
            continue
        if s.startswith("!") or s.lower() == "end":
            actual = None
            continue
        m_desc = re.match(r"^description\s+(.+?)\s*$", s, re.IGNORECASE)
        if m_desc:
            interfaces[actual]["description"] = m_desc.group(1).strip()
            continue
        m_mode = re.match(r"^switchport\s+mode\s+(\S+)", s, re.IGNORECASE)
        if m_mode:
            interfaces[actual]["mode"] = m_mode.group(1).strip().lower()
            continue
        m_acc = re.match(r"^switchport\s+access\s+vlan\s+(\d+)", s, re.IGNORECASE)
        if m_acc:
            interfaces[actual]["access_vlan"] = int(m_acc.group(1))
            continue
        m_nat = re.match(r"^switchport\s+trunk\s+native\s+vlan\s+(\d+)", s, re.IGNORECASE)
        if m_nat:
            interfaces[actual]["native_vlan"] = int(m_nat.group(1))
            continue
        m_allow = re.match(r"^switchport\s+trunk\s+allowed\s+vlan\s+(.+)", s, re.IGNORECASE)
        if m_allow:
            # Soporta 'allowed vlan add 20' acumulando valores.
            resto = m_allow.group(1).strip()
            es_add = resto.lower().startswith("add ")
            if es_add:
                resto = resto[4:].strip()
            nuevos = normalizar_allowed(resto)
            previos = interfaces[actual].get("allowed_vlans", [])
            if es_add:
                combinados = sorted(set(previos) | set(nuevos))
            else:
                combinados = nuevos
            interfaces[actual]["allowed_vlans"] = combinados
            interfaces[actual]["allowed_vlan_raw"] = fmt_allowed(combinados)
            continue
        # Router: encapsulation e IP.
        m_enc = re.match(r"^encapsulation\s+dot1Q\s+(\d+)(.*)", s, re.IGNORECASE)
        if m_enc:
            interfaces[actual]["encap_vlan"] = int(m_enc.group(1))
            interfaces[actual]["encap_native"] = "native" in m_enc.group(2).lower()
            continue
        m_ip = re.match(r"^ip\s+address\s+(\S+)\s+(\S+)", s, re.IGNORECASE)
        if m_ip:
            interfaces[actual]["ip"] = f"{m_ip.group(1)} {m_ip.group(2)}"
            continue
    return {"vlans": vlans, "interfaces": interfaces}


def parsear_vlan_brief(texto: str) -> dict[int, dict]:
    """Parsea 'show vlan brief' en {id: {nombre, estado, puertos}}."""
    datos: dict[int, dict] = {}
    for linea in texto.splitlines():
        s = linea.strip()
        if not s or s.startswith("!") or s.startswith("VLAN") or s.startswith("----"):
            continue
        # Ej: 10   Datos   active   Gi0/0, Gi0/1
        m = re.match(r"^(\d+)\s+(\S+)\s+(\S+)(?:\s+(.*))?\s*$", s)
        if not m:
            continue
        vid = int(m.group(1))
        nombre = m.group(2)
        estado = m.group(3).lower()
        puertos_raw = (m.group(4) or "").strip()
        puertos: list[str] = []
        if puertos_raw:
            for p in puertos_raw.split(","):
                p = p.strip()
                if p:
                    puertos.append(normalizar_puerto(p))
        datos[vid] = {"nombre": nombre, "estado": estado, "puertos": puertos}
    return datos


# ---------- Búsqueda de archivos ----------

def buscar_respaldo(nombres: list[str], base: Path | None = None) -> Path | None:
    """Devuelve el primer archivo existente de la lista de candidatos."""
    directorio = base if base is not None else DIR_RESPALDOS
    for n in nombres:
        p = directorio / n
        if p.is_file():
            return p
    return None


def candidatos_running(host: str, alias: str | None) -> list[str]:
    """Candidatos de running-config para un equipo (host y alias).

    Cubre los nombres reales de respaldos/ (ej. SW-SUC-01-CORE-...,
    R-SUC-01-..., SW1-..., Router_MB-...).
    """
    cands = [f"{host}-running-config.txt"]
    if alias and alias != host:
        cands.append(f"{alias}-running-config.txt")
    # Variantes con guion bajo por compatibilidad.
    cands += [c.replace("-", "_") for c in list(cands)]
    return cands


def candidatos_vlan(host: str, alias: str | None) -> list[str]:
    """Candidatos de show vlan brief para un equipo."""
    cands = [f"{host}-show-vlan-brief.txt", f"{host}-show-vlan.txt", f"{host}-vlan-brief.txt"]
    if alias and alias != host:
        cands += [f"{alias}-show-vlan-brief.txt", f"{alias}-show-vlan.txt"]
    cands += [c.replace("-", "_") for c in list(cands)]
    return cands


# ---------- Auditoría ----------

def agregar(filas: list[dict], elemento: str, chequeo: str, esperado: str, encontrado: str) -> None:
    """Agrega una fila con estado CUMPLE/NO CUMPLE."""
    cumple = str(esperado).strip().lower() == str(encontrado).strip().lower()
    # Para listas de VLANs compara como conjuntos normalizados.
    filas.append({
        "elemento": elemento,
        "chequeo": chequeo,
        "esperado": esperado,
        "encontrado": encontrado,
        "estado": "CUMPLE" if cumple else "NO CUMPLE",
    })


def auditar_vlans(filas: list[dict], esperadas: list[dict], brief: dict[int, dict] | None, running_vlans: dict) -> None:
    """Audita existencia, nombre y estado active de VLAN 10, 20 y 99.

    Usa running_vlans (del running-config) como respaldo cuando falta
    el 'show vlan brief': el nombre sale del running-config y el estado
    se marca como '(sin vlan brief)' para no dar falso CUMPLE.
    """
    running_vlans = running_vlans or {}
    for v in esperadas:
        vid = int(v["id"])
        esp_nombre = str(v["nombre"])
        esp_estado = str(v.get("estado", "active")).lower()
        if brief is None:
            # Respaldo: si la VLAN está definida en running-config, la
            # existencia y el nombre se verifican ahí; el estado no se
            # puede verificar sin el brief, así que es NO CUMPLE explícito.
            if vid in running_vlans:
                agregar(filas, f"VLAN {vid}", "existencia", "existe", "existe (solo running-config)")
                agregar(filas, f"VLAN {vid}", "nombre", esp_nombre, str(running_vlans[vid]))
                agregar(filas, f"VLAN {vid}", "estado", esp_estado, "(sin vlan brief)")
            else:
                agregar(filas, f"VLAN {vid}", "existencia", "existe", "sin archivo vlan brief")
                agregar(filas, f"VLAN {vid}", "nombre", esp_nombre, "(sin datos)")
                agregar(filas, f"VLAN {vid}", "estado", esp_estado, "(sin datos)")
            continue
        if vid not in brief:
            # Si el brief no la trae pero el running sí, se reporta la
            # discrepancia en vez de un simple 'no existe'.
            if vid in running_vlans:
                agregar(filas, f"VLAN {vid}", "existencia", "existe", "solo en running-config")
                agregar(filas, f"VLAN {vid}", "nombre", esp_nombre, str(running_vlans[vid]))
                agregar(filas, f"VLAN {vid}", "estado", esp_estado, "(no en brief)")
            else:
                agregar(filas, f"VLAN {vid}", "existencia", "existe", "no existe")
                agregar(filas, f"VLAN {vid}", "nombre", esp_nombre, "(no existe)")
                agregar(filas, f"VLAN {vid}", "estado", esp_estado, "(no existe)")
            continue
        agregar(filas, f"VLAN {vid}", "existencia", "existe", "existe")
        agregar(filas, f"VLAN {vid}", "nombre", esp_nombre, brief[vid]["nombre"])
        agregar(filas, f"VLAN {vid}", "estado", esp_estado, brief[vid]["estado"])


def auditar_acceso(filas: list[dict], puerto_esp: str, vlan_esp: int, desc_esp: str, interfaces: dict | None) -> None:
    """Audita un puerto de acceso: VLAN y descripción."""
    canon = normalizar_puerto(puerto_esp)
    if interfaces is None:
        agregar(filas, canon, "acceso vlan", str(vlan_esp), "(sin running-config)")
        agregar(filas, canon, "descripcion", desc_esp, "(sin running-config)")
        return
    cfg = interfaces.get(canon)
    if cfg is None:
        agregar(filas, canon, "acceso vlan", str(vlan_esp), "(interfaz no existe)")
        agregar(filas, canon, "descripcion", desc_esp, "(interfaz no existe)")
        return
    # Modo: debe ser access (si no se declara, se reporta como falta).
    modo = cfg.get("mode", "(sin modo)")
    # VLAN: si no hay línea, IOS asume 1.
    vlan_enc = str(cfg.get("access_vlan", "1 (por defecto)"))
    agregar(filas, canon, "modo access", "access", str(modo))
    agregar(filas, canon, "acceso vlan", str(vlan_esp), vlan_enc)
    agregar(filas, canon, "descripcion", desc_esp, cfg.get("description", "(sin descripcion)"))


def auditar_trunk(filas: list[dict], puerto_esp: str, nat_esp: int, allow_esp: list[int], desc_esp: str, interfaces: dict | None) -> None:
    """Audita un troncal: modo, nativa, permitidas y descripción."""
    canon = normalizar_puerto(puerto_esp)
    esp_allow = fmt_allowed(allow_esp)
    if interfaces is None:
        agregar(filas, canon, "modo trunk", "trunk", "(sin running-config)")
        agregar(filas, canon, "nativa", str(nat_esp), "(sin running-config)")
        agregar(filas, canon, "permitidas", esp_allow, "(sin running-config)")
        agregar(filas, canon, "descripcion", desc_esp, "(sin running-config)")
        return
    cfg = interfaces.get(canon)
    if cfg is None:
        agregar(filas, canon, "modo trunk", "trunk", "(interfaz no existe)")
        agregar(filas, canon, "nativa", str(nat_esp), "(interfaz no existe)")
        agregar(filas, canon, "permitidas", esp_allow, "(interfaz no existe)")
        agregar(filas, canon, "descripcion", desc_esp, "(interfaz no existe)")
        return
    agregar(filas, canon, "modo trunk", "trunk", str(cfg.get("mode", "(sin modo)")))
    agregar(filas, canon, "nativa", str(nat_esp), str(cfg.get("native_vlan", "(sin nativa)")))
    enc_allow = fmt_allowed(cfg.get("allowed_vlans", [])) if "allowed_vlans" in cfg else str(cfg.get("allowed_vlan_raw", "(sin lista)"))
    # Si no hay clave, muestra marcador.
    if "allowed_vlans" not in cfg and "allowed_vlan_raw" not in cfg:
        enc_allow = "(sin lista)"
    agregar(filas, canon, "permitidas", esp_allow, enc_allow)
    agregar(filas, canon, "descripcion", desc_esp, cfg.get("description", "(sin descripcion)"))


def auditar_router(filas: list[dict], router_esp: dict, interfaces: dict | None) -> None:
    """Audita subinterfaces del router (encapsulación e IP).

    La IP se compara en forma CIDR tolerante: '192.168.10.1
    255.255.255.0' y '192.168.10.1/24' se consideran iguales.
    """
    subs = router_esp.get("subinterfaces", [])
    # Tolera claves alternativas usando el lector flexible.
    subs_norm: list[dict] = []
    for s in subs:
        n = _leer_subinterfaz(s)
        if n is not None:
            subs_norm.append(n)
    for sub in subs_norm:
        nom = normalizar_puerto(sub["interfaz"])
        esp_enc = str(sub["encapsulation"]) + (" native" if sub.get("native") else "")
        esp_ip = normalizar_ip(str(sub.get("ip", "")))
        if interfaces is None:
            agregar(filas, nom, "encapsulacion", f"dot1Q {esp_enc}", "(sin running-config)")
            agregar(filas, nom, "ip", esp_ip, "(sin running-config)")
            continue
        cfg = interfaces.get(nom)
        if cfg is None:
            agregar(filas, nom, "encapsulacion", f"dot1Q {esp_enc}", "(subinterfaz no existe)")
            agregar(filas, nom, "ip", esp_ip, "(subinterfaz no existe)")
            continue
        enc_enc = str(cfg.get("encap_vlan", "(sin encap)"))
        if cfg.get("encap_native"):
            enc_enc += " native"
        agregar(filas, nom, "encapsulacion", f"dot1Q {esp_enc}", f"dot1Q {enc_enc}")
        agregar(filas, nom, "ip", esp_ip, normalizar_ip(cfg.get("ip", "(sin ip)")))


# ---------- Formato de salida ----------

def formatear_tabla(titulo: str, filas: list[dict]) -> str:
    """Genera una tabla legible con columnas fijas (sin recortar descripciones)."""
    lineas = [f"{titulo}", "-" * 100]
    if not filas:
        lineas.append("(sin elementos por auditar)")
        return "\n".join(lineas)
    # Anchos de columna: sin recorte agresivo para que las descripciones
    # normales (ej. 'Hacia R-SUC-01 Gi0/0') se vean completas.
    w_elem = max([len("ELEMENTO")] + [len(r["elemento"]) for r in filas])
    w_cheq = max([len("CHEQUEO")] + [len(r["chequeo"]) for r in filas])
    w_esp = max([len("ESPERADO")] + [len(str(r["esperado"])) for r in filas])
    w_enc = max([len("ENCONTRADO")] + [len(str(r["encontrado"])) for r in filas])
    w_est = len("NO CUMPLE")
    w_elem = min(w_elem, 32)
    w_cheq = min(w_cheq, 20)
    w_esp = min(w_esp, 42)
    w_enc = min(w_enc, 42)
    cap = f"| {'ELEMENTO'.ljust(w_elem)} | {'CHEQUEO'.ljust(w_cheq)} | {'ESPERADO'.ljust(w_esp)} | {'ENCONTRADO'.ljust(w_enc)} | {'ESTADO'.ljust(w_est)} |"
    sep = "-" * len(cap)
    lineas += [sep, cap, sep]
    for r in filas:
        el = str(r["elemento"]).ljust(w_elem)[:w_elem] if len(str(r["elemento"])) <= w_elem else str(r["elemento"])
        ch = str(r["chequeo"]).ljust(w_cheq)[:w_cheq] if len(str(r["chequeo"])) <= w_cheq else str(r["chequeo"])
        es = str(r["esperado"]).ljust(w_esp)[:w_esp] if len(str(r["esperado"])) <= w_esp else str(r["esperado"])
        en = str(r["encontrado"]).ljust(w_enc)[:w_enc] if len(str(r["encontrado"])) <= w_enc else str(r["encontrado"])
        st = r["estado"].ljust(w_est)
        lineas.append(f"| {el} | {ch} | {es} | {en} | {st} |")
    lineas.append(sep)
    # Detalle de incumplimientos: qué condición no coincide.
    malas = [r for r in filas if r["estado"] == "NO CUMPLE"]
    if malas:
        lineas.append(f"Incumplimientos ({len(malas)}):")
        for r in malas:
            lineas.append(f"  - {r['elemento']} [{r['chequeo']}]: esperado '{r['esperado']}' vs encontrado '{r['encontrado']}'")
    else:
        lineas.append("Todo CUMPLE en este bloque.")
    return "\n".join(lineas)


# ---------- Programa principal ----------

def resolver_rutas(args) -> tuple[Path, Path, Path]:
    """Resuelve rutas desde argumentos, entorno o valores por defecto."""
    dis = Path(args.diseno) if args.diseno else Path(os.environ.get("AUDITORIA_DISENO", str(RUTA_DISENO)))
    # Permite apuntar a carpeta temporal en pruebas sin tocar respaldos/.
    resp = args.respaldos or os.environ.get("AUDITORIA_RESPALDOS")
    res = Path(resp) if resp else DIR_RESPALDOS
    sal = Path(args.salida) if args.salida else Path(os.environ.get("AUDITORIA_SALIDA", str(RUTA_SALIDA)))
    return dis, res, sal


def main(argv: list[str] | None = None) -> int:
    """Ejecuta la auditoría completa y guarda la evidencia."""
    global DIR_RESPALDOS
    parser = argparse.ArgumentParser(description="Audita sucursal 01 (solo lectura, sin SSH).")
    parser.add_argument("--diseno", default=None, help="Ruta al YAML esperado (por defecto variables/sucursal_01.yml).")
    parser.add_argument("--respaldos", default=None, help="Carpeta de evidencia (por defecto respaldos/; usar temporal en pruebas).")
    parser.add_argument("--salida", default=None, help="Archivo de salida (por defecto evidencias/auditoria_python.txt).")
    args = parser.parse_args(argv)
    ruta_diseno, dir_respaldos, ruta_salida = resolver_rutas(args)
    DIR_RESPALDOS = dir_respaldos

    salida: list[str] = []
    salida.append("AUDITORIA DE SUCURSAL 01 (Python, solo lectura)")
    salida.append("=" * 100)
    salida.append(f"Diseno: {ruta_diseno}")
    salida.append(f"Evidencia: {dir_respaldos}")
    salida.append("")

    try:
        diseno = cargar_diseno(ruta_diseno)
    except FileNotFoundError:
        msg = f"ERROR: no existe el diseno esperado: {ruta_diseno}"
        print(msg)
        return 2
    except yaml.YAMLError as e:
        print(f"ERROR: YAML invalido en {ruta_diseno}: {e}")
        return 2

    if not isinstance(diseno, dict):
        print(f"ERROR: el diseno {ruta_diseno} no es un YAML valido (vacio o tipo incorrecto).")
        return 2

    vlans_esp = obtener_vlans_esperadas(diseno)
    trunk_def = diseno.get("trunk", {})
    if not isinstance(trunk_def, dict):
        trunk_def = {}
    nat_def = int(trunk_def.get("native_vlan", trunk_def.get("nativa", 99)))
    allow_def = normalizar_allowed(trunk_def.get("allowed_vlans", trunk_def.get("permitidas", [10, 20, 99])))

    # Validación anti falso CUMPLE: sin VLANs ni switches no hay nada que auditar.
    switches = diseno.get("switches", {})
    if not isinstance(switches, dict):
        switches = {}
    routers = diseno.get("routers", {})
    if not isinstance(routers, dict):
        routers = {}
    if not vlans_esp or not switches:
        print("ERROR: diseno incompleto: faltan 'vlans_esperadas' o 'switches'. No se puede auditar.")
        print(f"  vlans_esperadas: {len(vlans_esp)} definidas, switches: {len(switches)} definidos.")
        print("CUMPLIMIENTO GLOBAL: NO AUDITADO (diseno incompleto)")
        return 2

    total_ok = 0
    total_no = 0
    total_auditados = 0

    # Switches definidos en el diseño.
    if not switches:
        salida.append("AVISO: el diseno no define switches.")
    for host, data in switches.items():
        data = data or {}
        alias = data.get("alias")
        titulo = f"DISPOSITIVO: {host}" + (f" (alias {alias})" if alias else "")
        filas: list[dict] = []

        p_run = buscar_respaldo(candidatos_running(host, alias), dir_respaldos)
        p_vlan = buscar_respaldo(candidatos_vlan(host, alias), dir_respaldos)

        if p_run is None:
            salida.append(titulo)
            salida.append(f"  NO CUMPLE: falta running-config (buscado: {', '.join(candidatos_running(host, alias)[:2])})")
            salida.append("")
            total_no += 1
            total_auditados += 1
            continue
        texto_run = p_run.read_text(encoding="utf-8", errors="replace")
        parsed = parsear_running_config(texto_run)

        brief = None
        if p_vlan is not None:
            brief = parsear_vlan_brief(p_vlan.read_text(encoding="utf-8", errors="replace"))
        else:
            filas.append({
                "elemento": "(vlan brief)", "chequeo": "archivo",
                "esperado": "existe", "encontrado": "no encontrado",
                "estado": "NO CUMPLE",
            })

        salida.append(titulo)
        salida.append(f"  running-config: {p_run.name}")
        salida.append(f"  vlan brief: {p_vlan.name if p_vlan else '(no encontrado)'}")

        # 1) VLANs 10, 20 y 99.
        auditar_vlans(filas, vlans_esp, brief, parsed["vlans"])
        # 2) Puertos de acceso (tolera claves puerto/port/name).
        for pa_crudo in data.get("puertos_acceso", data.get("access_ports", [])):
            pa = _leer_puerto_acceso(pa_crudo)
            if pa is None:
                filas.append({
                    "elemento": "(puerto acceso)", "chequeo": "definicion",
                    "esperado": "puerto/vlan/descripcion", "encontrado": f"definicion invalida: {pa_crudo}",
                    "estado": "NO CUMPLE",
                })
                continue
            auditar_acceso(filas, pa["puerto"], int(pa["vlan"]), str(pa.get("descripcion", "")), parsed["interfaces"])
        # 3) Troncales.
        for pt_crudo in data.get("puertos_trunk", data.get("trunk_ports", [])):
            pt = _leer_puerto_trunk(pt_crudo)
            if pt is None:
                filas.append({
                    "elemento": "(puerto trunk)", "chequeo": "definicion",
                    "esperado": "puerto/native/allowed/descripcion", "encontrado": f"definicion invalida: {pt_crudo}",
                    "estado": "NO CUMPLE",
                })
                continue
            try:
                nat = int(pt.get("native_vlan", nat_def))
            except (ValueError, TypeError):
                nat = nat_def
            allow = normalizar_allowed(pt.get("allowed_vlans", allow_def))
            auditar_trunk(filas, pt["puerto"], nat, allow, str(pt.get("descripcion", "")), parsed["interfaces"])

        salida.append(formatear_tabla(f"BLOQUE: {host}", filas))
        salida.append("")
        total_ok += sum(1 for r in filas if r["estado"] == "CUMPLE")
        total_no += sum(1 for r in filas if r["estado"] == "NO CUMPLE")
        total_auditados += len(filas)

    # Routers (solo running-config, si existe).
    for host, data in routers.items():
        data = data or {}
        alias = data.get("alias")
        titulo = f"DISPOSITIVO: {host}" + (f" (alias {alias})" if alias else "")
        filas = []
        p_run = buscar_respaldo(candidatos_running(host, alias), dir_respaldos)
        salida.append(titulo)
        if p_run is None:
            # No se omite en silencio: falta de evidencia es NO CUMPLE.
            msg = f"  NO CUMPLE: falta running-config de router (buscado: {', '.join(candidatos_running(host, alias)[:2])})"
            salida.append(msg)
            salida.append("")
            total_no += 1
            total_auditados += 1
            continue
        salida.append(f"  running-config: {p_run.name}")
        parsed = parsear_running_config(p_run.read_text(encoding="utf-8", errors="replace"))
        auditar_router(filas, data, parsed["interfaces"])
        salida.append(formatear_tabla(f"BLOQUE: {host}", filas))
        salida.append("")
        total_ok += sum(1 for r in filas if r["estado"] == "CUMPLE")
        total_no += sum(1 for r in filas if r["estado"] == "NO CUMPLE")
        total_auditados += len(filas)

    # Si no se auditó ningún elemento, nunca es CUMPLE.
    if total_auditados == 0:
        salida.append("=" * 100)
        salida.append("RESUMEN: 0 elementos auditados")
        salida.append("CUMPLIMIENTO GLOBAL: NO AUDITADO (sin elementos)")
        texto_final = "\n".join(salida) + "\n"
        print(texto_final)
        ruta_salida.parent.mkdir(parents=True, exist_ok=True)
        ruta_salida.write_text(texto_final, encoding="utf-8")
        print(f"Evidencia guardada en: {ruta_salida}")
        return 2

    salida.append("=" * 100)
    salida.append(f"RESUMEN: {total_ok} CUMPLE, {total_no} NO CUMPLE")
    salida.append("CUMPLIMIENTO GLOBAL: " + ("CUMPLE" if total_no == 0 else "NO CUMPLE"))
    texto_final = "\n".join(salida) + "\n"
    print(texto_final)

    # Guarda la misma salida en evidencias/.
    ruta_salida.parent.mkdir(parents=True, exist_ok=True)
    ruta_salida.write_text(texto_final, encoding="utf-8")
    print(f"Evidencia guardada en: {ruta_salida}")
    return 0 if total_no == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
