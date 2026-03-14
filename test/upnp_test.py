import miniupnpc
import socket
import time
import sys


def test_upnp(target_port=5000):
    print("\n[*] Initializing UPnP interface...")
    upnp = miniupnpc.UPnP()

    # Delay in ms to wait for router responses
    upnp.discoverdelay = 200 
    
    try:
        print("[*] Broadcasting UPnP discovery message to the local network...")
        ndevices = upnp.discover()
        print(f"[*] Found {ndevices} UPnP device(s).")

        # Select the Internet Gateway Device (IGD) - Your Router
        upnp.selectigd()
        
        local_ip = upnp.lanaddr
        external_ip = upnp.externalipaddress()
        
        print("\n" + "="*50)
        print(f" ROUTER NEGOTIATION SUCCESS")
        print("="*50)
        print(f" Local IP   : {local_ip}")
        print(f" Public IP  : {external_ip}")
        print("="*50 + "\n")

    except Exception as e:
        print(f"\n[!] UPnP Error: Could not negotiate with the router. {e}")
        print("[!] Your router might have UPnP disabled for security, or it's not supported.")
        sys.exit(1)

    # --- ADD PORT MAPPING ---
    print(f"[*] Asking router to forward TCP Port {target_port} to {local_ip}:{target_port}...")
    try:
        # addportmapping(external_port, protocol, internal_host, internal_port, description, duration)
        # Duration '0' means indefinite until we delete it
        success = upnp.addportmapping(target_port, 'TCP', local_ip, target_port, 'Chordify_Test', '')
        
        if success:
            print(f"[*] SUCCESS! Port {target_port} is now globally open on {external_ip}.")
        else:
            print(f"[!] Router rejected the port mapping request.")
            sys.exit(1)
            
    except Exception as e:
        print(f"[!] Failed to add port mapping: {e}")
        sys.exit(1)

    # --- START TOY LISTENER ---
    # Now that the door is open, let's stand behind it and listen.
    print(f"\n[*] Starting tiny TCP server to prove it works...")
    try:
        server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server.bind(('0.0.0.0', target_port))
        server.listen(1)
        
        print(f"[*] Waiting for someone on the internet to connect to {external_ip}:{target_port}...")
        print("[*] (Press Ctrl+C to stop and close the port)")
        
        while True:
            conn, addr = server.accept()
            with conn:
                print(f"\n[!!!] CONNECTION RECEIVED FROM {addr[0]} [!!!]")
                conn.sendall(b"Hello from the automated UPnP server!\n")
                
    except KeyboardInterrupt:
        print("\n\n[*] Shutting down...")
    finally:
        # --- CLEANUP (CRITICAL) ---
        print(f"[*] Telling router to close TCP Port {target_port}...")
        upnp.deleteportmapping(target_port, 'TCP')
        print("[*] Port closed. Router is secure. Goodbye!")


if __name__ == "__main__":
    test_upnp(5000)
