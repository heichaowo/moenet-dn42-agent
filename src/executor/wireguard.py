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
        """Bring up WireGuard interface using direct wg commands.
        
        Uses wg command directly instead of wg-quick to avoid route conflicts
        (wg-quick adds routes for AllowedIPs which can conflict with dummy0 loopback).
        """
        iface = self._interface_name(identifier)
        config_path = self.config_dir / f"{iface}.conf"
        
        if not config_path.exists():
            logger.error(f"Config file not found: {config_path}")
            return False
        
        try:
            # Check if interface already exists
            check = subprocess.run(["ip", "link", "show", iface], capture_output=True)
            if check.returncode == 0:
                # Interface exists, just update config
                result = subprocess.run(
                    ["wg", "setconf", iface, str(config_path)],
                    capture_output=True, text=True
                )
                if result.returncode != 0:
                    logger.error(f"Failed to setconf {iface}: {result.stderr}")
                    return False
                logger.debug(f"Updated existing interface {iface}")
                return True
            
            # Create new interface
            subprocess.run(["ip", "link", "add", iface, "type", "wireguard"], check=True)
            subprocess.run(["wg", "setconf", iface, str(config_path)], check=True)
            subprocess.run(["ip", "link", "set", "mtu", "1420", "up", "dev", iface], check=True)
            
            logger.info(f"Brought up {iface}")
            return True
            
        except subprocess.CalledProcessError as e:
            logger.error(f"Failed to bring up {iface}: {e}")
            return False
        except Exception as e:
            logger.error(f"Error bringing up {iface}: {e}")
            return False
    
    def down(self, identifier) -> bool:
        """Bring down WireGuard interface using direct commands."""
        iface = self._interface_name(identifier)
        try:
            # Check if interface exists
            check = subprocess.run(["ip", "link", "show", iface], capture_output=True)
            if check.returncode == 0:
                subprocess.run(["ip", "link", "del", iface], capture_output=True)
                logger.info(f"Removed interface {iface}")
        except Exception as e:
            logger.debug(f"Interface {iface} may not exist: {e}")
        return True
    
    def get_status(self) -> dict:
        result = subprocess.run(["wg", "show", "interfaces"], capture_output=True, text=True)
        if result.returncode != 0:
            return {"interfaces": 0}
        interfaces = [i for i in result.stdout.split() if i.startswith("dn42-") or i.startswith("wg-")]
        return {"interfaces": len(interfaces), "names": interfaces}

