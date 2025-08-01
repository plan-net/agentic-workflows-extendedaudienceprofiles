"""
Framework-agnostic Masumi A2A (Agent-to-Agent) integration module.
Handles all Masumi Network interactions including agent discovery, job management, and payments.
"""
import os
import yaml
import asyncio
from typing import Dict, Any, Optional, List
from pathlib import Path
from masumi import Config, Agent, Payment, Purchase
import logging
import requests
import secrets

logger = logging.getLogger(__name__)


class MasumiClient:
    """Client for interacting with Masumi Network agents."""
    
    def __init__(self, config_path: str = "config/masumi.yaml"):
        """Initialize MasumiClient with configuration."""
        self.config_path = config_path
        self.budget_config = {}
        self.agents_config = []
        self.masumi_sdk_config = None
        self._spending_tracker = {}  # Track spending per agent
        self._total_spent = 0.0  # Track total spending
        self._load_config()
        self._validate_agent_availability()  # Validate all agents are available at startup
    
    def _load_config(self) -> None:
        """Load configuration from masumi.yaml file."""
        config_file = Path(self.config_path)
        if not config_file.exists():
            raise FileNotFoundError(f"Masumi configuration not found: {self.config_path}")
        
        with open(config_file, 'r') as f:
            config = yaml.safe_load(f)
        
        self.budget_config = config.get('budget', {})
        self.agents_config = config.get('agents', [])
        
        # Get service URLs and API keys from environment variables
        registry_service_url = os.getenv('MASUMI_REGISTRY_SERVICE_URL')
        registry_api_key = os.getenv('MASUMI_REGISTRY_API_KEY')
        payment_service_url = os.getenv('MASUMI_PAYMENT_SERVICE_URL')
        payment_api_key = os.getenv('MASUMI_PAYMENT_API_KEY')
        
        # Initialize Masumi SDK configuration
        self.masumi_sdk_config = Config(
            registry_service_url=registry_service_url,
            registry_api_key=registry_api_key,
            payment_service_url=payment_service_url,
            payment_api_key=payment_api_key
        )
        
        # Initialize spending tracker for each agent
        for agent in self.agents_config:
            self._spending_tracker[agent['name']] = 0.0
    
    def _validate_agent_availability(self) -> None:
        """Validate all configured agents are available at startup."""
        logger.info("Validating agent availability at startup...")
        unavailable_agents = []
        
        for agent in self.agents_config:
            if not agent.get('enabled', True):
                logger.info(f"Agent '{agent['name']}' is disabled in configuration")
                continue
                
            endpoint = agent.get('endpoint')
            if not endpoint:
                logger.warning(f"Agent '{agent['name']}' has no endpoint configured")
                unavailable_agents.append(f"{agent['name']} (no endpoint)")
                continue
            
            # Check availability endpoint
            availability_url = f"{endpoint.rstrip('/')}/availability"
            try:
                response = requests.get(availability_url, timeout=5.0)
                if response.status_code == 200:
                    data = response.json()
                    if data.get('status') != 'available':
                        unavailable_agents.append(f"{agent['name']} (status: {data.get('status', 'unknown')})")
                        logger.error(f"Agent '{agent['name']}' is not available")
                    else:
                        logger.info(f"Agent '{agent['name']}' is available")
                else:
                    unavailable_agents.append(f"{agent['name']} (HTTP {response.status_code})")
                    logger.error(f"Agent '{agent['name']}' returned status {response.status_code}")
            except requests.exceptions.Timeout:
                unavailable_agents.append(f"{agent['name']} (timeout)")
                logger.error(f"Timeout checking availability for agent '{agent['name']}'")
            except requests.exceptions.ConnectionError:
                unavailable_agents.append(f"{agent['name']} (connection error)")
                logger.error(f"Connection error checking availability for agent '{agent['name']}'")
            except Exception as e:
                unavailable_agents.append(f"{agent['name']} (error: {str(e)})")
                logger.error(f"Error checking availability for agent '{agent['name']}': {str(e)}")
        
        if unavailable_agents:
            error_msg = f"System startup failed. The following agents are not available: {', '.join(unavailable_agents)}"
            logger.error(error_msg)
            raise RuntimeError(error_msg)
        
        logger.info("All agents validated successfully - system is healthy")
    
    def get_available_agents(self) -> List[Dict[str, Any]]:
        """Get list of configured agents that are enabled."""
        return [agent for agent in self.agents_config if agent.get('enabled', True)]
    
    def get_agent_config(self, agent_name: str) -> Optional[Dict[str, Any]]:
        """Get configuration for a specific agent."""
        for agent in self.agents_config:
            if agent['name'] == agent_name:
                return agent
        return None
    
    def get_remaining_budget(self) -> float:
        """Get remaining total budget."""
        total_budget = self.budget_config.get('total_budget', float('inf'))
        return total_budget - self._total_spent
    
    def get_agent_remaining_budget(self, agent_name: str) -> float:
        """Get remaining budget for a specific agent."""
        agent_config = self.get_agent_config(agent_name)
        if not agent_config:
            return 0.0
        
        # Check agent-specific limit
        agent_max = agent_config.get('max_total_spend', float('inf'))
        agent_spent = self._spending_tracker.get(agent_name, 0.0)
        agent_remaining = agent_max - agent_spent
        
        # Also check against total budget
        total_remaining = self.get_remaining_budget()
        
        # Return the minimum of the two
        return min(agent_remaining, total_remaining)
    
    def get_agent_spending(self, agent_name: str) -> float:
        """Get total amount spent on a specific agent."""
        return self._spending_tracker.get(agent_name, 0.0)
    
    def get_total_spending(self) -> float:
        """Get total amount spent across all agents."""
        return self._total_spent
    
    def _record_spending(self, agent_name: str, amount: float) -> None:
        """Record spending for an agent."""
        self._spending_tracker[agent_name] = self._spending_tracker.get(agent_name, 0.0) + amount
        self._total_spent += amount
        logger.info(f"Recorded spending of {amount} for agent {agent_name}. "
                   f"Agent total: {self._spending_tracker[agent_name]}, "
                   f"Overall total: {self._total_spent}")
    
    def _transform_input_to_api_format(self, input_data: Dict[str, Any]) -> List[Dict[str, str]]:
        """
        Transform dictionary input to Masumi API format.
        
        Args:
            input_data: Dict with key-value pairs
            
        Returns:
            List of dicts with 'key' and 'value' fields
        """
        return [{"key": k, "value": str(v)} for k, v in input_data.items()]
    
    async def start_job(self, agent_name: str, input_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Start a job with the specified agent via the /start_job endpoint.
        
        Args:
            agent_name: Name of the agent to use
            input_data: Input data for the agent (as dict)
            
        Returns:
            Dict containing job_id and payment_id from the API
        """
        # Get agent configuration
        agent_config = self.get_agent_config(agent_name)
        if not agent_config:
            raise ValueError(f"Agent '{agent_name}' not found in configuration")
        
        if not agent_config.get('enabled', True):
            raise ValueError(f"Agent '{agent_name}' is disabled")
        
        endpoint = agent_config.get('endpoint')
        if not endpoint:
            raise ValueError(f"No endpoint configured for agent '{agent_name}'")
        
        # Construct start job URL
        start_job_url = f"{endpoint.rstrip('/')}/start_job"
        
        # Generate a random 26-char hex identifier for this purchase (Masumi SDK requirement)
        identifier_from_purchaser = secrets.token_hex(13)  # 13 bytes = 26 hex characters
        
        # Transform input to API format
        # Based on the API error, it expects input_data as a dict, not array
        api_payload = {
            "input_data": input_data,  # Send as dict directly
            "identifier_from_purchaser": identifier_from_purchaser
        }
        
        logger.info(f"Generated purchase identifier: {identifier_from_purchaser}")
        
        try:
            # Make POST request to start job
            response = requests.post(start_job_url, json=api_payload, timeout=10.0)
            
            if response.status_code == 400:
                logger.error(f"Invalid input data for agent '{agent_name}': {response.text}")
                raise ValueError(f"Invalid input data: {response.text}")
            elif response.status_code != 200:
                logger.error(f"Failed to start job for agent '{agent_name}': HTTP {response.status_code}, Response: {response.text}")
                raise RuntimeError(f"Failed to start job: HTTP {response.status_code} - {response.text}")
            
            # Parse the response
            data = response.json()
            
            # Log the actual response for debugging
            logger.info(f"Raw response from agent '{agent_name}': {data}")
            
            # Validate response contains required fields
            if 'job_id' not in data:
                raise RuntimeError(f"Invalid response from agent '{agent_name}': missing job_id. Got: {data}")
            
            # Map blockchainIdentifier to payment_id for consistency
            if 'blockchainIdentifier' in data and 'payment_id' not in data:
                data['payment_id'] = data['blockchainIdentifier']
            
            # Add the identifier to the response for use in purchase
            data['identifier_from_purchaser'] = identifier_from_purchaser
            
            logger.info(f"Started job {data['job_id']} for agent '{agent_name}'")
            return data
            
        except requests.exceptions.Timeout:
            logger.error(f"Timeout starting job for agent '{agent_name}'")
            raise RuntimeError(f"Timeout starting job for agent '{agent_name}'")
        except requests.exceptions.ConnectionError as e:
            logger.error(f"Connection error starting job for agent '{agent_name}': {str(e)}")
            raise RuntimeError(f"Connection error starting job for agent '{agent_name}'")
        except ValueError as e:
            # Re-raise ValueError (validation errors)
            raise
        except Exception as e:
            logger.error(f"Unexpected error starting job for agent '{agent_name}': {str(e)}")
            raise
    
    async def create_purchase(self, job_response: Dict[str, Any], input_data: Dict[str, Any], agent_name: str) -> Dict[str, Any]:
        """
        Create a purchase for a started job using Masumi SDK.
        
        Args:
            job_response: Full response from start_job containing all payment details
            input_data: Original input data sent to the agent
            agent_name: Name of the agent (for tracking)
            
        Returns:
            Dict containing purchase confirmation details
        """
        try:
            # Extract network from config (default to Preprod for testing)
            network = self.budget_config.get('network', 'Preprod')
            
            # Create purchase using all fields from job response
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
                network=network,
                input_data=input_data
            )
            
            # Execute the purchase
            result = await purchase.create_purchase_request()
            
            logger.info(f"Created purchase for job {job_response['job_id']} with blockchain ID {job_response['blockchainIdentifier']}")
            
            # Calculate actual cost from amounts (convert from units to dollars)
            # Assuming units are microdollars (1 dollar = 1,000,000 units)
            amounts = job_response.get('amounts', [])
            actual_cost = 0.0
            if amounts and len(amounts) > 0:
                actual_cost = amounts[0].get('amount', 0) / 1_000_000
            
            # Record the actual spending
            self._record_spending(agent_name, actual_cost)
            
            return {
                'success': True,
                'job_id': job_response['job_id'],
                'blockchain_identifier': job_response['blockchainIdentifier'],
                'purchase_result': result,
                'actual_cost': actual_cost
            }
            
        except Exception as e:
            logger.error(f"Failed to create purchase for job {job_response.get('job_id', 'unknown')}: {str(e)}")
            raise RuntimeError(f"Failed to create purchase: {str(e)}")
    
    async def poll_job_status(self, job_id: str, agent_name: str) -> Dict[str, Any]:
        """
        Poll the status of a job via the /status endpoint.
        
        Args:
            job_id: The job identifier
            agent_name: The agent name to poll
            
        Returns:
            Dict containing status and result (if completed)
        """
        # Look up the endpoint from config
        agent_config = self.get_agent_config(agent_name)
        if not agent_config:
            raise ValueError(f"Agent '{agent_name}' not found in configuration")
        endpoint = agent_config['endpoint']
        
        # Construct status URL
        status_url = f"{endpoint.rstrip('/')}/status"
        params = {"job_id": job_id}
        
        try:
            # Make GET request to check status
            response = requests.get(status_url, params=params, timeout=5.0)
            
            if response.status_code == 404:
                logger.error(f"Job {job_id} not found on agent '{agent_name}'")
                raise ValueError(f"Job {job_id} not found")
            elif response.status_code != 200:
                logger.error(f"Failed to get status for job {job_id}: HTTP {response.status_code}")
                raise RuntimeError(f"Failed to get status: HTTP {response.status_code}")
            
            # Parse the response
            data = response.json()
            
            # Ensure job_id is in response
            if 'job_id' not in data:
                data['job_id'] = job_id
            
            logger.debug(f"Job {job_id} status: {data.get('status', 'unknown')}")
            return data
            
        except requests.exceptions.Timeout:
            logger.error(f"Timeout getting status for job {job_id}")
            raise RuntimeError(f"Timeout getting status for job {job_id}")
        except requests.exceptions.ConnectionError as e:
            logger.error(f"Connection error getting status for job {job_id}: {str(e)}")
            raise RuntimeError(f"Connection error getting status for job {job_id}")
        except Exception as e:
            logger.error(f"Unexpected error getting status for job {job_id}: {str(e)}")
            raise
    
    async def get_job_result(self, job_id: str, agent_name: str) -> Dict[str, Any]:
        """
        Get the result of a completed job.
        
        Since the /status endpoint returns the result when status is 'completed',
        this method simply calls poll_job_status and ensures the job is completed.
        
        Args:
            job_id: The job identifier
            agent_name: The agent name running the job
            
        Returns:
            Dict containing the job result
        """
        status_response = await self.poll_job_status(job_id, agent_name)
        
        if status_response.get('status') != 'completed':
            raise RuntimeError(f"Job {job_id} is not completed yet. Status: {status_response.get('status')}")
        
        # Return the full status response which includes the result
        return status_response
    
    def _transform_schema_response(self, api_response: Dict[str, Any]) -> Dict[str, Any]:
        """
        Transform the Masumi API schema response into a JSON Schema format.
        
        Args:
            api_response: Response from /input_schema endpoint
            
        Returns:
            JSON Schema formatted dict
        """
        # Extract input_data array from API response
        input_data = api_response.get('input_data', [])
        
        # Build JSON Schema properties
        properties = {}
        required = []
        
        for item in input_data:
            field_id = item.get('id')
            field_name = item.get('name', field_id)
            field_type = item.get('type', 'string')
            field_data = item.get('data', {})
            validations = item.get('validations', [])
            
            if not field_id:
                continue
            
            # Check if field is optional
            is_optional = any(v.get('validation') == 'optional' and v.get('value') == 'true' 
                            for v in validations)
            
            # Map Masumi types to JSON Schema types
            type_mapping = {
                'string': 'string',
                'textarea': 'string',
                'number': 'number',
                'integer': 'integer',
                'option': 'string',  # For select/dropdown fields
                'boolean': 'boolean',
                'array': 'array',
                'object': 'object'
            }
            
            schema_type = type_mapping.get(field_type.lower(), 'string')
            
            # Build property schema
            property_schema = {
                'type': schema_type,
                'description': field_name
            }
            
            # Add placeholder as example if available
            placeholder = field_data.get('placeholder')
            if placeholder:
                property_schema['example'] = placeholder
            
            # Add enum values for option fields
            if field_type == 'option' and 'values' in field_data:
                property_schema['enum'] = field_data['values']
            
            # Add validation constraints
            for validation in validations:
                val_type = validation.get('validation')
                val_value = validation.get('value')
                
                if val_type == 'min':
                    if schema_type == 'number':
                        property_schema['minimum'] = float(val_value)
                    elif schema_type == 'string':
                        property_schema['minLength'] = int(val_value)
                elif val_type == 'max':
                    if schema_type == 'number':
                        property_schema['maximum'] = float(val_value)
                    elif schema_type == 'string':
                        property_schema['maxLength'] = int(val_value)
            
            properties[field_id] = property_schema
            
            # Add to required list if not optional
            if not is_optional:
                required.append(field_id)
        
        return {
            'type': 'object',
            'properties': properties,
            'required': required
        }
    
    async def get_agent_schema(self, agent_name: str) -> Dict[str, Any]:
        """
        Get the input schema for a specific agent from its /input_schema endpoint.
        
        Args:
            agent_name: Name of the agent
            
        Returns:
            Dict containing the input schema in JSON Schema format
        """
        agent_config = self.get_agent_config(agent_name)
        if not agent_config:
            raise ValueError(f"Agent '{agent_name}' not found in configuration")
        
        endpoint = agent_config.get('endpoint')
        if not endpoint:
            raise ValueError(f"No endpoint configured for agent '{agent_name}'")
        
        # Construct schema URL
        schema_url = f"{endpoint.rstrip('/')}/input_schema"
        
        try:
            # Make GET request to retrieve schema
            response = requests.get(schema_url, timeout=5.0)
            
            if response.status_code != 200:
                logger.error(f"Failed to get schema for agent '{agent_name}': HTTP {response.status_code}")
                raise RuntimeError(f"Failed to get schema: HTTP {response.status_code}")
            
            # Parse the response
            data = response.json()
            
            # Transform to standard JSON Schema format
            return self._transform_schema_response(data)
            
        except requests.exceptions.Timeout:
            logger.error(f"Timeout getting schema for agent '{agent_name}'")
            raise RuntimeError(f"Timeout getting schema for agent '{agent_name}'")
        except requests.exceptions.ConnectionError as e:
            logger.error(f"Connection error getting schema for agent '{agent_name}': {str(e)}")
            raise RuntimeError(f"Connection error getting schema for agent '{agent_name}'")
        except ValueError as e:
            logger.error(f"Invalid JSON response from agent '{agent_name}': {str(e)}")
            raise RuntimeError(f"Invalid schema response from agent '{agent_name}'")
        except Exception as e:
            logger.error(f"Unexpected error getting schema for agent '{agent_name}': {str(e)}")
            raise
    
    async def wait_for_completion(self, job_id: str, agent_name: str, poll_interval: float = 2.0, timeout: float = 300.0) -> Dict[str, Any]:
        """
        Wait for a job to complete, polling periodically.
        
        Args:
            job_id: The job identifier
            agent_name: The agent name running the job
            poll_interval: Seconds between status checks
            timeout: Maximum seconds to wait
            
        Returns:
            Dict containing the final job result
        """
        start_time = asyncio.get_event_loop().time()
        
        while True:
            status = await self.poll_job_status(job_id, agent_name)
            
            if status['status'] == 'completed':
                # Result is already in the status response
                return status
            elif status['status'] == 'failed':
                error_msg = status.get('result', status.get('message', 'Unknown error'))
                raise Exception(f"Job {job_id} failed: {error_msg}")
            
            elapsed = asyncio.get_event_loop().time() - start_time
            if elapsed > timeout:
                raise TimeoutError(f"Job {job_id} timed out after {timeout} seconds")
            
            await asyncio.sleep(poll_interval)
    
    def get_budget_summary(self) -> Dict[str, Any]:
        """Get a summary of budget usage."""
        total_budget = self.budget_config.get('total_budget', float('inf'))
        summary = {
            'total_budget': total_budget,
            'total_spent': self._total_spent,
            'total_remaining': self.get_remaining_budget(),
            'agents': {}
        }
        
        for agent in self.agents_config:
            agent_name = agent['name']
            agent_max = agent.get('max_total_spend', float('inf'))
            agent_spent = self.get_agent_spending(agent_name)
            
            summary['agents'][agent_name] = {
                'max_budget': agent_max if agent_max != float('inf') else 'unlimited',
                'spent': agent_spent,
                'remaining': self.get_agent_remaining_budget(agent_name),
                'enabled': agent.get('enabled', True)
            }
        
        return summary