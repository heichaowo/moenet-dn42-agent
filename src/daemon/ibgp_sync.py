"""MoeNet DN42 Agent - iBGP Sync

Syncs iBGP peer configurations with Control Plane.
Uses same topology as mesh_sync (RR <-> same-region edges, RR <-> cross-region RRs).

Configuration is written to /etc/bird/ibgp.d/*.conf
"""
import asyncio
import logging
from pathlib import Path
from typing import Set

from client.control_plane import ControlPlaneClient
from renderer.ibgp import render_ibgp_peer
from executor.bird import BirdExecutor

logger = logging.getLogger(__name__)

IBGP_CONFIG_DIR = Path("/etc/bird/ibgp.d")
DN42_ASN = 4242420998


class IBGPSync:
    """Handles iBGP peer configuration synchronization."""
    
    def __init__(
        self,
        client: ControlPlaneClient,
        bird_executor: BirdExecutor,
        node_id: int,
    ):
        self.client = client
        self.bird = bird_executor
        self.node_id = node_id
        self._active_peers: Set[str] = set()
    
    async def sync_ibgp(self) -> bool:
        """Sync iBGP peer configurations.
        
        1. Get mesh config from control plane (includes iBGP peers)
        2. Generate peer configs
        3. Clean up stale configs
        4. Reload BIRD
        """
        logger.info("Syncing iBGP peer configurations...")
        
        # Ensure config directory exists
        IBGP_CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        
        # Get mesh config (includes iBGP peers)
        mesh_config = await self.client.get_mesh_config()
        if not mesh_config:
            logger.warning("No mesh config available for iBGP sync")
            return False
        
        peers = mesh_config.get("peers", [])
        logger.info(f"iBGP peers: {len(peers)}")
        
        active_peer_names: Set[str] = set()
        
        for peer in peers:
            peer_name = peer["name"]
            peer_loopback = peer["loopback"]
            
            # Normalize peer name for filename
            safe_name = peer_name.replace(".", "_").replace("-", "_")
            active_peer_names.add(safe_name)
            
            # Render peer config
            config = render_ibgp_peer(
                peer_name=peer_name,
                peer_loopback=peer_loopback,
                asn=DN42_ASN,
            )
            
            # Write config file
            config_path = IBGP_CONFIG_DIR / f"{safe_name}.conf"
            config_path.write_text(config)
            logger.info(f"Configured iBGP peer: {peer_name} -> {peer_loopback}")
        
        # Cleanup stale configs
        self._cleanup_stale_configs(active_peer_names)
        
        # Reload BIRD
        self.bird.reload()
        logger.info("iBGP sync complete")
        
        return True
    
    def _cleanup_stale_configs(self, active_peers: Set[str]):
        """Remove iBGP configs for peers that are no longer active.
        
        Args:
            active_peers: Set of active peer names (normalized for filenames)
        """
        for config_file in IBGP_CONFIG_DIR.glob("*.conf"):
            peer_name = config_file.stem
            if peer_name not in active_peers:
                logger.info(f"Removing stale iBGP config: {config_file.name}")
                config_file.unlink()
