"""MoeNet DN42 Agent - Loopback IP Executor

Configures the dummy0 interface with DN42 IP addresses based on node_id.
"""
import logging
import subprocess
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


class LoopbackExecutor:
    """Configure dummy0 interface with DN42 IPs."""
    
    def __init__(
        self,
        interface: str = "dummy0",
        dn42_ipv4_prefix: str = "172.22.188.0/26",
        dn42_ipv6_prefix: str = "fd00:4242:7777::/48",
    ):
        self.interface = interface
        self.dn42_ipv4_prefix = dn42_ipv4_prefix
        self.dn42_ipv6_prefix = dn42_ipv6_prefix
    
    def setup_loopback(self, node_id: int) -> bool:
        """Configure dummy0 with DN42 IPs based on node_id.
        
        Configures:
        - IPv4 prefix (e.g., 172.22.188.0/26) for BGP announcement
        - IPv4 node address (e.g., 172.22.188.4/32) for krt_prefsrc
        - IPv6 prefix (e.g., fd00:4242:7777::/48) for BGP announcement
        - IPv6 node address (e.g., fd00:4242:7777::4/128) for krt_prefsrc
        """
        try:
            # Calculate node-specific addresses
            # IPv4: base prefix + node_id
            ipv4_base = self.dn42_ipv4_prefix.split('/')[0]
            ipv4_parts = ipv4_base.split('.')
            ipv4_node = f"{'.'.join(ipv4_parts[:-1])}.{node_id}"
            
            # IPv6: base prefix + ::node_id
            ipv6_base = self.dn42_ipv6_prefix.split('/')[0].rstrip(':')
            ipv6_node = f"{ipv6_base}::{node_id}"
            
            addresses = [
                (self.dn42_ipv4_prefix, "IPv4 prefix"),
                (f"{ipv4_node}/32", "IPv4 node"),
                (self.dn42_ipv6_prefix, "IPv6 prefix"),
                (f"{ipv6_node}/128", "IPv6 node"),
            ]
            
            for addr, desc in addresses:
                self._add_address(addr, desc)
            
            logger.info(f"Loopback configured: IPv4={ipv4_node}, IPv6={ipv6_node}")
            return True
            
        except Exception as e:
            logger.error(f"Failed to configure loopback: {e}")
            return False
    
    def _add_address(self, address: str, description: str) -> bool:
        """Add an IP address to the interface if not already present."""
        try:
            # Check if address already exists
            check = subprocess.run(
                ["ip", "addr", "show", self.interface],
                capture_output=True,
                text=True,
            )
            
            addr_only = address.split('/')[0]
            if addr_only in check.stdout:
                logger.debug(f"{description} {address} already configured")
                return True
            
            # Add address
            result = subprocess.run(
                ["ip", "addr", "add", address, "dev", self.interface],
                capture_output=True,
                text=True,
            )
            
            if result.returncode == 0:
                logger.info(f"Added {description}: {address}")
                return True
            elif "exists" in result.stderr.lower():
                logger.debug(f"{description} {address} already exists")
                return True
            else:
                logger.warning(f"Failed to add {description}: {result.stderr}")
                return False
                
        except Exception as e:
            logger.error(f"Error adding {description} {address}: {e}")
            return False
    
    def ensure_interface_up(self) -> bool:
        """Ensure dummy0 interface exists and is up."""
        try:
            # Check if interface exists
            check = subprocess.run(
                ["ip", "link", "show", self.interface],
                capture_output=True,
            )
            
            if check.returncode != 0:
                # Create interface
                subprocess.run(
                    ["ip", "link", "add", self.interface, "type", "dummy"],
                    capture_output=True,
                )
            
            # Bring interface up
            subprocess.run(
                ["ip", "link", "set", self.interface, "up"],
                capture_output=True,
            )
            
            return True
        except Exception as e:
            logger.error(f"Failed to ensure interface up: {e}")
            return False
