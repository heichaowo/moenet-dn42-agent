"""MoeNet DN42 Agent - Mesh Network Sync (P2P Mode)

Syncs WireGuard IGP mesh tunnels and Babel configuration.
Uses one WireGuard interface per peer (P2P mode) for:
- Complete AllowedIPs per interface (no conflicts)
- Per-interface MTU settings
- Per-interface Babel cost/metric

Port allocation scheme:
- Each interface listens on: base_port + peer_node_id
- Connects to peer on: base_port + local_node_id
- Example: Node 3 connecting to Node 1 -> listen on 51821, connect to 51823
"""
import asyncio
import logging
import subprocess
from pathlib import Path
from typing import Optional, Set

from client.control_plane import ControlPlaneClient
from renderer.wg_mesh import (
    get_or_create_mesh_key, 
    render_mesh_interface, 
    get_mesh_interface_name,
    get_mesh_listen_port,
    generate_link_local,
)
from renderer.babel import render_babel_config, render_ibgp_peer
from executor.wireguard import WireGuardExecutor
from executor.bird import BirdExecutor

logger = logging.getLogger(__name__)

MESH_KEY_PATH = Path("/var/lib/moenet-agent/mesh_private_key")
MESH_BASE_PORT = 51820  # Base port, actual = base + peer_node_id
MESH_MTU_DEFAULT = 1400  # Default MTU for public internet
MESH_MTU_PRIVATE = 1420  # MTU for private/dedicated links


class MeshSync:
    """Handles mesh network synchronization for IGP underlay (P2P Mode)."""
    
    def __init__(
        self,
        client: ControlPlaneClient,
        wg_executor: WireGuardExecutor,
        bird_executor: BirdExecutor,
        node_id: int,
    ):
        self.client = client
        self.wg = wg_executor
        self.bird = bird_executor
        self.node_id = node_id
        self._private_key: Optional[str] = None
        self._public_key: Optional[str] = None
        self._active_interfaces: Set[str] = set()
    
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
                    normalized_input = str(ipaddress.ip_address(addr_only))
                else:
                    normalized_input = addr_only
                
                # Check if already configured
                family = "-4" if "." in addr else "-6"
                result = subprocess.run(
                    ["ip", family, "addr", "show", "dev", dev],
                    capture_output=True, text=True
                )
                
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
        
        success = True
        success &= add_addr(loopback_ipv6)
        success &= add_addr(dn42_ipv4)
        success &= add_addr(dn42_ipv6)
        return success
    
    def _configure_interface_link_local(self, interface_name: str):
        """Configure link-local IPv6 address on mesh interface for Babel.
        
        Args:
            interface_name: Interface name (e.g., dn42-wg-igp-1)
        """
        link_local = generate_link_local(self.node_id)
        try:
            # Check if already configured
            result = subprocess.run(
                ["ip", "-6", "addr", "show", "dev", interface_name],
                capture_output=True, text=True
            )
            if link_local in result.stdout:
                logger.debug(f"Link-local {link_local} already configured on {interface_name}")
                return
            
            # Add link-local address
            subprocess.run(
                ["ip", "-6", "addr", "add", f"{link_local}/64", "dev", interface_name],
                capture_output=True, check=True
            )
            logger.info(f"Configured link-local {link_local}/64 on {interface_name}")
        except subprocess.CalledProcessError as e:
            if "exists" not in str(e.stderr):
                logger.warning(f"Failed to add link-local on {interface_name}: {e}")
        except Exception as e:
            logger.warning(f"Error configuring link-local on {interface_name}: {e}")
    
    def _set_interface_mtu(self, interface_name: str, mtu: int = MESH_MTU_DEFAULT):
        """Set MTU on mesh interface.
        
        Args:
            interface_name: Interface name
            mtu: MTU value (1400 for public, 1420 for private)
        """
        try:
            subprocess.run(
                ["ip", "link", "set", "dev", interface_name, "mtu", str(mtu)],
                capture_output=True, check=True
            )
            logger.debug(f"Set MTU {mtu} on {interface_name}")
        except subprocess.CalledProcessError as e:
            logger.warning(f"Failed to set MTU on {interface_name}: {e}")
    
    def _cleanup_stale_interfaces(self, active_peer_ids: Set[int]):
        """Remove interfaces for peers that are no longer in the mesh.
        
        Args:
            active_peer_ids: Set of currently active peer node IDs
        """
        # Get current mesh interfaces
        current_status = self.wg.get_status()
        current_names = set(current_status.get("names", []))
        
        for name in current_names:
            if name.startswith("dn42-wg-igp-"):
                try:
                    suffix = name.split("-")[-1]
                    peer_id = int(suffix)
                    if peer_id not in active_peer_ids:
                        logger.info(f"Removing stale mesh interface: {name}")
                        self.wg.down(name)
                        self.wg.remove_interface(name)
                except (ValueError, IndexError):
                    pass
    
    async def sync_mesh(self) -> bool:
        """Sync mesh network configuration (P2P Mode).
        
        1. Get mesh config from control plane
        2. Configure local loopback address
        3. Create/update WG IGP interface for each peer
        4. Configure link-local and MTU per interface
        5. Update Babel config
        """
        logger.info("Syncing mesh network (P2P mode)...")
        
        # Ensure keys are initialized
        private_key, public_key = await self.init_keys()
        
        # Get mesh config from control plane
        mesh_config = await self.client.get_mesh_config()
        if not mesh_config:
            logger.warning("No mesh config available")
            return False
        
        # Configure loopback addresses on dummy0
        local_loopback = mesh_config.get("loopback")
        dn42_ipv4 = mesh_config.get("dn42_ipv4")
        dn42_ipv6 = mesh_config.get("dn42_ipv6")
        if local_loopback or dn42_ipv4 or dn42_ipv6:
            self.configure_loopback(local_loopback, dn42_ipv4, dn42_ipv6)
        
        peers = mesh_config.get("peers", [])
        logger.info(f"Mesh peers: {len(peers)}")
        
        if not peers:
            logger.info("No mesh peers configured")
            return True
        
        # Track active peer IDs for cleanup
        active_peer_ids: Set[int] = set()
        
        # Configure each peer in P2P mode
        for peer in peers:
            peer_node_id = peer["node_id"]
            peer_name = peer["name"]
            active_peer_ids.add(peer_node_id)
            
            # Calculate ports:
            # We listen on: base_port + peer_node_id (unique per peer)
            # Peer listens on: base_port + our_node_id
            listen_port = get_mesh_listen_port(peer_node_id, MESH_BASE_PORT)
            peer_port = get_mesh_listen_port(self.node_id, MESH_BASE_PORT)
            
            # Render interface config
            config, interface_name, _ = render_mesh_interface(
                private_key=private_key,
                peer_node_id=peer_node_id,
                peer_name=peer_name,
                peer_public_key=peer["public_key"],
                peer_loopback=peer["loopback"],
                peer_endpoint=peer.get("endpoint"),
                peer_port=peer_port,
                base_port=MESH_BASE_PORT,
            )
            
            # Write and bring up interface
            self.wg.write_interface(interface_name, config)
            self.wg.up(interface_name)
            
            # Configure MTU (can be customized per peer in future)
            self._set_interface_mtu(interface_name, MESH_MTU_DEFAULT)
            
            # Configure link-local address
            self._configure_interface_link_local(interface_name)
            
            logger.info(f"Configured mesh interface: {interface_name} -> {peer_name} (port {listen_port})")
        
        # Cleanup stale interfaces
        self._cleanup_stale_interfaces(active_peer_ids)

        # Write Babel config only if changed
        babel_config = render_babel_config()
        babel_path = Path(self.bird.config_dir).parent / "babel.conf"
        
        config_changed = False
        if babel_path.exists():
            if babel_path.read_text() != babel_config:
                babel_path.write_text(babel_config)
                config_changed = True
                logger.info("Updated Babel configuration")
            else:
                logger.debug("Babel configuration unchanged")
        else:
            babel_path.write_text(babel_config)
            config_changed = True
            logger.info("Created Babel configuration")
        
        # Only reload BIRD if config changed
        if config_changed:
            self.bird.reload()
        
        logger.info("Mesh sync complete (P2P mode)")
        
        return True
