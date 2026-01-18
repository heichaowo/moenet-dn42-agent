"""
MoeNet DN42 Agent - Latency Probe

Automatically measures RTT to peers and updates latency communities.
"""
import asyncio
import logging
import re
import subprocess
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Optional, Tuple

from .constants import latency_to_tier, DN42_LATENCY

logger = logging.getLogger(__name__)


@dataclass
class ProbeResult:
    """Result of a latency probe."""
    target: str
    asn: int
    rtt_ms: float
    latency_tier: int
    timestamp: datetime = field(default_factory=datetime.utcnow)
    success: bool = True
    error: Optional[str] = None
    
    def to_dict(self) -> dict:
        return {
            "target": self.target,
            "asn": self.asn,
            "rtt_ms": round(self.rtt_ms, 2),
            "latency_tier": self.latency_tier,
            "timestamp": self.timestamp.isoformat(),
            "success": self.success,
            "error": self.error,
        }


@dataclass
class PeerInfo:
    """Information about a peer for latency probing."""
    asn: int
    endpoint: str  # IP or hostname
    last_rtt: Optional[float] = None
    last_tier: Optional[int] = None
    last_probe: Optional[datetime] = None
    probe_count: int = 0
    fail_count: int = 0


class LatencyProbe:
    """Probe latency to BGP peers and update community settings."""
    
    def __init__(self,
                 probe_interval: int = 300,  # 5 minutes
                 probe_count: int = 5,       # Ping count
                 timeout: int = 10,          # Ping timeout
                 ewma_alpha: float = 0.3):   # Exponential weighted moving average
        self.probe_interval = probe_interval
        self.probe_count = probe_count
        self.timeout = timeout
        self.ewma_alpha = ewma_alpha
        
        # Tracked peers
        self.peers: Dict[int, PeerInfo] = {}  # ASN -> PeerInfo
        
        # Probe history
        self.history: Dict[int, List[ProbeResult]] = {}  # ASN -> results
        self.max_history = 100
        
        # Running state
        self._running = False
        self._task: Optional[asyncio.Task] = None
        
        # Callback for updating communities
        self._update_callback: Optional[callable] = None
    
    def add_peer(self, asn: int, endpoint: str) -> None:
        """Add a peer to probe."""
        if asn not in self.peers:
            self.peers[asn] = PeerInfo(asn=asn, endpoint=endpoint)
            self.history[asn] = []
            logger.info(f"Added peer AS{asn} ({endpoint}) to latency probe")
        else:
            self.peers[asn].endpoint = endpoint
    
    def remove_peer(self, asn: int) -> None:
        """Remove a peer from probing."""
        if asn in self.peers:
            del self.peers[asn]
            if asn in self.history:
                del self.history[asn]
            logger.info(f"Removed peer AS{asn} from latency probe")
    
    def set_update_callback(self, callback: callable) -> None:
        """Set callback for when latency tier changes.
        
        Callback signature: callback(asn: int, tier: int, rtt_ms: float)
        """
        self._update_callback = callback
    
    async def start(self) -> None:
        """Start the latency probe daemon."""
        if self._running:
            return
        
        self._running = True
        self._task = asyncio.create_task(self._probe_loop())
        logger.info("Latency probe started")
    
    async def stop(self) -> None:
        """Stop the latency probe daemon."""
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("Latency probe stopped")
    
    async def _probe_loop(self) -> None:
        """Main probe loop."""
        while self._running:
            try:
                await self._probe_all_peers()
            except Exception as e:
                logger.error(f"Probe loop error: {e}")
            
            await asyncio.sleep(self.probe_interval)
    
    async def _probe_all_peers(self) -> None:
        """Probe all registered peers."""
        if not self.peers:
            return
        
        logger.debug(f"Probing {len(self.peers)} peers...")
        
        # Probe peers concurrently
        tasks = [
            self._probe_peer(peer)
            for peer in self.peers.values()
        ]
        
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        for result in results:
            if isinstance(result, ProbeResult) and result.success:
                peer = self.peers.get(result.asn)
                if peer:
                    old_tier = peer.last_tier
                    
                    # Update with EWMA
                    if peer.last_rtt is not None:
                        peer.last_rtt = (
                            self.ewma_alpha * result.rtt_ms +
                            (1 - self.ewma_alpha) * peer.last_rtt
                        )
                    else:
                        peer.last_rtt = result.rtt_ms
                    
                    peer.last_tier = latency_to_tier(peer.last_rtt)
                    peer.last_probe = result.timestamp
                    peer.probe_count += 1
                    
                    # Trigger callback if tier changed
                    if old_tier != peer.last_tier and self._update_callback:
                        try:
                            self._update_callback(
                                result.asn, 
                                peer.last_tier, 
                                peer.last_rtt
                            )
                        except Exception as e:
                            logger.error(f"Update callback failed: {e}")
    
    async def _probe_peer(self, peer: PeerInfo) -> ProbeResult:
        """Probe a single peer."""
        try:
            # Use ping to measure RTT
            rtt_ms = await self._ping(peer.endpoint)
            
            if rtt_ms is None:
                peer.fail_count += 1
                return ProbeResult(
                    target=peer.endpoint,
                    asn=peer.asn,
                    rtt_ms=0,
                    latency_tier=8,
                    success=False,
                    error="Ping failed",
                )
            
            tier = latency_to_tier(rtt_ms)
            result = ProbeResult(
                target=peer.endpoint,
                asn=peer.asn,
                rtt_ms=rtt_ms,
                latency_tier=tier,
            )
            
            # Store in history
            if peer.asn in self.history:
                self.history[peer.asn].append(result)
                # Trim history
                if len(self.history[peer.asn]) > self.max_history:
                    self.history[peer.asn] = self.history[peer.asn][-self.max_history:]
            
            logger.debug(f"Probe AS{peer.asn}: {rtt_ms:.2f}ms (tier {tier})")
            return result
            
        except Exception as e:
            peer.fail_count += 1
            return ProbeResult(
                target=peer.endpoint,
                asn=peer.asn,
                rtt_ms=0,
                latency_tier=8,
                success=False,
                error=str(e),
            )
    
    async def _ping(self, target: str) -> Optional[float]:
        """Ping a target and return average RTT in ms."""
        try:
            # Detect IPv6
            is_ipv6 = ":" in target
            ping_cmd = "ping6" if is_ipv6 else "ping"
            
            # Run ping
            proc = await asyncio.create_subprocess_exec(
                ping_cmd, "-c", str(self.probe_count), "-W", str(self.timeout), target,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, _ = await asyncio.wait_for(
                proc.communicate(), 
                timeout=self.timeout * self.probe_count + 5
            )
            
            if proc.returncode != 0:
                return None
            
            # Parse output
            output = stdout.decode()
            
            # Match "rtt min/avg/max/mdev = 1.234/2.345/3.456/0.123 ms"
            match = re.search(r'rtt min/avg/max/mdev = [\d.]+/([\d.]+)/[\d.]+/[\d.]+ ms', output)
            if match:
                return float(match.group(1))
            
            # Alternative: match "min/avg/max = 1.234/2.345/3.456 ms"
            match = re.search(r'min/avg/max = [\d.]+/([\d.]+)/[\d.]+ ms', output)
            if match:
                return float(match.group(1))
            
            return None
            
        except asyncio.TimeoutError:
            return None
        except Exception as e:
            logger.error(f"Ping {target} failed: {e}")
            return None
    
    def probe_now(self, asn: int) -> Optional[ProbeResult]:
        """Immediately probe a specific peer (synchronous)."""
        peer = self.peers.get(asn)
        if not peer:
            return None
        
        try:
            # Synchronous ping
            is_ipv6 = ":" in peer.endpoint
            ping_cmd = ["ping6" if is_ipv6 else "ping", 
                       "-c", str(self.probe_count), 
                       "-W", str(self.timeout), 
                       peer.endpoint]
            
            result = subprocess.run(
                ping_cmd,
                capture_output=True,
                text=True,
                timeout=self.timeout * self.probe_count + 5,
            )
            
            if result.returncode != 0:
                return ProbeResult(
                    target=peer.endpoint,
                    asn=asn,
                    rtt_ms=0,
                    latency_tier=8,
                    success=False,
                    error="Ping failed",
                )
            
            # Parse RTT
            match = re.search(r'rtt min/avg/max/mdev = [\d.]+/([\d.]+)/[\d.]+/[\d.]+ ms', result.stdout)
            if not match:
                match = re.search(r'min/avg/max = [\d.]+/([\d.]+)/[\d.]+ ms', result.stdout)
            
            if match:
                rtt_ms = float(match.group(1))
                tier = latency_to_tier(rtt_ms)
                
                probe_result = ProbeResult(
                    target=peer.endpoint,
                    asn=asn,
                    rtt_ms=rtt_ms,
                    latency_tier=tier,
                )
                
                # Update peer state
                peer.last_rtt = rtt_ms
                peer.last_tier = tier
                peer.last_probe = probe_result.timestamp
                peer.probe_count += 1
                
                return probe_result
            
            return ProbeResult(
                target=peer.endpoint,
                asn=asn,
                rtt_ms=0,
                latency_tier=8,
                success=False,
                error="Failed to parse RTT",
            )
            
        except subprocess.TimeoutExpired:
            return ProbeResult(
                target=peer.endpoint,
                asn=asn,
                rtt_ms=0,
                latency_tier=8,
                success=False,
                error="Timeout",
            )
        except Exception as e:
            return ProbeResult(
                target=peer.endpoint,
                asn=asn,
                rtt_ms=0,
                latency_tier=8,
                success=False,
                error=str(e),
            )
    
    def get_peer_stats(self, asn: int) -> Optional[dict]:
        """Get latency statistics for a peer."""
        peer = self.peers.get(asn)
        if not peer:
            return None
        
        history = self.history.get(asn, [])
        successful = [r for r in history if r.success]
        
        if not successful:
            return {
                "asn": asn,
                "endpoint": peer.endpoint,
                "last_rtt": peer.last_rtt,
                "last_tier": peer.last_tier,
                "last_probe": peer.last_probe.isoformat() if peer.last_probe else None,
                "probe_count": peer.probe_count,
                "fail_count": peer.fail_count,
                "history": [],
            }
        
        rtts = [r.rtt_ms for r in successful]
        
        return {
            "asn": asn,
            "endpoint": peer.endpoint,
            "last_rtt": peer.last_rtt,
            "last_tier": peer.last_tier,
            "last_probe": peer.last_probe.isoformat() if peer.last_probe else None,
            "probe_count": peer.probe_count,
            "fail_count": peer.fail_count,
            "stats": {
                "min_rtt": min(rtts),
                "max_rtt": max(rtts),
                "avg_rtt": sum(rtts) / len(rtts),
                "samples": len(rtts),
            },
            "history": [r.to_dict() for r in successful[-10:]],  # Last 10
        }
    
    def get_all_stats(self) -> dict:
        """Get statistics for all peers."""
        return {
            "probe_interval": self.probe_interval,
            "peer_count": len(self.peers),
            "running": self._running,
            "peers": {
                asn: self.get_peer_stats(asn)
                for asn in self.peers
            },
        }
