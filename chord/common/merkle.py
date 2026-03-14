import hashlib
from typing import List, Dict, Optional, Any, Union


class MerkleNode:
    """
    Represents a single node within the Merkle Tree.
    
    Attributes:
        left (Optional[MerkleNode]): The left child node.
        right (Optional[MerkleNode]): The right child node.
        value (str): The hex string representation of the node's hash.
        is_leaf (bool): Indicates if this node represents a direct data record.
    """
    
    def __init__(self,
            left: Optional['MerkleNode'],
            right: Optional['MerkleNode'], 
            hash_val: str,
            is_leaf: bool = False
        ) -> None:
        """
        Initializes a MerkleNode with children and a hash value.
        """
        self.left: Optional['MerkleNode'] = left
        self.right: Optional['MerkleNode'] = right
        self.value: str = hash_val
        self.is_leaf: bool = is_leaf


class MerkleTree:
    """
    Implements a binary Merkle Tree for efficient data verification and synchronization.
    
    This tree provides an O(1) root hash that represents the cumulative state of 
    distributed data. It uses SHA-256 to ensure cryptographic safety 
    against collisions.
    """
    
    def __init__(self,
            data_records: List[Dict[str, Any]]
        ) -> None:
        """
        Initializes and builds the Merkle Tree from a list of data records.

        Args:
            data_records: A list of dictionaries, each containing at least a 
                         'key' and a 'timestamp'.
        """
        self.root: Optional[MerkleNode] = None
        self.leaves: List[MerkleNode] = []
        if data_records:
            self._build_tree(data_records)


    def _hash(self,
            data_str: str
        ) -> str:
        """
        Computes a SHA-256 hash for a given data string.
        """
        return hashlib.sha256(data_str.encode('utf-8')).hexdigest()


    def _build_tree(self, data_records: List[Dict[str, Any]]) -> None:
        """
        Constructs the tree from data records.
        
        The records are sorted by key and timestamp to ensure the root hash is 
        deterministic regardless of input order.
        """
        # 1. MUST sort the records to ensure deterministic root hashes
        sorted_records = sorted(data_records, key=lambda x: (x["key"], x["timestamp"]))

        # 2. Create the Leaf Nodes from sorted data
        for record in sorted_records:
            data_str = f"{record['key']}:{record['timestamp']}"
            node_hash = self._hash(data_str)
            self.leaves.append(MerkleNode(None, None, node_hash, is_leaf=True))

        # 3. Build the tree structure bottom-up
        self.root = self._build_levels(self.leaves)


    def _build_levels(self, nodes: List[MerkleNode]) -> Optional[MerkleNode]:
        """
        Recursively builds tree levels by pairing and hashing child nodes.
        """
        if len(nodes) == 0:
            return None
        if len(nodes) == 1:
            return nodes[0] # The final Root Node!

        new_level = []
        
        # Step through nodes two at a time
        for i in range(0, len(nodes), 2):
            left = nodes[i]
            
            # If there is an odd number of nodes, duplicate the last node to pair with itself.
            # (This is the standard protocol for Bitcoin/Cassandra Merkle Trees)
            right = nodes[i+1] if i + 1 < len(nodes) else left
            
            # Hash the left child's hash + right child's hash
            # combined_hash = self._hash(left.value + right.value)
            combined_hash = hashlib.sha256(
                bytes.fromhex(left.value) + bytes.fromhex(right.value)
            ).hexdigest()

            # Create the parent node
            parent = MerkleNode(left, right, combined_hash)
            new_level.append(parent)

        # Recursively build the next level up
        return self._build_levels(new_level)


    def get_root_hash(self) -> Optional[str]:
        """
        Returns the hex string of the root hash representing the entire dataset state.
        """
        return self.root.value if self.root else None
    
    
    def print_tree(self) -> None:
        """
        Prints a visual representation of the tree structure to the console.
        """
        def _print_tree(node: Optional[MerkleNode], level: int = 0) -> None:
            if node is None:
                return
            print("  " * level + node.value)
            _print_tree(node.left, level + 1)
            _print_tree(node.right, level + 1)

        _print_tree(self.root)
