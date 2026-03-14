from __future__ import annotations
import json
import time
import threading
from typing import List, Dict, Any, Optional, TYPE_CHECKING

from chord.common.merkle import MerkleTree
from chord.common import utils

if TYPE_CHECKING:
    from ..node import ChordNode


class ReplicationManager:
    """
    Orchestrates distributed data consistency and background anti-entropy sync.
    
    This manager routes all read/write requests to the currently active 
    consistency strategy (Eventual, Chain, or Quorum) and maintains a background 
    worker to resolve data drift using Merkle Trees.
    """

    def __init__(self, node: ChordNode) -> None:
        """
        Initializes the replication manager with a reference to the parent node.
        """
        self.node: ChordNode = node


    # ==========================================
    # ENTRY POINTS
    # ==========================================

    def handle_insert(self, cmd: Dict[str, Any]) -> str:
        """
        Routes an incoming insert request to the configured consistency strategy.
        """
        consistency = self.node.state.consistency
        
        if consistency == "linear":
            return self._quorum_insert(cmd)
        elif consistency == "chain":
            return self._chain_insert(cmd)
        
        return self._eventual_insert(cmd)


    def handle_query(self, cmd: Dict[str, Any]) -> str:
        """
        Routes an incoming query request to the configured consistency strategy.
        """
        consistency = self.node.state.consistency
        
        if consistency == "linear":
            return self._quorum_query(cmd)
        elif consistency == "chain":
            return self._chain_query(cmd)
        
        return self._eventual_query(cmd)
    

    # ===========================================
    # ANTI-ENTROPY (MERKLE TREE SYNC)
    # ===========================================
    def anti_entropy_worker(self) -> None:
        """
        Continuous background thread that resolves data drift with replicas.
        
        This worker builds a local Merkle Tree of primary data and compares 
        root hashes with successors in O(1) time to detect inconsistencies.
        """
        while True:
            time.sleep(10.0) # Run every 10 seconds
            
            if not self.node.state.hasJoined:
                continue

            # 1. Get ONLY the data I am the primary Head for (Include Tombstones so hashes match perfectly!)
            primary_data = self.node.handoff.get_primary_data(exclude_tombstones=False)
            
            # 2. Build the Merkle Tree for O(1) comparison
            tree = MerkleTree(primary_data)
            root_hash = tree.get_root_hash()

            cmd = {
                "type": "merkle root check",
                "root_hash": root_hash,
                "range_start": self.node.state.predecessor["id"],
                "range_end": self.node.state.id
            }

            valid_replicas = [r for r in self.node.state.successor_list if r["id"] != self.node.state.id]
            for replica in valid_replicas:
                # Run the sync asynchronously to avoid blocking the worker
                threading.Thread(target=self._sync_with_replica, args=(replica, cmd, primary_data)).start()


    def _sync_with_replica(self,
            replica: Dict[str, Any],
            cmd: Dict[str, Any],
            primary_data: List[Dict[str, Any]]
        ) -> None:
        """
        Negotiates a delta-sync with a specific replica upon root hash mismatch.
        """
        reply_raw = self.node.net.send_command(replica["ip"], replica["port"], json.dumps(cmd))
        
        # If network dropped or application error, abort
        if reply_raw is None or "error" in reply_raw.lower() or reply_raw == "UNKNOWN":
            return
        
        try:
            reply = json.loads(reply_raw)
            if reply.get("status") == "mismatch":
                self.node.log.warning(f"Anti-Entropy: Hash mismatch with replica {replica['id']}... Resolving diff.")
                
                replica_summary = reply.get("replica_summary", {})
                data_to_heal = []

                # Find exactly which keys the replica is missing or has stale timestamps for
                for item in primary_data:
                    key = item["key"]
                    head_ts = item["timestamp"]
                    rep_ts = replica_summary.get(key)
                    
                    if rep_ts is None or head_ts > rep_ts:
                        data_to_heal.append(item)

                if data_to_heal:
                    self.node.log.info(f"Anti-Entropy: Pushing {len(data_to_heal)} missing/stale keys to {replica['id']}.")
                    heal_cmd = {
                        "type": "insert request",
                        "requester_ip": self.node.state.ip,
                        "requester_port": self.node.state.port,
                        "data_to_insert": data_to_heal,
                        "is_replica_write": True
                    }
                    self.node.net.send_command(replica["ip"], replica["port"], json.dumps(heal_cmd))
        except json.JSONDecodeError:
            pass


    def handle_merkle_root_check(self, cmd: Dict[str, Any]) -> str:
        """
        Replica-side handler for root hash verification.
        
        Calculates a local Merkle Tree for the requested ID range and returns 
        a data summary if a mismatch is detected.
        """
        range_start = cmd["range_start"]
        range_end = cmd["range_end"]
        head_root = cmd["root_hash"]

        # 1. Gather ONLY the data that belongs to the Head's mathematical range
        target_data = []
        for item in self.node.storage.get_all(exclude_tombstones=False):
            if utils.is_between(item["key"], range_start, range_end) or item["key"] == range_end:
                target_data.append(item)

        # 2. Build our own Merkle Tree
        tree = MerkleTree(target_data)
        my_root = tree.get_root_hash()

        # 3. Fast O(1) Check
        if my_root == head_root:
            return json.dumps({"status": "match"})

        # 4. Mismatch! Send our state summary so the Head can calculate the exact diff
        target_keys = [item["key"] for item in target_data]
        my_summary = self.node.storage.get_state_summary(target_keys)
        
        return json.dumps({
            "status": "mismatch",
            "replica_summary": my_summary
        })


    # ===========================================
    # EVENTUAL CONSISTENCY STRATEGY
    # ===========================================

    def _eventual_insert(self, cmd: Dict[str, Any]) -> str:
        """
        Fire-and-forget write: commits locally and broadcasts to replicas asynchronously.
        """
        # 1. Prevent Broadcast Storm (Replica logic)
        if cmd.get("is_replica_write"):
            self.node.log.debug("Eventual Replica: Silently loading background write.")
            self.node.storage.bulk_load(cmd["data_to_insert"])
            return "ACK"

        # --- COORDINATOR LOGIC ---
        data_list = cmd["data_to_insert"]
        
        # 2. Write Locally immediately
        self.node.storage.bulk_load(data_list)

        # 3. Parallel Network Blast (Background)
        # We don't wait for ACKs. We just shoot the data into the network.
        cmd.pop("chain_index", None)
        cmd["is_replica_write"] = True
        
        valid_replicas = [r for r in self.node.state.successor_list if r["id"] != self.node.state.id]

        def send_to_replica(replica_ip, replica_port):
            # Fire and forget!
            self.node.net.send_command(replica_ip, replica_port, json.dumps(cmd))

        if valid_replicas:
            self.node.log.info(f"Eventual Write: Key {str(data_list[0]['key'])} written locally. Blasting to {len(valid_replicas)} replicas in background.")
            for replica in valid_replicas:
                threading.Thread(target=send_to_replica, args=(replica["ip"], replica["port"])).start()
        elif self.node.state.k > 1:
            # ONLY warn if we EXPECTED backups but found none!
            self.node.log.warning(f"Eventual Write: Key {str(data_list[0]['key'])} written locally, but no valid replicas exist to blast to.")
        else:
            # Silent fallback for k=1 tests
            self.node.log.debug(f"Eventual Write: Key {str(data_list[0]['key'])} written locally. (k=1, no backups needed).")

        return "ACK"


    def _eventual_query(self, cmd: Dict[str, Any]) -> str:
        """
        Fast read: serves data directly from the local head node without verification.
        """
        key = cmd["key"]

        self.node.log.info(f"Eventual Read: Serving key {str(key)} instantly from local Head data.")
        
        local_data = self.node.storage.query(key)
        
        return json.dumps({
            "type": "query reply", 
            "requested_data": local_data
        })


    # =============================================
    # LINEARIZABILITY STRATEGY: Chain Replication
    # =============================================

    def _chain_insert(self, cmd: Dict[str, Any]) -> str:
        """
        Synchronous pipe: Head -> Middle -> Tail. Returns ACK only when Tail commits.
        """
        data_list = cmd["data_to_insert"]
        
        # 1. ALWAYS write to local storage first (Everyone in the chain does this)
        self.node.storage.bulk_load(data_list)

        # 2. Where are we in the chain?
        # If the client sent this, "chain_index" doesn't exist, so we default to 0 (We are the Head).
        current_index = cmd.get("chain_index", 0)
        
        self.node.log.debug(f"Chain Write: Key {cmd['data_to_insert'][0]['key']} loaded. My Chain Index: {current_index} (Tail={self.node.state.k - 1})")

        # 3. Am I the Tail? 
        if current_index >= self.node.state.k - 1:
            self.node.log.info(f"Chain Write: Reached the Tail for key {cmd['data_to_insert'][0]['key']}. Sending ACK back up the chain.")

            # Returning "ACK" will send the success message back up the chain.
            return "ACK"
        else:
            # 4. I AM NOT THE TAIL. Forward to the next link in the chain.
            # Increment the counter so the next node knows its place
            cmd["chain_index"] = current_index + 1
            
            # The next link in the chain is ALWAYS my immediate successor
            next_link = self.node.state.successor

            # Short-circuit if the chain wraps around to me!
            if next_link["id"] == self.node.state.id:
                self.node.log.warning(f"Chain Write: Chain wrapped around to myself. Aborting early with ACK.")
                return "ACK"

            # Send it synchronously
            self.node.log.debug(f"Chain Write: Forwarding to next link: {next_link['id']}.")
            reply = self.node.net.send_command(
                next_link['ip'], 
                next_link['port'], 
                json.dumps(cmd)
            )

            # Pass the Tail's ACK back up to whoever called me
            return reply


    def _chain_query(self, cmd: Dict[str, Any]) -> str:
        """
        Strict read: request is forwarded to the Tail to ensure linearizability.
        """
        key = cmd["key"]

        # 1. Where are we in the chain?
        current_index = cmd.get("chain_index", 0)

        self.node.log.debug(f"Chain Read: Request for key {str(key)}. My Chain Index: {current_index} (Tail={self.node.state.k - 1}).")

        # 2. Am I the Tail?
        if current_index >= self.node.state.k - 1:
            self.node.log.info(f"Chain Read: I am the Tail. Legally serving strict read for key {str(key)}.")

            # I AM THE TAIL! I am legally allowed to read the data.
            local_data = self.node.storage.query(key)

            # Package it and return it (this flows back up the chain)
            return json.dumps({
                "type": "query reply", 
                "requested_data": local_data
            })
            
        else:
            # 3. I AM NOT THE TAIL. Forward to the next link.
            cmd["chain_index"] = current_index + 1
            next_link = self.node.state.successor

            # Short-circuit if the chain wraps around to me!
            if next_link["id"] == self.node.state.id:
                local_data = self.node.storage.query(key)
                return json.dumps({
                    "type": "query reply", 
                    "requested_data": local_data
                })
            
            # Send it synchronously.
            self.node.log.debug(f"Chain Read: Forwarding request to next link: {next_link['id']}.")
            reply = self.node.net.send_command(
                next_link['ip'], 
                next_link['port'], 
                json.dumps(cmd)
            )
            
            # Pass the Tail's data back up to whoever called me
            return reply


    # =============================================
    # LINEARIZABILITY STRATEGY: Quorum Replication
    # =============================================

    def _quorum_insert(self, cmd: Dict[str, Any]) -> str:
        """
        Parallel write: returns success only after a majority (W) of replicas acknowledge.
        """
        # Prevent Broadcast Storm
        if cmd.get("is_replica_write"):
            self.node.log.debug("Quorum Replica: Silently casting vote and saving data.")
            self.node.storage.bulk_load(cmd["data_to_insert"])
            return "ACK"

        # --- COORDINATOR LOGIC ---
        # 1. GUARD CLAUSE: Is Quorum even mathematically possible?
        W = (self.node.state.k // 2) + 1
        valid_replicas = [r for r in self.node.state.successor_list if r["id"] != self.node.state.id]
        
        if len(valid_replicas) + 1 < W:
            self.node.log.error(f"Quorum Write Failed: Math impossible for key {str(data_list[0]['key'])}. Need {W} nodes, but only have {len(valid_replicas) + 1}.")
            return "[!] ERROR: Write Quorum mathematically impossible. Not enough nodes."

        # 2. PROCEED WITH WRITE
        data_list = cmd["data_to_insert"]
        
        # Thread-safe vote tracking
        ack_count = 0
        lock = threading.Lock()
        quorum_reached = threading.Event()

        # ALWAYS WRITE LOCALLY FIRST (This counts as 1 fast vote!)
        self.node.storage.bulk_load(data_list)

        # Count our local write
        with lock:
            ack_count += 1
            if ack_count >= W:
                quorum_reached.set()

        # Set the flag so the replicas don't forward this again!
        cmd.pop("chain_index", None)
        cmd["is_replica_write"] = True

        # 3. PARALLEL NETWORK BLAST
        def send_to_replica(replica_ip, replica_port):
            nonlocal ack_count
            
            # Send the exact same insert request to the replica
            reply = self.node.net.send_command(replica_ip, replica_port, json.dumps(cmd))
            
            if reply == "ACK":
                with lock:
                    ack_count += 1
                    # Wake up the main thread the instant we hit W votes
                    if ack_count >= W:
                        quorum_reached.set()

        self.node.log.info(f"Quorum Write: Blasting key {str(data_list[0]['key'])} to replicas. Waiting for W={W} ACKs.")
        
        # Spin up a background thread for every node in the valid_replicas list
        for replica in valid_replicas:
            threading.Thread(target=send_to_replica, args=(replica["ip"], replica["port"])).start()

        # 4. WAIT FOR QUORUM
        # Block the client until 'quorum_reached.set()' is called (or timeout)
        success = quorum_reached.wait(timeout=4.0) 

        if success:
            self.node.log.info(f"Quorum Write: Consensus REACHED for key {str(data_list[0]['key'])}. Returning ACK to client.")
            return "ACK"
        else:
            self.node.log.error(f"Quorum Write: TIMEOUT for key {str(data_list[0]['key'])}. Failed to reach W={W} votes.")
            return "ERROR: Failed to reach Write Quorum"


    def _quorum_query(self, cmd: Dict[str, Any]) -> str:
        """
        Parallel read: collects R responses and returns the data with the freshest timestamp.
        """
        key = cmd["key"]

        # Prevent Broadcast Storm: If I am a backup, just return my data and stop!
        if cmd.get("is_replica_read"):
            self.node.log.debug(f"Quorum Replica: Serving read request for key {str(key)}.")
            local_data = self.node.storage.query(key)
            return json.dumps({"type": "query reply", "requested_data": local_data})

        # --- COORDINATOR LOGIC ---
        # 1. GUARD CLAUSE: Is Quorum mathematically possible?
        R = (self.node.state.k // 2) + 1
        valid_replicas = [r for r in self.node.state.successor_list if r["id"] != self.node.state.id]

        if len(valid_replicas) + 1 < R:
            self.node.log.warning(f"Quorum Read Impossible: Need {R} nodes, but only have {len(valid_replicas) + 1}. Data may be stale.")
            return json.dumps({
                "type": "query reply", 
                "requested_data": [{"error": "[!] Read Quorum impossible. Data may be stale."}]
            })

        # 2. PROCEED WITH READ
        replies = []
        lock = threading.Lock()
        quorum_reached = threading.Event()

        # Helper function to process successful reads
        def add_reply(data_list):
            with lock:
                # We save the data (even if it is empty) so we can count the vote
                replies.append(data_list)
                
                # If we have reached our Read Quorum (R), wake up the main thread!
                if len(replies) >= R:
                    quorum_reached.set()

        # READ LOCALLY FIRST (This counts as 1 fast vote!)
        local_data = self.node.storage.query(key)
        add_reply(local_data)

        # Set the flag so replicas don't broadcast!
        cmd.pop("chain_index", None)
        cmd["is_replica_read"] = True

        # 3. PARALLEL NETWORK BLAST
        def ask_replica(replica_ip, replica_port):
            raw_reply = self.node.net.send_command(replica_ip, replica_port, json.dumps(cmd))

            # 1. Did the network fail?
            if raw_reply is None:
                return

            # 2. It didn't fail, so we can safely parse the JSON!
            try:
                reply_json = json.loads(raw_reply)
                
                # 3. Was it an application error (like UNKNOWN_COMMAND)?
                if "error" in reply_json:
                    return
                
                # Success!
                add_reply(reply_json.get("requested_data", []))

            except json.JSONDecodeError:
                pass

        self.node.log.info(f"Quorum Read: Blasting query for key {str(key)}. Waiting for R={R} responses.")
        
        # Spin up parallel threads to ask the backups
        for replica in valid_replicas:
            threading.Thread(target=ask_replica, args=(replica["ip"], replica["port"])).start()

        # 4. WAIT FOR QUORUM (R)
        success = quorum_reached.wait(timeout=4.0)

        # 5. CONFLICT RESOLUTION
        if success and len(replies) > 0:
            valid_items = []
            
            # Extract the actual data from the replies (ignoring empty [] responses)
            for data_list in replies:
                if data_list:  
                    valid_items.append(data_list[0])

            # If all responding nodes had empty data, the key doesn't exist
            if not valid_items:
                self.node.log.debug(f"Quorum Read: Consensus reached. Key {str(key)} does not exist.")
                return json.dumps({"type": "query reply", "requested_data": []})

            # We sort the valid items by timestamp in descending order (newest first)
            valid_items.sort(key=lambda x: x.get("timestamp", 0.0), reverse=True)
            
            # The freshest data is the first item in the sorted list!
            freshest_data = valid_items[0]

            self.node.log.info(f"Quorum Read: Consensus REACHED for key {str(key)}. Returning freshest timestamp.")

            return json.dumps({
                "type": "query reply", 
                "requested_data": [freshest_data]
            })
        
        self.node.log.error(f"Quorum Read: TIMEOUT for key {str(key)}. Failed to reach R={R} responses.")

        return json.dumps({
            "type": "query reply", 
            "requested_data": [] # Quorum failed
        })
