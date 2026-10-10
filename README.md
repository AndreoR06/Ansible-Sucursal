# Sucursal 01 â€” AutomatizaciÃ³n con Ansible + AuditorÃ­a con Python
- `datos/gen_respaldos.py`: solo auxiliar para generar evidencia de ejemplo; no es parte de la auditorÃ­a.

# Sucursal 01 â€” AutomatizaciÃ³n con Ansible + AuditorÃ­a con Python

## QuÃ© hace cada parte

- **Ansible (aplica, respalda, obtiene evidencia):**
  - Aplica la configuraciÃ³n deseada a switches y router (VLANs, puertos acceso/trunk, router-on-a-stick + DHCP).
  - Guarda configuraciÃ³n (`save_when: modified`) y obtiene evidencia (`show vlan brief`, `show interfaces trunk`, `running-config`) en `respaldos/`.
- **Python (analiza la evidencia y determina cumplimiento):**
  - No abre sesiones SSH ni configura dispositivos; solo lee archivos.
  - Lee el diseÃ±o esperado desde `variables/sucursal_01.yml` y la evidencia desde `respaldos/`.
  - Audita VLAN 10, 20 y 99 (existencia, nombre, estado `active`), puertos de acceso (VLAN + descripciÃ³n) y troncales (modo trunk, nativa 99, permitidas 10,20,99, descripciÃ³n), mÃ¡s subinterfaces del router si hay evidencia.
  - Muestra tabla por elemento con `CUMPLE / NO CUMPLE` y, si no cumple, indica esperado vs encontrado.
  - Guarda la misma salida en `evidencias/auditoria_python.txt`.

## Estructura del proyecto

```
ansible.cfg                 # inventory = inventario/hosts.yml
ejecutar_todo.sh            # ejecuta playbooks en orden (vlans -> puertos -> router -> verificar)
inventario/
  hosts.yml                 # grupos switches (SW-SUC-01-CORE/ACC1/ACC2/ACC3) y routers (R-SUC-01)
  group_vars/
    switches.yml            # vlans_sucursal, trunk_allowed_vlans, credenciales (*)
    routers.yml             # dhcp_excluidos, dhcp_pools, credenciales (*)
  host_vars/
    SW1.yml ... SW4.yml     # puertos_acceso / puertos_trunk por equipo
variables/
  sucursal_01.yml           # diseÃ±o esperado consolidado para Python (con descripciones)
playbooks/
  site.yml                  # importa los 5 playbooks (vlans, puertos, router, respaldos, verificar)
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

(*) `inventario/group_vars/*.yml` contiene hoy `ansible_password: Ansible123` en texto plano. Ver secciÃ³n Seguridad.

### Mapeo lÃ³gico de equipos

| Inventario Ansible | Alias lÃ³gico | IP           |
|--------------------|--------------|--------------|
| SW-SUC-01-CORE   | CORE lógico  | 192.168.99.14 |
| SW-SUC-01-ACC1   | ACC1 lógico  | 192.168.99.15 |
| SW-SUC-01-ACC2   | ACC2 lógico  | 192.168.99.16 |
| SW-SUC-01-ACC3   | ACC3 lógico  | 192.168.99.17 |
| R-SUC-01         | Router MB    | 192.168.99.1  |

El inventario usa la nomenclatura oficial (`SW-SUC-01-CORE/ACC1/ACC2/ACC3`, `R-SUC-01`) con las IPs de gestion ya definidas (.14-.17, .1). Los respaldos antiguos con nombres `SW1..SW4` se movieron a `respaldos/_anteriores/`; los vigentes se generan con `ansible-playbook playbooks/respaldos.yml`.

Descripciones estandar (iguales en `host_vars` y `variables/sucursal_01.yml`, las audita Python):
- Acceso: `PC2 - VLAN10`, `PC5 - VLAN20`, `PC6 - VLAN99`, etc. (nombre del PC + VLAN)
- Enlace a switch generico: `Switch1 - VLAN99`, `Switch2 - VLAN99`
- Troncal: `Hacia <destino> - Troncal Nativa99` (nativa 99, permitidas 10,20,99)
## Requisitos

- Ansible â‰¥ 2.14 con colecciones `cisco.ios`, `ansible.netcommon`.
- Python â‰¥ 3.9 con `PyYAML` (`pip install pyyaml`).
- Acceso SSH a 192.168.99.0/24 para la parte Ansible (no necesario para Python).

## CÃ³mo ejecutar Ansible

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

## CÃ³mo ejecutar Python (auditorÃ­a)

```bash
pip install pyyaml
python python/auditar_sucursal.py
# o
python3 python/auditar_sucursal.py
```

Salida en pantalla + `evidencias/auditoria_python.txt`. CÃ³digo de salida `0` si todo CUMPLE, `1` si hay algÃºn NO CUMPLE.

Ejemplo de tabla:

```
DISPOSITIVO: SW1 (alias SW-SUC-01-CORE)
| ELEMENTO | CHEQUEO | ESPERADO | ENCONTRADO | ESTADO |
...
Incumplimientos (1):
  - GigabitEthernet0/2 [acceso vlan]: esperado '10' vs encontrado '20'
```

## Seguridad / .gitignore

`.gitignore` excluye contraseÃ±as, vault, claves privadas (`*.pem`, `*.key`), `*.retry`, `__pycache__/` y venvs.

RevisiÃ³n de secretos (09-oct-2026):
- `respaldos/*`: **limpio**. Solo aparece la palabra â€œsecretosâ€ en el comentario â€œsin secretosâ€. Sin `enable secret`, `username`, `password`, ni hashes. Seguro para Git (contiene IPs de gestiÃ³n, no sensibles).
- `inventario/group_vars/switches.yml` y `routers.yml`: **usan `{{ vault_ansible_password }}` / `{{ vault_enable_password }}` (sin secretos en git)**. No se borraron sin confirmaciÃ³n. Recomendado: migrar a Ansible Vault (`ansible-vault encrypt_string`) y no subir credenciales reales.
- Hay `inventario/group_vars/credenciales\.ejemplo\.yml` como plantilla (copiar a `vault.yml`, ignorado por git). No hay `*.pem`, `*.key` ni `*.retry` en el repo.

## ReorganizaciÃ³n aplicada (sin romper rutas)

- `inventory/` â†’ `inventario/` (con `hosts.yml`, `group_vars/`, `host_vars/` intactos).
- `ansible.cfg`: `inventory = inventario/hosts.yml` (Ãºnico cambio de ruta).
- `playbooks/` sin cambios; `ejecutar_todo.sh` sigue vÃ¡lido (`playbooks/*.yml`).
- Nuevo `variables/sucursal_01.yml` (consolidado para Python; los playbooks siguen usando `group_vars/host_vars`).
- Nuevas carpetas: `respaldos/`, `evidencias/`, `datos/`, `documentacion/`, `python/` (ya existÃ­a).
- `datos/gen_respaldos.py`: solo auxiliar para generar evidencia de ejemplo; no es parte de la auditorÃ­a.
