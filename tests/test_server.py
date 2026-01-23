"""
MoeNet DN42 Agent - Server API Tests

Tests for the Agent HTTP API endpoints.
"""
import pytest
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch, AsyncMock

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))


# Create mock config before importing server
mock_config_data = MagicMock()
mock_config_data.agent_version = "1.0.0"
mock_config_data.node_name = "hk-edge"
mock_config_data.is_open = True
mock_config_data.max_peers = 100
mock_config_data.dn42_ipv4 = "172.20.0.1"
mock_config_data.dn42_ipv6 = "fd42:2337::1"
mock_config_data.wg_public_key = "TEST_WG_PUBKEY"
mock_config_data.api_token = "test-secret-token"
mock_config_data.api_host = "127.0.0.1"
mock_config_data.api_port = 54321
mock_config_data.bird_ctl = "/run/bird/bird.ctl"


class TestHealthEndpoint:
    """Tests for the / health endpoint."""
    
    @pytest.mark.asyncio
    async def test_health_check_success(self, aiohttp_client):
        """Test health check returns ok status."""
        with patch("src.api.server.config", mock_config_data):
            with patch("src.api.server.load_config", return_value=mock_config_data):
                from src.api.server import create_app
                
                # Create app without auth for this test
                with patch.object(mock_config_data, "api_token", None):
                    app = create_app()
                    client = await aiohttp_client(app)
                    
                    resp = await client.get("/")
                    assert resp.status == 200
                    
                    data = await resp.json()
                    assert data["status"] == "ok"
                    assert "version" in data
                    assert "node" in data


class TestAuthMiddleware:
    """Tests for authentication middleware."""
    
    @pytest.mark.asyncio
    async def test_auth_valid_token(self, aiohttp_client):
        """Test request with valid auth token succeeds."""
        with patch("src.api.server.config", mock_config_data):
            with patch("src.api.server.load_config", return_value=mock_config_data):
                from src.api.server import create_app
                
                app = create_app()
                client = await aiohttp_client(app)
                
                headers = {"Authorization": "Bearer test-secret-token"}
                resp = await client.get("/", headers=headers)
                
                assert resp.status == 200
    
    @pytest.mark.asyncio
    async def test_auth_invalid_token(self, aiohttp_client):
        """Test request with invalid token returns 401."""
        with patch("src.api.server.config", mock_config_data):
            with patch("src.api.server.load_config", return_value=mock_config_data):
                from src.api.server import create_app
                
                app = create_app()
                client = await aiohttp_client(app)
                
                headers = {"Authorization": "Bearer wrong-token"}
                resp = await client.get("/", headers=headers)
                
                assert resp.status == 401
    
    @pytest.mark.asyncio
    async def test_auth_missing_token(self, aiohttp_client):
        """Test request without token returns 401."""
        with patch("src.api.server.config", mock_config_data):
            with patch("src.api.server.load_config", return_value=mock_config_data):
                from src.api.server import create_app
                
                app = create_app()
                client = await aiohttp_client(app)
                
                resp = await client.get("/")
                
                assert resp.status == 401


class TestPingEndpoint:
    """Tests for the /ping endpoint."""
    
    @pytest.mark.asyncio
    async def test_ping_success(self, aiohttp_client):
        """Test ping with valid target."""
        with patch("src.api.server.config", mock_config_data):
            with patch("src.api.server.load_config", return_value=mock_config_data):
                with patch("src.api.server.simple_run") as mock_run:
                    mock_run.return_value = "PING 8.8.8.8 (8.8.8.8): 56 data bytes\n4 packets transmitted"
                    
                    from src.api.server import create_app
                    
                    with patch.object(mock_config_data, "api_token", None):
                        app = create_app()
                        client = await aiohttp_client(app)
                        
                        resp = await client.post("/ping", json={"target": "8.8.8.8", "count": 4})
                        
                        assert resp.status == 200
                        data = await resp.json()
                        assert "result" in data
                        assert "PING" in data["result"]
    
    @pytest.mark.asyncio
    async def test_ping_missing_target(self, aiohttp_client):
        """Test ping without target returns 400."""
        with patch("src.api.server.config", mock_config_data):
            with patch("src.api.server.load_config", return_value=mock_config_data):
                from src.api.server import create_app
                
                with patch.object(mock_config_data, "api_token", None):
                    app = create_app()
                    client = await aiohttp_client(app)
                    
                    resp = await client.post("/ping", json={})
                    
                    assert resp.status == 400
                    data = await resp.json()
                    assert "error" in data
    
    @pytest.mark.asyncio
    async def test_ping_timeout(self, aiohttp_client):
        """Test ping timeout returns Timeout message."""
        with patch("src.api.server.config", mock_config_data):
            with patch("src.api.server.load_config", return_value=mock_config_data):
                with patch("src.api.server.simple_run") as mock_run:
                    mock_run.return_value = None  # Timeout returns None
                    
                    from src.api.server import create_app
                    
                    with patch.object(mock_config_data, "api_token", None):
                        app = create_app()
                        client = await aiohttp_client(app)
                        
                        resp = await client.post("/ping", json={"target": "10.255.255.1"})
                        
                        assert resp.status == 200
                        data = await resp.json()
                        assert data["result"] == "Timeout"


class TestPeersEndpoint:
    """Tests for the /peers endpoints."""
    
    @pytest.mark.asyncio
    async def test_list_peers(self, aiohttp_client):
        """Test listing peers."""
        with patch("src.api.server.config", mock_config_data):
            with patch("src.api.server.load_config", return_value=mock_config_data):
                with patch("src.api.server.birdc") as mock_birdc:
                    mock_birdc.return_value = """BIRD 2.15.1 ready.
Name                 Proto      Table      State
dn42_4242420337      BGP        ---        up     2026-01-24  Established
dn42_4242420919      BGP        ---        start  2026-01-24  Connect"""
                    
                    from src.api.server import create_app
                    
                    with patch.object(mock_config_data, "api_token", None):
                        app = create_app()
                        client = await aiohttp_client(app)
                        
                        resp = await client.get("/peers")
                        
                        assert resp.status == 200
                        data = await resp.json()
                        assert "peers" in data
                        assert len(data["peers"]) == 2


class TestStatsEndpoint:
    """Tests for the /stats endpoints."""
    
    @pytest.mark.asyncio
    async def test_get_stats(self, aiohttp_client):
        """Test getting node statistics."""
        with patch("src.api.server.config", mock_config_data):
            with patch("src.api.server.load_config", return_value=mock_config_data):
                with patch("src.api.server.birdc") as mock_birdc:
                    with patch("src.api.server.simple_run") as mock_run:
                        mock_birdc.return_value = """BIRD 2.15.1 ready.
dn42_4242420337      BGP        ---        up     Established
dn42_4242420919      BGP        ---        start  Connect"""
                        mock_run.return_value = "interface\ttransfer-rx\ttransfer-tx"
                        
                        from src.api.server import create_app
                        
                        with patch.object(mock_config_data, "api_token", None):
                            app = create_app()
                            client = await aiohttp_client(app)
                            
                            resp = await client.get("/stats")
                            
                            assert resp.status == 200
                            data = await resp.json()
                            assert "peer_count" in data
                            assert "established" in data
                            assert data["peer_count"] == 2
                            assert data["established"] == 1


class TestRouteEndpoint:
    """Tests for the /route endpoint."""
    
    @pytest.mark.asyncio
    async def test_route_query(self, aiohttp_client):
        """Test route query."""
        with patch("src.api.server.config", mock_config_data):
            with patch("src.api.server.load_config", return_value=mock_config_data):
                with patch("src.api.server.birdc") as mock_birdc:
                    mock_birdc.return_value = "172.20.0.0/24 via 172.22.0.1"
                    
                    from src.api.server import create_app
                    
                    with patch.object(mock_config_data, "api_token", None):
                        app = create_app()
                        client = await aiohttp_client(app)
                        
                        resp = await client.post("/route", json={"target": "172.20.0.1"})
                        
                        assert resp.status == 200
                        data = await resp.json()
                        assert "result" in data
    
    @pytest.mark.asyncio
    async def test_route_missing_target(self, aiohttp_client):
        """Test route without target returns 400."""
        with patch("src.api.server.config", mock_config_data):
            with patch("src.api.server.load_config", return_value=mock_config_data):
                from src.api.server import create_app
                
                with patch.object(mock_config_data, "api_token", None):
                    app = create_app()
                    client = await aiohttp_client(app)
                    
                    resp = await client.post("/route", json={})
                    
                    assert resp.status == 400


class TestSimpleRun:
    """Tests for the simple_run helper function."""
    
    def test_simple_run_success(self):
        """Test successful command execution."""
        with patch("src.api.server.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=0,
                stdout="success output",
                stderr=""
            )
            
            from src.api.server import simple_run
            result = simple_run("echo test")
            
            assert result == "success output"
    
    def test_simple_run_failure(self):
        """Test failed command returns stderr."""
        with patch("src.api.server.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=1,
                stdout="",
                stderr="error output"
            )
            
            from src.api.server import simple_run
            result = simple_run("false")
            
            assert result == "error output"
    
    def test_simple_run_timeout(self):
        """Test command timeout returns None."""
        with patch("src.api.server.subprocess.run") as mock_run:
            import subprocess
            mock_run.side_effect = subprocess.TimeoutExpired(cmd="test", timeout=10)
            
            from src.api.server import simple_run
            result = simple_run("sleep 100", timeout=1)
            
            assert result is None
