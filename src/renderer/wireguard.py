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
        allowed_ips = [f"{bgp.get('peer_ipv4')}/32"] if bgp.get("peer_ipv4") else []
        if bgp.get("peer_ipv6"):
            allowed_ips.append(f"{bgp['peer_ipv6']}/128")
        allowed_ips.extend(["172.20.0.0/14", "10.0.0.0/8", "fd00::/8"])
        
        return self.env.get_template("wireguard.conf.j2").render(
            interface_name=f"dn42-{peer['asn']}",
            local_private_key=private_key,
            local_address=local_addr,
            listen_port=tunnel.get("listen_port"),
            peer_public_key=tunnel.get("peer_public_key"),
            preshared_key=tunnel.get("preshared_key"),  # PSK support
            peer_endpoint=tunnel.get("endpoint"),
            allowed_ips=allowed_ips,
        )
