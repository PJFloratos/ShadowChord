from __future__ import annotations
import socket
import threading
import json
from typing import Tuple, TYPE_CHECKING

if TYPE_CHECKING:
    from ..node import ChordNode


class FileStreamer:
    """
    The Data Plane: Handles pure, out-of-band P2P binary file transfers.
    
    This class bypasses the DHT Control Plane entirely. When a client wants to 
    download a file, they discover the seeder via the DHT, but then connect 
    directly to this dedicated Data Port to stream the raw bytes. 
    
    Streaming occurs in 64KB memory-safe chunks, allowing low-end mobile 
    devices to seed 50GB files without encountering out-of-memory crashes.
    """

    def __init__(self, node: ChordNode) -> None:
        """
        Initializes the File Streamer. 
        The Data Port is mathematically derived (DHT Port + 10000) to ensure 
        predictability without needing extra DHT metadata.
        """
        self.node: ChordNode = node
        self.data_port: int = self.node.state.port + 10000
        self.running: bool = False


    def start_server(self) -> None:
        """
        Runs in a background thread, listening for direct P2P download requests.
        """
        self.running = True
        server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        
        # Bind to 0.0.0.0 to allow QEMU port forwarding
        server.bind(('0.0.0.0', self.data_port))
        server.listen(10)

        self.node.log.info(f"Data Plane: File Streamer listening natively on 0.0.0.0:{self.data_port}")
        
        while self.running:
            try:
                conn, _ = server.accept()
                threading.Thread(target=self._handle_upload, args=(conn,), daemon=True).start()
            except Exception as e:
                if self.running:
                    self.node.log.error(f"Data Plane FATAL: {e}")


    def _handle_upload(self, conn: socket.socket) -> None:
        """
        Serves a requested file to a remote peer.
        
        Negotiates metadata (filesize) via JSON, waits for readiness, and then 
        floods the TCP socket with raw binary chunk data.
        """
        self.node.log.info(f"DEBUG: Incoming connection received on Data Port!")
        with conn:
            try:
                # 1. Receive the initial JSON request header
                req_raw = conn.recv(1024).decode('utf-8')
                if not req_raw: return
                req = json.loads(req_raw)
                filename = req.get("filename")

                # 2. Security Check: Do we have it?
                if not self.node.file_manager.file_exists(filename):
                    conn.sendall(json.dumps({"status": "error", "message": "File not found on this seeder."}).encode('utf-8'))
                    return

                # 3. Send the file metadata (size) to the downloader
                filesize = self.node.file_manager.get_file_size(filename)
                conn.sendall(json.dumps({"status": "ok", "filesize": filesize}).encode('utf-8'))

                # 4. Wait for the downloader to say "Ready" so we don't mix JSON and binary data
                conn.recv(1024) 

                # 5. STREAM THE RAW BYTES
                self.node.log.info(f"Data Plane: Seeding '{filename}' ({filesize} bytes) to peer.")
                path = self.node.file_manager.get_secure_path(filename)
                
                with open(path, 'rb') as f:
                    while chunk := f.read(65536): # 64KB chunks
                        conn.sendall(chunk)
                        
                self.node.log.info(f"Data Plane: Finished seeding '{filename}'.")
            except Exception as e:
                self.node.log.error(f"Data Plane Upload Error: {e}")


    def download_file(self, target_ip: str, target_dht_port: int, filename: str) -> Tuple[bool, str]:
        """
        Connects directly to a Seeder's out-of-band Data Port to leech a file.
        
        Args:
            target_ip: The IP address of the seeder node.
            target_dht_port: The DHT control port of the seeder (Data port is derived from this).
            filename: The name of the requested file.
            
        Returns:
            A tuple containing a boolean success flag and a status message.
        """
        target_data_port = target_dht_port + 10000
        
        # Self-Routing Bypass: If downloading from ourselves, use localhost
        actual_ip = target_ip
        if target_ip == self.node.state.ip and target_dht_port == self.node.state.port:
            actual_ip = "127.0.0.1"

        self.node.log.info(f"Data Plane: Initiating direct P2P leech from {actual_ip}:{target_data_port}...")
        
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.settimeout(10.0)
                s.connect((actual_ip, target_data_port))
                
                # 1. Request the file
                s.sendall(json.dumps({"filename": filename}).encode('utf-8'))
                
                # 2. Get the metadata response
                resp_raw = s.recv(1024).decode('utf-8')
                resp = json.loads(resp_raw)
                
                if resp.get("status") != "ok":
                    return False, resp.get("message", "Unknown seeder error.")
                    
                filesize = resp["filesize"]
                self.node.log.info(f"Data Plane: Incoming file is {filesize} bytes. Starting stream...")
                
                # 3. Tell the seeder to open the floodgates
                s.sendall(b"READY") 

                # 4. READ THE RAW BYTES DIRECTLY TO DISK
                bytes_received = 0
                path = self.node.file_manager.get_secure_path(filename)
                
                with open(path, 'wb') as f:
                    while bytes_received < filesize:
                        chunk = s.recv(min(65536, filesize - bytes_received))
                        if not chunk: 
                            break
                        f.write(chunk)
                        bytes_received += len(chunk)
                
                if bytes_received == filesize:
                    return True, f"Successfully downloaded '{filename}'!"
                else:
                    return False, "Connection dropped mid-download."
                    
        except Exception as e:
            return False, f"Data Plane Connection Error: {e}"
