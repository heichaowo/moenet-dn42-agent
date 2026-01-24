"""MoeNet DN42 Agent - Sync Daemon"""

import asyncio
import logging

from services.firewall import FirewallExecutor

from integrations.control_plane import ControlPlaneClient
from renderer.bird import BirdRenderer
from renderer.wireguard import WireGuardRenderer
from services.bird import BirdExecutor
from services.wireguard import WireGuardExecutor
from state.manager import StateManager

logger = logging.getLogger(__name__)


class SyncDaemon:
    def __init__(
        self,
        client: ControlPlaneClient,
        state_manager: StateManager,
        bird_executor: BirdExecutor,
        wg_executor: WireGuardExecutor,
        firewall_executor: FirewallExecutor = None,
        mesh_sync=None,  # Optional MeshSync instance for periodic mesh sync
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
            logger.warning("No config received from control-plane")
            return False

        ebgp_count = len(config.get("peers", []))
        ibgp_count = len(config.get("ibgp_peers", []))
        logger.info(f"Received config: {ebgp_count} eBGP peers, {ibgp_count} iBGP peers")

        remote_hash = config.get("version_hash") or self.client.compute_config_hash(config)

        # iBGP sync is now handled by ibgp_sync.py in main.py
        # Commenting out to avoid conflict with new ibgp_sync module
        # ibgp_peers = config.get("ibgp_peers", [])
        # if ibgp_peers:
        #     local_ipv6 = config.get("local_ipv6") or config.get("node_info", {}).get("dn42_ipv6")
        #     self._sync_ibgp(ibgp_peers, local_ipv6=local_ipv6)

        # Always check all peers - _add_peer uses hash comparison for efficiency
        current = {p["asn"] for p in self.state.get_applied_peers()}
        new_peers = {p["asn"] for p in config.get("peers", [])}

        # Track if any changes were made

        for peer in config.get("peers", []):
            self._add_peer(peer)

        for asn in current - new_peers:
            self._remove_peer(asn)

        # Only reload BIRD if hash changed (implies config changes)
        if remote_hash != self.state.get_config_hash():
            self.bird.reload()
            self.state.update_applied_config(config.get("peers", []), remote_hash)
            logger.info("Config sync complete")
        else:
            logger.debug("Config up to date")

        return True

    def _sync_ibgp(self, ibgp_peers: list, local_ipv6: str = None):
        """Sync iBGP peer configurations."""
        from renderer.ibgp import render_ibgp_config

        ibgp_config = render_ibgp_config(
            {
                "local_name": self.client.node_name,
                "local_asn": 4242420998,
                "local_ipv6": local_ipv6,  # For source address in BGP session
                "is_rr": any(
                    p.get("is_rr_client") for p in ibgp_peers
                ),  # We're RR if we have clients
                "peers": ibgp_peers,
            }
        )
        self.bird.write_ibgp(ibgp_config)

    def _add_peer(self, peer: dict):
        import hashlib

        asn = peer["asn"]
        listen_port = peer.get("listen_port") or self._calculate_listen_port(asn)

        # Generate expected configs
        local_addr = peer.get("bgp", {}).get("request_lla", "")
        expected_wg = self.wg_renderer.render_interface(peer, self.wg.private_key, local_addr)
        expected_bird = self.bird_renderer.render_peer(peer)

        wg_path = self.wg.config_dir / f"dn42-{asn}.conf"
        bird_path = self.bird.config_dir / f"dn42_{asn}.conf"

        # Compare with existing files using hash
        def file_hash(path) -> str:
            if path.exists():
                return hashlib.md5(path.read_text().encode()).hexdigest()
            return ""

        wg_needs_update = file_hash(wg_path) != hashlib.md5(expected_wg.encode()).hexdigest()
        bird_needs_update = file_hash(bird_path) != hashlib.md5(expected_bird.encode()).hexdigest()

        # Update WireGuard if needed
        if peer.get("tunnel", {}).get("type") == "wireguard":
            if wg_needs_update:
                self.firewall.allow_port(listen_port)
                self.wg.write_interface(asn, expected_wg)
                logger.info(f"Updated WG config for AS{asn}")
            # Always ensure interface is up (even if config unchanged)
            self.wg.up(asn)

        # Update BIRD if needed
        if bird_needs_update:
            self.bird.write_peer(asn, expected_bird)
            logger.info(f"Updated BIRD config for AS{asn}")

    def _calculate_listen_port(self, remote_as: int) -> int:
        """Calculate WireGuard listen port based on remote ASN."""
        if 4242420000 <= remote_as <= 4242429999:
            return 30000 + (remote_as % 10000)
        elif 4201270000 <= remote_as <= 4201279999:
            return 40000 + (remote_as % 10000)
        else:
            return 50000 + (remote_as % 10000)

    def _remove_peer(self, asn: int):
        # Close firewall port
        listen_port = self._calculate_listen_port(asn)
        self.firewall.remove_port(listen_port)

        self.wg.down(asn)
        self.wg.remove_interface(asn)
        self.bird.remove_peer(asn)

    async def send_heartbeat(self) -> bool:
        status = {
            **self.bird.get_status(),
            **self.wg.get_status(),
            "ebgp_public_key": self.wg.public_key,  # Include eBGP key in every heartbeat
        }
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
