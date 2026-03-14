from __future__ import annotations
import socket
import threading
import json
import struct
import time
from typing import List, Optional, Tuple, TYPE_CHECKING

if TYPE_CHECKING:
    from ..node import ChordNode


class NetworkHandler:
    """
    The Control Plane: Manages raw TCP socket communication for the DHT.
    
    Implements length-prefixed message framing to prevent TCP fragmentation, 
    and provides a robust, fault-tolerant client API with exponential backoff 
    retries to survive hostile network environments.
    """

    def __init__(self, node: ChordNode) -> None:
        """
        Initializes the network handler with a reference to the parent node.
        """
        self.node: ChordNode = node 
        self.running: bool = False


    # ==========================================
    # DISCOVERY UTILITIES
    # ==========================================

    def discover_local_vms(self, start_port: int = 5001, end_port: int = 5010) -> List[int]:
        """
        Stateless Discovery: Scans localhost to find active VM nodes.
        
        Used primarily during bootstrapping by the Host machine to map out 
        isolated virtual nodes before the DHT topology stabilizes.

        Args:
            start_port: The beginning of the port range to scan.
            end_port: The end of the port range to scan.
            
        Returns:
            A list of integers representing active TCP ports.
        """
        active_ports = []
        for port in range(start_port, end_port + 1):
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.settimeout(0.01) # 10ms timeout for instant local scanning
                try:
                    s.connect(('127.0.0.1', port))
                    active_ports.append(port)
                except (ConnectionRefusedError, socket.timeout, OSError):
                    continue
        return active_ports


    # ==========================================
    # MESSAGE FRAMING UTILITIES
    # ==========================================

    @staticmethod
    def __send_framed_msg(sock: socket.socket, msg_string: str) -> None:
        """
        Prefixes outgoing messages with a 4-byte integer indicating payload length.
        
        This is critical for JSON over TCP. It tells the receiving socket exactly 
        how many bytes to wait for, preventing two rapid messages from fusing together.
        """
        # 1. Convert string to bytes
        msg_bytes = msg_string.encode('utf-8')
        
        # 2. Pack the length into a 4-byte header ('!I' means Network-Byte-Order Unsigned Integer)
        header = struct.pack('!I', len(msg_bytes))
        
        # 3. Send the header AND the payload together
        sock.sendall(header + msg_bytes)


    @staticmethod
    def __recvall(sock: socket.socket, n: int) -> Optional[bytearray]:
        """
        Helper function to strictly receive exactly `n` bytes.
        
        TCP makes no guarantees that a single `recv()` call will return the whole 
        payload. This loops until the exact byte count is collected.
        """
        data = bytearray()
        while len(data) < n:
            packet = sock.recv(n - len(data))
            if not packet:
                return None # Connection closed early
            data.extend(packet)
        return data


    def __recv_framed_msg(self, sock: socket.socket) -> Optional[str]:
        """
        Reads the 4-byte header, determines payload size, and reads the exact JSON payload.
        """
        # 1. Read exactly 4 bytes to get the header
        raw_msglen = self.__recvall(sock, 4)
        if not raw_msglen:
            return None
            
        # 2. Unpack the 4 bytes back into a normal Python integer
        msglen = struct.unpack('!I', raw_msglen)[0]
        
        # 3. Read exactly 'msglen' bytes to get the full payload
        raw_data = self.__recvall(sock, msglen)
        if not raw_data:
            return None
            
        # 4. Decode and return
        return raw_data.decode('utf-8')


    # ==========================================
    # NETWORK SERVER (LISTENER)
    # ==========================================

    def stop_server(self) -> None:
        """Signals the background server thread to terminate."""
        self.running = False
        return


    def start_server(self) -> None:
        """
        Binds the TCP socket and continuously listens for incoming protocol messages.
        
        Spawns a new daemon thread for every incoming connection to ensure 
        the server never blocks while parsing large JSON payloads.
        """
        self.running = True
        try:
            self.node.log.debug(f"Network: Initializing server socket on 0.0.0.0:{self. node.state.port}.")

            # Creating the socket
            server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)

            # Allows you to restart the node immediately without "Address already in use" errors
            server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)

            # Bind to 0.0.0.0 so QEMU port-forwarding and Wi-Fi traffic work!
            server.bind(('0.0.0.0', self.node.state.port))
            server.listen(20)
            server.settimeout(1.0)

            # self.node.log.info(f"Network: Server securely bound. Node {self.node.state.id} is now listening on 0.0.0.0:{self.node.state.port}.")
            self.node.log.info(f"Network: Listening on {self.node.state.ip}:{self.node.state.port}")

            while self.running:
                try:
                    conn, addr = server.accept()
                    t = threading.Thread(target=self._handle_client, args=(conn, addr), daemon=True)
                    t.start()
                except socket.timeout:
                    continue

        except Exception as e:
            self.node.log.error(f"Network: FATAL Server Error on port {self.node.state.port}: {e}.")


    def _handle_client(self, conn: socket.socket, addr: Tuple[str, int]) -> None:
        """
        Handles a single incoming TCP connection from a remote peer.
        
        Frames the incoming bytes, parses the JSON, and delegates the logic 
        to the MessageDispatcher. Returns the Dispatcher's response via the socket.
        """
        with conn:
            try:
                # Add a timeout to the connection itself so a slow peer doesn't hang us
                conn.settimeout(5.0)
                raw_data = self.__recv_framed_msg(conn) # FRAMING RECEIVER!
                if not raw_data: 
                    return

                message = json.loads(raw_data)

                # Intercept 'discovery' requests before hitting the Chord dispatcher
                if message.get("type") == "discovery":
                    active_vms = self.discover_local_vms()
                    response = json.dumps({"active_vm_ports": active_vms})
                else:
                    # Send the parsed JSON straight to the Dispatcher
                    response = self.node.dispatcher.dispatch(message)

                if response is None:
                    response = json.dumps({"error": "NO_RESPONSE"})

                # USE THE NEW FRAMING SENDER to send the reply back!
                self.__send_framed_msg(conn, response)

            except Exception as e:
                self.node.log.debug(f"Network: Handler Error for {addr}: {e}")


    # ==========================================
    # NETWORK CLIENT (ROUTING & RETRIES)
    # ==========================================

    def _send_raw(self, target_ip: str, target_port: int, message: str) -> Optional[str]:
        """
        Strictly handles opening a TCP socket and sending/receiving bytes.
        No retries, no advanced routing logic.
        """
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.settimeout(3.0)
                s.connect((target_ip, target_port))
                self.__send_framed_msg(s, message)
                return self.__recv_framed_msg(s)
        except Exception:
            # Suppress logs here to prevent terminal spam during retries
            return None


    def _send_with_routing(self, ip: str, port: int, message: str) -> Optional[str]:
        """
        Stateless IP Routing wrapper.
        
        Short-circuits traffic destined for the local node to bypass the 
        physical network interface, relying on the OS Loopback instead.
        """
        # Short-circuit: If node is talking to itself, bypass the Wi-Fi/QEMU stack entirely
        if ip == self.node.state.ip and port == self.node.state.port:
            return self._send_raw("127.0.0.1", port, message)

        # Standard send. Rely on the OS and Port Forwarding.
        return self._send_raw(ip, port, message)

    def send_command(self, ip: str, port: int, message: str, max_retries: int = 3) -> Optional[str]:
        """
        Public API for dispatching network commands.
        
        Implements Fault Tolerance via Exponential Backoff. If the network drops 
        a packet, the system will wait, multiply the delay, and try again, 
        surviving transient network partitions natively.

        Args:
            ip: Target IPv4 address.
            port: Target TCP port.
            message: JSON string payload.
            max_retries: Number of attempts before marking the peer as dead.
            
        Returns:
            The JSON string response, or None if all retries are exhausted.
        """
        backoff = 0.5

        for attempt in range(max_retries):
            ## --- CHAOS MONKEY ---
            # if random.random() < 0.10:
            #     time.sleep(backoff)
            #     backoff *= 1.5 
            #     continue

            # Try to send the packet
            response = self._send_with_routing(ip, port, message)
            
            # Success!
            if response is not None:
                return response 
            
            # Network failed, apply exponential backoff and try again
            time.sleep(backoff)
            backoff *= 1.5

        # All retries exhausted
        self.node.log.error(f"Network: Node at {ip}:{port} is unreachable after {max_retries} attempts.")
        return None
