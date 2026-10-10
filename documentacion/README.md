# Documentación de prototipos — Sucursal 01 / 02

Automatización con Ansible + análisis con Python. Ansible consulta, configura y
obtiene evidencia de los equipos; Python analiza, organiza o produce información
(nunca configura equipos ni abre SSH, salvo el prototipo 5, que solo ejecuta el
`ping` del sistema como medición).

Estado de las evidencias descritas aquí: verificado contra los archivos reales
del repo el 10-oct-2026. Lo que aún no existe se marca como PENDIENTE.

## Prototipo 1 — Validación antes/después de un cambio aprobado

- Propósito: demostrar (a) que el cambio solicitado sí ocurrió y (b) que los
  elementos críticos no se alteraron, comparando evidencia ANTES contra DESPUÉS.
- Entradas:
  - `datos/cambio_aprobado.yml` (CHG-001: `SW-SUC-01-CORE Gi0/1`,
    `PC3 - VLAN10` → `PC3 - VLAN10 - CHG-001`; valor anterior confirmado en
    `variables/sucursal_01.yml`).
  - `variables/sucursal_01.yml` (qué es crítico).
  - Evidencia `evidencias/antes/` y `evidencias/despues/` que obtiene Ansible
    con `playbooks/evidencia_estado.yml -e fase=antes|despues` (switches: running,
    vlan brief, interfaces trunk, ip interface brief; router: running, ip brief,
    ip route). `playbooks/cambio_controlado.yml` aplica solo la descripción
    (`ios_config`, `save_when: modified`, idempotente).
- Salida: pantalla y `evidencias/validacion_cambio.txt` (PENDIENTE: requiere
  ejecución en campo). Ejemplo real (prueba sintética en temporal):
  `Cambio aplicado: SI, Críticos sin cambios: 51 de 51, CUMPLE, salida 0`; con
  nativa de troncal cambiada e IP de Vlan99 borrada: `48 de 51` con 3
  diferencias y salida 1.
- Conclusión: el programa distingue el cambio aprobado de cualquier alteración
  crítica o colateral (otra diferencia en la misma interfaz también es fallo).
- Complementariedad: Ansible toma la evidencia y aplica el cambio; Python decide
  si el cambio es válido comparando ambas fases.

## Prototipo 2 — Generación de datos para la Sucursal 02

- Propósito: calcular el direccionamiento VLSM de una sede nueva sin tocar nada
  de la Sucursal 01 ni el inventario.
- Entradas: `datos/sucursal_02_entrada.yml` (bloque `10.20.0.0/23`; VLAN 10/50,
  20/25, 99/10 dispositivos).
- Salida: `variables/sucursal_02_generada.yml` (lista para Ansible) y resumen en
  pantalla + `evidencias/plan_sucursal_02.txt`. Resultado real generado:
  `10.20.0.0/26 gw 10.20.0.1` (62 útiles), `10.20.0.64/27 gw 10.20.0.65` (30),
  `10.20.0.96/28 gw 10.20.0.97` (14); libre de `10.20.0.112` a `10.20.1.255`.
  Casos de error y límite probados en temporal (bloque que no cabe → salida 2;
  62 dispositivos → `/25`).
- Conclusión: el cálculo coincide con el diseño esperado y el programa se niega
  con un error claro cuando el bloque no alcanza.
- Complementariedad: Python produce variables; Ansible las consumirá después
  (este prototipo no configura nada).

## Prototipo 3 — Auditoría de flota (ampliación de `auditar_sucursal.py`)

- Propósito: auditar R-SUC-01, CORE y ACC1 por defecto (`--todos` suma ACC2/ACC3)
  en version, VLANs, accesos, troncales (running y brief), gestión, gateway,
  subinterfaces, brief y rutas. No modifica `auditar_sucursal.py`; importa sus
  parsers.
- Entradas: `variables/sucursal_01.yml` y `respaldos/`. PASO 0 real: solo hay
  9 de 24 archivos (running + vlan-brief; faltan version, trunk-brief, ip-brief
  e ip-route). `playbooks/respaldos.yml` se amplió para obtenerlos todos
  (sin ejecutar).
- Salida: `evidencias/auditoria_flota.csv` (columnas exactas
  `equipo,condicion_evaluada,resultado,evidencia_usada,detalle_diferencia`) y
  tabla en `evidencias/auditoria_flota.txt` (ambos PENDIENTES hasta regenerar
  evidencia). Pruebas en temporal: base real 21/42 salida 1; copia completada
  42/42 salida 0; con 4 fallos inyectados, los 5 NO CUMPLE con su detalle.
- Conclusión: con la evidencia actual el resultado es NO CUMPLE por archivos
  faltantes y por SVI/gateway ausentes en los running de ejemplo; el auditor no
  omite nada.
- Complementariedad: Ansible debe completar la evidencia (`respaldos.yml`);
  Python la convierte en cumplimiento por equipo.

## Prototipo 4 — Relación de fuentes (IPAM simulado)

- Propósito: conciliar el "IPAM" (JSON local, sin API) con YAML, `group_vars`,
  inventario y respaldos, en COINCIDENCIAS / FALTANTES / DIFERENCIAS.
- Entradas: `datos/ipam_sucursal_01.json` (coherente con el repo) y
  `datos/ipam_sucursal_01_con_errores.json` (4 errores deliberados, rotulados en
  el propio archivo y en el README raíz).
- Salida: pantalla y `evidencias/validacion_inventario.txt` (real: 15
  coincidencias, 4 faltantes, 0 diferencias, salida 1; los faltantes son huecos
  de evidencia de switches, no del IPAM). Con errores (temporal): gateway VLAN 20
  y gestión ACC1 en DIFERENCIAS con valores por fuente; ACC3 y ACC9 en FALTANTES.
- Conclusión: las fuentes gestionadas (IPAM/YAML/inventario) coinciden al 100 %;
  lo único pendiente es completar la evidencia de switches.
- Complementariedad: Ansible aporta la evidencia; Python cruza las cuatro
  fuentes con `ipaddress` y señala dónde falta o difiere cada dato.

## Prototipo 5 — Medición de conectividad de gestión

- Propósito: medir (no configurar) con el `ping` del sistema y clasificar
  `cumple` si pérdida = 0 % y promedio ≤ 50 ms (`UMBRAL_LATENCIA_MS`,
  `PERDIDA_MAXIMA` como constantes).
- Entradas: destinos de `inventario/hosts.yml` (los 3 del enunciado;
  `--todos` agrega ACC2/ACC3; `--destino NOMBRE=IP` para pruebas).
- Salida: `evidencias/mediciones_conectividad.csv` (historial, encabezado solo
  al crear) y `evidencias/mediciones_conectividad.txt` (ambos PENDIENTES: la
  medición real la ejecuta el usuario en el servidor). Pruebas: 9/9 unitarias
  del parser (Linux/Windows EN-ES, 0 %/parcial/100 %, umbral, ilegible) y flujo
  con `prueba=127.0.0.1` en temporal → `cumple`, salida 0.
- Conclusión: el parser soporta ambos sistemas e idiomas y nunca omite un
  destino (ilegible = `no cumple` con detalle).
- Complementariedad: Python ejecuta la medición del SO; Ansible no interviene
  (no hay nada que configurar, solo observar).

## Estructura del proyecto y orden de ejecución

```text
ansible.cfg / inventario/hosts.yml (+ group_vars, host_vars)
variables/sucursal_01.yml / variables/sucursal_02_generada.yml
playbooks/: vlans, puertos, router, verificar, site, respaldos,
  evidencia_estado, cambio_controlado
python/: auditar_sucursal, validar_cambio, generar_sucursal_02,
  auditar_flota, validar_inventario, medir_conectividad
datos/: cambio_aprobado, sucursal_02_entrada, ipam_*.json, gen_respaldos
respaldos/ / evidencias/ / documentacion/
```

Orden (Ansible lo ejecuta el usuario; Python no configura):

```bash
ansible-playbook playbooks/site.yml                 # configura sede 01
ansible-playbook playbooks/respaldos.yml            # evidencia flota (proto 3)
ansible-playbook playbooks/evidencia_estado.yml -e fase=antes
ansible-playbook playbooks/cambio_controlado.yml    # 2.ª vez: changed=0
ansible-playbook playbooks/evidencia_estado.yml -e fase=despues
python3 python/auditar_sucursal.py
python3 python/validar_cambio.py                    # prototipo 1
python3 python/generar_sucursal_02.py               # prototipo 2
python3 python/auditar_flota.py                     # prototipo 3
python3 python/validar_inventario.py                # prototipo 4
python3 python/medir_conectividad.py                # prototipo 5 (en el servidor)
```

## Cómo se cumplen las reglas

- Python no configura equipos ni abre SSH: solo lee archivos, calcula y ejecuta
  el `ping` del sistema (prototipo 5). Verificable en cada programa.
- Solo biblioteca estándar + PyYAML; rutas desde `Path(__file__).parent.parent`.
- Evidencia en texto/JSON/CSV dentro del repo (`respaldos/`, `evidencias/`,
  `datos/`); salidas 0/1/2 y faltantes siempre explícitos.
- Sin secretos: credenciales por vault (`vault_ansible_password`), `.gitignore`
  excluye vault/claves; revisión del 10-oct-2026 limpia en los archivos nuevos.

## Conclusión técnica general

Borrador: en una organización con varias sedes, Ansible aporta más valor que
Python, porque es el que elimina la deriva de configuración: aplica el mismo
diseño en cada sede de forma idempotente y recoge evidencia homogénea, que es
la condición previa para escalar. Python multiplica ese valor al convertir la
evidencia en cumplimiento demostrable (auditorías, validación de cambios,
VLSM, conciliación IPAM, mediciones), pero sin la base estandarizada de Ansible
solo auditaría desorden. Por eso la prioridad de inversión es Ansible primero y
Python como capa de verificación continua.
<!-- REVISAR: confirmar postura -->

## Entregables y ubicación

- Prototipo 1: `datos/cambio_aprobado.yml`, `playbooks/evidencia_estado.yml`,
  `playbooks/cambio_controlado.yml`, `python/validar_cambio.py`,
  `evidencias/validacion_cambio.txt` (PENDIENTE).
- Prototipo 2: `datos/sucursal_02_entrada.yml`, `python/generar_sucursal_02.py`,
  `variables/sucursal_02_generada.yml`, `evidencias/plan_sucursal_02.txt`.
- Prototipo 3: `python/auditar_flota.py`, `playbooks/respaldos.yml` (ampliado),
  `evidencias/auditoria_flota.csv` + `.txt` (PENDIENTES).
- Prototipo 4: `datos/ipam_sucursal_01.json`,
  `datos/ipam_sucursal_01_con_errores.json`, `python/validar_inventario.py`,
  `evidencias/validacion_inventario.txt`.
- Prototipo 5: `python/medir_conectividad.py`,
  `evidencias/mediciones_conectividad.csv` + `.txt` (PENDIENTES).
- Base previa: `python/auditar_sucursal.py`, `evidencias/auditoria_python.txt`.
