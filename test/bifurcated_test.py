import socket
import threading
import os
import platform
import sys
import json

def detect_environment():
    """Determines if we should act as a Pillar (Supernode) or a Guest (Light Client)."""
    if 'android' in platform.release().lower() or 'TERMUX_VERSION' in os.environ:
        return "LIGHT_CLIENT"
    return "SUPERNODE"

class ShadowNode:
    def __init__(self, role, target_ip=None, target_port=5000):
        self.role = role
        self.target_ip = target_ip
        self.target_port = target_port
        self.mailbox = [] # Simplified DAG storage simulation

    def start_supernode(self):
        """The Pillar: Stays open to catch outbound connections from NATed phones."""
        server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server.bind(('0.0.0.0', self.target_port))
        server.listen(5)
        print(f"[*] SUPERNODE active on port {self.target_port}. Waiting for Light Clients...")

        while True:
            conn, addr = server.accept()
            print(f"[+] Connection received from NATed device: {addr}")
            # In v2.0, we keep this connection alive or use it to swap Mailbox data
            data = conn.recv(1024).decode('utf-8')
            if data:
                print(f"[*] Client sent: {data}")
                conn.sendall(b"ACK: Message received and stored in Swarm.")
            conn.close()

    def start_light_client(self):
        """The Guest: Never listens. Always initiates outbound to bypass NAT."""
        print(f"[*] LIGHT_CLIENT (Mobile) connecting to Supernode at {self.target_ip}...")
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.settimeout(5)
                s.connect((self.target_ip, self.target_port))
                
                # Simulate pushing an encrypted block
                msg = "HELLO FROM BEHIND 5G NAT"
                s.sendall(msg.encode('utf-8'))
                
                response = s.recv(1024).decode('utf-8')
                print(f"[+] Supernode Replied: {response}")
                print("[*] Connectivity Successful. NAT bypassed via Outbound-Initiation.")
        except Exception as e:
            print(f"[!] Connectivity Failed: {e}")
            print("[!] Ensure the Supernode's Port 5000 is open/forwarded!")

if __name__ == "__main__":
    env = detect_environment()
    print(f"[*] Environment: {env}")

    if env == "SUPERNODE":
        # Run this on your Laptop
        node = ShadowNode(role="SUPERNODE")
        node.start_supernode()
    else:
        # Run this on your Phone (Termux)
        # Replace '1.2.3.4' with your Laptop's Public IP
        if len(sys.argv) < 2:
            print("Usage: python shadow_test.py <SUPERNODE_IP>")
            sys.exit(1)
        node = ShadowNode(role="LIGHT_CLIENT", target_ip=sys.argv[1])
        node.start_light_client()
