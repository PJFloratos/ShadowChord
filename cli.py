import sys
import threading
import time
import json
import argparse

from chord.node import ChordNode
from chord.common.utils import get_ip


"""
ShadowChord P2P Command Line Interface

Bootstrap (First Node):
    python3 cli.py <APP PORT>

Clients (Host Machines):
    python3 cli.py 5001 --entry <ENTRY IP> <ENTRY PORT>
"""


def start_cli(node: ChordNode) -> None:
    """
    Initializes the interactive Read-Eval-Print Loop (REPL) for the user.
    
    This function blocks the main thread, continuously parsing user input to 
    interact with the underlying ChordNode facade.
    """
    print("\n" + "="*50)
    print(f" Chordify Client Started on {node.state.ip}:{node.state.port}")
    print(" Type 'help' to see available commands.")
    print("="*50 + "\n")

    while True:
        try:
            user_input = input(f"Chordify ({node.state.id}) > ").strip().split()

            # Ignore empty inputs (like hitting Enter by accident)
            if not user_input:
                continue

            cmd = user_input[0].lower()
            args = user_input[1:]

            if cmd == "info":
                result = node.get_info()
                try:
                    info = json.loads(result)
                    print("\n" + "="*50)
                    print(f" NODE INFORMATION ({info['ip']}:{info['port']})")
                    print("="*50)
                    print(f" ID         : {str(info['id'])}")
                    print(f" Status     : {info['status']}")
                    print(f" k-Factor   : {info['k_factor']}")
                    print(f" Consistency: {info['consistency']}")
                    print("-" * 50)
                    print(f" Predecessor: {str(info['predecessor']['id'])}... ({info['predecessor']['ip']}:{info['predecessor']['port']})")
                    print(f" Successor  : {str(info['successor']['id'])}... ({info['successor']['ip']}:{info['successor']['port']})")
                    print("-" * 50)
                    
                    print(f" Successor List (k={info['k_factor']}):")
                    if not info['successor_list']:
                        print("    - (Empty / Alone in ring)")
                    for i, succ in enumerate(info['successor_list']):
                        print(f"    [{i+1}] {str(succ['id'])}... ({succ['ip']}:{succ['port']})")

                    print("-" * 50)
                    print(" Finger Table (Routing Jumps):")
                    if not info.get('finger_table'):
                        print("    - (Populating...)")
                    else:
                        for f in info['finger_table']:
                            print(f"    [Idx {f['range']:<7}] -> {str(f['id'])} ({f['ip']}:{f['port']})")

                    print("-" * 50)
                    print(f" Storage Metrics:")
                    print(f"    Primary Keys : {info['storage']['primary_count']}")
                    print(f"    Replica Keys : {info['storage']['replica_count']}")
                    print(f"    Total Keys   : {info['storage']['total_count']}")
                    print("="*50 + "\n")
                except Exception as e:
                    print(f"Info Error: {e}")
                    print(result)

            elif cmd == "depart":
                node.depart()
                print("Node departed gracefully. Goodbye!")
                sys.exit(0)

            # elif cmd == "insert":
            #     if len(args) < 1:
            #         print("Usage: insert <key>")
            #         continue

            #     key = args[0]
            #     result = node.insert_request(key)
            #     print("insert result: ", result)

            elif cmd == "query":
                if len(args) < 1:
                    print("Usage: query <key> (Use '*' for all data)")
                    continue
                
                key = args[0]
                result = node.query_request(key)
                # Try to pretty-print the JSON so it looks nice
                try:
                    parsed = json.loads(result)
                    print(json.dumps(parsed, indent=2))
                except:
                    print(f"Query Result: {result}")

            elif cmd == "delete":
                if len(args) < 1:
                    print("Usage: delete <key>")
                    continue
                
                key = args[0]
                result = node.delete_request(key)
                print("delete result: ", result)

            elif cmd == "announce":
                if len(args) < 1:
                    print("Usage: announce <filename>")
                    continue
                print(node.announce_file(args[0]))

            elif cmd == "download":
                if len(args) < 1:
                    print("Usage: download <filename>")
                    continue
                # This triggers the DHT Query + Data Plane Streaming
                print(node.download_file(args[0]))

            elif cmd == "overlay":
                result = node.request_overlay()
                try:
                    parsed = json.loads(result)["topology"]
                    print("\n" + "="*60)
                    print(" OVERLAY: NETWORK TOPOLOGY & DATA DISTRIBUTION")
                    print("="*60)
                    
                    for i, n_block in enumerate(parsed):
                        node_str = str(n_block['node_id'])
                        print(f"\n[{i}] Node: {node_str}... ({n_block['ip']}:{n_block['port']})")
                        
                        print(f"    Primary Data ({len(n_block['primary_data'])} keys):")
                        if not n_block['primary_data']:
                            print("      - (Empty)")
                        for item in n_block['primary_data']:
                            print(f"      - Key: {str(item['key'])}... | Value: {item['value']}")
                            
                        print(f"    Replica Data ({len(n_block['replica_data'])} keys):")
                        if not n_block['replica_data']:
                            print("      - (Empty)")
                        for item in n_block['replica_data']:
                            print(f"      - Key: {str(item['key'])}... | Value: {item['value']}")
                            
                    print("\n" + "="*60 + "\n")
                except Exception as e:
                    print(f"Overlay Result (Raw): {result}")
                    print(f"Parsing Error: {e}")

            elif cmd == "help":
                print("\n--- ShadowChord Commands ---")
                print("  announce <file> : Advertise a local file to the swarm")
                print("  download <file> : Locate and leech a file via P2P stream")
                print("  query <key>     : Inspect swarm metadata (Use '*' for all)")
                print("  delete <key>    : Unlink a file from the swarm (Tombstone)")
                print("  overlay         : Print global network topology")
                print("  info            : Display node local routing state")
                print("  depart          : Graceful 2-Phase Commit departure")
                print("  help            : Show command explanations")
                print("----------------------------\n")

            else:
                print("Unknown command. Type 'help' for options.")
                
        except KeyboardInterrupt:
            print("\nForce Close. Simulating node crash.")
            sys.exit(0)


if __name__ == "__main__":
    # System Hyperparameters
    K = 3
    CONSISTENCY = "eventual"

    # 1. Argument Parsing
    parser = argparse.ArgumentParser(description="Chordify P2P Node")
    parser.add_argument("port", type=int, help="The port this node will listen on")
    parser.add_argument("--ip", type=str, help="Override public IP (REQUIRED if running inside a VM)")
    parser.add_argument("--entry", nargs=2, metavar=('IP', 'PORT'), help="IP and Port of the entry node to join")

    args = parser.parse_args()

    # 2. Network Initialization
    my_port = args.port
    my_ip = get_ip(provided_ip=args.ip)
    
    node = ChordNode(my_ip, my_port, k=K, consistency=CONSISTENCY)

    # 3. Start Server (Daemon)
    threading.Thread(target=node.net.start_server, daemon=True).start()

    # Give the server 0.5s to securely bind the socket before reaching out
    time.sleep(0.5)

    # 4. Safe Join Logic
    if args.entry:
        entry_ip = args.entry[0]
        entry_port = int(args.entry[1])
        
        success = node.join(entry_ip, entry_port)

        if not success:
            print(f"\n[!] FATAL: Could not join the network via {entry_ip}:{entry_port}.")
            sys.exit(1) # Terminate cleanly!
    else:
        # I am the Bootstrap node, self-join to initialize.
        print("\n[*] Starting a new Chord ring...")
        node.join(my_ip, my_port)

    # 5. Start the Interactive Shell
    start_cli(node)
