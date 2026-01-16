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
    
    def _interface_name(self, identifier) -> str:
        """Convert identifier to interface name."""
        if isinstance(identifier, int):
            return f"dn42-{identifier}"
        return str(identifier)
    
    def write_interface(self, identifier, config: str) -> bool:
        """Write WireGuard interface config.
        
        Args:
            identifier: ASN (int) or interface name (str)
            config: WireGuard configuration content
        """
        try:
            self.config_dir.mkdir(parents=True, exist_ok=True)
            iface = self._interface_name(identifier)
            path = self.config_dir / f"{iface}.conf"
            path.write_text(config)
            os.chmod(path, 0o600)
            return True
        except Exception as e:
            logger.error(f"Write WG config failed: {e}")
            return False
    
    def remove_interface(self, identifier) -> bool:
        iface = self._interface_name(identifier)
        path = self.config_dir / f"{iface}.conf"
        if path.exists():
            path.unlink()
        return True
    
    def up(self, identifier) -> bool:
        iface = self._interface_name(identifier)
        result = subprocess.run(["wg-quick", "up", iface], capture_output=True)
        if result.returncode != 0:
            logger.error(f"Failed to bring up {iface}: {result.stderr.decode()}")
        return result.returncode == 0
    
    def down(self, identifier) -> bool:
        iface = self._interface_name(identifier)
        subprocess.run(["wg-quick", "down", iface], capture_output=True)
        return True
    
    def get_status(self) -> dict:
        result = subprocess.run(["wg", "show", "interfaces"], capture_output=True, text=True)
        if result.returncode != 0:
            return {"interfaces": 0}
        interfaces = [i for i in result.stdout.split() if i.startswith("dn42-") or i.startswith("wg-")]
        return {"interfaces": len(interfaces), "names": interfaces}

