"""
MoeNet DN42 Agent - Test Fixtures

Shared pytest fixtures for agent tests.
"""
import pytest
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

# Add src to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))


@pytest.fixture
def mock_config():
    """Provide mock config for testing."""
    config = {
        "node_id": "hk-edge",
        "control_plane_url": "http://localhost:8800",
        "api_secret": "test-secret",
        "bird_socket": "/run/bird/bird.ctl",
        "wg_interface_prefix": "dn42-",
    }
    
    with patch("src.api.server.config", config):
        yield config


@pytest.fixture
def mock_subprocess():
    """Mock subprocess calls for command execution."""
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="Mock output",
            stderr="",
        )
        yield mock_run


@pytest.fixture
def mock_birdc():
    """Mock BIRD control socket commands."""
    with patch("src.api.server.birdc") as mock:
        mock.return_value = "BIRD 2.15.1 ready."
        yield mock
