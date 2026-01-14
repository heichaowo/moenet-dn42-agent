"""MoeNet DN42 Agent - Firewall Executor

Manages iptables rules for WireGuard peer ports.
"""
import logging
import subprocess
from typing import List

logger = logging.getLogger(__name__)


class FirewallExecutor:
    """Manages iptables rules for DN42 WireGuard ports."""
    
    def __init__(self, chain: str = "INPUT"):
        self.chain = chain
        self._comment_prefix = "moenet-dn42"
    
    def allow_port(self, port: int, protocol: str = "udp") -> bool:
        """Open a port in iptables for WireGuard traffic.
        
        Args:
            port: Port number to open
            protocol: Protocol (default: udp)
            
        Returns:
            True if successful, False otherwise
        """
        if self._port_exists(port, protocol):
            logger.debug(f"Port {port}/{protocol} already open")
            return True
        
        comment = f"{self._comment_prefix}-{port}"
        
        # IPv4
        result4 = subprocess.run([
            "iptables", "-A", self.chain,
            "-p", protocol,
            "--dport", str(port),
            "-m", "comment", "--comment", comment,
            "-j", "ACCEPT"
        ], capture_output=True)
        
        # IPv6
        result6 = subprocess.run([
            "ip6tables", "-A", self.chain,
            "-p", protocol,
            "--dport", str(port),
            "-m", "comment", "--comment", comment,
            "-j", "ACCEPT"
        ], capture_output=True)
        
        success = result4.returncode == 0 and result6.returncode == 0
        if success:
            logger.info(f"Opened port {port}/{protocol}")
            self._save_rules()
        else:
            logger.error(f"Failed to open port {port}: {result4.stderr} {result6.stderr}")
        
        return success
    
    def remove_port(self, port: int, protocol: str = "udp") -> bool:
        """Remove a port rule from iptables.
        
        Args:
            port: Port number to close
            protocol: Protocol (default: udp)
            
        Returns:
            True if successful, False otherwise
        """
        comment = f"{self._comment_prefix}-{port}"
        
        # Remove IPv4 rule
        subprocess.run([
            "iptables", "-D", self.chain,
            "-p", protocol,
            "--dport", str(port),
            "-m", "comment", "--comment", comment,
            "-j", "ACCEPT"
        ], capture_output=True)
        
        # Remove IPv6 rule
        subprocess.run([
            "ip6tables", "-D", self.chain,
            "-p", protocol,
            "--dport", str(port),
            "-m", "comment", "--comment", comment,
            "-j", "ACCEPT"
        ], capture_output=True)
        
        logger.info(f"Removed port {port}/{protocol}")
        self._save_rules()
        return True
    
    def get_open_ports(self) -> List[int]:
        """Get list of ports opened by this agent.
        
        Returns:
            List of port numbers
        """
        result = subprocess.run(
            ["iptables", "-L", self.chain, "-n", "--line-numbers"],
            capture_output=True, text=True
        )
        
        ports = []
        for line in result.stdout.split("\n"):
            if self._comment_prefix in line:
                # Parse port from "dpt:30123"
                for part in line.split():
                    if part.startswith("dpt:"):
                        try:
                            port = int(part.split(":")[1])
                            ports.append(port)
                        except (ValueError, IndexError):
                            pass
        return sorted(set(ports))
    
    def sync_ports(self, expected_ports: List[int]) -> dict:
        """Ensure only expected ports are open.
        
        Args:
            expected_ports: List of ports that should be open
            
        Returns:
            dict with added and removed counts
        """
        current = set(self.get_open_ports())
        expected = set(expected_ports)
        
        to_add = expected - current
        to_remove = current - expected
        
        for port in to_add:
            self.allow_port(port)
        
        for port in to_remove:
            self.remove_port(port)
        
        return {"added": len(to_add), "removed": len(to_remove)}
    
    def _port_exists(self, port: int, protocol: str = "udp") -> bool:
        """Check if port rule already exists."""
        result = subprocess.run(
            ["iptables", "-C", self.chain,
             "-p", protocol, "--dport", str(port), "-j", "ACCEPT"],
            capture_output=True
        )
        return result.returncode == 0
    
    def _save_rules(self) -> None:
        """Persist iptables rules to disk."""
        # Try iptables-save
        subprocess.run(
            "iptables-save > /etc/iptables/rules.v4 2>/dev/null || true",
            shell=True
        )
        subprocess.run(
            "ip6tables-save > /etc/iptables/rules.v6 2>/dev/null || true",
            shell=True
        )
