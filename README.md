# Sucursal 01 — Automatización con Ansible + Auditoría con Python

## Qué hace cada parte

- **Ansible (aplica, respalda, obtiene evidencia):**
  - Aplica la configuración deseada a switches y router (VLANs, puertos acceso/trunk, router-on-a-stick + DHCP).
  - Guarda configuración (`save_when: modified`) y obtiene evidencia (`show vlan brief`, `show interfaces trunk`, `running-config`) en `respaldos/`.
- **Python (analiza la evidencia y determina cumplimiento):**
  - No abre sesiones SSH ni configura dispositivos; solo lee archivos.
  - Lee el diseño esperado desde `variables/sucursal_01.yml` y la evidencia desde `respaldos/`.
  - Audita VLAN 10, 20 y 99 (existencia, nombre, estado `active`), puertos de acceso (VLAN + descripción) y troncales (modo trunk, nativa 99, permitidas 10,20,99, descripción), más subinterfaces del router si hay evidencia.
  - Muestra tabla por elemento con `CUMPLE / NO CUMPLE` y, si no cumple, indica esperado vs encontrado.
  - Guarda la misma salida en `evidencias/auditoria_python.txt`.

## Estructura del proyecto

```
ansible.cfg                 # inventory = inventario/hosts.yml
ejecutar_todo.sh            # ejecuta playbooks en orden (vlans -> puertos -> router -> verificar)
inventario/
  hosts.yml                 # grupos switches (SW1..SW4) y routers (Router_MB)
  group_vars/
    switches.yml            # vlans_sucursal, trunk_allowed_vlans, credenciales (*)
    routers.yml             # dhcp_excluidos, dhcp_pools, credenciales (*)
  host_vars/
    SW1.yml ... SW4.yml     # puertos_acceso / puertos_trunk por equipo
variables/
  sucursal_01.yml           # diseño esperado consolidado para Python (con descripciones)
playbooks/
  site.yml                  # importa los 4 playbooks
  vlans.yml                 # crea VLAN 10,20,99
  puertos.yml               # configura acceso + trunk
  router.yml                # subinterfaces Gi0/1.10/.20/.99 + DHCP
  verificar.yml             # show vlan brief + show interfaces trunk
respaldos/                  # evidencia: *-running-config.txt y *-show-vlan-brief.txt
evidencias/
  auditoria_python.txt      # salida generada por Python
python/
  auditar_sucursal.py       # auditor (PyYAML, solo lectura)
datos/
  gen_respaldos.py          # generador de respaldos de ejemplo CUMPLE (auxiliar)
documentacion/              # (reservado para diagramas, memorias)
.gitignore
README.md
```

(*) `inventario/group_vars/*.yml` contiene hoy `ansible_password: Ansible123` en texto plano. Ver sección Seguridad.

### Mapeo lógico de equipos

| Inventario Ansible | Alias lógico | IP           |
|--------------------|--------------|--------------|
| SW1                | SW-SUC-01-CORE | 192.168.99.14 |
| SW2                | SW-SUC-01-ACC1 | 192.168.99.15 |
| SW3                | SW-SUC-01-ACC2 | 192.168.99.16 |
| SW4                | SW-SUC-01-ACC3 | 192.168.99.17 |
| Router_MB          | R-SUC-01       | 192.168.99.1  |

El auditor acepta ambas nomenclaturas: busca `SW1-running-config.txt` y también `SW-SUC-01-CORE-running-config.txt`. En `respaldos/` se incluyen ambos juegos (mismo contenido) para cumplir el enunciado literal y auditar los 4 switches.

Descripciones estándar generadas (no existían en `host_vars`):
- Acceso VLAN 10 → `PC-VLAN10-Datos`
- Acceso VLAN 20 → `PC-VLAN20-Otra_Red`
- Acceso VLAN 99 → `ADMIN-VLAN99-Nativa`
- Troncal → `Uplink-Trunk-Nativa99` (nativa 99, permitidas 10,20,99)

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
```

La evidencia real debe guardarse como:
- `respaldos/<HOST>-running-config.txt`
- `respaldos/<HOST>-show-vlan-brief.txt`

## Cómo ejecutar Python (auditoría)

```bash
pip install pyyaml
python python/auditar_sucursal.py
# o
python3 python/auditar_sucursal.py
```

Salida en pantalla + `evidencias/auditoria_python.txt`. Código de salida `0` si todo CUMPLE, `1` si hay algún NO CUMPLE.

Ejemplo de tabla:

```
DISPOSITIVO: SW1 (alias SW-SUC-01-CORE)
| ELEMENTO | CHEQUEO | ESPERADO | ENCONTRADO | ESTADO |
...
Incumplimientos (1):
  - GigabitEthernet0/2 [acceso vlan]: esperado '10' vs encontrado '20'
```

## Seguridad / .gitignore

`.gitignore` excluye contraseñas, vault, claves privadas (`*.pem`, `*.key`), `*.retry`, `__pycache__/` y venvs.

Revisión de secretos (09-oct-2026):
- `respaldos/*`: **limpio**. Solo aparece la palabra “secretos” en el comentario “sin secretos”. Sin `enable secret`, `username`, `password`, ni hashes. Seguro para Git (contiene IPs de gestión, no sensibles).
- `inventario/group_vars/switches.yml` y `routers.yml`: **contienen `ansible_password: Ansible123` y `ansible_become_password: Ansible123` en texto plano**. No se borraron sin confirmación. Recomendado: migrar a Ansible Vault (`ansible-vault encrypt_string`) y no subir credenciales reales.
- No hay archivos `*vault*`, `*.pem`, `*.key` ni `*.retry` en el repo.

## Reorganización aplicada (sin romper rutas)

- `inventory/` → `inventario/` (con `hosts.yml`, `group_vars/`, `host_vars/` intactos).
- `ansible.cfg`: `inventory = inventario/hosts.yml` (único cambio de ruta).
- `playbooks/` sin cambios; `ejecutar_todo.sh` sigue válido (`playbooks/*.yml`).
- Nuevo `variables/sucursal_01.yml` (consolidado para Python; los playbooks siguen usando `group_vars/host_vars`).
- Nuevas carpetas: `respaldos/`, `evidencias/`, `datos/`, `documentacion/`, `python/` (ya existía).
- `datos/gen_respaldos.py`: solo auxiliar para generar evidencia de ejemplo; no es parte de la auditoría.
