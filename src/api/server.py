"""
MoeNet DN42 Agent - API Server

HTTP API for bot commands (ping, trace, route, peer management)
"""
import asyncio
import logging
import shlex
import subprocess
from typing import Optional

from aiohttp import web

from config import load_config

logger = logging.getLogger(__name__)
config = load_config()


def simple_run(cmd: str, timeout: int = 10) -> Optional[str]:
    """Run a command and return output."""
    try:
        result = subprocess.run(
            shlex.split(cmd),
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        return result.stdout if result.returncode == 0 else result.stderr
    except subprocess.TimeoutExpired:
        return None
    except Exception as e:
        logger.error(f"Command failed: {e}")
        return None


def birdc(cmd: str) -> Optional[str]:
    """Execute BIRD control command."""
    return simple_run(f"birdc -s {config.bird_ctl} {cmd}")


# Auth middleware
@web.middleware
async def auth_middleware(request, handler):
    """Verify API secret token."""
    if config.api_token:
        auth = request.headers.get("Authorization", "")
        if auth != f"Bearer {config.api_token}":
            return web.json_response({"error": "Unauthorized"}, status=401)
    return await handler(request)


routes = web.RouteTableDef()


@routes.get("/")
async def index(request):
    """Health check and node info."""
    return web.json_response({
        "status": "ok",
        "version": config.agent_version,
        "node": config.node_name,
        "is_open": config.is_open,
    })


@routes.post("/ping")
async def cmd_ping(request):
    """Execute ping command."""
    data = await request.json()
    target = data.get("target", "")
    count = min(data.get("count", 4), 10)
    
    if not target:
        return web.json_response({"error": "Missing target"}, status=400)
    
    result = simple_run(f"ping -c {count} -W 2 {target}", timeout=15)
    return web.json_response({"result": result or "Timeout"})


@routes.post("/tcping")
async def cmd_tcping(request):
    """Execute tcping command."""
    data = await request.json()
    target = data.get("target", "")
    port = data.get("port", 80)
    
    if not target:
        return web.json_response({"error": "Missing target"}, status=400)
    
    # Try different tcping implementations
    result = simple_run(f"tcping {target} {port}", timeout=12)
    if result is None:
        # Fallback to nc
        result = simple_run(f"nc -zv -w5 {target} {port}", timeout=10)
    
    return web.json_response({"result": result or "Timeout"})


@routes.post("/trace")
async def cmd_traceroute(request):
    """Execute traceroute command."""
    data = await request.json()
    target = data.get("target", "")
    
    if not target:
        return web.json_response({"error": "Missing target"}, status=400)
    
    result = simple_run(f"traceroute -w 2 -q 1 {target}", timeout=30)
    return web.json_response({"result": result or "Timeout"})


@routes.post("/route")
async def cmd_route(request):
    """Query BIRD routing table."""
    data = await request.json()
    target = data.get("target", "")
    
    if not target:
        return web.json_response({"error": "Missing target"}, status=400)
    
    # Determine IPv4 or IPv6
    if ":" in target:
        result = birdc(f"show route for {target} all")
    else:
        result = birdc(f"show route for {target} all")
    
    return web.json_response({"result": result or "Not found"})


@routes.post("/path")
async def cmd_path(request):
    """Query AS-Path for prefix."""
    data = await request.json()
    target = data.get("target", "")
    
    if not target:
        return web.json_response({"error": "Missing target"}, status=400)
    
    result = birdc(f"show route for {target} all")
    
    # Extract AS path from result
    if result:
        for line in result.splitlines():
            if "BGP.as_path" in line:
                return web.json_response({"result": line.strip()})
    
    return web.json_response({"result": "Not found"}, status=404)


@routes.get("/info")
async def node_info(request):
    """Get detailed node info."""
    return web.json_response({
        "version": config.agent_version,
        "node": config.node_name,
        "is_open": config.is_open,
        "max_peers": config.max_peers,
        "dn42_ipv4": config.dn42_ipv4,
        "dn42_ipv6": config.dn42_ipv6,
        "wg_public_key": config.wg_public_key,
    })


# ==== Peer Management Endpoints ====

@routes.get("/peers")
async def list_peers(request):
    """List all configured peers."""
    result = birdc("show protocols")
    peers = []
    if result:
        for line in result.splitlines():
            if "BGP" in line:
                parts = line.split()
                if len(parts) >= 3:
                    peers.append({
                        "name": parts[0],
                        "proto": parts[1],
                        "state": parts[3] if len(parts) > 3 else "unknown",
                    })
    return web.json_response({"peers": peers})


@routes.post("/peers/restart")
async def restart_peer(request):
    """Restart a peer tunnel.
    
    Order: BGP ↓ → WG ↓ → WG ↑ → BGP ↑
    
    peer_name should be like 'dn42_4242420337' (BIRD protocol name)
    WG interface is 'dn42-4242420337' (hyphen instead of underscore)
    """
    data = await request.json()
    peer_name = data.get("peer_name", "")
    
    if not peer_name:
        return web.json_response({"error": "Missing peer_name"}, status=400)
    
    # Convert BIRD protocol name to WG interface name
    # dn42_4242420337 -> dn42-4242420337
    wg_interface = peer_name.replace("_", "-")
    
    results = []
    
    # 1. Stop BGP first
    bgp_down = birdc(f"disable {peer_name}")
    results.append(f"BGP disable: {bgp_down or 'ok'}")
    
    # 2. Stop WireGuard (use ip link, wg-quick not always available)
    wg_down = simple_run(f"ip link set {wg_interface} down", timeout=15)
    results.append(f"WG down: {wg_down or 'ok'}")
    
    # 3. Start WireGuard
    wg_up = simple_run(f"ip link set {wg_interface} up", timeout=15)
    results.append(f"WG up: {wg_up or 'ok'}")
    
    # 4. Start BGP
    bgp_up = birdc(f"enable {peer_name}")
    results.append(f"BGP enable: {bgp_up or 'ok'}")
    
    return web.json_response({"result": "restarted", "steps": results})


# ==== Statistics Endpoints ====

@routes.get("/stats")
async def get_stats(request):
    """Get node statistics."""
    # Get BIRD stats
    bird_result = birdc("show protocols")
    
    # Count peers
    peer_count = 0
    established = 0
    if bird_result:
        for line in bird_result.splitlines():
            if "BGP" in line:
                peer_count += 1
                if "Established" in line:
                    established += 1
    
    # Get WireGuard stats
    wg_result = simple_run("wg show all transfer", timeout=10)
    
    return web.json_response({
        "node": config.node_name,
        "peer_count": peer_count,
        "established": established,
        "wg_stats": wg_result or "",
    })


@routes.get("/stats/peer/{peer_name}")
async def get_peer_stats(request):
    """Get stats for specific peer."""
    peer_name = request.match_info["peer_name"]
    
    bird_result = birdc(f"show protocols all {peer_name}")
    
    # Convert BIRD protocol name to WG interface name
    # dn42_4242423374 -> dn42-4242423374
    wg_interface = peer_name.replace("_", "-")
    wg_result = simple_run(f"wg show {wg_interface}", timeout=10)
    
    return web.json_response({
        "peer_name": peer_name,
        "bird": bird_result or "Not found",
        "wireguard": wg_result or "Not found",
    })


# ==== Blacklist Endpoints ====

@routes.get("/blacklist")
async def get_blacklist(request):
    """Get blacklist from BIRD filter."""
    # This would read from a blacklist file or BIRD filter
    return web.json_response({"blocked": []})


@routes.post("/blacklist/add")
async def add_to_blacklist(request):
    """Add ASN to blacklist."""
    data = await request.json()
    asn = data.get("asn")
    
    if not asn:
        return web.json_response({"error": "Missing ASN"}, status=400)
    
    # TODO: Update BIRD filter and reload
    return web.json_response({"result": "added", "asn": asn})


@routes.post("/blacklist/remove")
async def remove_from_blacklist(request):
    """Remove ASN from blacklist."""
    data = await request.json()
    asn = data.get("asn")
    
    if not asn:
        return web.json_response({"error": "Missing ASN"}, status=400)
    
    # TODO: Update BIRD filter and reload
    return web.json_response({"result": "removed", "asn": asn})


def create_app() -> web.Application:
    """Create aiohttp application."""
    app = web.Application(middlewares=[auth_middleware])
    app.add_routes(routes)
    return app


async def run_api_server():
    """Run API server."""
    app = create_app()
    runner = web.AppRunner(app)
    await runner.setup()
    
    site = web.TCPSite(runner, config.api_host, config.api_port)
    await site.start()
    
    logger.info(f"API server running on {config.api_host}:{config.api_port}")
    
    # Keep running
    while True:
        await asyncio.sleep(3600)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(run_api_server())
