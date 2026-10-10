# Sucursal 01 — Automatización con Ansible + análisis con Python

Documentación de los cinco prototipos: `documentacion/README.md`.

## Qué hace cada parte

- **Ansible (aplica, respalda, obtiene evidencia):**
  - Aplica la configuración deseada a switches y router (VLANs, puertos acceso/trunk, router-on-a-stick + DHCP).
  - Guarda configuración (`save_when: modified`) y obtiene evidencia (`show running-config`, `show version`, `show vlan brief`, `show interfaces trunk`, `show ip interface brief`, `show ip route`) en `respaldos/`.
- **Python (analiza la evidencia y determina cumplimiento):**
  - No abre sesiones SSH ni configura dispositivos (salvo el prototipo 5, que solo ejecuta el `ping` del sistema); solo lee archivos.
  - Lee el diseño esperado desde `variables/sucursal_01.yml` y la evidencia desde `respaldos/`.
  - Audita VLAN 10, 20 y 99 (existencia, nombre, estado `active`), puertos de acceso (VLAN + descripción) y troncales (modo trunk, nativa 99, permitidas 10,20,99, descripción), más subinterfaces del router si hay evidencia.
  - Muestra tabla por elemento con `CUMPLE / NO CUMPLE` y, si no cumple, indica esperado vs encontrado.
  - Guarda la misma salida en `evidencias/`.

## Estructura del proyecto

```text
ansible.cfg                 # inventory = inventario/hosts.yml
ejecutar_todo.sh            # ejecuta playbooks en orden (vlans -> puertos -> router -> verificar)
inventario/
  hosts.yml                 # grupos switches (SW-SUC-01-CORE/ACC1/ACC2/ACC3) y routers (R-SUC-01)
  group_vars/
    switches.yml            # vlans_sucursal, trunk_allowed_vlans, credenciales vía vault
    routers.yml             # dhcp_excluidos, dhcp_pools, credenciales vía vault
  host_vars/
    SW-SUC-01-*.yml         # mgmt_ip, puertos_acceso / puertos_trunk por equipo
variables/
  sucursal_01.yml           # diseño esperado consolidado para Python (con descripciones)
  sucursal_02_generada.yml  # direccionamiento VLSM generado (prototipo 2)
playbooks/
  site.yml                  # vlans, puertos, router, respaldos, verificar
  vlans.yml                 # crea VLAN 10,20,99
  puertos.yml               # configura acceso + trunk
  router.yml                # subinterfaces Gi0/1.10/.20/.99 + DHCP
  verificar.yml             # show vlan brief + show interfaces trunk
  respaldos.yml             # evidencia completa de flota (prototipo 3)
  evidencia_estado.yml      # evidencia ANTES/DESPUÉS (prototipo 1)
  cambio_controlado.yml     # cambio de descripción idempotente (prototipo 1)
respaldos/                  # evidencia: <HOST>-<archivo>.txt
evidencias/                 # salidas generadas por Python
python/
  auditar_sucursal.py       # auditor base (PyYAML, solo lectura)
  validar_cambio.py         # prototipo 1
  generar_sucursal_02.py    # prototipo 2
  auditar_flota.py          # prototipo 3
  validar_inventario.py     # prototipo 4
  medir_conectividad.py     # prototipo 5
datos/
  gen_respaldos.py                      # generador de respaldos de ejemplo (auxiliar)
  cambio_aprobado.yml                   # cambio CHG-001 (prototipo 1)
  sucursal_02_entrada.yml               # entrada VLSM (prototipo 2)
  ipam_sucursal_01.json                 # IPAM simulado (prototipo 4)
  ipam_sucursal_01_con_errores.json     # datos sintéticos de prueba (prototipo 4)
documentacion/README.md     # documentación de los cinco prototipos
.gitignore
README.md
```

### Mapeo de equipos

| Inventario Ansible | Función      | IP            |
|--------------------|--------------|---------------|
| SW-SUC-01-CORE     | Switch core  | 192.168.99.14 |
| SW-SUC-01-ACC1     | Switch acc.1 | 192.168.99.15 |
| SW-SUC-01-ACC2     | Switch acc.2 | 192.168.99.16 |
| SW-SUC-01-ACC3     | Switch acc.3 | 192.168.99.17 |
| R-SUC-01           | Router       | 192.168.99.1  |

Los respaldos antiguos con nombres `SW1..SW4` se conservan en `respaldos/_anteriores/`; los vigentes usan la nomenclatura oficial y se generan con `ansible-playbook playbooks/respaldos.yml`.

Descripciones estándar (iguales en `host_vars` y `variables/sucursal_01.yml`, las audita Python):
- Acceso: `PC2 - VLAN10`, `PC5 - VLAN20`, `PC6 - VLAN99`, etc. (nombre del PC + VLAN)
- Enlace a switch genérico: `Switch1 - VLAN99`, `Switch2 - VLAN99`
- Troncal: `Hacia <destino> - Troncal Nativa99` (nativa 99, permitidas 10,20,99)

## Requisitos

- Ansible ≥ 2.14 con colecciones `cisco.ios`, `ansible.netcommon`.
- Python ≥ 3.9 con `PyYAML` (`pip install pyyaml`).
- Acceso SSH a 192.168.99.0/24 para la parte Ansible (no necesario para Python).

## Cómo ejecutar Ansible

```bash
# Ver inventario (usa ansible.cfg -> inventario/hosts.yml)
ansible-inventory --graph

# Simular (check) o aplicar por pasos
./ejecutar_todo.sh --check
./ejecutar_todo.sh

# O playbook por playbook
ansible-playbook playbooks/vlans.yml
ansible-playbook playbooks/puertos.yml
ansible-playbook playbooks/router.yml
ansible-playbook playbooks/verificar.yml
# Todo junto:
ansible-playbook playbooks/site.yml

# Evidencia de flota y de cambio (prototipos 3 y 1)
ansible-playbook playbooks/respaldos.yml
ansible-playbook playbooks/evidencia_estado.yml -e fase=antes
ansible-playbook playbooks/cambio_controlado.yml
ansible-playbook playbooks/evidencia_estado.yml -e fase=despues
```

La evidencia se guarda como `respaldos/<HOST>-<archivo>.txt` (`running-config`, `show-version`, `show-vlan-brief`, `show-interfaces-trunk`, `show-ip-interface-brief` y, en el router, `show-ip-route`).

## Cómo ejecutar Python

```bash
pip install pyyaml
python3 python/auditar_sucursal.py
python3 python/validar_cambio.py
python3 python/generar_sucursal_02.py
python3 python/auditar_flota.py
python3 python/validar_inventario.py
python3 python/medir_conectividad.py   # en el servidor de automatización
```

Salida en pantalla + `evidencias/`. Código de salida `0` si todo CUMPLE, `1` si hay algún NO CUMPLE, `2` ante entradas inválidas.

Ejemplo de tabla:

```text
DISPOSITIVO: SW-SUC-01-CORE
| ELEMENTO | CHEQUEO | ESPERADO | ENCONTRADO | ESTADO |
...
Incumplimientos (1):
  - GigabitEthernet0/2 [acceso vlan]: esperado '10' vs encontrado '20'
```

## Seguridad / .gitignore

`.gitignore` excluye contraseñas, vault, claves privadas (`*.pem`, `*.key`), `*.retry`, `__pycache__/` y venvs.

Revisión de secretos (10-oct-2026):
- `respaldos/*`: **limpio**. Sin `enable secret`, `username`, `password` ni hashes. Contiene IPs de gestión, no sensibles.
- `inventario/group_vars/switches.yml` y `routers.yml`: **usan `{{ vault_ansible_password }}` / `{{ vault_enable_password }}` (sin secretos en git)**. Crear el vault local con `ansible-vault` a partir de `inventario/group_vars/credenciales.ejemplo.yml` (ignorado por git).
- No hay `*.pem`, `*.key` ni `*.retry` en el repo.

## Datos de prueba (prototipos)

- `datos/ipam_sucursal_01_con_errores.json`: **datos sintéticos de prueba** (copia del IPAM con 4 errores deliberados: gateway VLAN 20, IP de ACC1, falta ACC3, extra ACC9) solo para demostrar la detección en `python/validar_inventario.py`. No es una fuente real.
