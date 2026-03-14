import socket
import threading
import json
import sys

"""

sudo ufw allow 5000:5010/tcp
"""

def listener_thread(my_port):
    """Runs in the background, listening for incoming messages."""
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    
    # Binding to 0.0.0.0 means "Listen on all available network interfaces"
    server.bind(('0.0.0.0', my_port))
    server.listen(5)
    
    print(f"[*] Listening for incoming P2P connections on port {my_port}...")
    
    while True:
        try:
            conn, addr = server.accept()
            with conn:
                raw_data = conn.recv(1024)
                if raw_data:
                    # Security: Parse as JSON to prevent malicious code execution
                    payload = json.loads(raw_data.decode('utf-8'))
                    print(f"\n[Incoming from {addr[0]}]: {payload.get('message', '')}")
                    print("You: ", end="", flush=True) # Reprint prompt
        except Exception as e:
            print(f"\n[!] Listener error: {e}")

def start_node(my_port, target_ip, target_port):
    """Starts the listener and handles sending outgoing messages."""
    # 1. Start the Server thread
    t = threading.Thread(target=listener_thread, args=(my_port,), daemon=True)
    t.start()
    
    # 2. Main loop for the Client (sending messages)
    print(f"[*] Node active. Type a message to send to {target_ip}:{target_port}")
    while True:
        try:
            msg = input("You: ")
            if not msg: continue
            
            # Format payload securely as JSON
            payload = json.dumps({"type": "chat", "message": msg})
            
            # Open a socket, send, and immediately close (Stateless P2P)
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.settimeout(3.0)
                s.connect((target_ip, target_port))
                s.sendall(payload.encode('utf-8'))
                
        except socket.timeout:
            print(f"[!] Timeout: Node at {target_ip}:{target_port} did not respond.")
        except ConnectionRefusedError:
            print(f"[!] Refused: Firewall blocked it, or node is dead.")
        except Exception as e:
            print(f"[!] Send error: {e}")


if __name__ == "__main__":
    if len(sys.argv) != 4:
        print("Usage: python3 p2p_chat.py <MY_PORT> <TARGET_IP> <TARGET_PORT>")
        sys.exit(1)
        
    my_port = int(sys.argv[1])
    target_ip = sys.argv[2]
    target_port = int(sys.argv[3])
    
    start_node(my_port, target_ip, target_port)


# import os
# import platform

# def detect_environment():
#     """
#     Fingerprints the hardware to determine if the node is running on a 
#     Physical PC, a Virtual Machine, or a Mobile Phone (Termux/PRoot).
#     """
#     # 1. Check for Termux or Android PRoot
#     # Even inside Kali PRoot, the underlying Android kernel bleeds through
#     if 'android' in platform.release().lower() or 'TERMUX_VERSION' in os.environ:
#         return "PHONE"
        
#     # 2. Check for Virtual Machines via DMI (Linux/Cloud)
#     try:
#         with open('/sys/class/dmi/id/product_name', 'r') as f:
#             hardware = f.read().strip().lower()
            
#             vm_signatures = [
#                 'standard pc', 'qemu', 'virtualbox', 'vmware', 
#                 'amazon ec2', 'google compute engine', 'virtual machine'
#             ]
            
#             if any(sig in hardware for sig in vm_signatures):
#                 return "VM"
#     except (FileNotFoundError, PermissionError):
#         # If DMI doesn't exist, it's likely physical hardware or a locked-down OS
#         pass
        
#     # 3. If neither VM nor Phone, assume it's the Physical Host Laptop
#     return "HOST_LAPTOP"

# # --- Test the Fingerprinter ---
# env = detect_environment()
# print(f"[*] Node Environment Detected: {env}")
