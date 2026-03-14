from __future__ import annotations
import time
import json
import uuid
import threading
from typing import Dict, List, Any, Optional, Union, TYPE_CHECKING

from ..common import utils

if TYPE_CHECKING:
    from ..node import ChordNode


class RoutingEngine:
    """
    Handles successor resolution, client API routing, and ring traversals.
    
    The Routing Engine implements the mathematical logic for jumping across the 
    Chord ring. It is responsible for finding the 'Head' node of any key and 
    executing ring-wide 'snowball' queries for global status updates.
    """

    def __init__(self, node: ChordNode) -> None:
        """
        Initializes the routing engine with a reference to the parent node.
        """
        self.node: ChordNode = node


    # ==========================================
    # O(log N) FINGER TABLE ROUTING 
    # ==========================================
    
    def closest_preceding_node(self, target_id: int) -> Dict[str, Any]:
        """
        Searches the finger table to find the furthest node preceding the target.
        
        This method executes the 'logarithmic jump'. It searches the finger table 
        backwards—from the largest jumps (half the ring) to the smallest—to find 
         the node that is closest to the target without overshooting it.

        Args:
            target_id: The SHA-1/BLAKE3 integer ID being searched for.
            
        Returns:
            A node dictionary {"ip":..., "port":..., "id":...} for the next hop.
        """
        # If the finger table isn't initialized yet, fallback to successor
        if not self.node.state.finger_table:
            return self.node.state.successor

        # Search backwards from the biggest jump (Index 159 down to 0)
        for i in range(self.node.state.m - 1, -1, -1):
            finger = self.node.state.finger_table[i]
            finger_node = finger.get("node")
            
            if finger_node and finger_node.get("id"):
                # Is this finger strictly between me and the target?
                if utils.is_between(finger_node["id"], self.node.state.id, target_id):
                    return finger_node
        
        # Fallback to successor list if fingers fail
        for backup in self.node.state.successor_list:
            if backup and backup.get("id") and utils.is_between(backup["id"], self.node.state.id, target_id):
                return backup

        # Ultimate fallback
        return self.node.state.successor


    def get_successor_port(self,
            target_id: int,
            entry_node: Optional[Dict[str, Any]] = None
        ) -> Union[Dict[str, Any], str]:
        """
        Iteratively hops through the ring until the true successor is resolved.
        
        This is the core iterative lookup. It starts at an entry node (or local fingers) 
        and follows the 'closest_preceding_node' suggestions from remote peers 
        until it reaches the node mathematically responsible for the target_id.

        Args:
            target_id: The ID to resolve.
            entry_node: An optional starting point node (used during Join operations).
            
        Returns:
            Success: A node dictionary containing the seeder's connectivity info.
            Failure: An error string describing the routing timeout or loop.
        """
        # 1. Trivial Case: Is it me?
        if self.node.state.hasJoined:
            if utils.is_between(target_id, self.node.state.predecessor["id"], self.node.state.id) or self.node.state.successor['id'] == self.node.state.id:
                return {"target_ip": self.node.state.ip, "target_port": self.node.state.port}

        # 2. Ask our entry node OR teleport to our best finger!
        current_hop = entry_node if entry_node else self.closest_preceding_node(target_id)

        # O(log N) magic means we will NEVER need more than ~20 hops, even with 1M nodes!
        max_hops = 32 
        visited_hops = set()

        for _ in range(max_hops):
            hop_identifier = f"{current_hop['ip']}:{current_hop['port']}"
            if hop_identifier in visited_hops:
                self.node.log.error(f"Routing Error: Infinite loop detected at {hop_identifier}!")
                return "ERROR: Routing Loop"
            
            visited_hops.add(hop_identifier)

            reply_raw = self.node.net.send_command(
                current_hop['ip'],
                current_hop['port'],
                json.dumps({
                    "type": "find successor step",
                    "target_id": target_id
                })
            )

            if reply_raw is None or "error" in reply_raw.lower():
                hop_id = current_hop.get("id", "ENTRY_NODE")
                self.node.log.error(f"Routing: Hop {hop_id}... failed to respond.")
                return "ERROR: Routing Failed"

            try:
                reply = json.loads(reply_raw)

                if reply.get("found"):
                    return {"target_ip": reply["ip"], "target_port": reply["port"], "id": reply.get("id")}
                else:
                    current_hop = {"ip": reply["next_ip"], "port": reply["next_port"], "id": reply.get("next_id", "")}
            except json.JSONDecodeError:
                return "ERROR: Malformed Routing Reply"

        return "ERROR: Max Hops Exceeded"
    

    def handle_find_successor_step(self, cmd: Dict[str, Any]) -> str:
        """
        Dispatcher Hook: Resolves if this node is the successor or identifies the next hop.
        
        This acts as the server-side responder for 'get_successor_port'. If the 
        target_id falls in the range (Predecessor, Me], it claims ownership. 
        Otherwise, it suggests the best finger to bridge the gap.
        """
        target_id = cmd["target_id"]

        # 1. Am I the true successor?
        if utils.is_between(target_id, self.node.state.predecessor["id"], self.node.state.id) or self.node.state.successor['id'] == self.node.state.id or target_id == self.node.state.id:
            return json.dumps({
                "found": True,
                "ip": self.node.state.ip,
                "port": self.node.state.port,
                "id": self.node.state.id
            })

        # 2. I am not the successor. Find the best Finger to jump to!
        next_hop = self.closest_preceding_node(target_id)
        
        # Prevent infinite loops if closest preceding node evaluates back to myself
        if next_hop["id"] == self.node.state.id:
            next_hop = self.node.state.successor

        return json.dumps({
            "found": False,
            "next_ip": next_hop['ip'],
            "next_port": next_hop['port'],
            "next_id": next_hop['id']
        })
    

    # ==========================================
    # CLIENT REQUEST COORDINATION
    # ==========================================

    def insert_request(self,
            key: str,
            custom_value: Optional[str] = None
        ) -> str:
        """
        Orchestrates a client-level INSERT by resolving the head node and routing data.
        
        This method hashes the key, finds the responsible node, and attaches a 
        coordinator timestamp to ensure CRDT-safe Last-Write-Wins conflict resolution.
        """
        self.node.log.info(f"Processing client INSERT request for key '{key}'.")

        hashed_key = utils.get_sha1_hash(key)
        
        # Use the custom value if provided, otherwise fallback to the IP string
        value = custom_value if custom_value is not None else f'{self.node.state.ip}:{self.node.state.port}'

        # The Coordinator generates the official timestamp for this write!
        write_timestamp = time.time()

        self.node.log.debug(f"Hashed key '{key}' to {hashed_key}. Resolving legal Head node.")

        ip_port = self.get_successor_port(hashed_key)

        # Catch the timeout before it crashes the node
        if isinstance(ip_port, str):
            self.node.log.error(f"Insert failed: Routing error for key '{key}'.")
            return f"[!] Routing Error: {ip_port}."

        receiver_ip = ip_port["target_ip"]
        receiver_port = ip_port["target_port"]

        self.node.log.debug(f"Owner for key {hashed_key} resolved to {receiver_ip}:{receiver_port}.")

        data_to_insert = [{"key": hashed_key, "value": value, "timestamp": write_timestamp}]
        message = {
            "type": "insert request",
            "requester_ip": self.node.state.ip,
            "requester_port": self.node.state.port,
            "data_to_insert": data_to_insert
        }
        
        # special case where the requester is also the key owner
        if self.node.state.ip == receiver_ip and self.node.state.port == receiver_port:
            self.node.log.debug(f"I am the Head for key {hashed_key}. Delegating to local Replication Manager.")
            return self.node.replicator.handle_insert(message)

        self.node.log.debug(f"Forwarding insert payload to remote Head node {receiver_ip}:{receiver_port}.")
        reply = self.node.net.send_command(receiver_ip, receiver_port, json.dumps(message))

        self.node.log.debug(f"Insert request for '{key}' completed with reply: {reply}.")

        return reply

    
    def delete_request(self, key: str) -> str:
        """
        Soft-deletes a key by propagating an authenticated Tombstone through the swarm.
        
        Instead of hard deletion, it inserts a high-timestamp record flagged 
        for deletion, ensuring all replicas eventually purge the data.
        """
        self.node.log.info(f"Processing client DELETE request for key '{key}'.")

        hashed_key = utils.get_sha1_hash(key)
        
        # The Coordinator generates the official timestamp for the deletion!
        write_timestamp = time.time()

        self.node.log.debug(f"Hashed key '{key}' to {hashed_key}. Resolving legal Head node for tombstone.")

        ip_port = self.get_successor_port(hashed_key)

        # Catch the timeout before it crashes the node
        if isinstance(ip_port, str): 
            self.node.log.error(f"Delete failed: Routing error for key '{key}'.")
            return f"[!] Routing Error: {ip_port}"

        receiver_ip = ip_port["target_ip"]
        receiver_port = ip_port["target_port"]

        self.node.log.debug(f"Head for key {hashed_key} resolved to {receiver_ip}:{receiver_port}.")

        # We package the payload with the smart flag turned ON
        data_to_insert = [{
            "key": hashed_key, 
            "value": f'{self.node.state.ip}:{self.node.state.port}',
            "timestamp": write_timestamp,
            "is_delete": True         # Flag it for remove_value!
        }]
        message = {
            "type": "insert request", # We use "insert" to force it through the replicator!
            "requester_ip": self.node.state.ip,
            "requester_port": self.node.state.port,
            "data_to_insert": data_to_insert
        }

        # special case where the requester is also the key owner
        if self.node.state.ip == receiver_ip and self.node.state.port == receiver_port:
            self.node.log.debug(f"I am the Head for key {hashed_key}. Pushing tombstone to local Replication Manager.")
            return self.node.replicator.handle_insert(message)

        self.node.log.debug(f"Forwarding tombstone payload to remote Head node {receiver_ip}:{receiver_port}.")
        reply = self.node.net.send_command(receiver_ip, receiver_port, json.dumps(message))

        self.node.log.debug(f"Delete request for '{key}' completed with reply: {reply}.")

        return reply


    def query_request(self, key: str) -> str:
        """
        Orchestrates data retrieval, supporting single-key lookups or global star queries.
        
        Implements local-first eventual consistency: if the node already possesses 
        the data (as a replica), it returns it instantly to reduce network traffic.
        """
        # 1. Handle Special Case
        if key == "*":
            return self.handle_star_query()
        
        self.node.log.info(f"Processing client QUERY request for key '{key}'.")

        hashed_key = utils.get_sha1_hash(key)

        # 2. Local-First Check (Eventual Consistency)
        if self.node.state.consistency == "eventual":
            local_reply = self._eventual_local_read(key, hashed_key)
            if local_reply:
                return local_reply

        # 3. Resolve Legal Head Node
        self.node.log.debug(f"Hashed key '{key}' to {hashed_key}. Resolving legal Head node.")

        target_node = self.get_successor_port(hashed_key)

        # Catch the timeout before it crashes the node
        if isinstance(target_node, str):
            self.node.log.error(f"Query failed: Routing error for key '{key}'.")
            return f"[!] Routing Error: {target_node}"
        
        receiver_ip = target_node["target_ip"]
        receiver_port = target_node["target_port"]

        self.node.log.debug(f"Head for key {hashed_key} resolved to {receiver_ip}:{receiver_port}.")

        # 4. Payload creation
        cmd = {
            "type": "query request",
            "requester_ip": self.node.state.ip,
            "requester_port": self.node.state.port,
            "key": hashed_key
        }

        # 5. Dispatch (Local vs. Remote)node.state.
        if self.node.state.ip == receiver_ip and self.node.state.port == receiver_port:
            self.node.log.debug(f"I am the Head for key {hashed_key}. Delegating to local Replication Manager.")
            reply = self.node.replicator.handle_query(cmd)
        else:
            self.node.log.debug(f"Forwarding query payload to remote Head node {receiver_ip}:{receiver_port}.")
            reply = self.node.net.send_command(receiver_ip, receiver_port, json.dumps(cmd))
        
        self.node.log.debug(f"Query request for '{key}' completed with reply: {reply}.")
        return reply


    def _eventual_local_read(self, key: str, hashed_key: int) -> Optional[str]:
        """
        Helper to serve reads instantly from local storage cache.
        """
        self.node.log.debug(f"Eventual consistency: Checking local storage for key '{key}'.")
        local_data = self.node.storage.query(hashed_key)
        
        if local_data:
            self.node.log.info(f"Eventual Read: Served key '{key}' directly from local storage. (Risk of stale data).")
            return json.dumps({"type": "query reply", "requested_data": local_data})
        
        return None
    

    # ==========================================
    # RING TRAVERSALS (SNOWBALL QUERIES)
    # ==========================================

    def handle_star_query(self) -> str:
        """
        Initiates a 'star' traversal to collect all data across the ring.
        """
        return self._initiate_ring_traversal("star")


    def request_overlay(self) -> str:
        """
        Initiates an 'overlay' traversal to map the global topology.
        """
        return self._initiate_ring_traversal("overlay")
    

    def _initiate_ring_traversal(self, traversal_type: str) -> str:
        """
        Generic method to start a snowball traversal and wait for its return.
        
        Blocks the calling thread using a threading.Event until the data packet 
        traverses the entire ring and returns to the origin node.
        """
        self.node.log.info(f"Initiating '{traversal_type}' ring traversal.")

        event = threading.Event()
        request_id = str(uuid.uuid4())[:16]
        self.node.state.pending_requests[request_id] = [event, None]

        initial_payload = [self._get_traversal_payload(traversal_type)]

        message = {
            "type": "ring traversal request",
            "traversal_type": traversal_type,
            "request_id": request_id,
            "origin_id": self.node.state.id,
            "payload": initial_payload
        }

        # Keep CLI parsing happy (CLI expects 'topology' for overlay and 'requested_data' for star)
        reply_key = "topology" if traversal_type == "overlay" else "requested_data"
        
        # If alone in the ring, return local instantly
        if self.node.state.successor['id'] == self.node.state.id:
            del self.node.state.pending_requests[request_id]
            return json.dumps({"type": f"{traversal_type} reply", reply_key: initial_payload})
        
        self.node.net.send_command(self.node.state.successor['ip'], self.node.state.successor['port'], json.dumps(message))
        
        finished = event.wait(timeout=4.0)
        
        if finished:
            full_data = self.node.state.pending_requests[request_id][1]
            self.node.log.info(f"Traversal '{traversal_type}' completed. Collected data from {len(full_data)} nodes.")
            del self.node.state.pending_requests[request_id]
            return json.dumps({"type": f"{traversal_type} reply", reply_key: full_data})
        else:
            self.node.log.error(f"Traversal '{traversal_type}' {request_id} failed: Timeout.")
            del self.node.state.pending_requests[request_id]
            return f"[!] ERROR: Timeout waiting for {traversal_type} traversal"
    

    def process_ring_traversal(self, cmd: Dict[str, Any]) -> str:
        """
        Relay Logic: Appends local node data to a passing snowball and forwards it.
        
        Detects cycles (packet returning to origin) to trigger event resolution, 
        and prevents infinite loops via visited node detection.
        """
        if cmd["origin_id"] == self.node.state.id:
            # CYCLE COMPLETE: We are the Origin.
            request_id = cmd["request_id"]
            self.node.log.debug(f"Traversal '{cmd['traversal_type']}' snowball returned to origin.")

            if request_id in self.node.state.pending_requests:
                self.node.state.pending_requests[request_id][1] = cmd["payload"]
                self.node.state.pending_requests[request_id][0].set()
        else:
            # CYCLE CONTINUE: Append my data and forward
            current_payload = cmd["payload"]

            # INFINITE LOOP DETECTION
            for block in current_payload:
                if block["node_id"] == self.node.state.id:
                    self.node.log.warning(f"Routing loop detected for {cmd['request_id']}. Dropping packet.")
                    return "ERROR: Routing Loop"

            self.node.log.debug(f"Appending local data to '{cmd['traversal_type']}' snowball and forwarding.")

            # Append the right type of data using our helper!
            current_payload.append(self._get_traversal_payload(cmd["traversal_type"]))
            cmd["payload"] = current_payload
            
            threading.Thread(target=self.node.net.send_command, 
                             args=(self.node.state.successor['ip'], self.node.state.successor['port'], json.dumps(cmd))).start()

        return "ACK"
    

    def _get_traversal_payload(self, traversal_type: str) -> Dict[str, Any]:
        """
        Gathers node-specific metadata or storage records for ring traversals.
        """
        if traversal_type == "star":
            return {
                "node_id": self.node.state.id,
                "data": self.node.storage.get_all(exclude_tombstones=True)
            }
        elif traversal_type == "overlay":
            # Delegate to Handoff Manager which calculates primary data logic
            primary_data = self.node.handoff.get_primary_data()
            primary_keys = [item["key"] for item in primary_data]
            replica_data = [item for item in self.node.storage.get_all(exclude_tombstones=True) if item["key"] not in primary_keys]
            
            return {
                "node_id": self.node.state.id,
                "ip": self.node.state.ip,
                "port": self.node.state.port,
                "primary_data": primary_data,
                "replica_data": replica_data
            }
