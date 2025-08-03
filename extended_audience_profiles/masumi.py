"""
Framework-agnostic Masumi A2A (Agent-to-Agent) integration module.
Handles all Masumi Network interactions including agent discovery, job management, and payments.
"""
import os
import yaml
import asyncio
import json
from typing import Dict, Any, Optional, List
from pathlib import Path
from masumi import Config, Purchase
import logging
import requests
import secrets

logger = logging.getLogger(__name__)


class MasumiClient:
    """Client for interacting with Masumi Network agents."""
    
    def __init__(self, config_path: str = "config/masumi.yaml"):
        """Initialize MasumiClient with configuration.
        
        Args:
            config_path: Path to masumi.yaml configuration
        """
        self.config_path = config_path
        self.budget_config = {}
        self.agents_config = []
        self.masumi_sdk_config = None
        self._load_config()
        self._validate_agent_availability()
    
    def _load_config(self) -> None:
        """Load configuration from masumi.yaml file."""
        config_file = Path(self.config_path)
        if not config_file.exists():
            raise FileNotFoundError(f"Masumi configuration not found: {self.config_path}")
        
        with open(config_file, 'r') as f:
            config = yaml.safe_load(f)
        
        self.budget_config = config.get('budget', {})
        self.agents_config = config.get('agents', [])
        
        # Initialize Masumi SDK configuration
        self.masumi_sdk_config = Config(
            registry_service_url=os.getenv('MASUMI_REGISTRY_SERVICE_URL'),
            registry_api_key=os.getenv('MASUMI_REGISTRY_API_KEY'),
            payment_service_url=os.getenv('MASUMI_PAYMENT_SERVICE_URL'),
            payment_api_key=os.getenv('MASUMI_PAYMENT_API_KEY')
        )
        
        # No longer tracking spending at instance level - using Ray budget state
    
    async def _make_request(self, method: str, url: str, **kwargs) -> requests.Response:
        """Make HTTP request with unified error handling."""
        timeout = kwargs.pop('timeout', 5.0)
        
        try:
            if method == 'GET':
                response = requests.get(url, timeout=timeout, **kwargs)
            elif method == 'POST':
                response = requests.post(url, timeout=timeout, **kwargs)
            else:
                raise ValueError(f"Unsupported method: {method}")
            
            return response
            
        except requests.exceptions.Timeout:
            raise RuntimeError(f"Request timeout: {url}")
        except requests.exceptions.ConnectionError:
            raise RuntimeError(f"Connection error: {url}")
    
    def _get_agent_endpoint(self, agent_name: str) -> str:
        """Get and validate agent endpoint."""
        agent = self.get_agent_config(agent_name)
        if not agent:
            raise ValueError(f"Agent '{agent_name}' not found")
        if not agent.get('enabled', True):
            raise ValueError(f"Agent '{agent_name}' is disabled")
        endpoint = agent.get('endpoint')
        if not endpoint:
            raise ValueError(f"No endpoint for agent '{agent_name}'")
        return endpoint
    
    def _validate_agent_availability(self) -> None:
        """Validate all configured agents are available at startup."""
        logger.info("Validating agent availability...")
        unavailable = []
        
        for agent in self.get_available_agents():
            try:
                endpoint = agent.get('endpoint')
                if not endpoint:
                    unavailable.append(f"{agent['name']} (no endpoint)")
                    continue
                
                response = requests.get(f"{endpoint.rstrip('/')}/availability", timeout=5.0)
                data = response.json() if response.status_code == 200 else {}
                
                if response.status_code != 200 or data.get('status') != 'available':
                    unavailable.append(f"{agent['name']} (unavailable)")
                else:
                    logger.info(f"Agent '{agent['name']}' is available")
                    
            except Exception as e:
                unavailable.append(f"{agent['name']} ({type(e).__name__})")
                logger.error(f"Error checking '{agent['name']}': {str(e)}")
        
        if unavailable:
            raise RuntimeError(f"Agents not available: {', '.join(unavailable)}")
        
        logger.info("All agents validated successfully")
    
    def get_available_agents(self) -> List[Dict[str, Any]]:
        """Get list of configured agents that are enabled."""
        return [a for a in self.agents_config if a.get('enabled', True)]
    
    def get_agent_config(self, agent_name: str) -> Optional[Dict[str, Any]]:
        """Get configuration for a specific agent."""
        return next((a for a in self.agents_config if a['name'] == agent_name), None)
    
    
    async def start_job(self, agent_name: str, input_data: Dict[str, Any]) -> Dict[str, Any]:
        """Start a job with the specified agent."""
        endpoint = self._get_agent_endpoint(agent_name)
        identifier = secrets.token_hex(13)
        
        payload = {
            "input_data": input_data,
            "identifier_from_purchaser": identifier
        }
        
        response = await self._make_request(
            'POST',
            f"{endpoint.rstrip('/')}/start_job",
            json=payload,
            timeout=10.0
        )
        
        if response.status_code == 400:
            raise ValueError(f"Invalid input data: {response.text}")
        elif response.status_code != 200:
            raise RuntimeError(f"Failed to start job: HTTP {response.status_code}")
        
        data = response.json()
        if 'job_id' not in data:
            raise RuntimeError(f"Invalid response: missing job_id")
        
        # Normalize response
        if 'blockchainIdentifier' in data:
            data['payment_id'] = data['blockchainIdentifier']
        data['identifier_from_purchaser'] = identifier
        
        logger.info(f"Started job {data['job_id']} for agent '{agent_name}'")
        return data
    
    async def create_purchase(self, job_response: Dict[str, Any], input_data: Dict[str, Any], agent_name: str) -> Dict[str, Any]:
        """Create a purchase for a started job using Masumi SDK."""
        try:
            purchase = Purchase(
                config=self.masumi_sdk_config,
                blockchain_identifier=job_response['blockchainIdentifier'],
                seller_vkey=job_response['sellerVKey'],
                agent_identifier=job_response['agentIdentifier'],
                identifier_from_purchaser=job_response['identifierFromPurchaser'],
                pay_by_time=job_response['payByTime'],
                submit_result_time=job_response['submitResultTime'],
                unlock_time=job_response['unlockTime'],
                external_dispute_unlock_time=job_response['externalDisputeUnlockTime'],
                network=self.budget_config.get('network', 'Preprod'),
                input_data=input_data
            )
            
            result = await purchase.create_purchase_request()
            
            # Calculate cost from the purchase result, not job response
            # Convert from lovelace to USDM: 1 USDM = 1,000,000 lovelace
            paid_funds = result.get('data', {}).get('PaidFunds', [])
            cost = 0.0
            if paid_funds and paid_funds[0].get('amount'):
                cost = int(paid_funds[0].get('amount', 0)) / 1_000_000
            
            # Debug logging for cost calculation
            logger.info(f"Cost calculation for {agent_name}:")
            logger.info(f"  - PaidFunds from purchase result: {paid_funds}")
            logger.info(f"  - calculated cost: {cost} USDM")
            
            return {
                'success': True,
                'job_id': job_response['job_id'],
                'blockchain_identifier': job_response['blockchainIdentifier'],
                'purchase_result': result,
                'actual_cost': cost
            }
            
        except Exception as e:
            raise RuntimeError(f"Failed to create purchase: {str(e)}")
    
    async def poll_job_status(self, job_id: str, agent_name: str) -> Dict[str, Any]:
        """Poll the status of a job."""
        endpoint = self._get_agent_endpoint(agent_name)
        
        response = await self._make_request(
            'GET',
            f"{endpoint.rstrip('/')}/status",
            params={"job_id": job_id}
        )
        
        if response.status_code == 404:
            raise ValueError(f"Job {job_id} not found")
        elif response.status_code != 200:
            raise RuntimeError(f"Failed to get status: HTTP {response.status_code}")
        
        data = response.json()
        data.setdefault('job_id', job_id)
        
        # Debug logging to see all fields in the response
        logger.debug(f"Status response for job {job_id} from agent {agent_name}:")
        logger.debug(f"  Status: {data.get('status', 'unknown')}")
        logger.debug(f"  Full response fields: {list(data.keys())}")
        
        # Log specific fields that might contain hashes
        if 'hash' in data:
            logger.info(f"  Hash found: {data['hash']}")
        if 'input_hash' in data:
            logger.info(f"  Input hash found: {data['input_hash']}")
        if 'output_hash' in data:
            logger.info(f"  Output hash found: {data['output_hash']}")
        if 'decision_log_hash' in data:
            logger.info(f"  Decision log hash found: {data['decision_log_hash']}")
        
        # Log any field containing 'hash' in its name
        hash_fields = {k: v for k, v in data.items() if 'hash' in k.lower()}
        if hash_fields:
            logger.info(f"  Hash-related fields: {hash_fields}")
        
        # If status is completed, log additional details
        if data.get('status') == 'completed':
            logger.debug(f"  Job completed - checking for final hash fields")
            logger.debug(f"  All response data: {json.dumps(data, indent=2)}")
        
        return data
    
    async def wait_for_completion(self, job_id: str, agent_name: str, poll_interval: float = 2.0, timeout: float = 300.0) -> Dict[str, Any]:
        """Wait for a job to complete, polling periodically."""
        start_time = asyncio.get_event_loop().time()
        
        while True:
            status = await self.poll_job_status(job_id, agent_name)
            
            if status['status'] == 'completed':
                return status
            elif status['status'] == 'failed':
                error = status.get('result', status.get('message', 'Unknown error'))
                raise Exception(f"Job {job_id} failed: {error}")
            
            if asyncio.get_event_loop().time() - start_time > timeout:
                raise TimeoutError(f"Job {job_id} timed out after {timeout}s")
            
            await asyncio.sleep(poll_interval)
    
    async def get_agent_schema(self, agent_name: str) -> Dict[str, Any]:
        """Get the input schema for a specific agent."""
        endpoint = self._get_agent_endpoint(agent_name)
        
        response = await self._make_request(
            'GET',
            f"{endpoint.rstrip('/')}/input_schema"
        )
        
        if response.status_code != 200:
            raise RuntimeError(f"Failed to get schema: HTTP {response.status_code}")
        
        # Transform to JSON Schema format
        api_response = response.json()
        properties = {}
        required = []
        
        for item in api_response.get('input_data', []):
            field_id = item.get('id')
            if not field_id:
                continue
            
            # Build property schema
            prop = {
                'type': self._map_type(item.get('type', 'string')),
                'description': item.get('name', field_id)
            }
            
            # Add constraints
            if placeholder := item.get('data', {}).get('placeholder'):
                prop['example'] = placeholder
            
            if item.get('type') == 'option' and 'values' in item.get('data', {}):
                prop['enum'] = item['data']['values']
            
            # Check if required
            validations = item.get('validations', [])
            is_optional = any(v.get('validation') == 'optional' and v.get('value') == 'true' 
                            for v in validations)
            
            if not is_optional:
                required.append(field_id)
            
            properties[field_id] = prop
        
        return {
            'type': 'object',
            'properties': properties,
            'required': required
        }
    
    def _map_type(self, masumi_type: str) -> str:
        """Map Masumi types to JSON Schema types."""
        mapping = {
            'string': 'string',
            'textarea': 'string',
            'number': 'number',
            'integer': 'integer',
            'option': 'string',
            'boolean': 'boolean',
            'array': 'array',
            'object': 'object'
        }
        return mapping.get(masumi_type.lower(), 'string')
    
