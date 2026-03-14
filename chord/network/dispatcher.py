from __future__ import annotations
import json
from typing import Dict, Any, Callable, TYPE_CHECKING

if TYPE_CHECKING:
    from ..node import ChordNode



class MessageDispatcher:
    """
    The central routing hub for all incoming Control Plane (DHT) messages.
    
    This class receives parsed JSON commands from the raw TCP sockets and 
    delegates them to the appropriate domain manager (Routing, Topology, Storage, etc.).
    It also intercepts incoming requests to silently update the node's historical 
    peer Rolodex for Split-Brain partition healing.
    """

    def __init__(self, node: ChordNode) -> None:
        """
        Initializes the dispatcher and wires up the default protocol commands.
        """
        self.node: ChordNode = node
        self.handlers: Dict[str, Callable[[Dict[str, Any]], str]] = {}
        self._register_default_commands()


    def register(self, cmd_type: str, handler_function: Callable[[Dict[str, Any]], str]) -> None:
        """
        Allows dynamic registration of new network command types.
        
        Args:
            cmd_type: The string identifier for the command (e.g., "insert request").
            handler_function: A callable that accepts the command dictionary and returns a JSON string response.
        """
        self.handlers[cmd_type] = handler_function


    def dispatch(self, cmd: Dict[str, Any]) -> str:
        """
        Extracts the command type, updates local peer metadata, and routes the payload.
        
        Args:
            cmd: The parsed JSON dictionary from the network payload.
            
        Returns:
            A JSON-encoded string to be sent back across the TCP socket to the requester.
        """
        # If the packet contains routing info, add it to our historical Rolodex!
        if "requester_ip" in cmd and "requester_port" in cmd:
            self.node.state.observe_peer(cmd["requester_ip"], cmd["requester_port"])
        elif "ip" in cmd and "port" in cmd:
            self.node.state.observe_peer(cmd["ip"], cmd["port"])

        cmd_type = cmd.get("type")
        
        handler = self.handlers.get(cmd_type)
        if handler:
            return handler(cmd)
        
        # Fallback for unknown commands
        self.node.log.warning(f"Dispatcher: Received unknown command type: '{cmd_type}'.")
        return json.dumps({"error": "UNKNOWN_COMMAND"})


    def _register_default_commands(self) -> None:
        """
        Wires up the standard Chord protocol messages to their respective domain managers.
        This isolates the networking layer from the business logic.
        """
        # --- HEALTH CHECKS ---
        self.register("ping", lambda cmd: "PONG")

        # --- RING TOPOLOGY (Delegated to TopologyManager) ---
        self.register("get predecessor", self._handle_get_predecessor)
        self.register("get successor list", lambda cmd: json.dumps(self.node.state.successor_list))
        self.register("update predecessor", lambda cmd: self.node.topology.handle_update_predecessor(cmd))
        self.register("update successor", lambda cmd: self.node.topology.handle_update_successor(cmd))
        self.register("force update successor list", lambda cmd: self.node.topology.handle_force_update(cmd))

        # --- ROUTING PROTOCOL (Delegated to RoutingEngine) ---
        self.register("find successor step", lambda cmd: self.node.routing.handle_find_successor_step(cmd))
        self.register("ring traversal request", lambda cmd: self.node.routing.process_ring_traversal(cmd))

        # --- DATA OPERATIONS (Delegated to ReplicationManager & Storage) ---
        self.register("insert request", lambda cmd: self.node.replicator.handle_insert(cmd))
        self.register("query request", lambda cmd: self.node.replicator.handle_query(cmd))
        
        # --- ANTI-ENTROPY (MERKLE SYNC) ---
        self.register("merkle root check", lambda cmd: self.node.replicator.handle_merkle_root_check(cmd))

        # --- DATA HANDOFFS (Delegated to DataHandoffManager / Storage) ---
        self.register("data transfer request", lambda cmd: self.node.handoff.process_data_transfer(cmd))
        self.register("data handoff", lambda cmd: self.node.handoff.process_departing_handoff(cmd))
        
        # Small wrapper for eviction
        self.register("drop keys", self._handle_drop_keys)


    # --- Small inline wrappers for simple calls ---

    def _handle_get_predecessor(self, cmd: Dict[str, Any]) -> str:
        """
        Returns the predecessor state as a JSON string.
        """
        if self.node.state.predecessor:
            return json.dumps(self.node.state.predecessor)
        return json.dumps(None)

    def _handle_drop_keys(self, cmd: Dict[str, Any]) -> str:
        """
        Executes a targeted hard-deletion of pushed-out replica keys.
        """
        self.node.log.info(f"Targeted Eviction: Dropping {len(cmd['keys'])} pushed-out replicas.")
        self.node.storage.delete_keys(cmd["keys"])
        return "ACK"
