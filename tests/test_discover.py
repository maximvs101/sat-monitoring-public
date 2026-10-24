import ipaddress
import socket
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

from monitoring.discover import parse_ifconfig, parse_ipconfig, probe_fsh, probe_ird, scan, scan_hosts
from tests.fake_scpi import FakeScpi

IFCONFIG = """lo0: flags=8049<UP,LOOPBACK,RUNNING,MULTICAST> mtu 16384
\tinet 127.0.0.1 netmask 0xff000000
en0: flags=8863<UP,BROADCAST,SMART,RUNNING,SIMPLEX,MULTICAST> mtu 1500
\tinet 10.10.1.190 netmask 0xffffff00 broadcast 10.10.1.255
en5: flags=8863<UP,BROADCAST,SMART,RUNNING,SIMPLEX,MULTICAST> mtu 1500
\tinet 169.254.3.4 netmask 0xffff0000 broadcast 169.254.255.255
en3: flags=8863<UP,BROADCAST,SMART,RUNNING,SIMPLEX,MULTICAST> mtu 1500
\tinet 172.16.4.20 netmask 0xffff0000 broadcast 172.16.255.255
"""

IPCONFIG_EN = """
Windows IP Configuration


Ethernet adapter Ethernet:

   Connection-specific DNS Suffix  . :
   Link-local IPv6 Address . . . . . : fe80::1c2d:3e4f:5a6b:7c8d%12
   IPv4 Address. . . . . . . . . . . : 192.168.10.20
   Subnet Mask . . . . . . . . . . . : 255.255.255.0
   Default Gateway . . . . . . . . . : 192.168.10.1

Ethernet adapter Ethernet 2:

   Autoconfiguration IPv4 Address. . : 169.254.12.7
   Subnet Mask . . . . . . . . . . . : 255.255.0.0
   Default Gateway . . . . . . . . . :

Wireless LAN adapter Wi-Fi:

   IPv4 Address. . . . . . . . . . . : 10.20.0.15(Preferred)
   Subnet Mask . . . . . . . . . . . : 255.255.0.0
   Default Gateway . . . . . . . . . : 10.20.0.1
"""

IPCONFIG_FR = """
Configuration IP de Windows


Carte Ethernet Ethernet :

   Suffixe DNS propre à la connexion. . . :
   Adresse IPv6 de liaison locale. . . . .: fe80::1c2d:3e4f:5a6b:7c8d%12
   Adresse IPv4. . . . . . . . . . . . . .: 192.168.10.20
   Masque de sous-réseau. . . . . . . . . : 255.255.255.0
   Passerelle par défaut. . . . . . . . . : 192.168.10.1
"""

STATUS = b"""<response command='status' uuid='x' status='ok'><system><systemName>IRD 5</systemName>
<vendorName>Ateme</vendorName><modelName>DR5000</modelName><id>1000-00002</id></system></response>"""
REFRESH = (Path(__file__).parent / "fixtures" / "ird_refresh.xml").read_bytes()


class FakeIrdHttp:
    def __init__(self):
        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
                self.send_response(200)
                self.end_headers()
                self.wfile.write(STATUS if b"status" in body else REFRESH)

            def log_message(self, *a):
                pass

        self.httpd = HTTPServer(("127.0.0.1", 0), Handler)
        self.port = self.httpd.server_port
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def stop(self):
        self.httpd.shutdown()
        self.httpd.server_close()


class NotHttp:
    """Port 80 d'un appareil qui ne parle pas HTTP (caméra, automate…) : une bannière puis la fermeture."""

    def __init__(self):
        self.sock = socket.socket()
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen()
        self.port = self.sock.getsockname()[1]
        threading.Thread(target=self._serve, daemon=True).start()

    def _serve(self):
        while True:
            try:
                conn, _ = self.sock.accept()
            except OSError:
                return
            try:
                conn.recv(4096)
                conn.sendall(b"SSH-2.0-OpenSSH_8.0\r\n")
            finally:
                conn.close()

    def stop(self):
        self.sock.close()


class DiscoverTest(unittest.TestCase):
    def test_parse_ifconfig(self):
        nets = parse_ifconfig(IFCONFIG)
        # Ni boucle locale ni lien local ; un réseau plus large qu'un /24 est réduit au /24 du Mac.
        self.assertEqual(nets, [ipaddress.ip_network("10.10.1.0/24"), ipaddress.ip_network("172.16.4.0/24")])

    def test_parse_ipconfig_english_and_french(self):
        # Windows : adresse puis masque, quelle que soit la langue ; lien local écarté, réseau large réduit au /24.
        self.assertEqual(parse_ipconfig(IPCONFIG_EN),
                         [ipaddress.ip_network("192.168.10.0/24"), ipaddress.ip_network("10.20.0.0/24")])
        self.assertEqual(parse_ipconfig(IPCONFIG_FR), [ipaddress.ip_network("192.168.10.0/24")])

    def test_probe_fsh(self):
        srv = FakeScpi({"*IDN?": "Rohde&Schwarz,FSH4,100000/000,V1.20"})
        self.addCleanup(srv.stop)
        found = probe_fsh("127.0.0.1", srv.port, timeout_s=0.5)
        self.assertEqual(found, {"host": "127.0.0.1", "port": srv.port, "model": "FSH4", "detail": "100000/000, firmware V1.20"})

    def test_probe_fsh_rejects_other_instrument(self):
        srv = FakeScpi({"*IDN?": "Keysight,N9320B,1234,A.01"})
        self.addCleanup(srv.stop)
        self.assertIsNone(probe_fsh("127.0.0.1", srv.port, timeout_s=0.5))

    def test_probe_fsh_silent_port(self):
        srv = FakeScpi({})
        self.addCleanup(srv.stop)
        self.assertIsNone(probe_fsh("127.0.0.1", srv.port, timeout_s=0.2))

    def test_probe_ird_not_http(self):
        srv = NotHttp()
        self.addCleanup(srv.stop)
        self.assertIsNone(probe_ird("127.0.0.1", srv.port, timeout_s=1.0))

    def test_probe_ird(self):
        srv = FakeIrdHttp()
        self.addCleanup(srv.stop)
        found = probe_ird("127.0.0.1", srv.port, timeout_s=1.0)
        self.assertEqual(found["name"], "IRD 5")
        self.assertEqual(found["serial"], "1000-00002")
        self.assertEqual(found["model"], "DR5000")
        self.assertEqual(found["input"], "satellite IF 1470,8 MHz, verrouillé")

    def test_probe_ird_not_ateme(self):
        self.assertIsNone(probe_ird("127.0.0.1", 9, timeout_s=0.3))

    def test_scan_hosts_finds_both(self):
        fsh = FakeScpi({"*IDN?": "Rohde&Schwarz,FSH4,100000/000,V1.20"})
        ird = FakeIrdHttp()
        self.addCleanup(fsh.stop)
        self.addCleanup(ird.stop)
        found = scan_hosts(["127.0.0.1", "127.0.0.2"], fsh_port=fsh.port, http_port=ird.port, timeout_s=0.3)
        self.assertEqual([f["host"] for f in found["fsh"]], ["127.0.0.1"])
        self.assertEqual([i["host"] for i in found["ird"]], ["127.0.0.1"])

    def test_scan_reports_networks(self):
        found = scan(networks=[ipaddress.ip_network("127.0.0.0/30")], fsh_port=9, http_port=9, timeout_s=0.1)
        self.assertEqual(found["networks"], ["127.0.0.0/30"])
        self.assertEqual(found["fsh"], [])


if __name__ == "__main__":
    unittest.main()
