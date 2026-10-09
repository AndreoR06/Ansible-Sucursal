import os
import shutil
from collections import defaultdict

# Datos base (coherentes con variables/sucursal_01.yml)
switches = {
    "SW1": {
        "acceso": [("GigabitEthernet0/0",10,"PC-VLAN10-Datos"),("GigabitEthernet0/1",10,"PC-VLAN10-Datos"),("GigabitEthernet0/2",20,"PC-VLAN20-Otra_Red"),("GigabitEthernet0/3",20,"PC-VLAN20-Otra_Red"),("GigabitEthernet1/0",99,"ADMIN-VLAN99-Nativa")],
        "trunk": [("GigabitEthernet1/1",),("GigabitEthernet1/2",)],
    },
    "SW2": {
        "acceso": [("GigabitEthernet0/1",10,"PC-VLAN10-Datos"),("GigabitEthernet0/2",20,"PC-VLAN20-Otra_Red"),("GigabitEthernet0/3",99,"ADMIN-VLAN99-Nativa")],
        "trunk": [("GigabitEthernet0/0",),("GigabitEthernet1/0",)],
    },
    "SW3": {
        "acceso": [("GigabitEthernet0/0",10,"PC-VLAN10-Datos"),("GigabitEthernet0/1",10,"PC-VLAN10-Datos"),("GigabitEthernet0/2",20,"PC-VLAN20-Otra_Red"),("GigabitEthernet0/3",20,"PC-VLAN20-Otra_Red"),("GigabitEthernet1/0",99,"ADMIN-VLAN99-Nativa")],
        "trunk": [("GigabitEthernet1/1",),("GigabitEthernet1/2",)],
    },
    "SW4": {
        "acceso": [("GigabitEthernet0/1",10,"PC-VLAN10-Datos"),("GigabitEthernet0/2",20,"PC-VLAN20-Otra_Red"),("GigabitEthernet0/3",99,"ADMIN-VLAN99-Nativa")],
        "trunk": [("GigabitEthernet0/0",)],
    },
}

def short(iface):
    return iface.replace("GigabitEthernet","Gi").replace("FastEthernet","Fa")

def running_switch(host, acceso, trunk):
    l=[]
    l.append("! Evidencia de ejemplo - running-config (sin secretos)")
    l.append(f"hostname {host}")
    l.append("!")
    l.append("vlan 10")
    l.append(" name Datos")
    l.append("!")
    l.append("vlan 20")
    l.append(" name Otra_Red")
    l.append("!")
    l.append("vlan 99")
    l.append(" name Nativa")
    l.append("!")
    for iface, vlan, desc in acceso:
        l.append(f"interface {iface}")
        l.append(f" description {desc}")
        l.append(" switchport mode access")
        l.append(f" switchport access vlan {vlan}")
        l.append(" spanning-tree portfast")
        l.append(" no shutdown")
        l.append("!")
    for (iface,) in trunk:
        l.append(f"interface {iface}")
        l.append(" description Uplink-Trunk-Nativa99")
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
    grp=defaultdict(list)
    for iface,vlan,desc in acceso:
        grp[vlan].append(short(iface))
    l=[]
    l.append(f"! Evidencia de ejemplo - show vlan brief ({host})")
    l.append("VLAN Name                             Status    Ports")
    l.append("---- -------------------------------- --------- -------------------------------")
    l.append("1    default                          active    Gi0/4, Gi0/5, Gi0/6, Gi0/7")
    for vid in [10,20,99]:
        nombre={"10":"Datos","20":"Otra_Red","99":"Nativa"}[str(vid)]
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

rc_r = """! Evidencia de ejemplo - running-config Router_MB (sin secretos)
hostname Router_MB
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
ip dhcp excluded-address 192.168.10.1 192.168.10.10
ip dhcp excluded-address 192.168.20.1 192.168.20.10
ip dhcp excluded-address 192.168.99.1 192.168.99.20
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
line con 0
 logging synchronous
line vty 0 4
 transport input ssh
!
end
"""
open("respaldos/Router_MB-running-config.txt","w",encoding="utf-8").write(rc_r)
print("OK Router_MB")

shutil.copyfile("respaldos/SW1-running-config.txt","respaldos/SW-SUC-01-CORE-running-config.txt")
shutil.copyfile("respaldos/SW1-show-vlan-brief.txt","respaldos/SW-SUC-01-CORE-show-vlan-brief.txt")
shutil.copyfile("respaldos/SW2-running-config.txt","respaldos/SW-SUC-01-ACC1-running-config.txt")
shutil.copyfile("respaldos/SW2-show-vlan-brief.txt","respaldos/SW-SUC-01-ACC1-show-vlan-brief.txt")
shutil.copyfile("respaldos/Router_MB-running-config.txt","respaldos/R-SUC-01-running-config.txt")
print("OK alias CORE/ACC1/R")
