#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Genera el direccionamiento VLSM de la Sucursal 02 (solo calculo).

Lee datos/sucursal_02_entrada.yml, asigna una subred por VLAN dentro
del bloque con VLSM y escribe variables/sucursal_02_generada.yml mas
el resumen en evidencias/plan_sucursal_02.txt. No configura equipos.
"""
from __future__ import annotations

import argparse
import ipaddress
import sys
from pathlib import Path

import yaml

# Rutas base relativas al repo (no al cwd).
BASE = Path(__file__).resolve().parent.parent
RUTA_ENTRADA = BASE / "datos" / "sucursal_02_entrada.yml"
RUTA_SALIDA = BASE / "variables" / "sucursal_02_generada.yml"
RUTA_RESUMEN = BASE / "evidencias" / "plan_sucursal_02.txt"


# ---------- Carga y validacion ----------

def cargar_yaml(ruta: Path):
    """Carga un YAML y devuelve su contenido."""
    with open(ruta, encoding="utf-8") as f:
        return yaml.safe_load(f)


def validar_entrada(datos) -> tuple[str, ipaddress.IPv4Network, list[dict]]:
    """Valida bloque y VLANs; devuelve (sucursal, bloque, vlans)."""
    if not isinstance(datos, dict):
        raise ValueError("la entrada no es un diccionario YAML valido.")
    sucursal = datos.get("sucursal")
    if not sucursal or not str(sucursal).strip():
        raise ValueError("falta 'sucursal' en la entrada.")
    bloque_txt = datos.get("bloque")
    if not bloque_txt or not str(bloque_txt).strip():
        raise ValueError("falta 'bloque' en la entrada.")
    try:
        bloque = ipaddress.ip_network(str(bloque_txt).strip(), strict=False)
    except ValueError:
        raise ValueError(f"bloque invalido: '{bloque_txt}'.")
    if not isinstance(bloque, ipaddress.IPv4Network):
        raise ValueError("el bloque debe ser IPv4.")
    vlans = datos.get("vlans")
    if not isinstance(vlans, list) or not vlans:
        raise ValueError("falta la lista 'vlans' en la entrada.")
    vistos: set[int] = set()
    buenas: list[dict] = []
    for v in vlans:
        if not isinstance(v, dict):
            raise ValueError(f"VLAN invalida (no es diccionario): {v}.")
        vid = v.get("vlan_id", v.get("id"))
        nombre = v.get("nombre", v.get("name"))
        disp = v.get("dispositivos", v.get("hosts"))
        if vid is None or nombre is None or disp is None:
            raise ValueError(f"VLAN incompleta (vlan_id/nombre/dispositivos): {v}.")
        try:
            vid = int(vid)
        except (ValueError, TypeError):
            raise ValueError(f"vlan_id no entero: {v}.")
        if not 1 <= vid <= 4094:
            raise ValueError(f"vlan_id fuera de rango 1-4094: {vid}.")
        if vid in vistos:
            raise ValueError(f"VLAN repetida: {vid}.")
        vistos.add(vid)
        if not str(nombre).strip():
            raise ValueError(f"nombre vacio en VLAN {vid}.")
        try:
            disp = int(disp)
        except (ValueError, TypeError):
            raise ValueError(f"dispositivos no entero en VLAN {vid}: {v}.")
        if disp <= 0:
            raise ValueError(f"dispositivos debe ser > 0 en VLAN {vid} (recibido {disp}).")
        buenas.append({"vlan_id": vid, "nombre": str(nombre).strip(), "dispositivos": disp})
    return str(sucursal).strip(), bloque, buenas


# ---------- Calculo VLSM ----------

def utilizables(prefijo: int) -> int:
    """Direcciones utilizables segun 2^(32-p) - 2."""
    return 2 ** (32 - prefijo) - 2


def prefijo_para(necesarias: int) -> int:
    """Prefijo mas largo cuya capacidad alcance las direcciones necesarias."""
    for p in range(30, -1, -1):  # de /30 (mas largo util) hacia /0
        if utilizables(p) >= necesarias:
            return p
    raise ValueError(f"ningun prefijo alcanza para {necesarias} direcciones.")


def alinear(valor: int, tamano: int) -> int:
    """Sube el valor al siguiente multiplo del tamano (alineacion de red)."""
    resto = valor % tamano
    return valor if resto == 0 else valor + (tamano - resto)


def asignar_vlsm(bloque: ipaddress.IPv4Network, vlans: list[dict]) -> list[dict]:
    """Asigna subredes sin solapamiento, de mayor a menor tamano."""
    pedidos = []
    for v in vlans:
        necesarias = v["dispositivos"] + 1  # +1 puerta de enlace
        prefijo = prefijo_para(necesarias)
        pedidos.append({**v, "necesarias": necesarias, "prefijo": prefijo,
                        "tamano": 2 ** (32 - prefijo)})
    # VLSM: mayor tamano primero; desempate por vlan_id.
    pedidos.sort(key=lambda d: (-d["tamano"], d["vlan_id"]))
    inicio_bloque = int(bloque.network_address)
    fin_bloque = int(bloque.broadcast_address)
    siguiente = inicio_bloque
    asignadas = []
    for p in pedidos:
        base = alinear(siguiente, p["tamano"])
        red = ipaddress.ip_network((base, p["prefijo"]), strict=True)
        fin_red = int(red.broadcast_address)
        if base < inicio_bloque or fin_red > fin_bloque:
            total = sum(q["tamano"] for q in pedidos)
            faltan_total = total - bloque.num_addresses
            resto = fin_bloque - base + 1
            detalle = (f"la VLAN {p['vlan_id']} ({p['nombre']}) necesita una /{p['prefijo']} "
                       f"de {p['tamano']} direcciones y solo quedan {max(resto, 0)} "
                       f"desde {ipaddress.ip_address(max(base, inicio_bloque))}.")
            if faltan_total > 0:
                detalle += (f" En total se necesitan {total} direcciones y el bloque "
                            f"{bloque} solo tiene {bloque.num_addresses} "
                            f"(faltan {faltan_total}).")
            raise ValueError(f"no caben todas en el bloque {bloque}. {detalle}")
        gateway = ipaddress.ip_address(int(red.network_address) + 1)
        asignadas.append({**p, "red": red, "gateway": gateway})
        siguiente = fin_red + 1
    return asignadas


def verificar(bloque: ipaddress.IPv4Network, asignadas: list[dict]) -> None:
    """Verifica con codigo que todo esta dentro del bloque y sin solapes."""
    for a in asignadas:
        if not a["red"].subnet_of(bloque):
            raise ValueError(f"{a['red']} no esta dentro del bloque {bloque}.")
    for i in range(len(asignadas)):
        for j in range(i + 1, len(asignadas)):
            if asignadas[i]["red"].overlaps(asignadas[j]["red"]):
                raise ValueError(f"solape entre {asignadas[i]['red']} y {asignadas[j]['red']}.")
        # El gateway debe ser la primera utilizable.
        esperado = ipaddress.ip_address(int(asignadas[i]["red"].network_address) + 1)
        if asignadas[i]["gateway"] != esperado:
            raise ValueError(f"gateway incorrecto en {asignadas[i]['red']}.")


def espacios_libres(bloque: ipaddress.IPv4Network, asignadas: list[dict]) -> list[tuple]:
    """Devuelve [(primera_ip, ultima_ip, cantidad)] de huecos libres."""
    ocupadas = sorted((a["red"] for a in asignadas), key=lambda r: int(r.network_address))
    libres = []
    cursor = int(bloque.network_address)
    fin = int(bloque.broadcast_address)
    for red in ocupadas:
        ini = int(red.network_address)
        if cursor < ini:
            libres.append((ipaddress.ip_address(cursor), ipaddress.ip_address(ini - 1),
                           ini - cursor))
        cursor = max(cursor, int(red.broadcast_address) + 1)
    if cursor <= fin:
        libres.append((ipaddress.ip_address(cursor), ipaddress.ip_address(fin),
                       fin - cursor + 1))
    return libres


# ---------- Salida ----------

def construir_yaml(sucursal: str, bloque: ipaddress.IPv4Network, asignadas: list[dict]) -> dict:
    """Arma el diccionario para variables/sucursal_02_generada.yml."""
    vlans_out = []
    for a in sorted(asignadas, key=lambda d: d["vlan_id"]):
        vlans_out.append({
            "vlan_id": a["vlan_id"],
            "name": a["nombre"],
            "state": "active",
            "red": str(a["red"]),
            "mascara": str(a["red"].netmask),
            "prefijo": a["prefijo"],
            "gateway": str(a["gateway"]),
            "hosts_previstos": a["dispositivos"],
            "hosts_utilizables": utilizables(a["prefijo"]),
        })
    return {"sucursal": sucursal, "bloque": str(bloque),
            "generado_por": "python/generar_sucursal_02.py",
            "vlans_sucursal_02": vlans_out}


def formatear_resumen(sucursal: str, bloque: ipaddress.IPv4Network, asignadas: list[dict],
                      libres: list[tuple], ruta_entrada: Path) -> str:
    """Arma el resumen legible con las tres secciones pedidas."""
    lineas = []
    lineas.append("PLAN DE DIRECCIONAMIENTO - SUCURSAL 02 (Python, solo calculo)")
    lineas.append("=" * 100)
    lineas.append("[1] QUE DATOS LEYO")
    lineas.append(f"  - Entrada: {ruta_entrada} (sucursal {sucursal}, bloque {bloque}, "
                  f"{len(asignadas)} VLANs)")
    for a in sorted(asignadas, key=lambda d: d["vlan_id"]):
        lineas.append(f"    VLAN {a['vlan_id']} {a['nombre']}: {a['dispositivos']} dispositivos "
                      f"(necesita {a['necesarias']} direcciones con gateway)")
    lineas.append("")
    lineas.append("[2] QUE CONDICION EVALUO")
    lineas.append("  - Prefijo mas largo con 2^(32-p)-2 >= dispositivos+1.")
    lineas.append("  - VLSM secuencial y alineado, de mayor a menor tamano.")
    lineas.append("  - Todo dentro del bloque y sin solapes (verificado con codigo).")
    lineas.append("  - Gateway = primera IP utilizable de cada subred.")
    lineas.append("")
    lineas.append("[3] CUAL FUE EL RESULTADO")
    cap = (f"| {'VLAN':<6} | {'NOMBRE':<15} | {'RED':<18} | {'MASCARA':<15} | "
           f"{'GATEWAY':<13} | {'PREV':<5} | {'UTIL':<5} | {'%USO':<6} |")
    sep = "-" * len(cap)
    lineas += [sep, cap, sep]
    for a in sorted(asignadas, key=lambda d: d["vlan_id"]):
        util = utilizables(a["prefijo"])
        porc = a["dispositivos"] * 100.0 / util
        lineas.append(f"| {a['vlan_id']:<6} | {a['nombre']:<15} | {str(a['red']):<18} | "
                      f"{str(a['red'].netmask):<15} | {str(a['gateway']):<13} | "
                      f"{a['dispositivos']:<5} | {util:<5} | {porc:5.1f}% |")
    lineas.append(sep)
    if libres:
        lineas.append("Espacio libre restante:")
        for ini, fin, cant in libres:
            lineas.append(f"  - de {ini} a {fin} ({cant} direcciones)")
    else:
        lineas.append("Espacio libre restante: ninguno (bloque completo).")
    lineas.append("CUMPLIMIENTO GLOBAL: CUMPLE (subredes dentro del bloque y sin solapes)")
    return "\n".join(lineas) + "\n"


def main(argv: list[str] | None = None) -> int:
    """Lee la entrada, calcula VLSM y escribe YAML + resumen."""
    parser = argparse.ArgumentParser(description="Genera el direccionamiento de la Sucursal 02.")
    parser.add_argument("--entrada", default=str(RUTA_ENTRADA), help="YAML de entrada.")
    parser.add_argument("--salida", default=str(RUTA_SALIDA), help="YAML generado.")
    parser.add_argument("--resumen", default=str(RUTA_RESUMEN), help="Resumen legible.")
    args = parser.parse_args(argv)

    ruta_entrada = Path(args.entrada)
    ruta_salida = Path(args.salida)
    ruta_resumen = Path(args.resumen)

    try:
        datos = cargar_yaml(ruta_entrada)
    except FileNotFoundError:
        print(f"ERROR: no existe la entrada: {ruta_entrada}")
        return 2
    except yaml.YAMLError as e:
        print(f"ERROR: YAML invalido en {ruta_entrada}: {e}")
        return 2
    try:
        sucursal, bloque, vlans = validar_entrada(datos)
        asignadas = asignar_vlsm(bloque, vlans)
        verificar(bloque, asignadas)
    except ValueError as e:
        print(f"ERROR: {e}")
        return 2

    libres = espacios_libres(bloque, asignadas)
    contenido = construir_yaml(sucursal, bloque, asignadas)
    ruta_salida.parent.mkdir(parents=True, exist_ok=True)
    with open(ruta_salida, "w", encoding="utf-8") as f:
        yaml.safe_dump(contenido, f, sort_keys=False, allow_unicode=True)

    texto = formatear_resumen(sucursal, bloque, asignadas, libres, ruta_entrada)
    print(texto)
    ruta_resumen.parent.mkdir(parents=True, exist_ok=True)
    ruta_resumen.write_text(texto, encoding="utf-8")
    print(f"YAML generado en: {ruta_salida}")
    print(f"Resumen guardado en: {ruta_resumen}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
