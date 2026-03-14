import threading
import json
import random
from typing import Optional

from .state import NodeState
from .common.log import NodeLogger
from .dht.storage import DataStore
from .dht.replication import ReplicationManager
from .dht.topology import TopologyManager
from .dht.routing import RoutingEngine
from .dht.handoff import DataHandoffManager
from .network.tcp import NetworkHandler
from .network.dispatcher import MessageDispatcher
from .file_io import FileManager
from .network.streamer import FileStreamer


class ChordNode:
    """
    The Main Facade for the Chordify DHT.
    
    This class acts as the orchestrator. It instantiates the shared state, 
    wires together the networking, routing, topology, and replication sub-systems, 
    spawns the necessary background daemons, and exposes the high-level 
    client API used by the CLI.
    """

    def __init__(self,
            ip: str,
            port: int,
            k: int = 3,
            consistency: str = "eventual",
            verbose: bool = False
        ) -> None:
        # 1. Initialize State & Logger
        self.state = NodeState(ip, port, k, consistency)
        self.log = NodeLogger(self.state.port, self.state.id, verbose=verbose)

        self.log.info(f"Node created at {self.state.ip}:{self.state.port}.")

        # 2. Initialize Sub-modules (Injecting 'self' so they can access state & peers)
        self.storage = DataStore(self.log)
        self.net = NetworkHandler(self)
        self.dispatcher = MessageDispatcher(self)
        self.routing = RoutingEngine(self)
        self.topology = TopologyManager(self)
        self.handoff = DataHandoffManager(self)
        self.replicator = ReplicationManager(self)
        self.file_manager = FileManager(self.state.id)
        self.streamer = FileStreamer(self)

        # 3. Start Control Plane background daemons
        threading.Thread(target=self.topology.stabilize_worker, daemon=True).start()
        threading.Thread(target=self.topology.fix_fingers_worker, daemon=True).start()
        threading.Thread(target=self.topology.partition_healer_worker, daemon=True).start()
        threading.Thread(target=self.replicator.anti_entropy_worker, daemon=True).start()

        # 4. Start the out-of-band Data Plane Server thread
        threading.Thread(target=self.streamer.start_server, daemon=True).start()


    # ==========================================
    # CLI / CLIENT API DELEGATION
    # ==========================================

    def join(self, bootstrap_ip: str, bootstrap_port: int) -> bool:
        """
        Delegates the atomic join process to the Topology Manager.
        """
        return self.topology.join(bootstrap_ip, bootstrap_port)


    def depart(self) -> None:
        """
        Executes a graceful 2-Phase Commit departure.
        """
        self.topology.depart()


    def insert_request(self, key: str, custom_value: Optional[str] = None) -> str:
        """
        Delegates a raw DHT write to the Routing Engine.
        """
        return self.routing.insert_request(key, custom_value)


    def query_request(self, key: str) -> str:
        """
        Delegates a raw DHT read to the Routing Engine.
        """
        return self.routing.query_request(key)


    def delete_request(self, key: str) -> str:
        """
        Delegates a DHT soft-deletion (Tombstone insertion) to the Routing Engine.
        """
        return self.routing.delete_request(key)


    def request_overlay(self) -> str:
        """
        Initiates a global topology and data-distribution scan.
        """
        return self.routing.request_overlay()


    def announce_file(self, filename: str) -> str:
        """
        P2P File Seeding: Checks if a file exists locally, and if so, advertises 
        to the global DHT that this node is currently seeding it.
        """
        self.log.info(f"Attempting to announce file '{filename}' to the network.")
        
        # 1. Verify the file is actually in our sandbox
        if not self.file_manager.file_exists(filename):
            self.log.error(f"Announce failed: '{filename}' not found in local storage.")
            return f"[!] Error: '{filename}' does not exist in your shared folder."

        # 2. Insert the metadata into the DHT
        # Our routing.insert_request automatically defaults the value to our IP:Port!
        reply = self.insert_request(filename)
        
        self.log.info(f"Successfully announced '{filename}' to the network.")
        return reply


    def download_file(self, filename: str) -> str:
        """
        Orchestrates the complete BitTorrent-style leech process.
        
        1. Queries the DHT Control Plane to find active seeders.
        2. Randomly load-balances across the seeders to avoid DDoS-ing one node.
        3. Reaches out via the Data Plane to stream the raw binary file.
        4. Provides automatic fallback if a seeder drops the connection mid-transfer.
        """
        self.log.info(f"Searching swarm for '{filename}'.")
        
        query_result_raw = self.query_request(filename)
        
        try:
            query_result = json.loads(query_result_raw)
            data = query_result.get("requested_data", [])
            
            if not data:
                return f"[!] Error: No seeders found in the swarm for '{filename}'."
                
            seeders = data[0].get("value", [])
            
            if not seeders or seeders == "TOMBSTONE":
                return f"[!] Error: '{filename}' was deleted from the swarm."

            # --- RANDOM LOAD BALANCING & FALLBACK ---
            # Shuffle the list so we don't DDoS the first node
            random.shuffle(seeders)
            
            self.log.info(f"Found {len(seeders)} seeders. Attempting to leech.")

            # Iterate through the randomized list until one succeeds
            for seeder in seeders:
                target_ip, target_port = seeder["ip"].split(":")
                
                # Skip ourselves
                if target_ip == self.state.ip and int(target_port) == self.state.port:
                    continue

                self.log.info(f"Trying seeder {seeder['ip']}.")
                
                # Attempt the Data Plane stream
                success, message = self.streamer.download_file(target_ip, int(target_port), filename)

                if success:
                    return f"[OK] {message}"
                else:
                    self.log.warning(f"Failed to leech from {seeder['ip']}: {message}. Trying next seeder...")

            return f"[!] Error: Exhausted all {len(seeders)} seeders. Download failed."
            
        except Exception as e:
            return f"[!] Download error: Failed to parse swarm response. {e}"


    def get_info(self) -> str:
        """
        Compiles and returns a comprehensive snapshot of the node's current 
        routing state, storage capacity, and topology pointers.
        
        Includes an algorithm to mathematically compress redundant Finger Table 
        entries, vastly improving the readability of the CLI output.
        """
        # Calculate data distribution by delegating to Handoff Manager and Storage
        primary_data = self.handoff.get_primary_data(exclude_tombstones=True)
        primary_keys = [item["key"] for item in primary_data]
        replica_data = [item for item in self.storage.get_all(exclude_tombstones=True) if item["key"] not in primary_keys]

        # --- SMART FINGER TABLE COMPRESSION ---
        compressed_fingers = []
        if self.state.finger_table:
            current_node = None
            start_idx = 0

            for i, finger in enumerate(self.state.finger_table):
                f_node = finger.get("node")
                f_id = f_node.get("id") if f_node else None
                
                if f_id != current_node:
                    if current_node is not None:
                        compressed_fingers.append({
                            "range": f"{start_idx}-{i-1}" if start_idx < i-1 else str(start_idx),
                            "id": current_node,
                            "ip": self.state.finger_table[start_idx]["node"]["ip"],
                            "port": self.state.finger_table[start_idx]["node"]["port"]
                        })
                    current_node = f_id
                    start_idx = i

            # Append the final block
            if current_node is not None:
                compressed_fingers.append({
                    "range": f"{start_idx}-159" if start_idx < 159 else str(start_idx),
                    "id": current_node,
                    "ip": self.state.finger_table[start_idx]["node"]["ip"],
                    "port": self.state.finger_table[start_idx]["node"]["port"]
                })

        info = {
            "id": self.state.id,
            "ip": self.state.ip,
            "port": self.state.port,
            "status": "Joined" if self.state.hasJoined else "Initializing",
            "k_factor": self.state.k,
            "consistency": self.state.consistency,
            "predecessor": self.state.predecessor,
            "successor": self.state.successor,
            "successor_list": self.state.successor_list,
            "finger_table": compressed_fingers,
            "storage": {
                "primary_count": len(primary_data),
                "replica_count": len(replica_data),
                "total_count": len(primary_data) + len(replica_data)
            }
        }

        return json.dumps(info)
