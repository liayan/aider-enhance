#!/usr/bin/env python3
"""Forward 127.0.0.1:<port> to the model gateway. Runs inside the sandbox.

The sandbox has no network. The gateway is either a bind-mounted Unix socket
(process sandbox, container) or a vsock port on the host (Firecracker). Aider
needs host:port, so this bridges the two.

Usage: portfwd.py <listen_port> <unix_socket_path | vsock:<cid>:<port>>
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


def connect_upstream(target):
    if target.startswith("vsock:"):
        _, cid, port = target.split(":")
        up = socket.socket(socket.AF_VSOCK, socket.SOCK_STREAM)
        addr = (int(cid), int(port))
    else:
        up = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        addr = target
    try:
        up.connect(addr)
    except OSError:
        up.close()
        raise
    return up


def handle(client, sock_path):
    try:
        up = connect_upstream(sock_path)
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
