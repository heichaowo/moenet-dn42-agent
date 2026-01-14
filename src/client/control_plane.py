"""
MoeNet DN42 Agent - Control Plane Client
"""
import hashlib
import json
import logging
from typing import Any, Optional

import aiohttp

logger = logging.getLogger(__name__)


class ControlPlaneClient:
    """HTTP client for control-plane API."""
    
    def __init__(
        self,
        base_url: str,
        node_name: str,
        api_token: Optional[str] = None,
        timeout: int = 30,
    ):
        self.base_url = base_url.rstrip("/")
        self.node_name = node_name
        self.api_token = api_token
        self.timeout = aiohttp.ClientTimeout(total=timeout)
        self._session: Optional[aiohttp.ClientSession] = None
    
    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            headers = {"Content-Type": "application/json"}
            if self.api_token:
                headers["Authorization"] = f"Bearer {self.api_token}"
            self._session = aiohttp.ClientSession(headers=headers, timeout=self.timeout)
        return self._session
    
    async def close(self):
        if self._session and not self._session.closed:
            await self._session.aclose()
    
    async def get_config(self) -> Optional[dict[str, Any]]:
        """Fetch configuration from control-plane."""
        try:
            session = await self._get_session()
            url = f"{self.base_url}/api/v1/agent/config"
            async with session.get(url, params={"node": self.node_name}) as resp:
                if resp.status == 200:
                    return await resp.json()
                logger.error(f"Failed to fetch config: HTTP {resp.status}")
                return None
        except Exception as e:
            logger.error(f"Control-plane error: {e}")
            return None
    
    async def send_heartbeat(self, agent_version: str, config_hash: Optional[str], status: dict) -> bool:
        """Send heartbeat to control-plane."""
        try:
            session = await self._get_session()
            payload = {
                "node_id": self.node_name,
                "agent_version": agent_version,
                "config_version_hash": config_hash,
                "status": status,
            }
            async with session.post(f"{self.base_url}/api/v1/agent/heartbeat", json=payload) as resp:
                return resp.status == 200
        except Exception as e:
            logger.error(f"Heartbeat error: {e}")
            return False
    
    async def report_state(self, last_state: dict) -> bool:
        """Report last_state.json for disaster recovery."""
        try:
            session = await self._get_session()
            payload = {"node_id": self.node_name, "last_state": last_state}
            async with session.post(f"{self.base_url}/api/v1/agent/state", json=payload) as resp:
                return resp.status == 200
        except Exception as e:
            logger.error(f"State report error: {e}")
            return False
    
    async def get_mesh_config(self) -> Optional[dict[str, Any]]:
        """Fetch mesh network configuration."""
        try:
            session = await self._get_session()
            url = f"{self.base_url}/api/v1/mesh/config/{self.node_name}"
            async with session.get(url) as resp:
                if resp.status == 200:
                    return await resp.json()
                logger.error(f"Failed to fetch mesh config: HTTP {resp.status}")
                return None
        except Exception as e:
            logger.error(f"Mesh config error: {e}")
            return None
    
    async def register_mesh_key(self, public_key: str) -> bool:
        """Register mesh WireGuard public key with control plane."""
        try:
            session = await self._get_session()
            url = f"{self.base_url}/api/v1/mesh/register-key/{self.node_name}"
            payload = {"public_key": public_key}
            async with session.post(url, json=payload) as resp:
                return resp.status == 200
        except Exception as e:
            logger.error(f"Mesh key registration error: {e}")
            return False
    
    async def register_node(
        self,
        agent_version: str,
        region: str = "unknown",
        ipv4: str = None,
        ipv6: str = None,
        dn42_ipv4: str = None,
        dn42_ipv6: str = None,
        node_id: int = None,
        loopback_ipv6: str = None,
        mesh_public_key: str = None,
        is_rr: bool = False,
    ) -> Optional[dict]:
        """Register this node with control-plane (auto-create if not exists)."""
        try:
            session = await self._get_session()
            payload = {
                "hostname": self.node_name,
                "agent_version": agent_version,
                "region": region,
                "is_rr": is_rr,
            }
            if ipv4:
                payload["ipv4"] = ipv4
            if ipv6:
                payload["ipv6"] = ipv6
            if dn42_ipv4:
                payload["dn42_ipv4"] = dn42_ipv4
            if dn42_ipv6:
                payload["dn42_ipv6"] = dn42_ipv6
            if node_id:
                payload["node_id"] = node_id
            if loopback_ipv6:
                payload["loopback_ipv6"] = loopback_ipv6
            if mesh_public_key:
                payload["mesh_public_key"] = mesh_public_key
            
            async with session.post(f"{self.base_url}/api/v1/agent/register", json=payload) as resp:
                if resp.status == 200:
                    result = await resp.json()
                    logger.info(f"Node registration: {result['status']} - {result['node_name']}")
                    return result
                logger.error(f"Node registration failed: HTTP {resp.status}")
                return None
        except Exception as e:
            logger.error(f"Registration error: {e}")
            return None
    
    @staticmethod
    def compute_config_hash(config: dict) -> str:
        config_str = json.dumps(config.get("peers", []), sort_keys=True)
        return f"sha256:{hashlib.sha256(config_str.encode()).hexdigest()[:16]}"
