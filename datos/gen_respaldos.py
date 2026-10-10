import os

# Datos base (coherentes con variables/sucursal_01.yml)
# Formato acceso: (interfaz, vlan, descripcion)
# Formato trunk: (interfaz, descripcion)
switches = {
    "SW-SUC-01-CORE": {
        "acceso": [("GigabitEthernet0/0",10,"PC2 - VLAN10"),("GigabitEthernet0/1",10,"PC3 - VLAN10"),("GigabitEthernet0/2",20,"PC5 - VLAN20"),("GigabitEthernet0/3",20,"PC4 - VLAN20"),("GigabitEthernet1/0",99,"PC6 - VLAN99")],
        "trunk": [("GigabitEthernet1/1","Hacia R-SUC-01 - Troncal Nativa99"),("GigabitEthernet1/2","Hacia SW-SUC-01-ACC1 Gi0/0 - Troncal Nativa99")],
    },
    "SW-SUC-01-ACC1": {
        "acceso": [("GigabitEthernet0/1",10,"PC7 - VLAN10"),("GigabitEthernet0/2",99,"Switch1 - VLAN99"),("GigabitEthernet0/3",20,"PC8 - VLAN20")],
        "trunk": [("GigabitEthernet0/0","Hacia SW-SUC-01-CORE Gi1/2 - Troncal Nativa99"),("GigabitEthernet1/0","Hacia SW-SUC-01-ACC2 Gi1/1 - Troncal Nativa99")],
    },
    "SW-SUC-01-ACC2": {
        "acceso": [("GigabitEthernet0/0",10,"PC12 - VLAN10"),("GigabitEthernet0/1",10,"PC14 - VLAN10"),("GigabitEthernet0/2",20,"PC10 - VLAN20"),("GigabitEthernet0/3",20,"PC11 - VLAN20"),("GigabitEthernet1/0",99,"PC13 - VLAN99")],
        "trunk": [("GigabitEthernet1/1","Hacia SW-SUC-01-ACC1 Gi1/0 - Troncal Nativa99"),("GigabitEthernet1/2","Hacia SW-SUC-01-ACC3 Gi0/0 - Troncal Nativa99")],
    },
    "SW-SUC-01-ACC3": {
        "acceso": [("GigabitEthernet0/1",10,"PC16 - VLAN10"),("GigabitEthernet0/2",99,"Switch2 - VLAN99"),("GigabitEthernet0/3",20,"PC17 - VLAN20")],
        "trunk": [("GigabitEthernet0/0","Hacia SW-SUC-01-ACC2 Gi1/2 - Troncal Nativa99")],
    },
}

from collections import defaultdict

def short(iface):
    return iface.replace("GigabitEthernet","Gi").replace("FastEthernet","Fa")

def running_switch(host, acceso, trunk):
    l=[]
    l.append("! Evidencia de ejemplo - running-config (sin secretos)")
    l.append(f"hostname {host}")
    l.append("!")
    l.append("vlan 10")
    l.append(" name USUARIOS_VLAN10")
    l.append("!")
    l.append("vlan 20")
    l.append(" name USUARIOS_VLAN20")
    l.append("!")
    l.append("vlan 99")
    l.append(" name GESTION")
    l.append("!")
    for iface, vlan, desc in acceso:
        l.append(f"interface {iface}")
        l.append(f" description {desc}")
        l.append(" switchport mode access")
        l.append(f" switchport access vlan {vlan}")
        l.append(" spanning-tree portfast")
        l.append(" no shutdown")
        l.append("!")
    for iface, desc in trunk:
        l.append(f"interface {iface}")
        l.append(f" description {desc}")
        l.append(" switchport trunk encapsulation dot1q")
        l.append(" switchport mode trunk")
        l.append(" switchport trunk native vlan 99")
        l.append(" switchport trunk allowed vlan 10,20,99")
        l.append(" no shutdown")
        l.append("!")
    l.append("line con 0")
    l.append(" logging synchronous")
    l.append("line vty 0 4")
    l.append(" transport input ssh")
    l.append("!")
    l.append("end")
    return "\n".join(l)+"\n"

def vlan_brief(host, acceso):
    from collections import defaultdict as dd
    grp=dd(list)
    for iface,vlan,desc in acceso:
        grp[vlan].append(short(iface))
    l=[]
    l.append(f"! Evidencia de ejemplo - show vlan brief ({host})")
    l.append("VLAN Name                             Status    Ports")
    l.append("---- -------------------------------- --------- -------------------------------")
    l.append("1    default                          active    Gi0/4, Gi0/5, Gi0/6, Gi0/7")
    for vid in [10,20,99]:
        nombre={"10":"USUARIOS_VLAN10","20":"USUARIOS_VLAN20","99":"GESTION"}[str(vid)]
        puertos=", ".join(sorted(grp[vid]))
        l.append(f"{vid:<4} {nombre:<32} active    {puertos}")
    l.append("1002 fddi-default                     act/unsup")
    l.append("1003 token-ring-default               act/unsup")
    l.append("1004 fddinet-default                  act/unsup")
    l.append("1005 trnet-default                    act/unsup")
    return "\n".join(l)+"\n"

os.makedirs("respaldos", exist_ok=True)
for host, d in switches.items():
    rc = running_switch(host, d["acceso"], d["trunk"])
    vb = vlan_brief(host, d["acceso"])
    open(f"respaldos/{host}-running-config.txt","w",encoding="utf-8").write(rc)
    open(f"respaldos/{host}-show-vlan-brief.txt","w",encoding="utf-8").write(vb)
    print(f"OK {host}")

rc_r = """! Evidencia de ejemplo - running-config R-SUC-01 (sin secretos)
hostname R-SUC-01
!
interface GigabitEthernet0/1
 no shutdown
!
interface GigabitEthernet0/1.10
 encapsulation dot1Q 10
 ip address 192.168.10.1 255.255.255.0
 no shutdown
!
interface GigabitEthernet0/1.20
 encapsulation dot1Q 20
 ip address 192.168.20.1 255.255.255.0
 no shutdown
!
interface GigabitEthernet0/1.99
 encapsulation dot1Q 99 native
 ip address 192.168.99.1 255.255.255.0
 no shutdown
!
ip dhcp excluded-address 192.168.10.1 192.168.10.30
ip dhcp excluded-address 192.168.20.1 192.168.20.30
ip dhcp excluded-address 192.168.99.1 192.168.99.30
!
ip dhcp pool POOL_VLAN10
 network 192.168.10.0 255.255.255.0
 default-router 192.168.10.1
 dns-server 8.8.8.8
 lease 7
!
ip dhcp pool POOL_VLAN20
 network 192.168.20.0 255.255.255.0
 default-router 192.168.20.1
 dns-server 8.8.8.8
 lease 7
!
ip dhcp pool POOL_VLAN99
 network 192.168.99.0 255.255.255.0
 default-router 192.168.99.1
 dns-server 8.8.8.8
 lease 7
!
end
"""
open("respaldos/R-SUC-01-running-config.txt","w",encoding="utf-8").write(rc_r)
print("OK R-SUC-01")

# hola bb ya que contigo no sirve la lavia y te crees muy sabia pero vas acaer te lo digo muger
# eaaaaa