"""
MoeNet DN42 Agent - Community Manager

Handles BGP community parsing, modification, and filtering rules.
"""
import logging
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

from .constants import (
    DN42_LATENCY,
    DN42_BANDWIDTH,
    DN42_CRYPTO,
    DN42_REGION,
    DN42_ACTIONS,
    describe_community,
    parse_community,
)

logger = logging.getLogger(__name__)


@dataclass
class RouteCommunities:
    """Parsed communities for a route."""
    prefix: str
    as_path: List[int] = field(default_factory=list)
    communities: List[Tuple[int, int]] = field(default_factory=list)
    large_communities: List[Tuple[int, int, int]] = field(default_factory=list)
    ext_communities: List[str] = field(default_factory=list)
    
    # Parsed community values
    latency_tier: Optional[int] = None
    bandwidth: Optional[str] = None
    crypto: Optional[str] = None
    region: Optional[str] = None
    actions: Set[str] = field(default_factory=set)
    
    def to_dict(self) -> dict:
        """Convert to dictionary for API response."""
        return {
            "prefix": self.prefix,
            "as_path": self.as_path,
            "communities": [f"({c[0]}, {c[1]})" for c in self.communities],
            "large_communities": [f"({c[0]}, {c[1]}, {c[2]})" for c in self.large_communities],
            "parsed": {
                "latency_tier": self.latency_tier,
                "bandwidth": self.bandwidth,
                "crypto": self.crypto,
                "region": self.region,
                "actions": list(self.actions),
            },
            "descriptions": [describe_community(c) for c in self.communities],
        }


@dataclass
class FilterRule:
    """Community-based filter rule."""
    name: str
    match_type: str  # "community", "large_community", "as_path"
    match_value: str  # e.g., "(64511, 1..9)" or "4242420000..4242429999"
    action: str  # "accept", "reject", "modify"
    modify_commands: List[str] = field(default_factory=list)  # For action=modify


class CommunityManager:
    """Manage BGP communities for routes and peers."""
    
    def __init__(self, 
                 bird_ctl: str = "/var/run/bird/bird.ctl",
                 filter_dir: str = "/etc/bird/filters.d"):
        self.bird_ctl = bird_ctl
        self.filter_dir = Path(filter_dir)
        self.filter_dir.mkdir(parents=True, exist_ok=True)
        
        # Per-peer community settings cache
        self.peer_communities: Dict[int, dict] = {}  # ASN -> community settings
        
        # Custom filter rules
        self.filter_rules: List[FilterRule] = []
    
    def _birdc(self, cmd: str) -> Optional[str]:
        """Execute BIRD control command."""
        try:
            result = subprocess.run(
                ["birdc", "-s", self.bird_ctl, cmd],
                capture_output=True,
                text=True,
                timeout=10,
            )
            return result.stdout if result.returncode == 0 else None
        except Exception as e:
            logger.error(f"birdc failed: {e}")
            return None
    
    def get_route_communities(self, prefix: str) -> Optional[RouteCommunities]:
        """Get communities for a specific route/prefix."""
        result = self._birdc(f"show route for {prefix} all")
        if not result:
            return None
        
        return self._parse_route_output(result, prefix)
    
    def get_peer_routes_communities(self, asn: int, limit: int = 10) -> List[RouteCommunities]:
        """Get communities for routes from a specific peer."""
        protocol_name = f"dn42_{asn}"
        result = self._birdc(f"show route protocol {protocol_name} all")
        if not result:
            return []
        
        routes = []
        current_prefix = None
        current_lines = []
        
        for line in result.splitlines():
            # New route starts with a prefix
            if line and not line.startswith(('\t', ' ', 'BIRD')):
                if current_prefix and current_lines:
                    route = self._parse_route_output('\n'.join(current_lines), current_prefix)
                    if route:
                        routes.append(route)
                        if len(routes) >= limit:
                            break
                
                # Extract prefix from line (first word)
                parts = line.split()
                if parts:
                    current_prefix = parts[0]
                    current_lines = [line]
            else:
                current_lines.append(line)
        
        # Don't forget the last route
        if current_prefix and current_lines and len(routes) < limit:
            route = self._parse_route_output('\n'.join(current_lines), current_prefix)
            if route:
                routes.append(route)
        
        return routes
    
    def _parse_route_output(self, output: str, prefix: str) -> Optional[RouteCommunities]:
        """Parse BIRD route output to extract communities."""
        route = RouteCommunities(prefix=prefix)
        
        for line in output.splitlines():
            line = line.strip()
            
            # Parse AS path
            if "BGP.as_path:" in line or "bgp_path:" in line:
                as_path_str = line.split(":", 1)[1].strip()
                route.as_path = [int(asn) for asn in as_path_str.split() if asn.isdigit()]
            
            # Parse standard communities
            if "BGP.community:" in line or "bgp_community:" in line:
                community_str = line.split(":", 1)[1].strip()
                for match in re.finditer(r'\((\d+),\s*(\d+)\)', community_str):
                    com = (int(match.group(1)), int(match.group(2)))
                    route.communities.append(com)
                    self._classify_community(route, com)
            
            # Parse large communities
            if "BGP.large_community:" in line or "bgp_large_community:" in line:
                large_com_str = line.split(":", 1)[1].strip()
                for match in re.finditer(r'\((\d+),\s*(\d+),\s*(\d+)\)', large_com_str):
                    route.large_communities.append(
                        (int(match.group(1)), int(match.group(2)), int(match.group(3)))
                    )
        
        return route
    
    def _classify_community(self, route: RouteCommunities, com: Tuple[int, int]) -> None:
        """Classify a community and update route attributes."""
        # Check latency
        for tier, expected in DN42_LATENCY.items():
            if com == expected:
                route.latency_tier = tier
                return
        
        # Check bandwidth
        for name, expected in DN42_BANDWIDTH.items():
            if com == expected:
                route.bandwidth = name
                return
        
        # Check crypto
        for name, expected in DN42_CRYPTO.items():
            if com == expected:
                route.crypto = name
                return
        
        # Check region
        for name, expected in DN42_REGION.items():
            if com == expected:
                route.region = name
                return
        
        # Check actions
        for name, expected in DN42_ACTIONS.items():
            if com == expected:
                route.actions.add(name)
                return
    
    def set_peer_communities(self, asn: int, settings: dict) -> None:
        """Set community settings for a peer.
        
        Args:
            asn: Peer ASN
            settings: {
                "latency_tier": 3,  # 0-8
                "bandwidth": "1g",  # 100k, 10m, 100m, 1g, 10g
                "crypto": "encrypted",  # none, unsafe, encrypted, latency
                "region": "as-e",  # eu, na-e, na-c, na-w, as-e, as-se, etc.
            }
        """
        self.peer_communities[asn] = settings
        logger.info(f"Set communities for AS{asn}: {settings}")
    
    def get_peer_communities(self, asn: int) -> dict:
        """Get community settings for a peer."""
        return self.peer_communities.get(asn, {})
    
    def add_filter_rule(self, rule: FilterRule) -> None:
        """Add a community filter rule."""
        self.filter_rules.append(rule)
        self._regenerate_filter_config()
    
    def remove_filter_rule(self, name: str) -> bool:
        """Remove a filter rule by name."""
        original_count = len(self.filter_rules)
        self.filter_rules = [r for r in self.filter_rules if r.name != name]
        if len(self.filter_rules) < original_count:
            self._regenerate_filter_config()
            return True
        return False
    
    def list_filter_rules(self) -> List[dict]:
        """List all filter rules."""
        return [
            {
                "name": r.name,
                "match_type": r.match_type,
                "match_value": r.match_value,
                "action": r.action,
                "modify_commands": r.modify_commands,
            }
            for r in self.filter_rules
        ]
    
    def _regenerate_filter_config(self) -> None:
        """Regenerate BIRD filter configuration from rules."""
        config_lines = [
            "# =============================================================================",
            "# Auto-generated community filter rules",
            "# DO NOT EDIT - Managed by MoeNet Agent",
            "# =============================================================================",
            "",
        ]
        
        # Generate filter function for each rule
        for i, rule in enumerate(self.filter_rules):
            func_name = f"community_rule_{i}"
            config_lines.append(f"# Rule: {rule.name}")
            config_lines.append(f"function {func_name}() {{")
            
            if rule.match_type == "community":
                config_lines.append(f"    if ({rule.match_value} ~ bgp_community) then {{")
            elif rule.match_type == "large_community":
                config_lines.append(f"    if ({rule.match_value} ~ bgp_large_community) then {{")
            elif rule.match_type == "as_path":
                config_lines.append(f"    if (bgp_path ~ [{rule.match_value}]) then {{")
            
            if rule.action == "reject":
                config_lines.append(f'        return false;')
            elif rule.action == "accept":
                config_lines.append(f'        return true;')
            elif rule.action == "modify":
                for cmd in rule.modify_commands:
                    config_lines.append(f'        {cmd};')
                config_lines.append(f'        return true;')
            
            config_lines.append("    }")
            config_lines.append("    return true;")
            config_lines.append("}")
            config_lines.append("")
        
        # Write to file
        filter_path = self.filter_dir / "community_rules.conf"
        filter_path.write_text('\n'.join(config_lines))
        logger.info(f"Regenerated community filters at {filter_path}")
    
    def generate_peer_filter(self, asn: int) -> str:
        """Generate BIRD filter snippet for a peer based on community settings.
        
        This generates per-peer import/export filter modifications.
        """
        settings = self.peer_communities.get(asn, {})
        if not settings:
            return ""
        
        lines = [f"# Community settings for AS{asn}"]
        
        # Generate latency community setting
        latency_tier = settings.get("latency_tier")
        if latency_tier is not None and 0 <= latency_tier <= 8:
            com = DN42_LATENCY[latency_tier]
            lines.append(f"define PEER_{asn}_LATENCY = ({com[0]}, {com[1]});")
        
        # Generate bandwidth
        bw = settings.get("bandwidth")
        if bw and bw in DN42_BANDWIDTH:
            com = DN42_BANDWIDTH[bw]
            lines.append(f"define PEER_{asn}_BANDWIDTH = ({com[0]}, {com[1]});")
        
        # Generate region
        region = settings.get("region")
        if region and region in DN42_REGION:
            com = DN42_REGION[region]
            lines.append(f"define PEER_{asn}_REGION = ({com[0]}, {com[1]});")
        
        return '\n'.join(lines)
    
    def get_community_stats(self) -> dict:
        """Get statistics about community usage across all routes."""
        # Get all routes
        result = self._birdc("show route all")
        if not result:
            return {"error": "Failed to get routes"}
        
        stats = {
            "total_routes": 0,
            "latency_distribution": {i: 0 for i in range(9)},
            "bandwidth_distribution": {k: 0 for k in DN42_BANDWIDTH.keys()},
            "crypto_distribution": {k: 0 for k in DN42_CRYPTO.keys()},
            "region_distribution": {k: 0 for k in DN42_REGION.keys()},
        }
        
        current_lines = []
        for line in result.splitlines():
            if line and not line.startswith(('\t', ' ', 'BIRD')):
                if current_lines:
                    route = self._parse_route_output('\n'.join(current_lines), "")
                    if route:
                        stats["total_routes"] += 1
                        if route.latency_tier is not None:
                            stats["latency_distribution"][route.latency_tier] += 1
                        if route.bandwidth:
                            stats["bandwidth_distribution"][route.bandwidth] += 1
                        if route.crypto:
                            stats["crypto_distribution"][route.crypto] += 1
                        if route.region:
                            stats["region_distribution"][route.region] += 1
                current_lines = [line]
            else:
                current_lines.append(line)
        
        # Process last route
        if current_lines:
            route = self._parse_route_output('\n'.join(current_lines), "")
            if route:
                stats["total_routes"] += 1
                if route.latency_tier is not None:
                    stats["latency_distribution"][route.latency_tier] += 1
        
        return stats
