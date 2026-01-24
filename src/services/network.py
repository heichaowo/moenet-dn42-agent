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
        result4 = subprocess.run(
            [
                "iptables",
                "-A",
                self.chain,
                "-p",
                protocol,
                "--dport",
                str(port),
                "-m",
                "comment",
                "--comment",
                comment,
                "-j",
                "ACCEPT",
            ],
            capture_output=True,
        )

        # IPv6
        result6 = subprocess.run(
            [
                "ip6tables",
                "-A",
                self.chain,
                "-p",
                protocol,
                "--dport",
                str(port),
                "-m",
                "comment",
                "--comment",
                comment,
                "-j",
                "ACCEPT",
            ],
            capture_output=True,
        )

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
        subprocess.run(
            [
                "iptables",
                "-D",
                self.chain,
                "-p",
                protocol,
                "--dport",
                str(port),
                "-m",
                "comment",
                "--comment",
                comment,
                "-j",
                "ACCEPT",
            ],
            capture_output=True,
        )

        # Remove IPv6 rule
        subprocess.run(
            [
                "ip6tables",
                "-D",
                self.chain,
                "-p",
                protocol,
                "--dport",
                str(port),
                "-m",
                "comment",
                "--comment",
                comment,
                "-j",
                "ACCEPT",
            ],
            capture_output=True,
        )

        logger.info(f"Removed port {port}/{protocol}")
        self._save_rules()
        return True

    def get_open_ports(self) -> List[int]:
        """Get list of ports opened by this agent.

        Returns:
            List of port numbers
        """
        result = subprocess.run(
            ["iptables", "-L", self.chain, "-n", "--line-numbers"], capture_output=True, text=True
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
            ["iptables", "-C", self.chain, "-p", protocol, "--dport", str(port), "-j", "ACCEPT"],
            capture_output=True,
        )
        return result.returncode == 0

    def _save_rules(self) -> None:
        """Persist iptables rules to disk."""
        # Try iptables-save
        subprocess.run("iptables-save > /etc/iptables/rules.v4 2>/dev/null || true", shell=True)
        subprocess.run("ip6tables-save > /etc/iptables/rules.v6 2>/dev/null || true", shell=True)


"""MoeNet DN42 Agent - Loopback IP Executor

Configures the dummy0 interface with DN42 IP addresses based on node_id.
"""


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

        Args:
            node_id: Must be 1-62 for /26 subnet (64 addresses minus network/broadcast)
        """
        # Validate node_id is within /26 range
        prefix_len = int(self.dn42_ipv4_prefix.split("/")[1])
        max_host_id = (2 ** (32 - prefix_len)) - 2  # Subtract network and broadcast

        if node_id < 1 or node_id > max_host_id:
            logger.error(
                f"Invalid node_id={node_id}: must be 1-{max_host_id} for /{prefix_len} subnet. "
                f"Re-register with Control Plane to get a valid node_id."
            )
            return False

        try:
            # Calculate node-specific addresses
            # IPv4: base prefix + node_id
            ipv4_base = self.dn42_ipv4_prefix.split("/")[0]
            ipv4_parts = ipv4_base.split(".")
            ipv4_node = f"{'.'.join(ipv4_parts[:-1])}.{node_id}"

            # IPv6: base prefix + ::node_id
            ipv6_base = self.dn42_ipv6_prefix.split("/")[0].rstrip(":")
            ipv6_node = f"{ipv6_base}::{node_id}"

            # Note: Only add specific /32 and /128 addresses to the interface
            # The prefixes are announced via BGP from the direct protocol
            # Do NOT add /26 or /48 here - it makes the kernel treat all addresses as local!
            # This would create a connected route that prevents inter-node routing.
            addresses = [
                # Only the /32 node address, NOT the /26 prefix
                (f"{ipv4_node}/32", "IPv4 node"),
                # Only the /128 loopback, NOT the /48 prefix
                (f"{ipv6_node}/128", "IPv6 node"),
            ]

            # Cleanup any stale node-specific addresses (from old node_id)
            self._cleanup_stale_addresses(ipv4_node, ipv6_node)

            for addr, desc in addresses:
                self._add_address(addr, desc)

            logger.info(f"Loopback configured: IPv4={ipv4_node}, IPv6={ipv6_node}")
            return True

        except Exception as e:
            logger.error(f"Failed to configure loopback: {e}")
            return False

    def _cleanup_stale_addresses(self, current_ipv4: str, current_ipv6: str) -> None:
        """Remove stale node-specific addresses from old node_id.

        Keeps:
        - The IPv4/IPv6 prefix addresses (for BGP announcement)
        - The current node-specific addresses

        Removes:
        - Any other /32 or /128 addresses in the DN42 range
        """
        try:
            # Get current addresses on interface
            result = subprocess.run(
                ["ip", "addr", "show", self.interface],
                capture_output=True,
                text=True,
            )

            if result.returncode != 0:
                return

            # Parse IPv4 prefix info
            ipv4_base = self.dn42_ipv4_prefix.split("/")[0]
            ipv4_prefix_parts = ipv4_base.rsplit(".", 1)[0]  # e.g., "172.22.188"

            # Parse IPv6 prefix info
            ipv6_base = self.dn42_ipv6_prefix.split("/")[0].rstrip(":")

            # Find and remove stale addresses
            import re

            for line in result.stdout.split("\n"):
                # Match IPv4 addresses
                ipv4_match = re.search(r"inet (\d+\.\d+\.\d+\.\d+)/(\d+)", line)
                if ipv4_match:
                    addr = ipv4_match.group(1)
                    prefix_len = ipv4_match.group(2)
                    # Check if it's a /32 node address in our range but NOT current
                    if (
                        prefix_len == "32"
                        and addr.rsplit(".", 1)[0] == ipv4_prefix_parts
                        and addr != current_ipv4
                    ):
                        logger.info(f"Removing stale IPv4: {addr}/32")
                        subprocess.run(
                            ["ip", "addr", "del", f"{addr}/32", "dev", self.interface],
                            capture_output=True,
                        )

                # Match IPv6 addresses
                ipv6_match = re.search(r"inet6 ([a-f0-9:]+)/(\d+)", line)
                if ipv6_match:
                    addr = ipv6_match.group(1)
                    prefix_len = ipv6_match.group(2)
                    # Check if it's a /128 node address in our range but NOT current
                    if (
                        prefix_len == "128"
                        and addr.startswith(ipv6_base.rstrip(":"))
                        and addr != current_ipv6
                    ):
                        logger.info(f"Removing stale IPv6: {addr}/128")
                        subprocess.run(
                            ["ip", "-6", "addr", "del", f"{addr}/128", "dev", self.interface],
                            capture_output=True,
                        )

        except Exception as e:
            logger.warning(f"Error cleaning up stale addresses: {e}")

    def _add_address(self, address: str, description: str) -> bool:
        """Add an IP address to the interface if not already present."""
        try:
            # Check if address already exists
            check = subprocess.run(
                ["ip", "addr", "show", self.interface],
                capture_output=True,
                text=True,
            )

            addr_only = address.split("/")[0]
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
