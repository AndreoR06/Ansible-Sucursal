#!/bin/bash
# Ejecuta la automatizacion archivo por archivo en orden
# Uso: ./ejecutar_todo.sh  (o ./ejecutar_todo.sh --check para simular)
set -e
cd "$(dirname "$0")"
EXTRA="$1"
echo "== Inventario =="
ansible-inventory --graph
echo ""
echo "== [1/5] VLANs =="
ansible-playbook playbooks/vlans.yml $EXTRA
echo ""
echo "== [2/5] Puertos trunk/acceso + gestion =="
ansible-playbook playbooks/puertos.yml $EXTRA
echo ""
echo "== [3/5] R-SUC-01 + DHCP =="
ansible-playbook playbooks/router.yml $EXTRA
echo ""
echo "== [4/5] Respaldos =="
ansible-playbook playbooks/respaldos.yml $EXTRA
echo ""
echo "== [5/5] Verificacion =="
ansible-playbook playbooks/verificar.yml $EXTRA
echo ""
echo "TODO OK"
