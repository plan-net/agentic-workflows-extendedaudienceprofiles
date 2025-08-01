"""
Unit tests for Masumi integration module.
"""
import pytest
import asyncio
from unittest.mock import Mock, patch, AsyncMock, mock_open
from extended_audience_profiles.masumi import MasumiClient


class TestMasumiClient:
    """Tests for MasumiClient class."""
    
    @patch('builtins.open', new_callable=mock_open, read_data="""
masumi:
  registry_service_url: "https://registry.masumi.network/api/v1"
  registry_api_key: "test_registry_key"
  payment_service_url: "https://payment.masumi.network/api/v1"
  payment_api_key: "test_payment_key"
  budget:
    daily_limit: 100.0
    per_request_max: 10.0

agents:
  - name: "advanced-web-research"
    endpoint: "https://agent.example.com/advanced-web-research"
    description: "Test agent"
    max_cost_per_request: 5.0
    enabled: true
""")
    @patch('pathlib.Path.exists', return_value=True)
    def test_load_config(self, mock_exists, mock_file):
        """Test configuration loading."""
        client = MasumiClient()
        
        assert client.masumi_config is not None
        assert client.masumi_config['registry_service_url'] == "https://registry.masumi.network/api/v1"
        assert len(client.agents_config) == 1
        assert client.agents_config[0]['name'] == "advanced-web-research"
    
    @patch('builtins.open', new_callable=mock_open, read_data="""
masumi:
  budget:
    per_request_max: 10.0
agents:
  - name: "test-agent"
    enabled: true
  - name: "disabled-agent"
    enabled: false
""")
    @patch('pathlib.Path.exists', return_value=True)
    def test_get_available_agents(self, mock_exists, mock_file):
        """Test getting available agents."""
        client = MasumiClient()
        available = client.get_available_agents()
        
        assert len(available) == 1
        assert available[0]['name'] == "test-agent"
    
    @patch('builtins.open', new_callable=mock_open, read_data="""
masumi: {}
agents:
  - name: "test-agent"
    enabled: true
  - name: "disabled-agent"
    enabled: false
""")
    @patch('pathlib.Path.exists', return_value=True)
    def test_check_agent_availability(self, mock_exists, mock_file):
        """Test checking agent availability."""
        client = MasumiClient()
        
        assert client.check_agent_availability("test-agent") is True
        assert client.check_agent_availability("disabled-agent") is False
        assert client.check_agent_availability("nonexistent-agent") is False
    
    @patch('builtins.open', new_callable=mock_open, read_data="""
masumi:
  budget:
    per_request_max: 10.0
agents:
  - name: "test-agent"
    max_cost_per_request: 5.0
    enabled: true
""")
    @patch('pathlib.Path.exists', return_value=True)
    async def test_start_job(self, mock_exists, mock_file):
        """Test starting a job."""
        client = MasumiClient()
        
        result = await client.start_job("test-agent", {"query": "test"})
        
        assert 'job_id' in result
        assert 'payment_id' in result
        assert result['agent_name'] == "test-agent"
        assert result['status'] == 'pending'
    
    @patch('builtins.open', new_callable=mock_open, read_data="""
masumi:
  budget:
    per_request_max: 5.0
agents:
  - name: "expensive-agent"
    max_cost_per_request: 10.0
    enabled: true
""")
    @patch('pathlib.Path.exists', return_value=True)
    async def test_start_job_budget_exceeded(self, mock_exists, mock_file):
        """Test starting a job with budget exceeded."""
        client = MasumiClient()
        
        with pytest.raises(ValueError, match="exceeds budget limit"):
            await client.start_job("expensive-agent", {"query": "test"})
    
    @patch('builtins.open', new_callable=mock_open, read_data="""
masumi: {}
agents: []
""")
    @patch('pathlib.Path.exists', return_value=True)
    async def test_poll_job_status(self, mock_exists, mock_file):
        """Test polling job status."""
        client = MasumiClient()
        
        status = await client.poll_job_status("test_job_123")
        
        assert status['job_id'] == "test_job_123"
        assert status['status'] == 'running'
        assert 'progress' in status
    
    @patch('builtins.open', new_callable=mock_open, read_data="""
masumi: {}
agents: []
""")
    @patch('pathlib.Path.exists', return_value=True)
    async def test_get_job_result(self, mock_exists, mock_file):
        """Test getting job result."""
        client = MasumiClient()
        
        result = await client.get_job_result("test_job_123")
        
        assert result['job_id'] == "test_job_123"
        assert result['status'] == 'completed'
        assert 'result' in result
        assert 'content' in result['result']
    
    @patch('builtins.open', new_callable=mock_open, read_data="""
masumi: {}
agents: []
""")
    @patch('pathlib.Path.exists', return_value=True)
    async def test_wait_for_completion(self, mock_exists, mock_file):
        """Test waiting for job completion."""
        client = MasumiClient()
        
        # Mock poll_job_status to return completed after first call
        call_count = 0
        async def mock_poll(job_id):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return {'status': 'running'}
            return {'status': 'completed'}
        
        client.poll_job_status = mock_poll
        client.get_job_result = AsyncMock(return_value={
            'job_id': 'test_job',
            'status': 'completed',
            'result': {'content': 'Test result'}
        })
        
        result = await client.wait_for_completion("test_job", poll_interval=0.1)
        
        assert result['status'] == 'completed'
        assert call_count == 2
    
    @patch('builtins.open', new_callable=mock_open, read_data="""
masumi: {}
agents: []
""")
    @patch('pathlib.Path.exists', return_value=True)
    async def test_wait_for_completion_timeout(self, mock_exists, mock_file):
        """Test waiting for job completion with timeout."""
        client = MasumiClient()
        
        # Mock poll_job_status to always return running
        client.poll_job_status = AsyncMock(return_value={'status': 'running'})
        
        with pytest.raises(TimeoutError, match="timed out"):
            await client.wait_for_completion("test_job", poll_interval=0.1, timeout=0.2)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])