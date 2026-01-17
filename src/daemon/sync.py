"""MoeNet DN42 Agent - Sync Daemon"""
import asyncio
import logging
from typing import Optional

from client.control_plane import ControlPlaneClient
from state.manager import StateManager
from executor.bird import BirdExecutor
from executor.wireguard import WireGuardExecutor
from executor.firewall import FirewallExecutor
from renderer.bird import BirdRenderer
from renderer.wireguard import WireGuardRenderer

logger = logging.getLogger(__name__)


class SyncDaemon:
    def __init__(
        self,
        client: ControlPlaneClient,
        state_manager: StateManager,
        bird_executor: BirdExecutor,
        wg_executor: WireGuardExecutor,
        firewall_executor: FirewallExecutor = None,
        mesh_sync = None,  # Optional MeshSync instance for periodic mesh sync
        sync_interval: int = 60,
        heartbeat_interval: int = 30,
    ):
        self.client = client
        self.state = state_manager
        self.bird = bird_executor
        self.wg = wg_executor
        self.firewall = firewall_executor or FirewallExecutor()
        self.bird_renderer = BirdRenderer()
        self.wg_renderer = WireGuardRenderer()
        self.mesh_sync = mesh_sync
        self.sync_interval = sync_interval
        self.heartbeat_interval = heartbeat_interval
        self._running = False
    
    async def sync_config(self) -> bool:
        logger.info("Syncing config from control-plane...")
        config = await self.client.get_config()
        if not config:
            return False
        
        remote_hash = config.get("version_hash") or self.client.compute_config_hash(config)
        
        # Always sync iBGP (config file might be missing even if hash matches)
        ibgp_peers = config.get("ibgp_peers", [])
        if ibgp_peers:
            local_ipv6 = config.get("local_ipv6") or config.get("node_info", {}).get("dn42_ipv6")
            self._sync_ibgp(ibgp_peers, local_ipv6=local_ipv6)
        
        # Check if any expected peer config files are missing (force regeneration)
        needs_regeneration = False
        for peer in config.get("peers", []):
            asn = peer["asn"]
            bird_path = self.bird.peers_dir / f"dn42_{asn}.conf"
            wg_path = self.wg.config_dir / f"dn42-{asn}.conf"
            if not bird_path.exists() or not wg_path.exists():
                logger.info(f"Missing config for AS{asn}, forcing regeneration")
                needs_regeneration = True
                break
        
        if remote_hash == self.state.get_config_hash() and not needs_regeneration:
            logger.info("Config up to date")
            return True
        
        logger.info(f"Config changed, applying...")
        current = {p["asn"] for p in self.state.get_applied_peers()}
        new_peers = {p["asn"] for p in config.get("peers", [])}
        
        for peer in config.get("peers", []):
            if peer["asn"] not in current:
                self._add_peer(peer)
            else:
                self._update_peer(peer)
        
        for asn in current - new_peers:
            self._remove_peer(asn)
        
        self.bird.reload()
        self.state.update_applied_config(config.get("peers", []), remote_hash)
        logger.info("Config sync complete")
        return True
    
    def _sync_ibgp(self, ibgp_peers: list, local_ipv6: str = None):
        """Sync iBGP peer configurations."""
        from renderer.ibgp import render_ibgp_config
        
        ibgp_config = render_ibgp_config({
            "local_name": self.client.node_name,
            "local_asn": 4242420998,
            "local_ipv6": local_ipv6,  # For source address in BGP session
            "is_rr": any(p.get("is_rr_client") for p in ibgp_peers),  # We're RR if we have clients
            "peers": ibgp_peers,
        })
        self.bird.write_ibgp(ibgp_config)
    
    def _add_peer(self, peer: dict):
        asn = peer["asn"]
        listen_port = peer.get("listen_port") or self._calculate_listen_port(asn)
        
        # Open firewall port for WireGuard
        if peer.get("tunnel", {}).get("type") == "wireguard":
            self.firewall.allow_port(listen_port)
            config = self.wg_renderer.render_interface(peer, self.wg.private_key, "")
            self.wg.write_interface(asn, config)
            self.wg.up(asn)
        
        self.bird.write_peer(asn, self.bird_renderer.render_peer(peer))
    
    def _calculate_listen_port(self, remote_as: int) -> int:
        """Calculate WireGuard listen port based on remote ASN."""
        if 4242420000 <= remote_as <= 4242429999:
            return 30000 + (remote_as % 10000)
        elif 4201270000 <= remote_as <= 4201279999:
            return 40000 + (remote_as % 10000)
        else:
            return 50000 + (remote_as % 10000)
    
    def _update_peer(self, peer: dict):
        self._add_peer(peer)
    
    def _remove_peer(self, asn: int):
        # Close firewall port
        listen_port = self._calculate_listen_port(asn)
        self.firewall.remove_port(listen_port)
        
        self.wg.down(asn)
        self.wg.remove_interface(asn)
        self.bird.remove_peer(asn)
    
    async def send_heartbeat(self) -> bool:
        status = {**self.bird.get_status(), **self.wg.get_status()}
        self.state.update_health(status)
        return await self.client.send_heartbeat("2.1.0", self.state.get_config_hash(), status)
    
    async def run(self):
        self._running = True
        await self.sync_config()
        counter = 0
        while self._running:
            await asyncio.sleep(self.heartbeat_interval)
            counter += self.heartbeat_interval
            await self.send_heartbeat()
            if counter >= self.sync_interval:
                await self.sync_config()
                # Also sync mesh network periodically (retry failed tunnels)
                if self.mesh_sync:
                    try:
                        await self.mesh_sync.sync_mesh()
                    except Exception as e:
                        logger.warning(f"Mesh sync failed: {e}")
                counter = 0
    
    async def stop(self):
        self._running = False
