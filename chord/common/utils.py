import hashlib
import socket
import platform
import os
from typing import Optional, Union, Literal


def get_sha1_hash(key: str) -> int:
    """
    Computes a SHA-1 hash and converts the hex result into a large integer.
    
    This integer serves as the 'Node ID' or 'Data ID' on the Chord ring. 
    In the final ShadowChord implementation, this will be replaced with 
    a 256-bit BLAKE3 hash.
    
    Args:
        key: The string to be hashed (e.g., "IP:PORT" or a filename).
        
    Returns:
        A large integer representation of the SHA-1 hash.
    """
    result = hashlib.sha1(key.encode())

    return int(result.hexdigest(), 16)


def is_between(id_to_check: int, start: int, end: int) -> bool:
    """
    Mathematical check to see if an ID falls within a circular range (start, end].
    
    This function accounts for the 'ring wrap-around' where the start ID 
    is numerically higher than the end ID.
    
    Args:
        id_to_check: The ID to evaluate.
        start: The beginning of the range (exclusive).
        end: The end of the range (inclusive).
        
    Returns:
        True if the ID is logically between start and end on the ring.
    """
    if start < end:
        return start < id_to_check <= end

    return start < id_to_check or id_to_check <= end


def detect_environment() -> Literal["PHONE", "VM", "HOST_LAPTOP"]:
    """
    Fingerprints the current hardware to determine the host type.
    
    Used to warn the user about NAT constraints or adjust auto-detection 
    logic for mobile vs. virtualized environments.
    
    Returns:
        A string literal representing the detected environment type.
    """
    if 'android' in platform.release().lower() or 'TERMUX_VERSION' in os.environ:
        return "PHONE"
    try:
        with open('/sys/class/dmi/id/product_name', 'r') as f:
            hardware = f.read().strip().lower()
            vm_sigs = ['standard pc', 'qemu', 'virtualbox', 'vmware', 'amazon ec2']
            if any(sig in hardware for sig in vm_sigs):
                return "VM"
    except (FileNotFoundError, PermissionError):
        pass
    return "HOST_LAPTOP"


def get_ip(provided_ip: Optional[str] = None) -> str:
    """
    Dynamically finds the best IP address for P2P communication.
    
    The priority hierarchy is:
    1. Explicit User Input (Override)
    2. Tailscale Overlay (100.x.x.x)
    3. Native LAN/Wi-Fi
    4. Localhost (Fallback)
    
    Args:
        provided_ip: An optional IP address string provided via CLI.
        
    Returns:
        The most viable IP address for the node to advertise to the swarm.
    """
    # 1. Explicit Override (Highest Priority)
    if provided_ip:
        print(f"[*] Identity Override: Using explicitly provided IP {provided_ip}")
        return provided_ip

    # 2. Hunt for the Tailscale Overlay IP (The 100.x.x.x subnet)
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        # Tailscale's MagicDNS server is always exactly at 100.100.100.100
        # By "connecting" to it, we force the OS to yield the tun0 interface IP.
        s.connect(("100.100.100.100", 53))
        overlay_ip = s.getsockname()[0]
        s.close()
        
        if overlay_ip.startswith("100."):
            print(f"[*] Auto-Detected Overlay IP: {overlay_ip} (Tailscale SD-WAN)")
            return overlay_ip
    except Exception:
        pass # Tailscale is not running or not installed, gracefully fall back.

    # 3. Fallback to Standard Native IP (LAN/Wi-Fi)
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        local_ip = s.getsockname()[0]
        s.close()
        
        # Warn the user if they are trapped in the QEMU NAT without an overlay
        if local_ip == "10.0.2.15":
            print("[!] Warning: Auto-detected QEMU NAT IP (10.0.2.15).")
            print("[!] Without Tailscale or hostfwd rules, external nodes cannot reach you.")
        
        print(f"[*] Auto-Detected Native IP: {local_ip} (LAN)")
        return local_ip
        
    except Exception:
        # 4. Total Offline Fallback
        print("[!] Warning: Machine appears totally offline. Falling back to localhost.")
        return "127.0.0.1"
