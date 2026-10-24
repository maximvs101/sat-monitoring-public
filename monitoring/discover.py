"""Recherche du FSH4 et des IRD Ateme sur les réseaux locaux de la machine (lecture seule)."""

import http.client
import ipaddress
import re
import socket
import subprocess
import sys
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor

from .ird import REFRESH_BODY, STATUS_BODY, IrdError, open_url, parse_refresh, xml_request

INET = re.compile(r"inet (\d+\.\d+\.\d+\.\d+) netmask (0x[0-9a-fA-F]+)")  # ifconfig (Mac, Linux)
IPV4 = re.compile(r":\s*(\d+\.\d+\.\d+\.\d+)")  # ipconfig (Windows)
MIN_PREFIXLEN = 24  # un réseau plus large est réduit au /24 de la machine pour rester rapide


def _add_network(networks: list, addr: str, prefixlen: int) -> None:
    """Réseau d'une adresse locale, ni boucle locale ni lien local, sans doublon."""
    ip = ipaddress.ip_address(addr)
    if ip.is_loopback or ip.is_link_local:
        return
    net = ipaddress.ip_network(f"{addr}/{max(prefixlen, MIN_PREFIXLEN)}", strict=False)
    if net not in networks:
        networks.append(net)


def parse_ifconfig(text: str) -> list:
    networks = []
    for addr, mask in INET.findall(text):
        _add_network(networks, addr, bin(int(mask, 16)).count("1"))
    return networks


def parse_ipconfig(text: str) -> list:
    """Sortie de `ipconfig` (Windows), quelle que soit la langue : chaque adresse IPv4 est suivie de son masque
    (la seule valeur commençant par 255.) ; la passerelle qui suit est ignorée."""
    networks, last = [], None
    for line in text.splitlines():
        m = IPV4.search(line)
        if not m:
            continue
        value = m.group(1)
        if value.startswith("255.") and last:
            _add_network(networks, last, ipaddress.ip_network(f"0.0.0.0/{value}").prefixlen)
            last = None
        else:
            last = value
    return networks


def local_networks() -> list:
    if sys.platform == "win32":
        out = subprocess.run(["ipconfig"], capture_output=True, text=True, errors="replace", timeout=5).stdout
        return parse_ipconfig(out)
    out = subprocess.run(["ifconfig"], capture_output=True, text=True, timeout=5).stdout
    return parse_ifconfig(out)


def _port_open(host: str, port: int, timeout_s: float) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout_s):
            return True
    except OSError:
        return False


def probe_fsh(host: str, port: int = 5555, timeout_s: float = 0.5) -> dict | None:
    """Demande *IDN? ; ne retient qu'un analyseur R&S de la famille FSH."""
    try:
        with socket.create_connection((host, port), timeout=timeout_s) as sock:
            sock.settimeout(timeout_s * 4)
            sock.sendall(b"*IDN?\n")
            buf = b""
            while not buf.endswith(b"\n"):
                chunk = sock.recv(1024)
                if not chunk:
                    break
                buf += chunk
    except OSError:
        return None
    parts = [p.strip() for p in buf.decode(errors="replace").split(",")]
    if len(parts) < 4 or not parts[0].startswith("Rohde") or not parts[1].upper().startswith("FSH"):
        return None
    return {"host": host, "port": port, "model": parts[1], "detail": f"{parts[2]}, firmware {parts[3]}"}


def _post(url: str, body: bytes, timeout_s: float) -> bytes:
    with open_url(xml_request(url, body), timeout=timeout_s) as resp:
        return resp.read()


def _describe_input(data: bytes) -> str:
    root = ET.fromstring(data)
    kind = root.findtext("probe/input/type", "").strip()
    if kind != "sat":
        return f"entrée {kind or 'inconnue'}"
    s = parse_refresh(data)
    freq = "?" if s.if_khz is None else f"{s.if_khz / 1000:.1f}".replace(".", ",")
    return f"satellite IF {freq} MHz, {'verrouillé' if s.locked else 'non verrouillé'}"


def probe_ird(host: str, port: int = 80, timeout_s: float = 1.0) -> dict | None:
    """Reconnaît un IRD Ateme par sa requête status, puis décrit son entrée active."""
    url = f"http://{host}/xml-rpc" if port == 80 else f"http://{host}:{port}/xml-rpc"
    try:
        root = ET.fromstring(_post(url, STATUS_BODY, timeout_s))
    except (urllib.error.URLError, OSError, ET.ParseError, http.client.HTTPException):  # port 80 qui ne parle pas HTTP
        return None
    if root.findtext("system/vendorName", "").strip() != "Ateme":
        return None
    found = {"host": host, "port": port, "url": url,
             "serial": root.findtext("system/id", "").strip(),
             "name": root.findtext("system/systemName", "").strip(),
             "model": root.findtext("system/modelName", "").strip()}
    try:
        found["input"] = _describe_input(_post(url, REFRESH_BODY, timeout_s * 3))
    except (urllib.error.URLError, OSError, ET.ParseError, http.client.HTTPException, IrdError, ValueError):
        found["input"] = "entrée inconnue"
    return found


def scan_hosts(hosts, fsh_port: int = 5555, http_port: int = 80, timeout_s: float = 0.5, workers: int = 64) -> dict:
    hosts = list(hosts)
    with ThreadPoolExecutor(workers) as pool:
        # Les deux ports de chaque adresse testés d'un seul balayage.
        pairs = [(h, p) for h in hosts for p in (fsh_port, http_port)]
        open_ports = {pair for pair, ok in zip(pairs, pool.map(lambda hp: _port_open(*hp, timeout_s), pairs)) if ok}
        fsh = pool.map(lambda h: probe_fsh(h, fsh_port, timeout_s), [h for h in hosts if (h, fsh_port) in open_ports])
        ird = pool.map(lambda h: probe_ird(h, http_port, timeout_s * 2), [h for h in hosts if (h, http_port) in open_ports])
        key = lambda d: ipaddress.ip_address(d["host"])
        return {"fsh": sorted(filter(None, fsh), key=key), "ird": sorted(filter(None, ird), key=key)}


def scan(networks=None, fsh_port: int = 5555, http_port: int = 80, timeout_s: float = 0.5) -> dict:
    networks = local_networks() if networks is None else networks
    hosts = [str(h) for net in networks for h in net.hosts()]
    found = scan_hosts(hosts, fsh_port, http_port, timeout_s)
    return {"networks": [str(n) for n in networks], **found}
