from __future__ import annotations
import json
import threading
from typing import List, Dict, Any, Union, TYPE_CHECKING

from ..common import utils

# Using TYPE_CHECKING prevents circular imports at runtime while allowing 
# static type checkers to verify the ChordNode structure.
if TYPE_CHECKING:
    from ..node import ChordNode


class DataHandoffManager:
    """
    Manages the redistribution and synchronization of data during ring topology shifts.
    
    This manager handles 'Join Handoffs' (giving data to new neighbors) and 
    'Departure Handoffs' (taking data from nodes leaving the ring). It ensures 
    that the $k$ replicas are correctly distributed across the current successor list.
    """

    def __init__(self,
            node: ChordNode
        ) -> None:
        """
        Initializes the handoff manager with a reference to the parent ChordNode.
        """
        self.node: ChordNode = node


    def get_primary_data(self, exclude_tombstones: bool = False) -> List[Dict[str, Any]]:
        """
        Scans local storage and returns ONLY the data where this node is the True Head.
        
        A node is the Head for a key if the key falls in the range: (Predecessor_ID, My_ID].

        Args:
            exclude_tombstones: If True, filters out soft-deleted records.
            
        Returns:
            A list of primary data records.
        """
        primary_data = []
        for item in self.node.storage.get_all(exclude_tombstones=exclude_tombstones):
            if utils.is_between(item["key"], self.node.state.predecessor["id"], self.node.state.id) or item["key"] == self.node.state.id:
                primary_data.append(item)
        return primary_data
    

    def process_data_transfer(self, cmd: Dict[str, Any]) -> str:
        """
        Handles the 'Join Handoff' logic when a new node enters the ring.
        
        The current node identifies which keys now mathematically belong to the 
        joining node and prepares them for transfer.

        Args:
            cmd: Command dictionary containing 'requester_id' and 'requester_pred_id'.
            
        Returns:
            A JSON-encoded 'data transfer reply' containing the relevant data blocks.
        """
        new_id = cmd["requester_id"]
        new_pred_id = cmd["requester_pred_id"]
        self.node.log.debug(f"Serving data transfer request for new node {new_id}.")

        transfer_data = []
        keys_to_drop_at_tail = []

        for item in self.node.storage.get_all():
            key = item["key"]
            
            # A: Stolen Primary Data
            if utils.is_between(key, new_pred_id, new_id):
                transfer_data.append(item)
                keys_to_drop_at_tail.append(key) 
                
            # B: Inherited Replicas
            elif not utils.is_between(key, self.node.state.predecessor["id"], self.node.state.id):
                transfer_data.append(item)

        # Evict the old Tail instantly!
        if keys_to_drop_at_tail and self.node.state.successor_list:
            tail = self.node.state.successor_list[-1]
            self.node.log.info(f"Join Handoff: Notifying Tail ({tail['id']}) to drop {len(keys_to_drop_at_tail)} pushed-out keys.")
            msg = json.dumps({"type": "drop keys", "keys": keys_to_drop_at_tail})
            threading.Thread(target=self.node.net.send_command, args=(tail['ip'], tail['port'], msg)).start()

        return json.dumps({"type": "data transfer reply", "transfered_data": transfer_data})
    

    def process_departing_handoff(self, cmd: Dict[str, Any]) -> str:
        """
        Dispatcher hook for receiving data from a gracefully departing predecessor.
        
        Implements a 2-Phase Commit (2PC) style handoff where data is committed 
        to local disk before an acknowledgment is returned.

        Args:
            cmd: Contains 'data_to_insert' from the departing node.
            
        Returns:
            A JSON 'ACK' confirming the data is safely persisted.
        """
        self.node.log.info(f"Received departing data handoff ({len(cmd['data_to_insert'])} keys). Loading locally.")

        # 1. 2PC COMMIT: We MUST write to disk synchronously BEFORE replying!
        self.node.storage.bulk_load(cmd["data_to_insert"])

        # 2. Delegate the network healing (pushing replicas) to the background
        threading.Thread(target=self._process_handoff_worker, args=(cmd["data_to_insert"],)).start()

        # 3. Return a valid JSON ACK confirming the data is safely on disk
        return json.dumps({"status": "success", "message": "ACK"})
    

    def _process_handoff_worker(self, data_list: List[Dict[str, Any]]) -> None:
        """
        Handles post-handoff network maintenance in the background.
        
        This worker restores the replication factor (k) by pushing inherited 
        replicas further down the ring after a predecessor departs.
        """
        if not data_list:
            return

        # Normal Heal: If I am now the Head for any of this data, re-sync it!
        self.heal_replication()

        # Replica Forwarding: Find data I do NOT own.
        # Since the chain shrank by 1, I must push these replicas to my successor to maintain k.
        inherited_replicas = []
        for item in data_list:
            if not utils.is_between(item["key"], self.node.state.predecessor["id"], self.node.state.id):
                inherited_replicas.append(item)

        if inherited_replicas:
            self.node.log.info(f"Handoff: Pushing {len(inherited_replicas)} inherited replicas forward to maintain k.")
            self.node.net.send_command(
                self.node.state.successor['ip'],
                self.node.state.successor['port'],
                json.dumps({
                    "type": "insert request",
                    "requester_ip": self.node.state.ip,
                    "requester_port": self.node.state.port,
                    "data_to_insert": inherited_replicas,
                    "is_replica_write": True,  # For Eventual/Quorum
                    "chain_index": self.node.state.k + 1  # For Chain: Tricks the next node into acting as the Tail
                })
            )

    def heal_replication(self) -> None:
        """
        Forces a re-sync of Primary data to k successors after a topology change.
        
        This ensures that the replication factor remains intact even as nodes 
        move, join, or fail.
        """
        primary_data = self.get_primary_data()

        # If I own primary data, force it through the replication manager
        if primary_data:
            self.node.log.info(f"Re-syncing {len(primary_data)} keys via '{self.node.state.consistency}' strategy.")
            message = {
                "type": "insert request",
                "requester_ip": self.node.state.ip,
                "requester_port": self.node.state.port,
                "data_to_insert": primary_data
            }
            
            # Only add the chain index if we are actually using Chain Replication
            if self.node.state.consistency == "chain":
                message["chain_index"] = 0 

            # Let the Replicator handle it based on the active strategy!
            self.node.replicator.handle_insert(message)
