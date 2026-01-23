"""
MoeNet DN42 Agent - API Server

HTTP API for bot commands (ping, trace, route, peer management)
"""
import asyncio
import logging
import os
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

# Blacklist file path - generates BIRD function syntax
BLACKLIST_FILE = "/etc/bird/blacklist.conf"


def load_blacklist() -> set:
    """Load blacklist ASNs from file.
    
    Parses the BIRD function syntax to extract ASN numbers.
    """
    if not os.path.exists(BLACKLIST_FILE):
        return set()
    
    asns = set()
    try:
        with open(BLACKLIST_FILE) as f:
            content = f.read()
            # Extract ASNs from: bgp_path ~ [= * [ASN1, ASN2, ...] * =]
            import re
            match = re.search(r'\[(\d+(?:,\s*\d+)*)\]', content)
            if match:
                asn_str = match.group(1)
                for asn in asn_str.split(','):
                    asn = asn.strip()
                    if asn.isdigit():
                        asns.add(int(asn))
    except Exception as e:
        logger.error(f"Failed to load blacklist: {e}")
    
    return asns


def save_blacklist(asns: set) -> bool:
    """Save blacklist as BIRD function syntax and reload config.
    
    Generates a BIRD function that checks if the AS-path contains
    any of the blacklisted ASNs.
    """
    try:
        # Ensure directory exists
        os.makedirs(os.path.dirname(BLACKLIST_FILE), exist_ok=True)
        
        with open(BLACKLIST_FILE, 'w') as f:
            f.write("# Blacklist - Managed by moenet-agent\n")
            f.write("# DO NOT EDIT MANUALLY - changes will be overwritten\n")
            f.write("#\n")
            f.write("# This file is included by bird.conf and provides is_blacklisted()\n")
            f.write("# to check if a route passes through any blacklisted ASN.\n\n")
            
            f.write("function is_blacklisted() -> bool {\n")
            if asns:
                asn_list = ", ".join(str(a) for a in sorted(asns))
                # Match any route that passes through these ASNs
                f.write(f"    return bgp_path ~ [= * [{asn_list}] * =];\n")
            else:
                f.write("    return false;  # No ASNs in blacklist\n")
            f.write("}\n")
        
        # Reload BIRD config
        result = birdc("configure")
        if result and "Reconfigured" in result:
            logger.info(f"Blacklist saved with {len(asns)} ASNs, BIRD reconfigured")
            return True
        else:
            logger.warning(f"Blacklist saved but BIRD reconfigure may have failed: {result}")
            return True  # File was saved, even if BIRD reload had issues
            
    except Exception as e:
        logger.error(f"Failed to save blacklist: {e}")
        return False


@routes.get("/blacklist")
async def get_blacklist(request):
    """Get current blacklist.
    
    Returns list of blocked ASNs.
    """
    blocked = sorted(load_blacklist())
    return web.json_response({
        "blocked": blocked,
        "count": len(blocked),
    })


@routes.post("/blacklist/add")
async def add_to_blacklist(request):
    """Add ASN to blacklist.
    
    Body: {"asn": 4242421234}
    """
    data = await request.json()
    asn = data.get("asn")
    
    if not asn:
        return web.json_response({"error": "Missing ASN"}, status=400)
    
    try:
        asn = int(asn)
    except (ValueError, TypeError):
        return web.json_response({"error": "Invalid ASN format"}, status=400)
    
    blacklist = load_blacklist()
    if asn in blacklist:
        return web.json_response({
            "result": "already_blocked",
            "asn": asn,
            "total": len(blacklist),
        })
    
    blacklist.add(asn)
    if save_blacklist(blacklist):
        logger.info(f"Added AS{asn} to blacklist")
        return web.json_response({
            "result": "added",
            "asn": asn,
            "total": len(blacklist),
        })
    else:
        return web.json_response({"error": "Failed to save blacklist"}, status=500)


@routes.post("/blacklist/remove")
async def remove_from_blacklist(request):
    """Remove ASN from blacklist.
    
    Body: {"asn": 4242421234}
    """
    data = await request.json()
    asn = data.get("asn")
    
    if not asn:
        return web.json_response({"error": "Missing ASN"}, status=400)
    
    try:
        asn = int(asn)
    except (ValueError, TypeError):
        return web.json_response({"error": "Invalid ASN format"}, status=400)
    
    blacklist = load_blacklist()
    if asn not in blacklist:
        return web.json_response({
            "result": "not_found",
            "asn": asn,
            "total": len(blacklist),
        })
    
    blacklist.discard(asn)
    if save_blacklist(blacklist):
        logger.info(f"Removed AS{asn} from blacklist")
        return web.json_response({
            "result": "removed",
            "asn": asn,
            "total": len(blacklist),
        })
    else:
        return web.json_response({"error": "Failed to save blacklist"}, status=500)


# ==== Community Management Endpoints ====

# Lazy initialization of community components
_community_manager = None
_latency_probe = None


def get_community_manager():
    """Get or create the community manager singleton."""
    global _community_manager
    if _community_manager is None:
        from community.manager import CommunityManager
        _community_manager = CommunityManager(bird_ctl=config.bird_ctl)
    return _community_manager


def get_latency_probe():
    """Get or create the latency probe singleton."""
    global _latency_probe
    if _latency_probe is None:
        from community.latency_probe import LatencyProbe
        _latency_probe = LatencyProbe()
        
        # Set callback to update community manager
        def update_community(asn: int, tier: int, rtt_ms: float):
            manager = get_community_manager()
            settings = manager.get_peer_communities(asn)
            settings["latency_tier"] = tier
            settings["last_rtt"] = rtt_ms
            manager.set_peer_communities(asn, settings)
        
        _latency_probe.set_update_callback(update_community)
    return _latency_probe


@routes.get("/communities")
async def get_community_stats(request):
    """Get community usage statistics across all routes."""
    manager = get_community_manager()
    stats = manager.get_community_stats()
    return web.json_response(stats)


@routes.post("/communities/route")
async def get_route_communities(request):
    """Query communities for a specific route/prefix."""
    data = await request.json()
    prefix = data.get("prefix", "")
    
    if not prefix:
        return web.json_response({"error": "Missing prefix"}, status=400)
    
    manager = get_community_manager()
    route = manager.get_route_communities(prefix)
    
    if not route:
        return web.json_response({"error": "Route not found"}, status=404)
    
    return web.json_response(route.to_dict())


@routes.get("/communities/peer/{asn}")
async def get_peer_communities(request):
    """Get community settings for a peer."""
    asn = int(request.match_info["asn"])
    
    manager = get_community_manager()
    settings = manager.get_peer_communities(asn)
    
    # Also get routes from this peer
    routes = manager.get_peer_routes_communities(asn, limit=5)
    
    return web.json_response({
        "asn": asn,
        "settings": settings,
        "sample_routes": [r.to_dict() for r in routes],
    })


@routes.post("/communities/peer/{asn}")
async def set_peer_communities(request):
    """Set community settings for a peer.
    
    Body: {
        "latency_tier": 3,  // 0-8
        "bandwidth": "1g",  // 100k, 10m, 100m, 1g, 10g
        "crypto": "encrypted",  // none, unsafe, encrypted, latency
        "region": "as-e"  // eu, na-e, na-c, na-w, as-e, as-se, etc.
    }
    """
    asn = int(request.match_info["asn"])
    data = await request.json()
    
    manager = get_community_manager()
    manager.set_peer_communities(asn, data)
    
    # Generate filter config
    filter_snippet = manager.generate_peer_filter(asn)
    
    return web.json_response({
        "result": "ok",
        "asn": asn,
        "settings": data,
        "filter_snippet": filter_snippet,
    })


@routes.get("/communities/filters")
async def list_filter_rules(request):
    """List all community filter rules."""
    manager = get_community_manager()
    rules = manager.list_filter_rules()
    return web.json_response({"rules": rules})


@routes.post("/communities/filters")
async def add_filter_rule(request):
    """Add a community filter rule.
    
    Body: {
        "name": "block_high_latency",
        "match_type": "community",  // community, large_community, as_path
        "match_value": "(64511, 8..9)",
        "action": "reject"  // accept, reject, modify
        "modify_commands": []  // For action=modify
    }
    """
    data = await request.json()
    
    from community.manager import FilterRule
    
    rule = FilterRule(
        name=data.get("name", "unnamed"),
        match_type=data.get("match_type", "community"),
        match_value=data.get("match_value", ""),
        action=data.get("action", "reject"),
        modify_commands=data.get("modify_commands", []),
    )
    
    manager = get_community_manager()
    manager.add_filter_rule(rule)
    
    return web.json_response({"result": "added", "rule": data})


@routes.delete("/communities/filters/{name}")
async def delete_filter_rule(request):
    """Delete a filter rule by name."""
    name = request.match_info["name"]
    
    manager = get_community_manager()
    if manager.remove_filter_rule(name):
        return web.json_response({"result": "removed", "name": name})
    else:
        return web.json_response({"error": "Rule not found"}, status=404)


# ==== Latency Probe Endpoints ====

@routes.get("/communities/probe")
async def get_probe_stats(request):
    """Get latency probe statistics."""
    probe = get_latency_probe()
    return web.json_response(probe.get_all_stats())


@routes.post("/communities/probe/add")
async def add_probe_peer(request):
    """Add a peer to latency probing.
    
    Body: {
        "asn": 4242420337,
        "endpoint": "10.0.0.1"  // Tunnel endpoint IP
    }
    """
    data = await request.json()
    asn = data.get("asn")
    endpoint = data.get("endpoint")
    
    if not asn or not endpoint:
        return web.json_response({"error": "Missing asn or endpoint"}, status=400)
    
    probe = get_latency_probe()
    probe.add_peer(asn, endpoint)
    
    return web.json_response({"result": "added", "asn": asn, "endpoint": endpoint})


@routes.post("/communities/probe/remove")
async def remove_probe_peer(request):
    """Remove a peer from latency probing."""
    data = await request.json()
    asn = data.get("asn")
    
    if not asn:
        return web.json_response({"error": "Missing asn"}, status=400)
    
    probe = get_latency_probe()
    probe.remove_peer(asn)
    
    return web.json_response({"result": "removed", "asn": asn})


@routes.post("/communities/probe/now/{asn}")
async def probe_peer_now(request):
    """Immediately probe a specific peer."""
    asn = int(request.match_info["asn"])
    
    probe = get_latency_probe()
    result = probe.probe_now(asn)
    
    if result:
        return web.json_response(result.to_dict())
    else:
        return web.json_response({"error": "Peer not found in probe list"}, status=404)


@routes.get("/communities/probe/peer/{asn}")
async def get_probe_peer_stats(request):
    """Get latency statistics for a specific peer."""
    asn = int(request.match_info["asn"])
    
    probe = get_latency_probe()
    stats = probe.get_peer_stats(asn)
    
    if stats:
        return web.json_response(stats)
    else:
        return web.json_response({"error": "Peer not found"}, status=404)


@routes.post("/communities/probe/start")
async def start_latency_probe(request):
    """Start the latency probe daemon."""
    probe = get_latency_probe()
    await probe.start()
    return web.json_response({"result": "started"})


@routes.post("/communities/probe/stop")
async def stop_latency_probe(request):
    """Stop the latency probe daemon."""
    probe = get_latency_probe()
    await probe.stop()
    return web.json_response({"result": "stopped"})


# ==== Maintenance Mode Endpoints (RFC 8326 Graceful Shutdown) ====

# Track maintenance mode state
_maintenance_mode = False


@routes.get("/maintenance")
async def get_maintenance_status(request):
    """Get current maintenance mode status."""
    return web.json_response({
        "maintenance_mode": _maintenance_mode,
        "node": config.node_name,
    })


@routes.post("/maintenance/start")
async def start_maintenance(request):
    """Start maintenance mode - graceful shutdown.
    
    Process:
    1. Write 'define MAINTENANCE_MODE = true;' to /etc/bird/maintenance.conf
    2. Reload BIRD configuration
    3. Traffic will drain via (65535, 0) community
    """
    global _maintenance_mode
    
    if _maintenance_mode:
        return web.json_response({
            "result": "already_in_maintenance",
            "node": config.node_name,
        })
    
    # 1. Write maintenance flag
    try:
        os.makedirs("/etc/bird", exist_ok=True)
        with open("/etc/bird/maintenance.conf", "w") as f:
            f.write("define MAINTENANCE_MODE = true;\n")
    except Exception as e:
        logger.error(f"Failed to create maintenance.conf: {e}")
        return web.json_response({"error": f"Failed to write flag: {e}"}, status=500)
    
    # 2. Reload BIRD config
    result = birdc("configure")
    if not result or "Reconfigured" not in result:
        logger.warning(f"BIRD reconfigure failed or delayed: {result}")
    
    _maintenance_mode = True
    logger.info(f"Maintenance mode STARTED - community (65535, 0) attached to all exports")
    
    return web.json_response({
        "result": "maintenance_started",
        "node": config.node_name,
        "bird_status": result or "no output",
    })


@routes.post("/maintenance/stop")
async def stop_maintenance(request):
    """Stop maintenance mode - bring node back online.
    """
    global _maintenance_mode
    
    if not _maintenance_mode:
        return web.json_response({
            "result": "not_in_maintenance",
            "node": config.node_name,
        })
    
    # 1. Update maintenance flag to false
    try:
        with open("/etc/bird/maintenance.conf", "w") as f:
            f.write("define MAINTENANCE_MODE = false;\n")
    except Exception as e:
        logger.error(f"Failed to reset maintenance.conf: {e}")
        return web.json_response({"error": f"Failed to reset flag: {e}"}, status=500)
    
    # 2. Reload BIRD config
    result = birdc("configure")
    
    _maintenance_mode = False
    logger.info(f"Maintenance mode STOPPED - node normalized")
    
    return web.json_response({
        "result": "maintenance_stopped",
        "node": config.node_name,
        "bird_status": result or "no output",
    })


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
