"""
MoeNet MTU Probe

Probes path MTU to detect optimal MTU for each mesh peer.
Especially important for intercontinental links.

Usage:
    probe = MTUProbe()
    mtu = await probe.probe_mtu("peer.example.com")
"""

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Dict, Optional

logger = logging.getLogger(__name__)


# MTU values to test (descending order)
MTU_TEST_VALUES = [1420, 1400, 1380, 1320, 1280]

# Minimum safe MTU (IPv6 minimum)
MIN_MTU = 1280

# Overhead for ICMP/IP headers
ICMP_OVERHEAD = 28


@dataclass
class MTUProbeResult:
    """Result of MTU probe."""

    target: str
    optimal_mtu: int
    tested_at: float  # timestamp
    is_intercontinental: bool = False
    is_low_mtu: bool = False  # MTU < 1400


@dataclass
class MTUProbe:
    """Probe path MTU to mesh peers."""

    # Cache of probe results
    cache: Dict[str, MTUProbeResult] = field(default_factory=dict)

    # Timeout for each ping
    timeout: float = 5.0

    async def probe_mtu(self, target: str, is_intercontinental: bool = False) -> MTUProbeResult:
        """
        Probe the optimal MTU to a target.

        Args:
            target: Hostname or IP to probe
            is_intercontinental: Whether this is an intercontinental link

        Returns:
            MTUProbeResult with optimal MTU
        """
        import time

        optimal_mtu = MIN_MTU

        for mtu in MTU_TEST_VALUES:
            packet_size = mtu - ICMP_OVERHEAD

            if await self._ping_with_size(target, packet_size):
                optimal_mtu = mtu
                break  # Found working MTU

        result = MTUProbeResult(
            target=target,
            optimal_mtu=optimal_mtu,
            tested_at=time.time(),
            is_intercontinental=is_intercontinental,
            is_low_mtu=(optimal_mtu < 1400),
        )

        self.cache[target] = result

        logger.info(
            f"MTU probe {target}: {optimal_mtu} "
            f"(intercont={is_intercontinental}, low={result.is_low_mtu})"
        )

        return result

    async def _ping_with_size(self, target: str, size: int) -> bool:
        """
        Ping target with specific packet size and DF flag.

        Args:
            target: Target to ping
            size: Packet size (excluding IP/ICMP headers)

        Returns:
            True if ping succeeded, False otherwise
        """
        # Determine if IPv6
        is_ipv6 = ":" in target

        if is_ipv6:
            cmd = ["ping6", "-c", "1", "-W", str(int(self.timeout)), "-s", str(size), target]
        else:
            # -M do = set DF flag (don't fragment)
            cmd = [
                "ping",
                "-c",
                "1",
                "-W",
                str(int(self.timeout)),
                "-M",
                "do",
                "-s",
                str(size),
                target,
            ]

        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            await asyncio.wait_for(proc.wait(), timeout=self.timeout + 1)
            return proc.returncode == 0
        except asyncio.TimeoutError:
            return False
        except Exception as e:
            logger.debug(f"Ping failed for {target} size={size}: {e}")
            return False

    def get_cached_mtu(self, target: str) -> Optional[int]:
        """Get cached MTU for target if available."""
        result = self.cache.get(target)
        return result.optimal_mtu if result else None

    def should_use_low_mtu(self, target: str) -> bool:
        """Check if target should use low MTU based on cache."""
        result = self.cache.get(target)
        return result.is_low_mtu if result else False


# Singleton instance
_mtu_probe: Optional[MTUProbe] = None


def get_mtu_probe() -> MTUProbe:
    """Get singleton MTU probe instance."""
    global _mtu_probe
    if _mtu_probe is None:
        _mtu_probe = MTUProbe()
    return _mtu_probe
