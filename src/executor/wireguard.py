"""MoeNet DN42 Agent - WireGuard Executor"""
import logging
import os
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)


class WireGuardExecutor:
    def __init__(self, config_dir: str = "/etc/wireguard", private_key_path: str = None):
        self.config_dir = Path(config_dir)
        self.config_dir.mkdir(parents=True, exist_ok=True)
        
        # Load or generate private key
        if private_key_path:
            self._key_path = Path(private_key_path)
        else:
            self._key_path = self.config_dir / "private.key"
        
        self.private_key, self.public_key = self._load_or_create_key()
    
    def _load_or_create_key(self) -> tuple[str, str]:
        """Load existing private key or generate a new one.
        
        Returns:
            Tuple of (private_key, public_key)
        """
        if self._key_path.exists():
            private_key = self._key_path.read_text().strip()
            logger.info(f"Loaded WG private key from {self._key_path}")
        else:
            # Generate new key
            result = subprocess.run(
                ["wg", "genkey"],
                capture_output=True,
                text=True,
                check=True
            )
            private_key = result.stdout.strip()
            
            # Save to file
            self._key_path.write_text(private_key)
            os.chmod(self._key_path, 0o600)
            logger.info(f"Generated new WG private key at {self._key_path}")
        
        # Derive public key
        result = subprocess.run(
            ["wg", "pubkey"],
            input=private_key,
            capture_output=True,
            text=True,
            check=True
        )
        public_key = result.stdout.strip()
        
        return private_key, public_key
    
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
        
        Note: wg setconf doesn't accept PrivateKey, so we need to:
        1. Extract PrivateKey and ListenPort from config
        2. Use wg set to configure interface settings
        3. Use wg setconf with peer-only config (or wg set peer)
        """
        import tempfile
        import re
        
        iface = self._interface_name(identifier)
        config_path = self.config_dir / f"{iface}.conf"
        
        if not config_path.exists():
            logger.error(f"Config file not found: {config_path}")
            return False
        
        try:
            # Parse config file
            config_content = config_path.read_text()
            
            # Extract PrivateKey
            private_key_match = re.search(r'PrivateKey\s*=\s*(\S+)', config_content)
            private_key = private_key_match.group(1) if private_key_match else None
            
            # Extract ListenPort
            listen_port_match = re.search(r'ListenPort\s*=\s*(\d+)', config_content)
            listen_port = listen_port_match.group(1) if listen_port_match else None
            
            # Extract peer section (everything from [Peer] onwards)
            peer_match = re.search(r'(\[Peer\].*)', config_content, re.DOTALL)
            peer_config = peer_match.group(1) if peer_match else None
            
            # Extract Address (for link-local fe80:: addresses)
            address_match = re.search(r'Address\s*=\s*(\S+)', config_content)
            address = address_match.group(1) if address_match else None
            
            # Check if interface already exists
            check = subprocess.run(["ip", "link", "show", iface], capture_output=True)
            interface_exists = (check.returncode == 0)
            
            if not interface_exists:
                # Create new interface
                subprocess.run(["ip", "link", "add", iface, "type", "wireguard"], check=True)
            
            # Set peer config via temp file (peers-only config for setconf)
            # NOTE: setconf MUST come FIRST, as it resets all interface settings!
            if peer_config:
                with tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.conf') as f:
                    f.write(peer_config)
                    peer_conf_file = f.name
                try:
                    subprocess.run(["wg", "setconf", iface, peer_conf_file], check=True)
                finally:
                    import os
                    os.unlink(peer_conf_file)
            
            # Set private key AFTER setconf (setconf resets it!)
            if private_key:
                with tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.key') as f:
                    f.write(private_key)
                    key_file = f.name
                try:
                    subprocess.run(["wg", "set", iface, "private-key", key_file], check=True)
                finally:
                    import os
                    os.unlink(key_file)
            
            # Set listen port AFTER setconf (setconf resets it!)
            if listen_port:
                subprocess.run(["wg", "set", iface, "listen-port", listen_port], check=True)
            
            # Bring interface up if not already
            if not interface_exists:
                subprocess.run(["ip", "link", "set", "mtu", "1420", "up", "dev", iface], check=True)
            
            # Configure Address on interface (important for link-local BGP!)
            if address:
                # Check if address already configured
                family = "-6" if ":" in address else "-4"
                addr_check = subprocess.run(
                    ["ip", family, "addr", "show", "dev", iface],
                    capture_output=True, text=True
                )
                addr_only = address.split("/")[0]
                if addr_only not in addr_check.stdout:
                    # Ensure proper prefix length
                    if "/" not in address:
                        address = f"{address}/64" if ":" in address else f"{address}/32"
                    subprocess.run(
                        ["ip", family, "addr", "add", address, "dev", iface],
                        capture_output=True  # Ignore errors if already exists
                    )
                    logger.info(f"Configured address {address} on {iface}")
            
            logger.info(f"Configured {iface}")
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

