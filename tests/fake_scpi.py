"""Faux serveur SCPI local pour tester le client FSH4."""

import socket
import threading
import time


class FakeScpi:
    """Répond aux commandes selon `responses` ; None = ne répond jamais (comme le -113 du FSH4)."""

    def __init__(self, responses, delay_s=0.0):
        self.responses = dict(responses)
        self.delay_s = delay_s
        self.received = []
        self.connections = 0
        self._sock = socket.socket()
        self._sock.bind(("127.0.0.1", 0))
        self._sock.listen()
        self.port = self._sock.getsockname()[1]
        self._clients = []
        self._running = True
        threading.Thread(target=self._accept, daemon=True).start()

    def _accept(self):
        while self._running:
            try:
                conn, _ = self._sock.accept()
            except OSError:
                return
            self.connections += 1
            self._clients.append(conn)
            threading.Thread(target=self._serve, args=(conn,), daemon=True).start()

    def _serve(self, conn):
        buf = b""
        try:
            while True:
                chunk = conn.recv(4096)
                if not chunk:
                    return
                buf += chunk
                while b"\n" in buf:
                    line, buf = buf.split(b"\n", 1)
                    cmd = line.decode().strip()
                    self.received.append(cmd)
                    answer = self.responses.get(cmd)
                    if answer is None:
                        continue
                    time.sleep(self.delay_s)
                    conn.sendall((answer + "\n").encode())
        except OSError:
            return

    def drop_clients(self):
        for c in self._clients:
            try:
                c.shutdown(socket.SHUT_RDWR)
                c.close()
            except OSError:
                pass
        self._clients.clear()

    def stop(self):
        self._running = False
        self.drop_clients()
        self._sock.close()
