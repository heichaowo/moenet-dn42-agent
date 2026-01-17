#!/usr/bin/env python3
"""
MoeNet DN42 Agent - Main Entry Point

This is a pure agent that:
1. Pulls configuration from control-plane
2. Renders and applies BIRD/WireGuard configs
3. Reports health status back
4. Saves last_state.json for disaster recovery
"""
import asyncio
import json
import logging
import os
import signal
import sys
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent))

from config import load_config
from client.control_plane import ControlPlaneClient
from state.manager import StateManager
from renderer.bird import BirdRenderer
from renderer.wireguard import WireGuardRenderer
from executor.bird import BirdExecutor
from executor.wireguard import WireGuardExecutor
from daemon.sync import SyncDaemon
from daemon.mesh_sync import MeshSync
from executor.loopback import LoopbackExecutor

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("moenet-agent")


def _persist_node_id(config_path: str, node_id: int) -> None:
    """Persist node_id to config file to survive restarts without CP access."""
    try:
        config_file = Path(config_path)
        if config_file.exists():
            with open(config_file) as f:
                data = json.load(f)
            data['node_id'] = node_id
            with open(config_file, 'w') as f:
                json.dump(data, f, indent=2)
            logger.info(f"Persisted node_id={node_id} to {config_path}")
    except Exception as e:
        logger.warning(f"Failed to persist node_id: {e}")


async def main():
    """Main entry point."""
    from aiohttp import web
    from api.server import create_app
    
    # Load configuration (track path for persistence)
    config_path = os.environ.get("AGENT_CONFIG", "/opt/moenet-agent/config.json")
    config = load_config(config_path)
    
    logger.info(f"MoeNet DN42 Agent starting...")
    logger.info(f"Node: {config.node_name}")
    logger.info(f"Control Plane: {config.control_plane_url}")
    logger.info(f"API Server: {config.api_host}:{config.api_port}")
    
    # Initialize components
    client = ControlPlaneClient(
        base_url=config.control_plane_url,
        node_name=config.node_name,
        api_token=config.control_plane_token,
    )
    
    state_manager = StateManager(config.state_path)
    state_manager.set_node_id(config.node_name)
    
    # Create sync daemon (mesh_sync will be set after it's created)
    bird_executor = BirdExecutor(config.bird_config_dir, config.bird_ctl)
    wg_executor = WireGuardExecutor(config.wg_config_dir)
    
    daemon = SyncDaemon(
        client=client,
        state_manager=state_manager,
        bird_executor=bird_executor,
        wg_executor=wg_executor,
        mesh_sync=None,  # Set later after mesh_sync is created
        sync_interval=config.sync_interval,
        heartbeat_interval=config.heartbeat_interval,
    )
    
    # Register node with control plane (auto-create if not exists)
    # This MUST happen before loopback setup to get valid node_id
    logger.info("Registering with control plane...")
    node_id = config.node_id  # Start with config value
    
    try:
        is_rr = "rr" in config.node_name.lower()
        registration = await client.register_node(
            agent_version=config.agent_version,
            region=getattr(config, 'region', 'unknown'),
            location=getattr(config, 'location', ''),
            provider=getattr(config, 'provider', ''),
            ipv4=getattr(config, 'public_ipv4', '') or None,
            ipv6=getattr(config, 'public_ipv6', '') or None,
            dn42_ipv4=getattr(config, 'dn42_ipv4', None),
            dn42_ipv6=getattr(config, 'dn42_ipv6', None),
            is_rr=is_rr,
            allow_cn_peers=getattr(config, 'allow_cn_peers', False),
            supports_ipv4=getattr(config, 'supports_ipv4', True),
            supports_ipv6=getattr(config, 'supports_ipv6', True),
            open_for_peer=getattr(config, 'is_open', True),
            max_peers=getattr(config, 'max_peers', 0),
            ebgp_public_key=wg_executor.public_key,  # Report eBGP WG public key
        )
        if registration:
            logger.info(f"✅ Node registered: {registration.get('status')}")
            # Use node_id from Control Plane (authoritative source)
            cp_node_id = registration.get('numeric_node_id')
            if cp_node_id and 1 <= cp_node_id <= 62:
                node_id = cp_node_id
                logger.info(f"Using node_id={node_id} from Control Plane")
                # Persist to config file to avoid needing CP on next restart
                _persist_node_id(config_path, node_id)
    except Exception as e:
        logger.warning(f"Node registration failed (will retry): {e}")
    
    # Validate node_id before proceeding - FAIL EARLY, no dangerous fallback!
    if not node_id or node_id < 1 or node_id > 62:
        logger.error(f"Invalid node_id={node_id}: must be 1-62 for /26 subnet")
        logger.error("Cannot proceed without valid node_id - this would cause IP conflicts!")
        logger.error("Please ensure Control Plane is reachable and node is registered.")
        logger.error("Or manually set 'node_id' in config.json to a unique value (1-62).")
        sys.exit(1)  # Fail early instead of causing duplicate IP conflicts
    
    # Create mesh sync for IGP underlay (P2P mode)
    mesh_sync = MeshSync(
        client=client,
        wg_executor=wg_executor,
        bird_executor=bird_executor,
        node_id=node_id,
    )
    
    # Configure loopback interface with DN42 IPs
    logger.info("Configuring loopback interface...")
    loopback = LoopbackExecutor(
        dn42_ipv4_prefix=getattr(config, 'dn42_ipv4_prefix', '172.22.188.0/26'),
        dn42_ipv6_prefix=getattr(config, 'dn42_ipv6_prefix', 'fd00:4242:7777::/48'),
    )
    loopback.ensure_interface_up()
    loopback.setup_loopback(node_id)
    logger.info("✅ Loopback interface configured")
    
    # Initialize mesh network (generate keys, register with CP)
    logger.info("Initializing mesh network...")
    try:
        await mesh_sync.sync_mesh()
        logger.info("✅ Mesh network initialized")
    except Exception as e:
        logger.warning(f"Mesh sync failed (will retry): {e}")
    
    # Set mesh_sync on daemon for periodic sync (retry failed tunnels)
    daemon.mesh_sync = mesh_sync
    
    # Create API server
    api_app = create_app()
    api_runner = web.AppRunner(api_app)
    await api_runner.setup()
    api_site = web.TCPSite(api_runner, config.api_host, config.api_port)
    await api_site.start()
    logger.info(f"✅ API server started on port {config.api_port}")
    
    # Handle shutdown signals
    loop = asyncio.get_event_loop()
    
    def shutdown_handler():
        logger.info("Shutdown signal received")
        asyncio.create_task(daemon.stop())
    
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, shutdown_handler)
    
    # Run daemon
    try:
        await daemon.run()
    except Exception as e:
        logger.error(f"Daemon error: {e}")
        raise
    finally:
        await api_runner.cleanup()
        await client.close()
    
    logger.info("MoeNet DN42 Agent stopped")


if __name__ == "__main__":
    asyncio.run(main())
