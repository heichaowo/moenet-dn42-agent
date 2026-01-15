"""MoeNet DN42 Agent - Mesh Network Sync

Syncs WireGuard IGP mesh tunnels and Babel configuration.
"""
import asyncio
import logging
import subprocess
from pathlib import Path
from typing import Optional

from client.control_plane import ControlPlaneClient
from renderer.wg_mesh import get_or_create_mesh_key, render_mesh_interface
from renderer.babel import render_babel_config, render_ibgp_peer
from executor.wireguard import WireGuardExecutor
from executor.bird import BirdExecutor

logger = logging.getLogger(__name__)

MESH_KEY_PATH = Path("/var/lib/moenet-agent/mesh_private_key")


class MeshSync:
    """Handles mesh network synchronization for IGP underlay."""
    
    def __init__(
        self,
        client: ControlPlaneClient,
        wg_executor: WireGuardExecutor,
        bird_executor: BirdExecutor,
        node_id: int,
        mesh_port: int = 51820,
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
            if not addr:
                return True
            
            # Ensure proper suffix
            if "/" not in addr:
                suffix = "/32" if "." in addr else "/128"
                addr = f"{addr}{suffix}"
            
            try:
                # Check if already configured
                family = "-4" if "." in addr else "-6"
                result = subprocess.run(
                    ["ip", family, "addr", "show", "dev", dev],
                    capture_output=True, text=True
                )
                if addr.split("/")[0] in result.stdout:
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
    
    async def sync_mesh(self) -> bool:
        """Sync mesh network configuration.
        
        1. Get mesh config from control plane
        2. Configure local loopback address
        3. Create/update WG IGP interfaces
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
        
        # Create/update WG IGP interfaces
        for peer in peers:
            interface_name = f"wg-igp-{peer['node_id']}"
            
            config = render_mesh_interface(
                interface_name=interface_name,
                private_key=private_key,
                listen_port=self.mesh_port,
                node_id=self.node_id,
                peer_name=peer["name"],
                peer_public_key=peer["public_key"],
                peer_node_id=peer["node_id"],
                peer_loopback=peer["loopback"],
                peer_endpoint=peer.get("endpoint"),
                peer_port=peer.get("port", 51820),
            )
            
            self.wg.write_interface(interface_name, config)
            self.wg.up(interface_name)
            logger.info(f"Configured mesh interface: {interface_name}")
        
        # Generate iBGP configs using loopback addresses
        local_is_rr = mesh_config.get("is_rr", False)
        
        for peer in peers:
            ibgp_config = render_ibgp_peer(
                peer_name=peer["name"],
                peer_loopback=peer["loopback"],
                asn=4242420998,
                is_rr_client=local_is_rr and not peer.get("is_rr", False),
            )
            
            peer_file = f"ibgp_{peer['name'].replace('.', '_').replace('-', '_')}.conf"
            self.bird.write_peer(peer_file, ibgp_config)
        
        # Write Babel config
        babel_config = render_babel_config()
        babel_path = Path(self.bird.config_dir).parent / "babel.conf"
        babel_path.write_text(babel_config)
        logger.info("Updated Babel configuration")
        
        # Reload BIRD
        self.bird.reload()
        logger.info("Mesh sync complete")
        
        return True

