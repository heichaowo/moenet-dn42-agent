"""
MoeNet DN42 Agent - Configuration
"""
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


@dataclass
class AgentConfig:
    """Agent configuration."""
    # Control Plane
    control_plane_url: str
    control_plane_token: str
    node_name: str
    
    # Sync settings
    sync_interval: int = 60
    heartbeat_interval: int = 30
    
    # Paths
    state_path: str = "/var/lib/moenet-agent/last_state.json"
    bird_config_dir: str = "/etc/bird/peers.d"
    bird_ctl: str = "/var/run/bird/bird.ctl"
    wg_config_dir: str = "/etc/wireguard"
    
    # Agent info
    agent_version: str = "2.1.0"
    
    # API Server (for bot commands)
    api_host: str = "0.0.0.0"
    api_port: int = 54321
    api_token: str = ""
    
    # Network info
    dn42_ipv4: str = ""
    dn42_ipv6: str = ""
    dn42_link_local: str = ""
    wg_public_key: str = ""
    is_open: bool = True
    max_peers: int = 0


def load_config(config_path: Optional[str] = None) -> AgentConfig:
    """Load configuration from file or environment."""
    # Try config file first
    if config_path is None:
        config_path = os.environ.get("AGENT_CONFIG", "config.json")
    
    config_file = Path(config_path)
    
    if config_file.exists():
        with open(config_file) as f:
            data = json.load(f)
        
        return AgentConfig(
            # Required
            control_plane_url=data.get("control_plane_url", ""),
            control_plane_token=data.get("control_plane_token", ""),
            node_name=data.get("node_name", ""),
            # Sync settings
            sync_interval=data.get("sync_interval", 60),
            heartbeat_interval=data.get("heartbeat_interval", 30),
            # Paths
            state_path=data.get("state_path", "/var/lib/moenet-agent/last_state.json"),
            bird_config_dir=data.get("bird_config_dir", "/etc/bird/peers.d"),
            bird_ctl=data.get("bird_ctl", "/var/run/bird/bird.ctl"),
            wg_config_dir=data.get("wg_config_dir", "/etc/wireguard"),
            # API Server
            api_host=data.get("api_host", "0.0.0.0"),
            api_port=data.get("api_port", 54321),
            api_token=data.get("api_token", ""),
            # Network info
            dn42_ipv4=data.get("dn42_ipv4", ""),
            dn42_ipv6=data.get("dn42_ipv6", ""),
            dn42_link_local=data.get("dn42_link_local", ""),
            is_open=data.get("is_open", True),
            max_peers=data.get("max_peers", 0),
        )
    
    # Fall back to environment variables
    return AgentConfig(
        control_plane_url=os.environ.get("CONTROL_PLANE_URL", ""),
        control_plane_token=os.environ.get("CONTROL_PLANE_TOKEN", ""),
        node_name=os.environ.get("NODE_NAME", ""),
        sync_interval=int(os.environ.get("SYNC_INTERVAL", "60")),
        heartbeat_interval=int(os.environ.get("HEARTBEAT_INTERVAL", "30")),
        state_path=os.environ.get("STATE_PATH", "/var/lib/moenet-agent/last_state.json"),
        api_host=os.environ.get("API_HOST", "0.0.0.0"),
        api_port=int(os.environ.get("API_PORT", "54321")),
        api_token=os.environ.get("API_TOKEN", ""),
    )
