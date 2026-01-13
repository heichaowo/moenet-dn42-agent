"""MoeNet DN42 Agent - Sync Daemon"""
import asyncio
import logging
from typing import Optional

from client.control_plane import ControlPlaneClient
from state.manager import StateManager
from executor.bird import BirdExecutor
from executor.wireguard import WireGuardExecutor
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
        sync_interval: int = 60,
        heartbeat_interval: int = 30,
    ):
        self.client = client
        self.state = state_manager
        self.bird = bird_executor
        self.wg = wg_executor
        self.bird_renderer = BirdRenderer()
        self.wg_renderer = WireGuardRenderer()
        self.sync_interval = sync_interval
        self.heartbeat_interval = heartbeat_interval
        self._running = False
    
    async def sync_config(self) -> bool:
        logger.info("Syncing config from control-plane...")
        config = await self.client.get_config()
        if not config:
            return False
        
        remote_hash = config.get("version_hash") or self.client.compute_config_hash(config)
        if remote_hash == self.state.get_config_hash():
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
    
    def _add_peer(self, peer: dict):
        asn = peer["asn"]
        if peer.get("tunnel", {}).get("type") == "wireguard":
            config = self.wg_renderer.render_interface(peer, self.wg.private_key, "")
            self.wg.write_interface(asn, config)
            self.wg.up(asn)
        self.bird.write_peer(asn, self.bird_renderer.render_peer(peer))
    
    def _update_peer(self, peer: dict):
        self._add_peer(peer)
    
    def _remove_peer(self, asn: int):
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
                counter = 0
    
    async def stop(self):
        self._running = False
