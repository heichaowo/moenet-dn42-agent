"""MoeNet DN42 Agent - WireGuard Executor"""
import logging
import os
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)


class WireGuardExecutor:
    def __init__(self, config_dir: str = "/etc/wireguard", private_key: str = ""):
        self.config_dir = Path(config_dir)
        self.private_key = private_key
    
    def write_interface(self, asn: int, config: str) -> bool:
        try:
            self.config_dir.mkdir(parents=True, exist_ok=True)
            path = self.config_dir / f"dn42-{asn}.conf"
            path.write_text(config)
            os.chmod(path, 0o600)
            return True
        except Exception as e:
            logger.error(f"Write WG config failed: {e}")
            return False
    
    def remove_interface(self, asn: int) -> bool:
        path = self.config_dir / f"dn42-{asn}.conf"
        if path.exists():
            path.unlink()
        return True
    
    def up(self, asn: int) -> bool:
        result = subprocess.run(["wg-quick", "up", f"dn42-{asn}"], capture_output=True)
        return result.returncode == 0
    
    def down(self, asn: int) -> bool:
        subprocess.run(["wg-quick", "down", f"dn42-{asn}"], capture_output=True)
        return True
    
    def get_status(self) -> dict:
        result = subprocess.run(["wg", "show", "interfaces"], capture_output=True, text=True)
        if result.returncode != 0:
            return {"interfaces": 0}
        interfaces = [i for i in result.stdout.split() if i.startswith("dn42-")]
        return {"interfaces": len(interfaces), "names": interfaces}
