from __future__ import annotations
import time
import random
import json
import threading
from typing import Dict, Any, TYPE_CHECKING

from ..common import utils

if TYPE_CHECKING:
    from ..node import ChordNode


class TopologyManager:
    """
    Manages join, depart, stabilization, and failure recovery of the Chord ring.
    
    This manager runs several continuous background threads to monitor network 
    health, update logarithmic routing tables, and dynamically stitch the 
    network topology together as nodes connect, disconnect, or crash.
    """

    def __init__(self, node: ChordNode) -> None:
        """
        Initializes the topology manager with a reference to the parent node.
        """
        self.node: ChordNode = node


    # ==========================================
    # DISPATCHER HANDLERS
    # ==========================================

    def handle_update_predecessor(self, cmd: Dict[str, Any]) -> str:
        """
        Processes an inbound notification that this node has a new predecessor.
        
        This usually occurs during a Join or a Stabilization cycle when a new 
        node inserts itself directly behind us in the ring.
        """
        current_pred_id = self.node.state.predecessor.get("id") if self.node.state.predecessor else None

        # Only execute the update and log if it is ACTUALLY a new predecessor!
        if cmd["id"] != current_pred_id:
            self.node.state.predecessor = {"ip": cmd["ip"], "port": cmd["port"], "id": cmd["id"]}
            self.node.log.info(f"Topology Shift: Predecessor updated to {cmd['id']}.")
            
        return json.dumps({"status": "success"})


    def handle_update_successor(self, cmd: Dict[str, Any]) -> str:
        """
        Processes an inbound notification that this node has a new immediate successor.
        
        This triggers a background rebuild of the k-length successor list to 
        ensure fault-tolerance is immediately restored along the new network path.
        """
        current_succ_id = self.node.state.successor.get("id") if self.node.state.successor else None

        if cmd["id"] != current_succ_id:
            self.node.state.successor = {"ip": cmd["ip"], "port": cmd["port"], "id": cmd["id"]}
            self.node.log.debug(f"Topology Shift: Successor updated to {cmd['id']}. Rebuilding successor list.")
            
            threading.Thread(target=self.update_successor_list).start()
        
        return json.dumps({"status": "success"})
    

    def handle_force_update(self, cmd: Dict[str, Any]) -> str:
        """
        Forces a manual rebuild of the successor list. Often triggered by a 
        predecessor whose own successor list has structurally shifted.
        """
        threading.Thread(target=self.update_successor_list).start()

        return json.dumps({"status": "success"})


    # ==========================================
    # RING OPERATIONS (JOIN & DEPART)
    # ==========================================

    def join(self, entry_ip: str, entry_port: int) -> bool:
        """
        Executes a safe, atomic 4-step join operation via any known entry node.
        
        Steps:
        1. Find True Successor: Ask the entry node to route us to our mathematical successor.
        2. Get Predecessor: Ask our new successor who their current predecessor is.
        3. Data Handoff: Receive the keyspace that now mathematically belongs to us.
        4. Active Rewiring: Tell our new neighbors to point their pointers at us.

        Returns:
            True if the join and data transfer succeeded, False if it failed/aborted.
        """
        if entry_ip == self.node.state.ip and entry_port == self.node.state.port:
            self.node.state.hasJoined = True
            self.node.state.init_finger_table()
            self.node.log.info("Initialized as the first node in a new ring.")
            return True

        self.node.log.info(f"Attempting to join ring via entry node {entry_ip}:{entry_port}.")

        # STEP 1: FIND TRUE SUCCESSOR
        self.node.log.debug(f"Requesting my true successor from entry node.")
        
        # Pass the entry node directly to the routing engine! No temporary state needed.
        entry_node = {"ip": entry_ip, "port": entry_port}
        res = self.node.routing.get_successor_port(self.node.state.id, entry_node=entry_node)

        # GUARD: Did the entry node die or timeout?
        if not res or isinstance(res, str) and "error" in res.lower():
            self.node.log.error("[!] Join Aborted: Entry node is offline or routing failed.")
            return False

        self.node.state.successor = {
            "ip": res["target_ip"],
            "port": res["target_port"],
            "id": utils.get_sha1_hash(f"{res['target_ip']}:{res['target_port']}")
        }
        self.node.log.debug(f"Resolved true successor: {self.node.state.successor['id']}.")

        # STEP 2: GET PREDECESSOR
        self.node.log.debug("Wiring predecessor pointer.")
        p_resp = self.node.net.send_command(
            self.node.state.successor['ip'],
            self.node.state.successor['port'],
            json.dumps({"type": "get predecessor"})
        )

        # GUARD: Did our true successor crash right as we found it?
        if p_resp is None or "error" in p_resp.lower():
            self.node.log.error("[!] Join Aborted: True successor died before sending predecessor data.")
            return False

        pred_data = json.loads(p_resp)
        self.node.state.predecessor = pred_data if pred_data else self.node.state.successor

        # STEP 3: DATA HANDOFF (Passive Receipt)
        self.node.log.debug(f"Requesting data handoff from successor.")
        resp = self.node.net.send_command(
            self.node.state.successor['ip'],
            self.node.state.successor['port'],
            json.dumps({
                "type": "data transfer request", 
                "requester_id": self.node.state.id,
                "requester_pred_id": self.node.state.predecessor["id"]
            })
        )

        # GUARD: Did the successor die while packaging the data?
        if resp is None or "error" in resp.lower():
            self.node.log.error("[!] Join Aborted: Successor crashed during data handoff.")
            return False

        try:
            incoming_data = json.loads(resp).get("transfered_data", [])
            self.node.storage.bulk_load(incoming_data)
            self.node.log.info(f"Successfully loaded {len(incoming_data)} keys from successor.")
        except json.JSONDecodeError:
            self.node.log.error(f"[!] Join Aborted: Received malformed data. Raw response: {resp}")
            return False

        # STEP 4: ACTIVE REWIRING
        msg1 = json.dumps({"type": "update predecessor", "ip": self.node.state.ip, "port": self.node.state.port, "id": self.node.state.id})
        msg2 = json.dumps({"type": "update successor", "ip": self.node.state.ip, "port": self.node.state.port, "id": self.node.state.id})

        # We fire these and assume they land. If they drop, our stabilize_worker will fix it.
        self.node.net.send_command(self.node.state.successor['ip'], self.node.state.successor['port'], msg1)
        self.node.net.send_command(self.node.state.predecessor['ip'], self.node.state.predecessor['port'], msg2)

        self.node.state.hasJoined = True
        self.node.state.init_finger_table()
        self.update_successor_list()

        self.node.log.info(f"Wired In: Pred {self.node.state.predecessor['id']}... <-> Me <-> Succ {self.node.state.successor['id']}...")
        
        return True

    def depart(self) -> None:
        """
        Executes a 2-Phase Commit (2PC) graceful departure from the swarm.
        
        Phase 1: Transfers all locally held primary and replica data to the 
                 successor and waits for a synchronous disk ACK.
        Phase 2: Actively rewires the predecessor and successor to point at 
                 each other, effectively stitching the ring closed before exit.
        """
        if self.node.state.successor['id'] == self.node.state.id:
            self.node.log.info("I was alone in the ring. Departing.")
            return

        self.node.log.info(f"Initiating 2PC Depart. Handing off data to {self.node.state.successor['id']}.")

        # PHASE 1: DATA COMMIT (PREPARE & TRANSFER)
        data_export = self.node.storage.get_all()
        message = {"type": "data handoff", "data_to_insert": data_export}

        self.node.log.info(f"PHASE 1: Handing off {len(data_export)} keys to successor and waiting for disk ACK.")

        reply = self.node.net.send_command(
            self.node.state.successor["ip"], 
            self.node.state.successor["port"], 
            json.dumps(message)
        )

        # Did the network drop, or did the successor return an error?
        if reply is None or "error" in reply.lower():
            self.node.log.error(f"[!] 2PC ABORT: Successor failed to acknowledge data receipt. Reply: {reply}")
            return # Abort! Node stays alive so data isn't lost.
        
        # PHASE 2: ACTIVE REWIRING
        self.node.log.info("PHASE 2: Data safely committed. Actively rewiring neighbors.")

        # 1. Tell Predecessor: "Your new successor is my current successor"
        self.node.log.debug(f"Wiring predecessor {self.node.state.predecessor['id']} directly to my successor.")
        pred_reply = self.node.net.send_command(
            self.node.state.predecessor['ip'],
            self.node.state.predecessor['port'],
            json.dumps({
                "type": "update successor",
                "ip": self.node.state.successor['ip'],
                "port": self.node.state.successor['port'],
                "id": self.node.state.successor['id']
            })
        )

        if pred_reply is None or "error" in pred_reply.lower():
            self.node.log.warning("[!] Predecessor failed to ACK pointer update. Stabilizer will catch it eventually.")

        # 2. Tell Successor: "Your new predecessor is my current predecessor"
        self.node.log.debug(f"Wiring successor {self.node.state.successor['id']} directly to my predecessor.")
        succ_reply = self.node.net.send_command(
            self.node.state.successor['ip'],
            self.node.state.successor['port'],
            json.dumps({
                "type": "update predecessor",
                "ip": self.node.state.predecessor['ip'],
                "port": self.node.state.predecessor['port'],
                "id": self.node.state.predecessor['id']
            })
        )

        if succ_reply is None or "error" in succ_reply.lower():
            self.node.log.warning("[!] Successor failed to ACK pointer update. Stabilizer will catch it eventually.")

        self.node.log.info("Node departed gracefully. Ring stitched successfully.")
        self.node.net.stop_server()


    # ==========================================
    # TOPOLOGY MAINTENANCE & FAULT TOLERANCE
    # ==========================================

    def update_successor_list(self) -> None:
        """
        Builds and maintains the k-length fault-tolerance successor list.
        
        This queries the immediate successor for their list, appends it, and 
        trims it to k-1. If the list structurally changes, it automatically 
        triggers replication heals or replica evictions to maintain consistency.
        """
        # If I am the only node in the ring, I have no backups. Clear the list and abort!
        if self.node.state.successor['id'] == self.node.state.id:
            self.node.state.successor_list = []
            return

        # 1. Start with my immediate successor
        new_list = [self.node.state.successor]

        # 2. Ask them for their list (Recursive step)
        try:
            # We don't use threading here to keep it simple and linear for now
            response = self.node.net.send_command(
                self.node.state.successor['ip'],
                self.node.state.successor['port'],
                json.dumps({"type": "get successor list"})
            )

            if response not in (None, "UNKNOWN", "ERROR: No response"):
                succ_neighbors = json.loads(response)
                
                # Append their neighbors to my list
                for node in succ_neighbors:
                    # Avoid duplicates and adding myself (important for small rings)
                    if node["id"] != self.node.state.id and node["id"] != self.node.state.successor['id']:
                        # Don't blindly trust our successor's list! Ping the node to ensure 
                        # it isn't a "ghost" node from a stale topology state
                        ping_res = self.node.net.send_command(node['ip'], node['port'], json.dumps({"type": "ping"}))
                        
                        if ping_res == "PONG":
                            new_list.append(node)
                        else:
                            self.node.log.debug(f"Topology: Rejected dead ghost node {node['id']} from successor's list.")

        except Exception as e:
            self.node.log.error(f"Error updating successor list: {e}")

        # 3. Trim to length k-1
        old_list = self.node.state.successor_list
        old_list_ids = [n['id'] for n in old_list]
        
        self.node.state.successor_list = new_list[:self.node.state.k - 1]
        new_list_ids = [n['id'] for n in self.node.state.successor_list]

        # ONLY heal/evict/ripple if the list ACTUALLY changed!
        if old_list_ids != new_list_ids:
            self.node.log.info(f"Successor list updated: {[n['id'] for n in self.node.state.successor_list]}")

            # 1. HEAL: Push primary data forward
            threading.Thread(target=self.node.handoff.heal_replication).start()

            # 2. EVICT: Tell dropped nodes to delete our primary data
            dropped_nodes = [n for n in old_list if n['id'] not in new_list_ids]
            if dropped_nodes:
                primary_keys = [item["key"] for item in self.node.handoff.get_primary_data()]

                if primary_keys:
                    msg = json.dumps({"type": "drop keys", "keys": primary_keys})
                    for dn in dropped_nodes:
                        self.node.log.info(f"Evicting replicas from dropped node {dn['id']}.")
                        threading.Thread(target=self.node.net.send_command, args=(dn['ip'], dn['port'], msg)).start()

            # 3. RIPPLE: Tell predecessor to check their list
            if self.node.state.predecessor and self.node.state.predecessor["id"] != self.node.state.id:
                msg = json.dumps({"type": "force update successor list"})
                threading.Thread(target=self.node.net.send_command, args=(self.node.state.predecessor['ip'], self.node.state.predecessor['port'], msg)).start()


    def stabilize_worker(self) -> None:
        """
        Continuous background thread that monitors neighbors and heals the ring.
        
        This executes the standard Chord stabilization protocol:
        1. Ping predecessor. If dead, execute self-promotion of data.
        2. Query successor for its predecessor. If it's a new node, adopt it.
        3. Inform successor of our existence to finalize topology links.
        """
        while True:
            time.sleep(3.0) # Heartbeat every 3 seconds

            if not self.node.state.hasJoined or self.node.state.successor['id'] == self.node.state.id:
                continue # Skip if alone or initializing

            # 1. CHECK PREDECESSOR HEALTH
            if self.node.state.predecessor and self.node.state.predecessor['id'] != self.node.state.id:
                res_pred = self.node.net.send_command(
                    self.node.state.predecessor['ip'],
                    self.node.state.predecessor['port'],
                    json.dumps({"type": "ping"})
                )
                if res_pred is None:
                    self.node.log.error(f"[!] Predecessor {self.node.state.predecessor['id']} died! Initiating auto-recovery.")
                    self.handle_predecessor_failure()

            # 2. CHECK SUCCESSOR HEALTH & STABILIZE
            res_succ = self.node.net.send_command(
                self.node.state.successor['ip'],
                self.node.state.successor['port'],
                json.dumps({"type": "get predecessor"})
            )

            if res_succ is None:
                self.node.log.error(f"[!] Successor {self.node.state.successor['id']} died! Falling back to successor list.")
                self.handle_successor_failure()
            else:
                # Normal Chord Stabilization (Handles concurrent joins safely)
                try:
                    succ_pred = json.loads(res_succ)
                    if succ_pred and succ_pred['id'] != self.node.state.id:
                        # If there is a new node between me and my successor, adopt it!
                        if utils.is_between(succ_pred['id'], self.node.state.id, self.node.state.successor['id']):
                            self.node.log.info(f"Stabilization: Adopting new successor {succ_pred['id']}")
                            self.node.state.successor = succ_pred

                    # Tell my successor that I exist
                    self.node.net.send_command(
                        self.node.state.successor['ip'],
                        self.node.state.successor['port'],
                        json.dumps({
                            "type": "update predecessor",
                            "ip": self.node.state.ip,
                            "port": self.node.state.port,
                            "id": self.node.state.id
                        })
                    )

                    # Ensure my k-radius is fully mapped
                    self.update_successor_list()
                except Exception as e:
                    self.node.log.warning(f"Stabilization parsing error: {e}")

    def handle_predecessor_failure(self) -> None:
        """
        Executes self-promotion of data when the predecessor dies (e.g., SIGKILL).
        
        By temporarily clearing the predecessor pointer, the node mathematically 
        absorbs the dead node's keyspace. This promotes all inherited replicas 
        into Primary Data, triggering a replication cycle to restore k-factor.
        """
        # Set predecessor to self. The actual new predecessor will find us via stabilization.
        self.node.state.predecessor = {"ip": self.node.state.ip, "port": self.node.state.port, "id": self.node.state.id}
        
        # Force a replication cycle to push newly promoted primary data down the chain
        threading.Thread(target=self.node.handoff.heal_replication).start()


    def handle_successor_failure(self):
        """
        Stitches the ring back together using the k-length backup list.
        
        Iterates through the known successors, bypassing the dead node, and 
        actively wires the first surviving neighbor as the new primary successor.
        """
        alive_successor = None
        
        # Iterate through our backup list to find the next surviving node
        for node in self.node.state.successor_list:
            if node['id'] == self.node.state.successor['id']:
                continue # Skip the dead guy

            res = self.node.net.send_command(node['ip'], node['port'], json.dumps({"type": "ping"}))
            if res == "PONG":
                alive_successor = node
                break # Found a survivor!

        if alive_successor:
            self.node.log.info(f"Ring Stitched: New successor is {alive_successor['id']}.")
            self.node.state.successor = alive_successor
            
            # Immediately tell the survivor that I am their new predecessor
            self.node.net.send_command(
                self.node.state.successor['ip'],
                self.node.state.successor['port'],
                json.dumps({
                    "type": "update predecessor",
                    "ip": self.node.state.ip,
                    "port": self.node.state.port,
                    "id": self.node.state.id
                })
            )
            
            # Rebuild the list from the new successor
            self.update_successor_list()
        else:
            self.node.log.info("All k successors are dead! The ring is irrevocably broken.")
            self.node.state.successor = {"ip": self.node.state.ip, "port": self.node.state.port, "id": self.node.state.id}
            self.node.state.predecessor = {"ip": self.node.state.ip, "port": self.node.state.port, "id": self.node.state.id}
            
            # Purge the dead nodes from the local state so they stop haunting the CLI
            self.node.state.successor_list = []


    def fix_fingers_worker(self):
        """
        Continuous background thread that optimizes O(log N) routing tables.
        
        Selects a random finger index, queries the ring for the true successor, 
        and updates the table. Includes a batch-optimization that cascades the 
        discovered node to subsequent fingers to massively reduce TCP traffic.
        """
        while True:
            time.sleep(0.5) 
            
            if not self.node.state.hasJoined or not self.node.state.finger_table:
                continue

            try:
                # 1. Pick a random finger to fix (Prevents network sync storms)
                idx = random.randint(0, self.node.state.m - 1)
                
                target_id = self.node.state.finger_table[idx]["start"]
                res = self.node.routing.get_successor_port(target_id)
                
                if isinstance(res, dict) and "target_ip" in res:
                    new_node = {
                        "ip": res["target_ip"],
                        "port": res["target_port"],
                        "id": res.get("id", utils.get_sha1_hash(f"{res['target_ip']}:{res['target_port']}"))
                    }
                    
                    # Update the random finger we picked
                    self.node.state.finger_table[idx]["node"] = new_node

                    # 2. THE BATCHING OPTIMIZATION
                    # Cascade this new node forward to subsequent fingers IF their start IDs 
                    # also fall before this new node. This saves DOZENS of network calls!
                    next_idx = (idx + 1) % self.node.state.m

                    while next_idx != idx: # Don't loop forever
                        next_start = self.node.state.finger_table[next_idx]["start"]

                        # If the next finger's jump is still smaller than the node we just found,
                        # we can safely point it to the same node without making a TCP request!
                        # We only know the space is empty between the jump's start and the node we found!
                        if utils.is_between(next_start, target_id, new_node["id"]) or next_start == new_node["id"]:
                            self.node.state.finger_table[next_idx]["node"] = new_node
                            next_idx = (next_idx + 1) % self.node.state.m
                        else:
                            # The jump is now big enough to overshoot this node. Break the batch.
                            break

            except Exception as e:
                self.node.log.debug(f"Finger table update suppressed an error: {e}")


    def partition_healer_worker(self):
        """
        Continuous background thread dedicated to Split-Brain network recovery.
        
        Periodically selects a random historical peer from the local Rolodex 
        and probes them. If the peer belongs to an isolated sub-ring but possesses 
        an ID mathematically closer than the current successor, the node actively 
        bridges the gap, allowing the stabilizer to securely zip the rings together.
        """
        while True:
            # Run very slowly (e.g., every 15 seconds) so it doesn't spam the network
            # time.sleep(15.0)
            time.sleep(5.0)

            if not self.node.state.hasJoined or not self.node.state.known_peers:
                continue

            try:
                # 1. Pick a random node from our entire history
                peer_str = random.choice(list(self.node.state.known_peers))
                peer_ip, peer_port = peer_str.split(":")

                entry_node = {"ip": peer_ip, "port": int(peer_port)}

                # 2. Ask this random node: "Who do YOU think my successor is?"
                # We reuse our existing O(log N) routing, but we force it to start at the random node!
                res = self.node.routing.get_successor_port(self.node.state.id, entry_node=entry_node)

                if isinstance(res, dict) and "target_ip" in res:
                    candidate_id = res.get("id", utils.get_sha1_hash(f"{res['target_ip']}:{res['target_port']}"))

                    # Ignore it if it just points back to ourselves
                    if candidate_id == self.node.state.id:
                        continue

                    # 3. THE MATHEMATICAL BRIDGE CHECK
                    # If this candidate falls strictly between us and our current successor, 
                    # it means we found a node from a merged/recovering ring!
                    current_succ_id = self.node.state.successor["id"]

                    if current_succ_id == self.node.state.id or utils.is_between(candidate_id, self.node.state.id, current_succ_id):
                        self.node.log.warning(f"PARTITION HEALED! Discovered closer successor {candidate_id} via historical peer {peer_str}.")
                        
                        # Adopt the bridge node! The stabilize_worker will zip the rest of the ring together automatically.
                        self.node.state.successor = {
                            "ip": res["target_ip"],
                            "port": res["target_port"],
                            "id": candidate_id
                        }
                        self.update_successor_list()

            except Exception as e:
                self.node.log.debug(f"Partition healer suppressed an error: {e}")
