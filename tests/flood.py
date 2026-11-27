"""Faux appareil qui répond sans fin, sans jamais finir sa ligne (bornes de taille et de durée)."""

import socket
import threading
import time


class Flood:
    def __init__(self, chunk=b"x" * 512, every_s=0.005):
        self._sock = socket.socket()
        self._sock.bind(("127.0.0.1", 0))
        self._sock.listen()
        self.port = self._sock.getsockname()[1]
        self._running = True
        self.chunk, self.every_s = chunk, every_s
        threading.Thread(target=self._accept, daemon=True).start()

    def _accept(self):
        while self._running:
            try:
                conn, _ = self._sock.accept()
            except OSError:
                return
            threading.Thread(target=self._serve, args=(conn,), daemon=True).start()

    def _serve(self, conn):
        try:
            while self._running:
                conn.sendall(self.chunk)
                time.sleep(self.every_s)
        except OSError:
            pass
        finally:
            conn.close()

    def stop(self):
        self._running = False
        self._sock.close()
