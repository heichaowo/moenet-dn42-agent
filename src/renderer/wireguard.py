"""MoeNet DN42 Agent - WireGuard Renderer"""
from pathlib import Path
from jinja2 import Environment, FileSystemLoader

TEMPLATE_DIR = Path(__file__).parent.parent.parent / "templates"


class WireGuardRenderer:
    def __init__(self, template_dir: Path = TEMPLATE_DIR):
        self.env = Environment(loader=FileSystemLoader(str(template_dir)), trim_blocks=True, lstrip_blocks=True)
    
    def render_interface(self, peer: dict, private_key: str, local_addr: str) -> str:
        tunnel = peer.get("tunnel", {})
        bgp = peer.get("bgp", {})
        
        # AllowedIPs: Full tunnel mode - all IPv4, IPv6 ULA, and link-local
        allowed_ips = ["0.0.0.0/0", "fd00::/8", "fe80::/64"]
        
        # Local addresses for WireGuard interface (our IPs)
        wg_local_addresses = []
        if bgp.get("local_ipv6"):
            wg_local_addresses.append(bgp["local_ipv6"])
        elif local_addr:
            wg_local_addresses.append(local_addr)
        if bgp.get("local_ipv4"):
            wg_local_addresses.append(bgp["local_ipv4"])
        
        wg_local_addr = ", ".join(wg_local_addresses) if wg_local_addresses else None
        
        return self.env.get_template("wireguard.conf.j2").render(
            interface_name=f"dn42-{peer['asn']}",
            local_private_key=private_key,
            local_address=wg_local_addr,
            listen_port=tunnel.get("listen_port"),
            peer_public_key=tunnel.get("public_key"),
            preshared_key=tunnel.get("preshared_key"),
            peer_endpoint=tunnel.get("endpoint"),
            allowed_ips=allowed_ips,
        )

