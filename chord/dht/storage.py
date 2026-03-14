from __future__ import annotations
import threading
import time
from typing import Dict, List, Any, Optional, Union, TYPE_CHECKING

from ..common import utils

if TYPE_CHECKING:
    from ..common.log import NodeLogger


class DataStore:
    """
    Handles thread-safe local storage with CRDT-based conflict resolution.
    
    This class manages the lifecycle of data records, including additions, 
    soft-deletions (tombstones), and bulk synchronizations. It uses 
    Last-Write-Wins (LWW) logic to ensure eventual consistency across the swarm.
    """

    def __init__(self, log: NodeLogger) -> None:
        """
        Initializes the internal dictionary and thread locks.
        
        The internal data structure follows this schema:
        {
            hash_key (int): {
                "peers": { "IP:PORT" (str): timestamp (float), ... },
                "timestamp": float,  # The freshest timestamp across all peers
                "is_tombstone": bool # Marks the key for soft-deletion
            }
        }
        """
        # The internal storage (dictionary)
        self.data: Dict[int, Dict[str, Any]] = {}
        self.lock: threading.Lock = threading.Lock()
        self.log: NodeLogger = log


    @staticmethod
    def _format_value(record: Dict[str, Any]) -> Union[List[Dict[str, Any]], str]:
        """
        Helper to convert internal dictionary format to the public JSON List format.
        
        Args:
            record: The internal record dictionary.
            
        Returns:
            The string "TOMBSTONE" if deleted, otherwise a list of active peer objects.
        """
        if record["is_tombstone"]:
            return "TOMBSTONE"

        # Converts {"127.0.0.1:5000": 123.4} -> [{"ip": "127.0.0.1:5000", "timestamp": 123.4}]
        return [{"ip": ip, "timestamp": ts} for ip, ts in record["peers"].items()]
    

    def insert(self, key: int, value: str, timestamp: Optional[float] = None) -> str:
        """
        Adds or updates a SINGLE peer (IP) for a specific key.
        
        Implements LWW: An update is only applied if its timestamp is strictly 
        newer than the existing local data for that specific peer.

        Args:
            key: The hashed integer ID of the data.
            value: The "IP:PORT" string of the node hosting the file.
            timestamp: Optional coordinator-provided timestamp.
            
        Returns:
            "ACK" upon successful thread-safe write.
        """
        timestamp = timestamp or time.time()
        ip_str = str(value)

        with self.lock:
            if key not in self.data:
                self.data[key] = {"peers": {}, "timestamp": 0.0, "is_tombstone": False}

            record = self.data[key]

            # Revive from tombstone if this insert is newer
            if record["is_tombstone"]:
                if timestamp > record["timestamp"]:
                    record["is_tombstone"] = False
                    record["peers"] = {ip_str: timestamp}
                    record["timestamp"] = timestamp
                    self.log.debug(f"Storage: Revived key `{str(key)}` from TOMBSTONE.")
                return "ACK"

            # CRDT Merge: Only update if the incoming timestamp is newer
            current_peer_ts = record["peers"].get(ip_str, 0.0)
            if timestamp > current_peer_ts:
                record["peers"][ip_str] = timestamp
                self.log.debug(f"Storage: Added/Updated IP {ip_str} for key {str(key)}.")

            if timestamp > record["timestamp"]:
                record["timestamp"] = timestamp

        return "ACK"


    def remove_value(self, key: int, value: str, timestamp: Optional[float] = None) -> str:
        """
        Removes a SINGLE peer (IP) from a key. 
        
        If no peers are left after removal, the key is marked with a Tombstone.
        The removal is only processed if the timestamp is newer than the existing record.

        Args:
            key: The hashed integer ID.
            value: The "IP:PORT" string to remove.
            timestamp: Optional coordinator-provided timestamp.
        """
        timestamp = timestamp or time.time()
        ip_str = str(value)

        with self.lock:
            if key not in self.data:
                self.log.debug(f"Storage: Ignored delete for non-existent key {str(key)}.")
                return "ACK"

            record = self.data[key]

            if record["is_tombstone"]:
                if timestamp > record["timestamp"]:
                    record["timestamp"] = timestamp
                return "ACK"

            # Remove IP only if the delete timestamp is newer
            current_peer_ts = record["peers"].get(ip_str, 0.0)
            if timestamp > current_peer_ts:
                if ip_str in record["peers"]:
                    del record["peers"][ip_str]
                    self.log.debug(f"Storage: Unlinked IP {ip_str} from key {str(key)[:8]}...")
                
                if timestamp > record["timestamp"]:
                    record["timestamp"] = timestamp

                # Auto-Tombstone if no IPs are left hosting the file
                if not record["peers"]:
                    record["is_tombstone"] = True
                    self.log.debug(f"Storage: Last peer removed. Dropped TOMBSTONE for key {str(key)[:8]}...")

        return "ACK"


    def _tombstone_entire_key(self, key: int, timestamp: float) -> None:
        """
        Forcefully marks a key as deleted across all replicas.
        Used during bulk synchronization and replication reconciliation.
        """
        with self.lock:
            current_ts = self.data.get(key, {}).get("timestamp", 0.0)
            if timestamp > current_ts:
                self.data[key] = {"peers": {}, "timestamp": timestamp, "is_tombstone": True}
                self.log.debug(f"Storage: Key {str(key)}. forcefully TOMBSTONED via sync.")


    def _overwrite_state(self, key: int, peer_list: List[Dict[str, Any]], timestamp: float) -> None:
        """
        Completely overwrites a key's state using a provided peer list.
        Used primarily by the Anti-Entropy (Merkle) worker and Handoff manager.
        """
        with self.lock:
            current_ts = self.data.get(key, {}).get("timestamp", 0.0)
            if timestamp > current_ts:
                new_peers = {p["ip"]: p["timestamp"] for p in peer_list}
                self.data[key] = {"peers": new_peers, "timestamp": timestamp, "is_tombstone": False}

    
    def query(self, key: int) -> List[Dict[str, Any]]:
        """
        Retrieves seeder data for a specific key, strictly hiding soft-deleted (Tombstoned) records.
        
        Returns:
            A list containing the data block if found and active, otherwise an empty list.
        """
        with self.lock:
            if key in self.data:
                record = self.data[key]
                if record["is_tombstone"]:
                    return []
            
                return [{
                    "key": key,
                    "value": self._format_value(record),
                    "timestamp": record["timestamp"]
                }]

            return []


    def get_all(self, exclude_tombstones: bool = False) -> List[Dict[str, Any]]:
        """
        Exports the entire database for synchronization or reporting.
        
        Args:
            exclude_tombstones: If True, only returns keys that have active seeders.
        """
        export_list = []
        with self.lock:
            for k, record in self.data.items():
                if exclude_tombstones and record["is_tombstone"]:
                    continue
                export_list.append({
                    "key": k, 
                    "value": self._format_value(record), 
                    "timestamp": record["timestamp"]
                })
        return export_list


    def extract_range(self, start_id: int, end_id: int) -> List[Dict[str, Any]]:
        """
        Atomic Range Extraction: Moves data belonging to a new neighbor out of local storage.
        
        This is a destructive operation used during Joins/Departs to hand off 
        ownership of specific keyspace segments.

        Returns:
            A list of keys being transferred.
        """
        transfer_data = []
        new_storage = {}

        with self.lock:
            for key, record in self.data.items():
                # If the key falls in the requester's range, we move it
                if utils.is_between(key, start_id, end_id):
                    transfer_data.append({
                        "key": key, 
                        "value": self._format_value(record), 
                        "timestamp": record["timestamp"]
                    })
                else:
                    new_storage[key] = record
            
            # Update internal storage to remove transferred keys
            self.data = new_storage

        self.log.info(f"Storage: Range extraction complete. Handing off {len(transfer_data)} keys. Retaining {len(new_storage)} keys.")

        return transfer_data


    def get_state_summary(self, target_keys: Optional[List[int]] = None) -> Dict[int, float]:
        """
        Returns a lightweight dictionary mapping keys to their freshest timestamps.
        
        Used by the Anti-Entropy manager to find differences in dataset state 
        without transmitting large values over the network.
        """
        summary = {}
        with self.lock:
            # If target_keys is provided, only summarize those (for replicas)
            keys_to_scan = target_keys if target_keys is not None else self.data.keys()
            
            for k in keys_to_scan:
                if k in self.data:
                    summary[k] = self.data[k]["timestamp"]
        return summary
    

    def delete_keys(self, keys_list: List[int]) -> None:
        """
        Hard-deletes a list of keys entirely from local disk.
        Used for eviction when a node is no longer responsible for a replica.
        """
        self.log.debug(f"Storage: Hard deleting {len(keys_list)} keys from disk.")
        
        with self.lock:
            for k in keys_list:
                if k in self.data:
                    del self.data[k]


    def bulk_load(self, data_list: List[Dict[str, Any]]) -> None:
        """
        CRDT-Safe Synchronization Router. 
        
        Delegates incoming data items (from handoffs or anti-entropy) 
        to the correct single-purpose CRDT method based on the object type.
        """
        for item in data_list:
            key = item["key"]
            val = item["value"]
            overall_ts = item.get("timestamp", time.time())
            is_del = item.get("is_delete", False)

            if isinstance(val, list):
                # Type 1: Full State Sync (From Anti-Entropy or Handoffs)
                self._overwrite_state(key, val, overall_ts)
            
            elif val == "TOMBSTONE":
                # Type 2: Explicit Tombstone Sync
                self._tombstone_entire_key(key, overall_ts)
            
            elif is_del:
                # Type 3: Delta Delete (Single IP Removal)
                self.remove_value(key, val, timestamp=overall_ts)
            
            else:
                # Type 4: Delta Insert (Single IP Addition)
                self.insert(key, val, timestamp=overall_ts)
