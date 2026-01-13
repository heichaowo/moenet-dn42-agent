"""
MoeNet DN42 Agent - State Manager
"""
import json
import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


class StateManager:
    """Manages last_state.json persistence."""
    
    def __init__(self, state_path: str = "/var/lib/moenet-agent/last_state.json"):
        self.state_path = Path(state_path)
        self._state: Optional[dict] = None
    
    def load(self) -> dict:
        if self._state is not None:
            return self._state
        
        if self.state_path.exists():
            try:
                with open(self.state_path) as f:
                    self._state = json.load(f)
            except Exception as e:
                logger.error(f"Failed to load state: {e}")
                self._state = self._empty_state()
        else:
            self._state = self._empty_state()
        return self._state
    
    def save(self) -> bool:
        if self._state is None:
            return False
        try:
            self.state_path.parent.mkdir(parents=True, exist_ok=True)
            self._state["last_update"] = datetime.utcnow().isoformat() + "Z"
            temp = self.state_path.with_suffix(".tmp")
            with open(temp, "w") as f:
                json.dump(self._state, f, indent=2)
            os.replace(temp, self.state_path)
            return True
        except Exception as e:
            logger.error(f"Failed to save state: {e}")
            return False
    
    def get_config_hash(self) -> Optional[str]:
        return self.load().get("config_version_hash")
    
    def get_applied_peers(self) -> list:
        return self.load().get("applied_config", {}).get("peers", [])
    
    def update_applied_config(self, peers: list, config_hash: str) -> None:
        state = self.load()
        if state.get("applied_config"):
            state["rollback_snapshot"] = {
                "previous_hash": state.get("config_version_hash"),
                "created_at": datetime.utcnow().isoformat() + "Z",
            }
        state["config_version_hash"] = config_hash
        state["applied_config"] = {"peers": peers, "applied_at": datetime.utcnow().isoformat() + "Z"}
        self.save()
    
    def update_health(self, health: dict) -> None:
        state = self.load()
        state["health_status"] = {**health, "last_check": datetime.utcnow().isoformat() + "Z"}
        self.save()
    
    def set_node_id(self, node_id: str) -> None:
        state = self.load()
        state["node_id"] = node_id
        self.save()
    
    def get_full_state(self) -> dict:
        return self.load().copy()
    
    def _empty_state(self) -> dict:
        return {
            "version": "2.1.0",
            "node_id": None,
            "last_update": datetime.utcnow().isoformat() + "Z",
            "config_version_hash": None,
            "applied_config": {"peers": []},
            "health_status": {},
            "rollback_snapshot": None,
        }
