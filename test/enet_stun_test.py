import enet
import sys
import stun  # from pystun3


def get_public_info(local_port):
    print(f"[*] Contacting Google STUN servers from local port {local_port}...")
    try:
        # We force STUN to use the same local port we will use for ENet
        # This primes the router's NAT table and leaves the door propped open!
        nat_type, external_ip, external_port = stun.get_ip_info(source_port=local_port)
        print(f"[*] NAT Type Detected: {nat_type}")
        return external_ip, external_port
    except Exception as e:
        print(f"[!] STUN failed: {e}")
        return None, None


def run_server(port):
    ext_ip, ext_port = get_public_info(port)
    
    address = enet.Address(None, port)
    host = enet.Host(address, 10, 2, 0, 0)
    
    print("\n" + "="*50)
    print(f" UDP HOLE-PUNCHING SERVER READY")
    print(f" Local Binding : 0.0.0.0:{port}")
    if ext_ip:
        print(f" PUBLIC ADDRESS: {ext_ip}:{ext_port}  <-- TYPE THIS INTO THE PHONE")
    print("="*50 + "\n")
    print("[*] Waiting for a connection from the 5G network...")
    
    while True:
        try:
            event = host.service(1000)
            if event.type == enet.EVENT_TYPE_CONNECT:
                print(f"\n[!!!] HOLE PUNCH SUCCESS! Connected to {event.peer.address} [!!!]")
            elif event.type == enet.EVENT_TYPE_RECEIVE:
                msg = event.packet.data.decode('utf-8')
                print(f"[*] Received: {msg}")
                
                reply_str = f"Server got your WAN packet: '{msg}'"
                packet = enet.Packet(reply_str.encode('utf-8'), enet.PACKET_FLAG_RELIABLE)
                event.peer.send(0, packet)
            elif event.type == enet.EVENT_TYPE_DISCONNECT:
                print(f"[-] {event.peer.address} disconnected.")
        except KeyboardInterrupt:
            print("\n[*] Shutting down server...")
            break


def run_client(ip, port, local_bind_port):
    # The client ALSO uses STUN to punch an outbound hole from the 5G carrier!
    my_ext_ip, my_ext_port = get_public_info(local_bind_port)
    
    # We bind the client to a specific local port so the NAT mapping stays consistent
    host = enet.Host(enet.Address(None, local_bind_port), 1, 2, 0, 0)
    address = enet.Address(ip.encode('utf-8'), port)
    
    print(f"\n[*] Firing UDP packets at {ip}:{port}...")
    peer = host.connect(address, 2)
    
    # Wait up to 5000ms for the packets to pierce the NAT
    event = host.service(5000)
    if event.type == enet.EVENT_TYPE_CONNECT:
        print("\n[!!!] WE ARE IN! Direct P2P connection established! [!!!]")
        
        msg = b"Hello from the 5G Network!"
        packet = enet.Packet(msg, enet.PACKET_FLAG_RELIABLE)
        peer.send(0, packet)
        
        while True:
            event = host.service(1000)
            if event.type == enet.EVENT_TYPE_RECEIVE:
                print(f"[*] Server replied: {event.packet.data.decode('utf-8')}")
                break
                
        peer.disconnect()
        host.service(1000) 
        print("[*] Done.")
    else:
        print("\n[!] Connection failed. The NAT bouncer blocked us.")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python3 enet_stun_test.py server <PORT>")
        print("       python3 enet_stun_test.py client <SERVER_PUBLIC_IP> <SERVER_PUBLIC_PORT> <CLIENT_LOCAL_PORT>")
        sys.exit(1)

    mode = sys.argv[1].lower()
    if mode == "server":
        run_server(int(sys.argv[2]))
    elif mode == "client":
        run_client(sys.argv[2], int(sys.argv[3]), int(sys.argv[4]))
