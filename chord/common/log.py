import logging
import os
from typing import Dict, Union


class NodeLogger:
    """
    A specialized logging wrapper for Chord nodes that supports multi-node context injection.
    
    This logger creates a dual-output system: 
    1. A detailed debug file log per node for deep troubleshooting.
    2. A clean, filtered console output for real-time CLI feedback.
    
    Attributes:
        port (int): The DHT control port this node is listening on.
        node_id (str): The unique hash identity of the node (SHA-1 or BLAKE3).
        logger (logging.Logger): The underlying Python logging instance.
    """
    
    def __init__(self,
            port: int,
            node_id: Union[str, int],
            log_dir: str = "logs",
            verbose: bool = False
        ) -> None:
        """
        Initializes the node-specific logger and sets up file/console handlers.

        Args:
            port: Network port used for context headers.
            node_id: The node's unique ID in the Chord ring.
            log_dir: Directory where log files will be persisted.
            verbose: If True, console output will include DEBUG level messages.
        """
        self.port = port
        self.node_id = str(node_id) # Keep the ID short for readable logs

        # 1. Directory Management: Create the 'logs' folder if it doesn't exist
        if not os.path.exists(log_dir):
            os.makedirs(log_dir)
            
        log_file = os.path.join(log_dir, f"node_{self.node_id}.log")
        
        # 2. Logger Initialization
        self.logger = logging.getLogger(f"Node_{self.node_id}")
        self.logger.setLevel(logging.DEBUG) # The base logger must catch EVERYTHING
        self.logger.propagate = False

        # Prevent duplicate handlers if the logger is instantiated twice
        if not self.logger.handlers:
            
            # 3. Asynchronous Time Correlation & Context Injection Formatter
            # Adds exact milliseconds, Node Port, and short ID to every file log
            file_formatter = logging.Formatter(
                fmt='%(asctime)s.%(msecs)03d | [%(levelname)s] | Node %(id)s | %(message)s',
                datefmt='%Y-%m-%d %H:%M:%S'
            )

            # Clean Formatter for the CLI
            console_formatter = logging.Formatter(
                fmt='[!] %(levelname)s: %(message)s'
            )
            
            # 4. File Handler: Captures absolutely everything (DEBUG level)
            file_handler = logging.FileHandler(log_file)
            file_handler.setLevel(logging.DEBUG)
            file_handler.setFormatter(file_formatter)
            
            # 5. Console Handler: Keeps the CLI clean (WARNING/ERROR only, unless verbose)
            console_handler = logging.StreamHandler()
            console_handler.setLevel(logging.DEBUG if verbose else logging.WARNING)
            console_handler.setFormatter(console_formatter)
            
            self.logger.addHandler(file_handler)
            self.logger.addHandler(console_handler)


    # --- WRAPPER METHODS FOR CONTEXT INJECTION ---
    # This automatically passes the port and ID into the formatter for every log!

    def _get_extra(self) -> Dict[str, Union[int, str]]:
        """
        Generates the context dictionary required by the log formatter.
        
        Returns:
            A dictionary containing 'port' and 'id' for log message prefixing.
        """
        return {'port': self.port, 'id': self.node_id}


    def debug(self,
            msg: str
        ) -> None:
        """
        Logs a message with DEBUG severity, typically used for network traces.
        """
        self.logger.debug(msg, extra=self._get_extra())
        

    def info(self,
            msg: str
        ) -> None:
        """
        Logs a message with INFO severity for significant state changes like Joins or Departs.
        """
        self.logger.info(msg, extra=self._get_extra())
        

    def warning(self, msg: str) -> None:
        """
        Logs a message with WARNING severity for non-fatal anomalies.
        """
        self.logger.warning(msg, extra=self._get_extra())
        

    def error(self, msg: str) -> None:
        """
        Logs a message with ERROR severity for fatal issues like unreachable peers.
        """
        self.logger.error(msg, extra=self._get_extra())
        
        
    def exception(self, msg: str) -> None:
        """
        Logs an ERROR message and automatically appends the current stack trace.
        Ideal for use inside 'except' blocks.
        """
        self.logger.exception(msg, extra=self._get_extra())
