"""MoeNet DN42 Agent - BIRD Executor

Includes delayed coalesce reload mechanism to prevent BIRD 3.2.0 crash
from rapid consecutive 'birdc configure' calls (assertion failure in conf.c:209).
"""

import logging
import subprocess
import threading
from pathlib import Path

logger = logging.getLogger(__name__)


class BirdExecutor:
    """BIRD configuration executor with delayed coalesce reload.

    The reload() method uses a delayed coalesce pattern:
    - Each call resets a timer (default 2 seconds)
    - Only one actual reload happens after the timer expires
    - This prevents BIRD 3.2.0 crash from rapid consecutive configure calls
    """

    # Class-level shared state for singleton-like behavior across instances
    _reload_timer: threading.Timer = None
    _reload_lock = threading.Lock()
    _reload_pending = False
    _coalesce_delay = 2.0  # seconds to wait before executing reload

    def __init__(
        self, config_dir: str = "/etc/bird/peers", bird_ctl: str = "/var/run/bird/bird.ctl"
    ):
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

    def write_ibgp(self, config: str) -> bool:
        """Write iBGP configuration file."""
        try:
            ibgp_dir = self.config_dir.parent / "ibgp.d"
            ibgp_dir.mkdir(parents=True, exist_ok=True)
            (ibgp_dir / "ibgp_peers.conf").write_text(config)
            return True
        except Exception as e:
            logger.error(f"Write iBGP config failed: {e}")
            return False

    def reload(self) -> bool:
        """Request a BIRD configuration reload with delayed coalescing.

        This method schedules a reload to happen after a delay. If called
        multiple times within the delay window, the timer resets and only
        one reload will execute after all calls have settled.

        This prevents BIRD 3.2.0 crash from assertion failure when multiple
        'birdc configure' commands are issued in rapid succession.

        Returns:
            bool: True (reload is scheduled), actual result logged asynchronously
        """
        with BirdExecutor._reload_lock:
            # Cancel any existing timer
            if BirdExecutor._reload_timer is not None:
                BirdExecutor._reload_timer.cancel()
                logger.debug("BIRD reload timer reset (coalescing requests)")

            BirdExecutor._reload_pending = True

            # Schedule new reload
            BirdExecutor._reload_timer = threading.Timer(
                BirdExecutor._coalesce_delay, self._execute_reload
            )
            BirdExecutor._reload_timer.daemon = True
            BirdExecutor._reload_timer.start()

            logger.debug(f"BIRD reload scheduled in {BirdExecutor._coalesce_delay}s")

        return True

    def _execute_reload(self) -> bool:
        """Actually execute the BIRD reload (called by timer)."""
        with BirdExecutor._reload_lock:
            BirdExecutor._reload_pending = False
            BirdExecutor._reload_timer = None

        logger.info("Executing BIRD configuration reload")
        result = subprocess.run(
            ["birdc", "-s", self.bird_ctl, "configure"], capture_output=True, text=True
        )

        if result.returncode == 0:
            logger.info("BIRD reload successful")
            return True
        else:
            logger.warning(f"BIRD reload failed: {result.stderr}")
            return False

    def reload_now(self) -> bool:
        """Force immediate BIRD reload, bypassing coalesce delay.

        Use this only when a reload MUST happen immediately, such as
        during shutdown or critical error recovery.
        """
        with BirdExecutor._reload_lock:
            if BirdExecutor._reload_timer is not None:
                BirdExecutor._reload_timer.cancel()
                BirdExecutor._reload_timer = None
            BirdExecutor._reload_pending = False

        logger.info("Executing immediate BIRD configuration reload")
        result = subprocess.run(
            ["birdc", "-s", self.bird_ctl, "configure"], capture_output=True, text=True
        )

        if result.returncode == 0:
            logger.info("BIRD reload successful")
            return True
        else:
            logger.warning(f"BIRD reload failed: {result.stderr}")
            return False

    def get_status(self) -> dict:
        result = subprocess.run(
            ["birdc", "-s", self.bird_ctl, "show", "protocols"], capture_output=True, text=True
        )
        if result.returncode != 0:
            return {"running": False}
        up = sum(
            1 for line in result.stdout.split("\n") if "dn42_" in line and "Established" in line
        )
        down = sum(
            1 for line in result.stdout.split("\n") if "dn42_" in line and "Established" not in line
        )
        return {"running": True, "protocols_up": up, "protocols_down": down}
