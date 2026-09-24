#!/usr/bin/env python3
"""Forward 127.0.0.1:<port> to a Unix socket. Runs inside the sandbox.

The sandbox has no network; the model gateway is mounted in as a Unix socket.
Aider needs host:port, so this bridges the two.

Usage: portfwd.py <listen_port> <unix_socket_path>
"""
import os
import socket
import sys
import threading


def splice(a, b):
    try:
        while True:
            data = a.recv(65536)
            if not data:
                break
            b.sendall(data)
    except OSError:
        pass
    finally:
        for s in (a, b):
            try:
                s.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass


def handle(client, sock_path):
    up = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        up.connect(sock_path)
    except OSError as e:
        client.close()
        sys.stderr.write(f"[portfwd] upstream connect failed: {e}\n")
        return
    t = threading.Thread(target=splice, args=(client, up), daemon=True)
    t.start()
    splice(up, client)


def main():
    port, sock_path = int(sys.argv[1]), sys.argv[2]
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("127.0.0.1", port))
    srv.listen(16)
    sys.stderr.write(f"[portfwd] 127.0.0.1:{port} -> {sock_path}\n")
    while True:
        client, _ = srv.accept()
        threading.Thread(target=handle, args=(client, sock_path), daemon=True).start()


if __name__ == "__main__":
    main()
