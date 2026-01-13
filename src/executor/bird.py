"""MoeNet DN42 Agent - BIRD Executor"""
import logging
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)


class BirdExecutor:
    def __init__(self, config_dir: str = "/etc/bird/peers.d", bird_ctl: str = "/var/run/bird/bird.ctl"):
        self.config_dir = Path(config_dir)
        self.bird_ctl = bird_ctl
    
    def write_peer(self, asn: int, config: str) -> bool:
        try:
            self.config_dir.mkdir(parents=True, exist_ok=True)
            (self.config_dir / f"dn42_{asn}.conf").write_text(config)
            return True
        except Exception as e:
            logger.error(f"Write BIRD config failed: {e}")
            return False
    
    def remove_peer(self, asn: int) -> bool:
        path = self.config_dir / f"dn42_{asn}.conf"
        if path.exists():
            path.unlink()
        return True
    
    def reload(self) -> bool:
        result = subprocess.run(["birdc", "-s", self.bird_ctl, "configure"], capture_output=True)
        return result.returncode == 0
    
    def get_status(self) -> dict:
        result = subprocess.run(["birdc", "-s", self.bird_ctl, "show", "protocols"], capture_output=True, text=True)
        if result.returncode != 0:
            return {"running": False}
        up = sum(1 for line in result.stdout.split("\n") if "dn42_" in line and "Established" in line)
        down = sum(1 for line in result.stdout.split("\n") if "dn42_" in line and "Established" not in line)
        return {"running": True, "protocols_up": up, "protocols_down": down}
