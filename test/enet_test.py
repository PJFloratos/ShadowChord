import enet
import sys


"""
sudo ufw allow 5000/udp
"""


def run_server(port):
    # enet.Address(None, port) binds to 0.0.0.0 (all interfaces)
    address = enet.Address(None, port)
    
    # host = enet.Host(address, max_peers, max_channels, in_bandwidth, out_bandwidth)
    host = enet.Host(address, 10, 2, 0, 0)
    print(f"[*] ENet Server listening on UDP Port {port}...")
    
    while True:
        try:
            # Wait up to 1000ms for a network event (Connect, Receive, Disconnect)
            event = host.service(1000)
            
            if event.type == enet.EVENT_TYPE_CONNECT:
                print(f"[+] New connection from {event.peer.address}")
                
            elif event.type == enet.EVENT_TYPE_RECEIVE:
                msg = event.packet.data.decode('utf-8')
                print(f"[*] Received on Channel {event.channelID}: {msg}")
                
                # --- Send a Reliable Reply ---
                reply_str = f"Server received: '{msg}'"
                # PACKET_FLAG_RELIABLE tells ENet to handle ACKs, retries, and chunking!
                packet = enet.Packet(reply_str.encode('utf-8'), enet.PACKET_FLAG_RELIABLE)
                
                # Send on Channel 0
                event.peer.send(0, packet)
                
            elif event.type == enet.EVENT_TYPE_DISCONNECT:
                print(f"[-] {event.peer.address} disconnected.")
                
        except KeyboardInterrupt:
            print("\n[*] Shutting down server...")
            break

def run_client(ip, port):
    # A client host doesn't bind to a specific local port (Address = None)
    host = enet.Host(None, 1, 2, 0, 0)
    address = enet.Address(ip.encode('utf-8'), port)
    
    print(f"[*] Connecting to {ip}:{port} via UDP...")
    
    # Initiate connection. We request 2 channels (0 and 1).
    peer = host.connect(address, 2)
    
    # Wait up to 5000ms for the connection to succeed
    event = host.service(5000)
    if event.type == enet.EVENT_TYPE_CONNECT:
        print("[+] Connected successfully!")
        
        # --- Send a Reliable Packet ---
        msg = b"Hello from the ENet Client! I am using UDP!"
        packet = enet.Packet(msg, enet.PACKET_FLAG_RELIABLE)
        peer.send(0, packet)
        print("[*] Packet queued for sending...")
        
        # Wait for the server's echo response
        while True:
            event = host.service(1000)
            if event.type == enet.EVENT_TYPE_RECEIVE:
                print(f"[*] Server replied: {event.packet.data.decode('utf-8')}")
                break
                
        # Gracefully disconnect
        print("[*] Disconnecting...")
        peer.disconnect()
        
        # We must call service() one last time to actually push the disconnect packet out
        host.service(1000) 
        print("[*] Done.")
    else:
        print("[!] Connection failed (Server offline or firewall blocked UDP).")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python3 enet_test.py server <PORT>")
        print("       python3 enet_test.py client <IP> <PORT>")
        sys.exit(1)

    mode = sys.argv[1].lower()
    
    if mode == "server" and len(sys.argv) == 3:
        run_server(int(sys.argv[2]))
    elif mode == "client" and len(sys.argv) == 4:
        run_client(sys.argv[2], int(sys.argv[3]))
    else:
        print("Invalid arguments.")