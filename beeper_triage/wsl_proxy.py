#!/usr/bin/env python3
"""
Beeper WSL Proxy - Runs on Windows
Bridges WSL to Beeper's IPv6-only API by:
- Auto-detecting which port Beeper Desktop is listening on
- Listening on all interfaces (0.0.0.0:PORT) for WSL connections
- Forwarding to Beeper's IPv6 loopback ([::1]:PORT)
"""
import socket
import threading
import sys

# Ports to try — Beeper Desktop alternates between these across updates
BEEPER_PORTS = [23374, 23373]


def detect_beeper_port():
    """Try each candidate port and return the first one Beeper is listening on."""
    for port in BEEPER_PORTS:
        try:
            sock = socket.socket(socket.AF_INET6, socket.SOCK_STREAM)
            sock.settimeout(2)
            sock.connect(('::1', port))
            sock.close()
            return port
        except (ConnectionRefusedError, OSError, socket.timeout):
            continue
    return None

def forward_data(source, destination, direction):
    """Forward data from source to destination socket"""
    try:
        while True:
            data = source.recv(4096)
            if not data:
                break
            destination.sendall(data)
    except Exception as e:
        pass
    finally:
        try:
            source.close()
        except:
            pass
        try:
            destination.close()
        except:
            pass

def handle_client(client_socket, client_addr, beeper_port):
    """Handle a client connection by creating IPv6 connection to Beeper"""
    remote_socket = None
    try:
        # Connect to Beeper on IPv6 loopback
        remote_socket = socket.socket(socket.AF_INET6, socket.SOCK_STREAM)
        remote_socket.connect(('::1', beeper_port))

        print(f"[+] Connected: {client_addr} <-> [::1]:{beeper_port}")

        # Start forwarding in both directions
        client_to_server = threading.Thread(
            target=forward_data,
            args=(client_socket, remote_socket, "client->server"),
            daemon=True
        )
        server_to_client = threading.Thread(
            target=forward_data,
            args=(remote_socket, client_socket, "server->client"),
            daemon=True
        )

        client_to_server.start()
        server_to_client.start()

        # Wait for threads to complete
        client_to_server.join()
        server_to_client.join()

        print(f"[-] Disconnected: {client_addr}")

    except Exception as e:
        print(f"[!] Error handling {client_addr}: {e}", file=sys.stderr)
    finally:
        if remote_socket:
            try:
                remote_socket.close()
            except:
                pass
        try:
            client_socket.close()
        except:
            pass

def main():
    print("=" * 60)
    print("Beeper WSL Proxy")
    print("=" * 60)

    # Auto-detect which port Beeper Desktop is listening on
    print(f"[*] Probing ports {BEEPER_PORTS} on [::1] ...")
    beeper_port = detect_beeper_port()
    if beeper_port is None:
        print(f"[!] Beeper Desktop not found on any of {BEEPER_PORTS}", file=sys.stderr)
        print("[!] Make sure Beeper Desktop is running", file=sys.stderr)
        sys.exit(1)

    print(f"[+] Beeper detected on port {beeper_port}")

    # Listen on all interfaces so WSL can connect
    listen_host = '0.0.0.0'
    listen_port = beeper_port

    print(f"Listening on:  {listen_host}:{listen_port} (accessible from WSL)")
    print(f"Forwarding to: [::1]:{listen_port} (Beeper IPv6 loopback)")
    print("Press Ctrl+C to stop")
    print("=" * 60)

    server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)

    try:
        server_socket.bind((listen_host, listen_port))
        server_socket.listen(5)
        print("[+] Proxy started successfully\n")
    except Exception as e:
        print(f"[!] Failed to start proxy: {e}", file=sys.stderr)
        print(f"[!] Make sure port {listen_port} is not in use", file=sys.stderr)
        sys.exit(1)

    try:
        while True:
            client_socket, addr = server_socket.accept()
            thread = threading.Thread(
                target=handle_client,
                args=(client_socket, addr, beeper_port),
                daemon=True
            )
            thread.start()
    except KeyboardInterrupt:
        print("\n\n[+] Shutting down proxy...")
    finally:
        server_socket.close()
        print("[+] Proxy stopped")

if __name__ == '__main__':
    main()
