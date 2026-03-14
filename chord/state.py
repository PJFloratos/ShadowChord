from typing import Dict, List, Any, Set

from .common import utils


class NodeState:
    """
    Holds all mutable, shared state for the Chord node. 
    
    Centralizing this state prevents circular Python import dependencies 
    between domain managers (e.g., Routing needs Topology, Topology needs Routing), 
    acting as the single source of truth for the node's identity and network pointers.
    """

    def __init__(self, ip: str, port: int, k: int, consistency: str) -> None:
        # 1. Identity
        self.ip = ip
        self.port = port
        self.id = utils.get_sha1_hash(f"{self.ip}:{self.port}")

        # 2. Replication Configuration
        self.k = k
        self.consistency = consistency

        # 3. Ring Topology Pointers
        self.hasJoined = False
        self.successor = {"ip": self.ip, "port": self.port, "id": self.id}
        self.predecessor = {"ip": self.ip, "port": self.port, "id": self.id}
        self.successor_list = [] # Format: [{"ip":..., "port":..., "id":...}, ...]
        
        # 4. Network / Asynchronous State
        # Stores threading.Events to block clients while snowballs traverse the ring
        # Format: { "request_id": [threading.Event(), result_payload] }
        self.pending_requests = {}

        # 5. Logarithmic Routing (Finger Table)
        self.m = 160  # SHA-1 produces a 160-bit hash
        self.finger_table = []

        # 6. P2P Discovery Layer (Used to heal Split-Brain Network Partitions)
        self.known_peers = set() # Stores "IP:PORT" strings
        self.max_peers = 2000    # Cap to prevent memory leaks


    def init_finger_table(self) -> None:
        """
        Initializes the m-entry logarithmic routing table. 
        
        Before the network is fully explored via background workers, all 
        fingers safely default to pointing at our immediate successor.
        """
        self.finger_table = []
        
        for i in range(self.m):
            # Calculate the mathematical jump: (n + 2^i) mod 2^m
            # self.id is ALREADY an integer, so we just use it directly!
            start_id = (self.id + (2 ** i)) % (2 ** self.m)
            
            # Default to pointing to our immediate successor until fix_fingers updates it
            self.finger_table.append({
                "start": start_id, # Keep it as an integer!
                "node": self.successor 
            })


    def observe_peer(self, ip: str, port: int) -> None:
        """
        Passively records any node that communicates with us.
        
        This historical 'Rolodex' is utilized by the partition_healer_worker 
        to attempt bridging isolated networks back together.
        """
        peer_str = f"{ip}:{port}"
        
        # Don't add ourselves!
        if peer_str == f"{self.ip}:{self.port}":
            return
            
        if peer_str not in self.known_peers:
            # If the graveyard is full, forget an arbitrary old peer
            if len(self.known_peers) >= self.max_peers:
                self.known_peers.pop()
            self.known_peers.add(peer_str)
