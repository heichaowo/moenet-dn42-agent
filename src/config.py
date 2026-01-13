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
    wg_private_key: str = ""
    
    # Agent info
    agent_version: str = "2.1.0"


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
            control_plane_url=data.get("control_plane_url", ""),
            control_plane_token=data.get("control_plane_token", ""),
            node_name=data.get("node_name", ""),
            sync_interval=data.get("sync_interval", 60),
            heartbeat_interval=data.get("heartbeat_interval", 30),
            state_path=data.get("state_path", "/var/lib/moenet-agent/last_state.json"),
            bird_config_dir=data.get("bird_config_dir", "/etc/bird/peers.d"),
            bird_ctl=data.get("bird_ctl", "/var/run/bird/bird.ctl"),
            wg_config_dir=data.get("wg_config_dir", "/etc/wireguard"),
            wg_private_key=data.get("wg_private_key", ""),
        )
    
    # Fall back to environment variables
    return AgentConfig(
        control_plane_url=os.environ.get("CONTROL_PLANE_URL", ""),
        control_plane_token=os.environ.get("CONTROL_PLANE_TOKEN", ""),
        node_name=os.environ.get("NODE_NAME", ""),
        sync_interval=int(os.environ.get("SYNC_INTERVAL", "60")),
        heartbeat_interval=int(os.environ.get("HEARTBEAT_INTERVAL", "30")),
        state_path=os.environ.get("STATE_PATH", "/var/lib/moenet-agent/last_state.json"),
    )
