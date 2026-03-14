import os
from typing import Generator, Union


class FileManager:
    """
    Handles local file storage, secure path resolution, and memory-safe 
    chunked reading/writing for the P2P Data Plane.
    
    This class enforces strict sandbox isolation, preventing malicious peers 
    from executing Path Traversal attacks (e.g., requesting '../../etc/passwd').
    """

    def __init__(self, node_id: Union[str, int]) -> None:
        """
        Initializes the FileManager and maps out the absolute path to the sandbox.
        """
        # Create a unique directory for this node based on its ID
        # Using abspath ensures we always know exactly where the sandbox is
        self.sandbox_dir: str = os.path.abspath(f"./shared_files_{node_id}")
        self._ensure_sandbox_exists()


    def _ensure_sandbox_exists(self) -> None:
        """
        Creates the physical sandbox directory on the host machine if it doesn't exist.
        """
        if not os.path.exists(self.sandbox_dir):
            os.makedirs(self.sandbox_dir)


    def get_secure_path(self, filename: str) -> str:
        """
        SECURITY: Prevents Path Traversal attacks.
        
        Strips away any folder paths, slashes, or relative navigators from the 
        requested filename and forces the physical file into the local sandbox.
        
        Args:
            filename: The requested filename (e.g., "../../secret.txt" -> "secret.txt")
            
        Returns:
            The absolute, secured path to the file inside the sandbox.
        """
        safe_filename = os.path.basename(filename)

        return os.path.join(self.sandbox_dir, safe_filename)


    def file_exists(self, filename: str) -> bool:
        """
        Checks if a requested file safely exists within the node's sandbox.
        """
        path = self.get_secure_path(filename)
        
        return os.path.isfile(path)

    def get_file_size(self, filename: str) -> int:
        """Returns the size of the file in bytes, or 0 if it doesn't exist.
        
        """
        if self.file_exists(filename):
            return os.path.getsize(self.get_secure_path(filename))
        
        return 0


    def read_file_chunks(self, filename: str, chunk_size: int = 65536) -> Generator[bytes, None, None]:
        """
        GENERATOR: Yields file data in discrete memory-safe chunks (Default 64KB).
        
        This allows a low-end node (like a Raspberry Pi or an Android phone) with 
        only 512MB of RAM to comfortably seed a 50GB 4K video file without crashing!
        """
        path = self.get_secure_path(filename)
        if not self.file_exists(filename):
            raise FileNotFoundError(f"File {filename} not found in sandbox.")

        with open(path, 'rb') as f:
            while True:
                chunk = f.read(chunk_size)
                if not chunk:
                    break
                yield chunk


    def save_chunk(self, filename: str, data: bytes, append: bool = True) -> None:
        """
        Writes raw binary data to the disk. 
        Uses 'ab' (append binary) to stitch dynamically downloaded TCP chunks together.
        """
        path = self.get_secure_path(filename)
        mode = 'ab' if append else 'wb'
        with open(path, mode) as f:
            f.write(data)
