"""
Unit and integration tests for the Extended Audience Profiles agent.
"""
import os
import pytest
import asyncio
from unittest.mock import Mock, patch, AsyncMock
from extended_audience_profiles.agent import generate_audience_profile, generate_audience_profile_sync


class TestGenerateAudienceProfileUnit:
    """Unit tests with mocked external dependencies."""
    
    @pytest.mark.asyncio
    @patch('extended_audience_profiles.agent.MasumiClient')
    async def test_successful_profile_generation(self, mock_masumi_class):
        """Test successful audience profile generation."""
        # Setup mock
        mock_client = Mock()
        mock_masumi_class.return_value = mock_client
        
        mock_client.check_agent_availability.return_value = True
        mock_client.start_job = AsyncMock(return_value={
            'job_id': 'test_job_123',
            'payment_id': 'payment_456',
            'status': 'pending'
        })
        mock_client.wait_for_completion = AsyncMock(return_value={
            'result': {
                'content': '# Audience Profile\n\nMillennials interested in sustainable fashion...',
                'metadata': {
                    'agent': 'advanced-web-research',
                    'processing_time': 3.5,
                    'cost': 2.0
                }
            }
        })
        
        # Test
        result = await generate_audience_profile(
            "Millennials interested in sustainable fashion",
            "Focus on purchasing behavior"
        )
        
        # Assertions
        assert result['success'] is True
        assert result['audience_description'] == "Millennials interested in sustainable fashion"
        assert result['profile'] is not None
        assert 'Millennials' in result['profile']
        assert result['metadata']['agent'] == 'advanced-web-research'
        assert result['job_id'] == 'test_job_123'
        assert result['error'] is None
    
    @pytest.mark.asyncio
    @patch('extended_audience_profiles.agent.MasumiClient')
    async def test_agent_not_available(self, mock_masumi_class):
        """Test handling when agent is not available."""
        # Setup mock
        mock_client = Mock()
        mock_masumi_class.return_value = mock_client
        
        mock_client.check_agent_availability.return_value = False
        
        # Test
        result = await generate_audience_profile("Test audience")
        
        # Assertions
        assert result['success'] is False
        assert result['profile'] is None
        assert "not available" in result['error']
    
    @pytest.mark.asyncio
    @patch('extended_audience_profiles.agent.MasumiClient')
    async def test_job_failure(self, mock_masumi_class):
        """Test handling of job failures."""
        # Setup mock
        mock_client = Mock()
        mock_masumi_class.return_value = mock_client
        
        mock_client.check_agent_availability.return_value = True
        mock_client.start_job = AsyncMock(side_effect=Exception("Network error"))
        
        # Test
        result = await generate_audience_profile("Test audience")
        
        # Assertions
        assert result['success'] is False
        assert result['profile'] is None
        assert "Network error" in result['error']
    
    def test_sync_wrapper(self):
        """Test synchronous wrapper function."""
        async def mock_coro():
            return {
                'success': True,
                'profile': 'Test profile'
            }
        
        with patch('extended_audience_profiles.agent.generate_audience_profile', return_value=mock_coro()):
            
            result = generate_audience_profile_sync("Test audience")
            
            # Should have created a new event loop and called the async function
            assert isinstance(result, dict)


class TestAgentIntegration:
    """Integration tests with Masumi client."""
    
    @pytest.mark.integration
    async def test_real_masumi_integration(self):
        """Test with real Masumi integration (when available)."""
        # Skip if no API keys
        if not os.getenv("MASUMI_REGISTRY_API_KEY") or not os.getenv("MASUMI_PAYMENT_API_KEY"):
            pytest.skip("Masumi API keys not set - skipping integration test")
        
        result = await generate_audience_profile("Tech-savvy Gen Z consumers")
        
        # Basic assertions - actual results will depend on Masumi Network
        assert isinstance(result, dict)
        assert 'success' in result
        assert 'audience_description' in result


class TestAgentResponseFormat:
    """Tests for response format consistency."""
    
    @pytest.mark.asyncio
    @patch('extended_audience_profiles.agent.MasumiClient')
    async def test_response_structure(self, mock_masumi_class):
        """Test that response always has required fields."""
        # Setup mock
        mock_client = Mock()
        mock_masumi_class.return_value = mock_client
        
        mock_client.check_agent_availability.return_value = True
        mock_client.start_job = AsyncMock(return_value={'job_id': 'test'})
        mock_client.wait_for_completion = AsyncMock(return_value={
            'result': {'content': 'Profile', 'metadata': {}}
        })
        
        result = await generate_audience_profile("Test audience")
        
        # Check required fields exist
        required_fields = ["audience_description", "profile", "success", "error"]
        for field in required_fields:
            assert field in result
        
        # Check types
        assert isinstance(result["audience_description"], str)
        assert isinstance(result["success"], bool)
        assert result["profile"] is None or isinstance(result["profile"], str)
        assert result["error"] is None or isinstance(result["error"], str)
    
    @pytest.mark.asyncio
    @patch('extended_audience_profiles.agent.MasumiClient')
    async def test_various_audience_descriptions(self, mock_masumi_class):
        """Test response format with various audience descriptions."""
        # Setup mock
        mock_client = Mock()
        mock_masumi_class.return_value = mock_client
        
        mock_client.check_agent_availability.return_value = True
        mock_client.start_job = AsyncMock(return_value={'job_id': 'test'})
        mock_client.wait_for_completion = AsyncMock(return_value={
            'result': {'content': 'Profile', 'metadata': {}}
        })
        
        descriptions = [
            "",
            "Simple audience",
            "A very long audience description with lots of details and specifics that goes on and on",
            "Audience with special chars: @#$%^&*()",
            "Audience\nwith\nnewlines"
        ]
        
        for description in descriptions:
            result = await generate_audience_profile(description)
            
            assert result["audience_description"] == description
            assert isinstance(result["success"], bool)


if __name__ == "__main__":
    # Run tests
    pytest.main([__file__, "-v"])