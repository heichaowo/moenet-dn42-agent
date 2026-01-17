"""MoeNet DN42 Agent - Mesh Network Sync

Syncs WireGuard IGP mesh tunnels and OSPFv3 configuration.
Uses single WireGuard interface with multiple peers to avoid port conflicts.
"""
import asyncio
import logging
import subprocess
from pathlib import Path
from typing import Optional

from client.control_plane import ControlPlaneClient
from renderer.wg_mesh import get_or_create_mesh_key, render_mesh_config
from renderer.ospf import render_ospf_neighbors
from executor.wireguard import WireGuardExecutor
from executor.bird import BirdExecutor

logger = logging.getLogger(__name__)

MESH_KEY_PATH = Path("/var/lib/moenet-agent/mesh_private_key")
MESH_INTERFACE_NAME = "dn42-wg-igp"  # Single interface for all mesh peers


class MeshSync:
    """Handles mesh network synchronization for IGP underlay."""
    
    def __init__(
        self,
        client: ControlPlaneClient,
        wg_executor: WireGuardExecutor,
        bird_executor: BirdExecutor,
        node_id: int,
        mesh_port: int = 51821,  # Use 51821 to avoid conflict with eBGP WG on 51820
    ):
        self.client = client
        self.wg = wg_executor
        self.bird = bird_executor
        self.node_id = node_id
        self.mesh_port = mesh_port
        self._private_key: Optional[str] = None
        self._public_key: Optional[str] = None
    
    async def init_keys(self) -> tuple[str, str]:
        """Initialize mesh WireGuard keys.
        
        Returns:
            Tuple of (private_key, public_key)
        """
        if self._private_key and self._public_key:
            return self._private_key, self._public_key
        
        self._private_key, self._public_key = get_or_create_mesh_key(MESH_KEY_PATH)
        logger.info(f"Mesh public key: {self._public_key[:20]}...")
        
        # Register key with control plane
        try:
            await self.client.register_mesh_key(self._public_key)
            logger.info("Registered mesh key with control plane")
        except Exception as e:
            logger.warning(f"Failed to register mesh key: {e}")
        
        return self._private_key, self._public_key
    
    def configure_loopback(self, loopback_ipv6: str, dn42_ipv4: str = None, dn42_ipv6: str = None) -> bool:
        """Configure loopback addresses on dummy0 interface.
        
        Args:
            loopback_ipv6: Loopback IPv6 address (e.g., fd00:4242:7777:0::3)
            dn42_ipv4: DN42 IPv4 address (e.g., 172.22.188.3)
            dn42_ipv6: DN42 IPv6 address (optional)
        
        Returns:
            True if configured successfully
        """
        def add_addr(addr: str, dev: str = "dummy0") -> bool:
            """Add address to interface if not already present."""
            import ipaddress
            
            if not addr:
                return True
            
            # Ensure proper suffix
            if "/" not in addr:
                suffix = "/32" if "." in addr else "/128"
                addr = f"{addr}{suffix}"
            
            try:
                # Normalize the input address for comparison
                addr_only = addr.split("/")[0]
                if ":" in addr_only:
                    # IPv6 - normalize to compare properly
                    normalized_input = str(ipaddress.ip_address(addr_only))
                else:
                    normalized_input = addr_only
                
                # Check if already configured
                family = "-4" if "." in addr else "-6"
                result = subprocess.run(
                    ["ip", family, "addr", "show", "dev", dev],
                    capture_output=True, text=True
                )
                
                # Check if normalized address is in output
                if normalized_input in result.stdout:
                    logger.debug(f"Address {addr} already configured on {dev}")
                    return True
                
                # Add address
                result = subprocess.run(
                    ["ip", family, "addr", "add", addr, "dev", dev],
                    capture_output=True, text=True
                )
                if result.returncode == 0 or "exists" in result.stderr:
                    logger.info(f"Configured {addr} on {dev}")
                    return True
                else:
                    logger.error(f"Failed to add {addr}: {result.stderr}")
                    return False
            except Exception as e:
                logger.error(f"Error adding address: {e}")
                return False
        
        # Configure all addresses on dummy0
        success = True
        success &= add_addr(loopback_ipv6)
        success &= add_addr(dn42_ipv4)
        success &= add_addr(dn42_ipv6)
        return success
    
    def _cleanup_old_interfaces(self):
        """Remove old per-peer mesh interfaces (migration from old design)."""
        current_status = self.wg.get_status()
        current_names = set(current_status.get("names", []))
        
        for name in current_names:
            # Old format: dn42-wg-igp-{node_id} or wg-igp-{node_id}
            if name.startswith("dn42-wg-igp-") or name.startswith("wg-igp-"):
                # Check if it's the old per-peer format (has node_id suffix)
                suffix = name.split("-")[-1]
                if suffix.isdigit():
                    logger.info(f"Removing old per-peer mesh interface: {name}")
                    self.wg.down(name)
                    self.wg.remove_interface(name)
    
    def _configure_mesh_link_local(self):
        """Configure link-local IPv6 address on mesh interface for Babel.
        
        Babel requires IPv6 addresses on interfaces to exchange routes.
        We use fe80::{node_id}/64 as the link-local address.
        """
        link_local = f"fe80::{self.node_id}"
        try:
            # Check if already configured
            result = subprocess.run(
                ["ip", "-6", "addr", "show", "dev", MESH_INTERFACE_NAME],
                capture_output=True, text=True
            )
            if link_local in result.stdout:
                logger.debug(f"Link-local {link_local} already configured")
                return
            
            # Add link-local address
            subprocess.run(
                ["ip", "-6", "addr", "add", f"{link_local}/64", "dev", MESH_INTERFACE_NAME],
                capture_output=True, check=True
            )
            logger.info(f"Configured link-local {link_local}/64 on {MESH_INTERFACE_NAME}")
        except subprocess.CalledProcessError as e:
            # May already exist, ignore error
            if "exists" not in str(e.stderr):
                logger.warning(f"Failed to add link-local: {e}")
        except Exception as e:
            logger.warning(f"Error configuring link-local: {e}")
    
    async def sync_mesh(self) -> bool:
        """Sync mesh network configuration.
        
        1. Get mesh config from control plane
        2. Configure local loopback address
        3. Create/update single WG IGP interface with all peers
        4. Generate iBGP peer configs using loopback addresses
        """
        logger.info("Syncing mesh network...")
        
        # Ensure keys are initialized
        private_key, public_key = await self.init_keys()
        
        # Get mesh config from control plane
        mesh_config = await self.client.get_mesh_config()
        if not mesh_config:
            logger.warning("No mesh config available")
            return False
        
        # Configure loopback addresses on dummy0 (created by Ansible)
        local_loopback = mesh_config.get("loopback")
        dn42_ipv4 = mesh_config.get("dn42_ipv4")
        dn42_ipv6 = mesh_config.get("dn42_ipv6")
        if local_loopback or dn42_ipv4 or dn42_ipv6:
            self.configure_loopback(local_loopback, dn42_ipv4, dn42_ipv6)
        
        peers = mesh_config.get("peers", [])
        logger.info(f"Mesh peers: {len(peers)}")
        
        # Clean up old per-peer interfaces (migration)
        self._cleanup_old_interfaces()
        
        if not peers:
            logger.info("No mesh peers configured")
            return True
        
        # Build peer list for config
        peer_configs = []
        for peer in peers:
            peer_configs.append({
                "name": peer["name"],
                "node_id": peer["node_id"],
                "public_key": peer["public_key"],
                "loopback": peer["loopback"],
                "endpoint": peer.get("endpoint"),
                "port": peer.get("port", self.mesh_port),
            })
        
        # Render single interface config with all peers
        config = render_mesh_config(
            private_key=private_key,
            listen_port=self.mesh_port,
            peers=peer_configs,
        )
        
        # Write and bring up the single mesh interface
        self.wg.write_interface(MESH_INTERFACE_NAME, config)
        self.wg.up(MESH_INTERFACE_NAME)
        logger.info(f"Configured mesh interface: {MESH_INTERFACE_NAME} with {len(peers)} peers")
        
        # Add link-local IPv6 address for OSPFv3 (fe80::{node_id})
        # This is required for OSPFv3 unicast hellos on the mesh interface
        self._configure_mesh_link_local()

        # Write OSPFv3 neighbors config (PTMP mode uses unicast, not multicast)
        ospf_peers = [{
            "name": peer["name"],
            "node_id": peer["node_id"],
            "link_local": f"fe80::{peer['node_id']}",
        } for peer in peers]
        ospf_config = render_ospf_neighbors(ospf_peers)
        ospf_path = Path(self.bird.config_dir) / "ospf_neighbors.conf"
        ospf_path.write_text(ospf_config)
        logger.info("Updated OSPFv3 neighbors configuration")
        
        # NOTE: iBGP peers are now managed by SyncDaemon to prevent duplication
        
        # Reload BIRD
        self.bird.reload()
        logger.info("Mesh sync complete")
        
        return True

