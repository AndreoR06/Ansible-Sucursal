# Sucursal 01 — Automatización con Ansible + análisis con Python

> **Resumen.** Ansible **actúa** sobre la red: configura, respalda y obtiene evidencias de los equipos. Python **analiza** esa evidencia: valida, calcula, audita, relaciona fuentes y mide. Python no configura routers ni switches, y toda evidencia, dato de entrada y resultado queda dentro de este repositorio y registrado en Git.
>
> Contenido: **1.** función de cada herramienta · **2.** conclusión técnica de los cinco prototipos · **3.** cuál aporta más valor a una organización con varias sedes · después, el detalle del proyecto (estructura, requisitos y ejecución). La documentación ampliada de los prototipos está en `documentacion/README.md`.

---

## 1. Función de Ansible y Python dentro de la solución

| | **Ansible** | **Python** |
|---|---|---|
| **Papel** | Consultar, configurar y obtener evidencias de los dispositivos | Analizar, organizar o producir información |
| **¿Toca los equipos?** | Sí (SSH con `cisco.ios`) | No. Solo lee archivos (el prototipo 5 ejecuta el `ping` del sistema, que es una medición, no una configuración) |
| **Entrada** | Inventario, variables YAML, playbooks | Diseño YAML, JSON de IPAM y evidencias guardadas por Ansible |
| **Salida** | Configuración aplicada y archivos de evidencia en `respaldos/` y `evidencias/` | Tablas, CSV, YAML generado e informes con resultado explícito |
| **Pregunta que responde** | «¿Cómo dejo la red en el estado deseado y qué dicen los equipos?» | «¿Lo que dicen los equipos coincide con el diseño y con las demás fuentes?» |

**Ansible (aplica, respalda, obtiene evidencia):**
- Aplica la configuración deseada a switches y router (VLANs, puertos acceso/trunk, router-on-a-stick + DHCP).
- Guarda configuración (`save_when: modified`) y obtiene evidencia (`show running-config`, `show version`, `show vlan brief`, `show interfaces trunk`, `show ip interface brief`, `show ip route`) en `respaldos/`.
- Es idempotente: al ejecutarlo de nuevo con el diseño ya cumplido no realiza cambios (`changed=0`).

**Python (analiza la evidencia y determina cumplimiento):**
- No abre sesiones SSH ni configura dispositivos; solo lee archivos.
- Lee el diseño esperado desde `variables/sucursal_01.yml` y la evidencia desde `respaldos/` y `evidencias/`.
- Audita VLAN 10, 20 y 99 (existencia, nombre, estado `active`), puertos de acceso (VLAN + descripción) y troncales (modo trunk, nativa 99, permitidas 10,20,99, descripción), más subinterfaces del router si hay evidencia.
- Muestra tabla por elemento con `CUMPLE / NO CUMPLE` y, si no cumple, indica esperado vs encontrado.
- Guarda la misma salida en `evidencias/`.

**Flujo de trabajo:** `Ansible obtiene la evidencia → se guarda en el repositorio → Python la analiza → el resultado se guarda y se versiona en Git`.

---

## 2. Conclusión técnica por prototipo

### Prototipo 1 — Validación antes y después de un cambio (`python/validar_cambio.py`)

- **Propósito:** demostrar que un cambio aprobado ocurrió y que los elementos críticos conservaron su estado. No basta con ejecutar el cambio; hay que probar que no rompió nada.
- **Datos:** `datos/cambio_aprobado.yml` (cambio CHG-001: en `SW-SUC-01-CORE`, interfaz Gi0/1, la descripción pasa de `PC3 - VLAN10` a `PC3 - VLAN10 - CHG-001`, más la lista de elementos críticos), `variables/sucursal_01.yml` y la evidencia en `evidencias/antes/` y `evidencias/despues/`.
- **Resultado:** el programa emite, por condición, uno de estos veredictos: *cambio aplicado correctamente*, *elemento crítico sin cambios* o *diferencia detectada* (con valor antes y después). Los elementos críticos son las VLAN 10, 20 y 99 (en especial la 99), la IP de `Vlan99` y el `ip default-gateway` de cada switch, los troncales, las subinterfaces y rutas del router, y los demás puertos de acceso. Cualquier otra diferencia en la interfaz del cambio también se reporta. Salida en `evidencias/validacion_cambio.txt`.
  - **Resultado de la ejecución real:** _[completar con el resumen de `python3 python/validar_cambio.py`: cambio aplicado sí/no y N de M críticos sin cambios]_
- **Complementariedad:** Ansible toma la evidencia antes y después (`evidencia_estado.yml`) y aplica el cambio de forma idempotente (`cambio_controlado.yml`); Python compara ambos estados y decide si el cambio fue seguro. Ansible produce el hecho; Python lo verifica.

### Prototipo 2 — Generación de datos para una nueva sede (`python/generar_sucursal_02.py`)

- **Propósito:** producir de forma automática el plan de direccionamiento de la Sucursal 02 sin configurar ningún equipo real.
- **Datos:** `datos/sucursal_02_entrada.yml` con el bloque `10.20.0.0/23` y los dispositivos por VLAN: 50 en ADMINISTRACION (VLAN 10), 25 en OPERACION (VLAN 20) y 10 en GESTION (VLAN 99).
- **Resultado:** el programa suma la puerta de enlace a cada VLAN, elige la máscara más pequeña que soporta esa cantidad y asigna las subredes sin solapamiento (VLSM, de mayor a menor). Genera `variables/sucursal_02_generada.yml` y un resumen en pantalla y en `evidencias/plan_sucursal_02.txt`:

  | VLAN | Nombre | Red | Máscara | Gateway | Hosts previstos | Hosts utilizables |
  |---|---|---|---|---|---|---|
  | 10 | ADMINISTRACION | 10.20.0.0/26 | 255.255.255.192 | 10.20.0.1 | 50 | 62 |
  | 20 | OPERACION | 10.20.0.64/27 | 255.255.255.224 | 10.20.0.65 | 25 | 30 |
  | 99 | GESTION | 10.20.0.96/28 | 255.255.255.240 | 10.20.0.97 | 10 | 14 |

  Queda libre el rango `10.20.0.112` a `10.20.1.255`. Si los datos no caben en el bloque, el programa termina con un error que indica cuántas direcciones faltan.
- **Complementariedad:** Python calcula (aritmética de subredes que Ansible no hace bien); el YAML generado queda listo para que Ansible lo use como variables cuando la sede se construya de verdad. Python diseña, Ansible ejecutaría.

### Prototipo 3 — Auditoría de una flota pequeña (`python/auditar_flota.py`)

- **Propósito:** ampliar la auditoría inicial a `R-SUC-01`, `SW-SUC-01-CORE` y `SW-SUC-01-ACC1` (con `--todos`, también ACC2 y ACC3), tratando cada equipo como una muestra de una flota mayor.
- **Datos:** `variables/sucursal_01.yml` y, en `respaldos/`, `running-config`, `show version`, `show vlan brief`, `show interfaces trunk`, `show ip interface brief` y, en el router, `show ip route`.
- **Resultado:** `evidencias/auditoria_flota.csv` con las columnas `equipo, condicion_evaluada, resultado, evidencia_usada, detalle_diferencia`, más una tabla legible en `evidencias/auditoria_flota.txt`. Cuando una condición no cumple, el detalle indica «esperado X, encontrado Y». Una evidencia faltante cuenta como `NO CUMPLE`, nunca se omite.
  - **Resultado de la ejecución real:** _[completar con el resumen: total de condiciones, cuántas CUMPLE y cuántas NO CUMPLE por equipo]_
- **Complementariedad:** Ansible recolecta de forma uniforme las mismas salidas de todos los equipos (`respaldos.yml`); Python las compara con el diseño y entrega un informe estructurado que se puede filtrar, ordenar y comparar en el tiempo.

### Prototipo 4 — Relación de fuentes de información (`python/validar_inventario.py`)

- **Propósito:** reproducir la lógica de integración que se hace al consultar un IPAM, inventario, DNS, nube o firewall: cruzar fuentes y detectar dónde no coinciden.
- **Datos:** `datos/ipam_sucursal_01.json` (simula la respuesta de un IPAM por API; no es una API real), el diseño YAML (`variables/sucursal_01.yml`, `inventario/group_vars/`, `inventario/hosts.yml`) y la evidencia de Ansible en `respaldos/`. Para demostrar la detección existe `datos/ipam_sucursal_01_con_errores.json`, con **datos sintéticos de prueba**.
- **Resultado:** la salida separa tres secciones: **coincidencias**, **faltantes** (dato presente en una fuente y ausente en otra) y **diferencias** (valores distintos entre fuentes, con el valor de cada una). Con el archivo de prueba detecta los cuatro errores sembrados: gateway de la VLAN 20 (`.254` en el IPAM frente a `.1` en el diseño y el router), IP de gestión de `SW-SUC-01-ACC1`, `SW-SUC-01-ACC3` ausente en el IPAM y un `SW-SUC-01-ACC9` que no existe en el inventario. Salida en `evidencias/validacion_inventario.txt`.
  - **Resultado de la ejecución real con el IPAM limpio:** _[completar con coincidencias / faltantes / diferencias]_
- **Complementariedad:** Ansible aporta el estado real de los equipos (la evidencia) y el inventario; Python lo relaciona con las otras fuentes, que están en formatos distintos (YAML, JSON, texto de CLI), algo que un playbook no hace con comodidad.

### Prototipo 5 — Medición de conectividad de gestión (`python/medir_conectividad.py`)

- **Propósito:** obtener un registro y una interpretación básica de la calidad de la conexión de gestión hacia los equipos, desde el servidor de automatización.
- **Datos:** direcciones de gestión leídas de `inventario/hosts.yml` (`R-SUC-01`, `SW-SUC-01-CORE`, `SW-SUC-01-ACC1`; con `--todos`, también ACC2 y ACC3) y diez pruebas ICMP por destino.
- **Resultado:** `evidencias/mediciones_conectividad.csv` con fecha, destino, paquetes enviados y recibidos, pérdida y latencia mínima, promedio y máxima. Cada destino se clasifica como **cumple** si no tiene pérdida y la latencia promedio es menor o igual a 50 ms; en caso contrario, **no cumple**, con el detalle de la condición que falló.
  - **Resultado de la ejecución real:** _[completar con la tabla de destinos y su clasificación]_
- **Complementariedad:** Ansible depende de esa conectividad para poder gestionar los equipos, y por eso la mide Python, que registra y clasifica la calidad del enlace de gestión. Ansible usa el canal; Python comprueba si está en condiciones.

---

## 3. ¿Cuál aporta más valor a una organización con múltiples sedes?

**Ansible aporta más valor como base, y Python lo multiplica.** Si hubiera que elegir una sola herramienta para una organización con muchas sedes, la elección sería Ansible, por estas razones:

1. **Escala con el mismo trabajo.** Un playbook y un inventario sirven para una sede o para cien: agregar una sede es agregar sus variables, no escribir otro procedimiento. El prototipo 2 lo muestra: genera las variables de la Sucursal 02 y los mismos playbooks de la Sucursal 01 se podrían reutilizar.
2. **Garantiza consistencia.** El estado deseado está declarado y es idempotente, lo que elimina la deriva de configuración y los errores de captura manual, que son el principal riesgo de operar muchas sedes.
3. **Es la única herramienta que cambia el estado de la red y obtiene la evidencia.** Python no puede analizar lo que Ansible no recolectó, mientras que Ansible funciona sin Python.
4. **Reduce tiempo y riesgo operativo.** Un cambio aprobado se aplica y se respalda de forma repetible y auditable en todas las sedes.

**Matiz importante:** el valor de Python crece con el número de sedes. Revisar a mano la configuración de cientos de equipos no es viable. Python permite auditar la flota en CSV, validar cada cambio contra un estado crítico, planificar direccionamiento y cruzar la red real con el IPAM y el inventario. Una red configurada con Ansible pero sin verificación con Python es una red cuya conformidad se asume, no se demuestra. Por eso la solución completa es la combinación: **Ansible hace que la red esté como debe estar; Python demuestra que lo está.**

---

## Detalle del proyecto

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
