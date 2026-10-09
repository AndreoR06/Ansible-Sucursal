#!/bin/bash
# Ejecuta la automatizacion archivo por archivo en orden
# Uso: ./ejecutar_todo.sh  (o ./ejecutar_todo.sh --check para simular)
set -e
cd "$(dirname "$0")"
EXTRA="$1"
echo "== Inventario =="
ansible-inventory --graph
echo ""
echo "== [1/4] VLANs =="
ansible-playbook playbooks/vlans.yml $EXTRA
echo ""
echo "== [2/4] Puertos trunk/acceso =="
ansible-playbook playbooks/puertos.yml $EXTRA
echo ""
echo "== [3/4] Router-MB + DHCP =="
ansible-playbook playbooks/router.yml $EXTRA
echo ""
echo "== [4/4] Verificacion =="
ansible-playbook playbooks/verificar.yml $EXTRA
echo ""
echo "TODO OK"
