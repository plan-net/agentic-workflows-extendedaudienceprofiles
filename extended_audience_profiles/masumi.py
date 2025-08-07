"""
Framework-agnostic Masumi A2A (Agent-to-Agent) integration module.
Handles all Masumi Network interactions including agent discovery, job management, and payments.
"""
import os
import yaml
import asyncio
import json
from typing import Dict, Any, Optional, List, Tuple
from pathlib import Path
from masumi import Config, Purchase
import logging
import requests
import secrets
from .exceptions import AgentNotFoundError, MasumiNetworkError

logger = logging.getLogger(__name__)


class MasumiClient:
    """Client for interacting with Masumi Network agents."""
    
    def __init__(self, config_path: str = "config/masumi.yaml"):
        """Initialize MasumiClient with configuration.
        
        Args:
            config_path: Path to masumi.yaml configuration
        """
        script_dir = Path(__file__).parent.parent
        self.config_path = str(script_dir / config_path)
        # self.config_path = config_path
        self.budget_config = {}
        self.agents_config = []
        self.masumi_sdk_config = None
        self._load_config()
        self._validate_agent_availability()
    
    def _load_config(self) -> None:
        """
        Load configuration from masumi.yaml file.
        
        Reads the masumi.yaml configuration file and extracts:
        - Budget configuration settings
        - Available agents and their endpoints
        - Masumi SDK configuration from environment variables
        
        Raises:
            FileNotFoundError: If configuration file doesn't exist
        """
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
        """
        Make HTTP request with unified error handling.
        
        Provides a centralized method for making HTTP requests with consistent
        timeout handling and error management. Supports GET and POST methods.
        
        Args:
            method: HTTP method ('GET' or 'POST')
            url: URL to request
            **kwargs: Additional arguments passed to requests
            
        Returns:
            Response object from the request
            
        Raises:
            RuntimeError: On timeout or connection errors
            ValueError: If unsupported HTTP method is provided
        """
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
        """
        Get and validate agent endpoint.
        
        Retrieves the endpoint URL for a specific agent and validates that
        the agent exists, is enabled, and has a valid endpoint configured.
        
        Args:
            agent_name: Name of the agent to get endpoint for
            
        Returns:
            Agent endpoint URL
            
        Raises:
            ValueError: If agent not found, disabled, or missing endpoint
        """
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
        """
        Validate all configured agents are available at startup.
        
        Performs a health check on all configured agents by calling their
        availability endpoint. This ensures all agents are reachable and
        ready to accept jobs before the service starts processing requests.
        
        Raises:
            RuntimeError: If any configured agents are unavailable
        """
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
                    
            except (OSError, requests.RequestException) as e:
                unavailable.append(f"{agent['name']} (network error)")
                logger.error(f"Network error checking '{agent['name']}': {str(e)}")
            except Exception as e:
                unavailable.append(f"{agent['name']} ({type(e).__name__})")
                logger.error(f"Unexpected error checking '{agent['name']}': {str(e)}")
        
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
        
        # Log payload for debugging
        logger.debug(f"Sending payload to {agent_name}: {json.dumps(payload, indent=2)}")
        
        response = await self._make_request(
            'POST',
            f"{endpoint.rstrip('/')}/start_job",
            json=payload,
            timeout=10.0
        )
        
        if response.status_code == 400:
            # Log the full error details
            logger.error(f"API validation error for {agent_name}:")
            logger.error(f"  Status: {response.status_code}")
            logger.error(f"  Response: {response.text}")
            logger.error(f"  Sent data: {json.dumps(input_data, indent=2)}")
            raise ValueError(f"Invalid input data: {response.text}")
        elif response.status_code != 200:
            logger.error(f"API error for {agent_name}: HTTP {response.status_code}, Response: {response.text}")
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
            
        except (OSError, requests.RequestException) as e:
            raise MasumiNetworkError("create purchase", agent_name, e)
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
            
            # Determine the correct type for this field
            field_type = item.get('type', 'string')
            
            # Get validations for all fields
            validations = item.get('validations', [])
            
            # For option fields, check if it should be an array
            if field_type == 'option':
                data = item.get('data', {})
                
                # Check for explicit array validation
                has_array_validation = any(
                    v.get('validation') == 'array' or 
                    v.get('validation') == 'multiple' or
                    v.get('type') == 'array'
                    for v in validations
                )
                
                # Check for multi-select indicators in data
                is_multiselect = (
                    data.get('multiple') == True or
                    data.get('multiselect') == True or
                    data.get('type') == 'multiselect' or
                    # Also check if the field expects an array in its structure
                    isinstance(data.get('default'), list) or
                    isinstance(data.get('value'), list)
                )
                
                # Check field naming conventions (generalized heuristic)
                field_suggests_multiple = (
                    field_id.endswith('s') and not field_id.endswith('ss') or
                    'multiple' in field_id.lower() or
                    'options' in field_id.lower() or
                    'selections' in field_id.lower()
                )
                
                # If any indicator suggests this is a multi-select, use array type
                if has_array_validation or is_multiselect or field_suggests_multiple:
                    schema_type = 'array'
                else:
                    schema_type = 'string'
            else:
                schema_type = self._map_type(field_type)
            
            # Build property schema
            prop = {
                'type': schema_type,
                'description': item.get('name', field_id)
            }
            
            # Store original type for reference
            prop['_original_type'] = field_type
            
            # Add constraints
            if placeholder := item.get('data', {}).get('placeholder'):
                prop['example'] = placeholder
            
            # For option fields, handle enum values
            if field_type == 'option' and 'values' in item.get('data', {}):
                if schema_type == 'array':
                    # For array type, enum defines the allowed items
                    prop['items'] = {
                        'type': 'string',
                        'enum': item['data']['values']
                    }
                else:
                    # For string type, enum defines the allowed values
                    prop['enum'] = item['data']['values']
            
            # Check if required
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
        """
        Map Masumi types to JSON Schema types.
        
        Converts Masumi-specific field types to standard JSON Schema types
        for compatibility with OpenAI function calling format.
        
        Args:
            masumi_type: Masumi field type (e.g., 'string', 'textarea', 'option')
            
        Returns:
            Corresponding JSON Schema type (defaults to 'string' if unknown)
        """
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
    
    def validate_input_against_schema(self, input_data: Dict[str, Any], schema: Dict[str, Any]) -> Tuple[bool, List[str], Dict[str, Any]]:
        """
        Validate input data against a JSON schema.
        
        Args:
            input_data: The input data to validate
            schema: The JSON schema to validate against
            
        Returns:
            Tuple of (is_valid, errors, transformed_data)
        """
        errors = []
        transformed_data = input_data.copy()
        
        # Check required fields
        for field in schema.get('required', []):
            if field not in input_data:
                errors.append(f"Missing required field: '{field}'")
        
        # Validate and transform each field
        properties = schema.get('properties', {})
        for field, value in input_data.items():
            if field not in properties:
                # Field not in schema - could be extra field
                continue
                
            field_schema = properties[field]
            expected_type = field_schema.get('type')
            original_type = field_schema.get('_original_type')
            
            # Special handling for option fields that need index transformation
            if original_type == 'option' and 'enum' in field_schema:
                # Check if value is a string that needs to be converted to index
                if isinstance(value, str) and value in field_schema['enum']:
                    # Convert enum value to its index
                    index = field_schema['enum'].index(value)
                    # Option fields always need to be arrays of indices
                    transformed_data[field] = [index]
                    logger.debug(f"Transformed '{field}' from enum value '{value}' to index array [{index}]")
                    continue
                elif isinstance(value, list) and len(value) > 0 and isinstance(value[0], str):
                    # Handle array of enum values
                    indices = []
                    for v in value:
                        if v in field_schema['enum']:
                            indices.append(field_schema['enum'].index(v))
                        else:
                            errors.append(f"Invalid enum value '{v}' for field '{field}'")
                    if not errors:
                        transformed_data[field] = indices
                        logger.debug(f"Transformed '{field}' from enum values {value} to indices {indices}")
                    continue
            
            # For array fields with enum items
            if expected_type == 'array' and 'items' in field_schema and 'enum' in field_schema.get('items', {}):
                enum_values = field_schema['items']['enum']
                if isinstance(value, str) and value in enum_values:
                    # Convert single enum value to array with index
                    index = enum_values.index(value)
                    transformed_data[field] = [index]
                    logger.debug(f"Transformed '{field}' from enum value '{value}' to index array [{index}]")
                    continue
                elif isinstance(value, list):
                    # Convert array of enum values to indices
                    indices = []
                    for v in value:
                        if isinstance(v, str) and v in enum_values:
                            indices.append(enum_values.index(v))
                        elif isinstance(v, int):
                            # Already an index
                            indices.append(v)
                        else:
                            errors.append(f"Invalid enum value '{v}' for field '{field}'")
                    if not errors:
                        transformed_data[field] = indices
                        logger.debug(f"Transformed '{field}' from enum values {value} to indices {indices}")
                    continue
            
            # Type validation and transformation
            if expected_type == 'array':
                if not isinstance(value, list):
                    # For arrays, wrap non-list values in a list
                    if value is None:
                        # Skip None values for optional fields
                        if field not in schema.get('required', []):
                            continue
                        else:
                            errors.append(f"Required field '{field}' cannot be null")
                    else:
                        # Convert single value to array
                        transformed_data[field] = [value]
                        logger.debug(f"Transformed field '{field}' from {type(value).__name__} to array: {value} -> [{value}]")
            
            elif expected_type == 'number' or expected_type == 'integer':
                if not isinstance(value, (int, float)):
                    try:
                        transformed_data[field] = float(value) if expected_type == 'number' else int(value)
                    except (ValueError, TypeError):
                        errors.append(f"Field '{field}' must be a {expected_type}, got {type(value).__name__}")
            
            elif expected_type == 'boolean':
                if not isinstance(value, bool):
                    if isinstance(value, str):
                        transformed_data[field] = value.lower() in ('true', 'yes', '1')
                    else:
                        errors.append(f"Field '{field}' must be a boolean, got {type(value).__name__}")
            
            elif expected_type == 'string':
                if not isinstance(value, str):
                    transformed_data[field] = str(value)
            
            # Enum validation (for non-option fields)
            if 'enum' in field_schema and original_type != 'option' and value not in field_schema['enum']:
                errors.append(f"Field '{field}' must be one of {field_schema['enum']}, got '{value}'")
        
        return len(errors) == 0, errors, transformed_data
    
    def build_schema_example(self, schema: Dict[str, Any]) -> Dict[str, Any]:
        """
        Build an example input based on schema alone.
        
        Args:
            schema: The JSON schema
            
        Returns:
            Example input dictionary
        """
        example = {}
        properties = schema.get('properties', {})
        required_fields = schema.get('required', [])
        
        for field, field_schema in properties.items():
            # Always include required fields, optionally include others for clarity
            if field in required_fields or len(properties) <= 10:  # Include all if not too many
                # For array types with enum items, provide a single-item array example
                if field_schema.get('type') == 'array' and 'items' in field_schema:
                    items_schema = field_schema['items']
                    if 'enum' in items_schema and items_schema['enum']:
                        example[field] = [items_schema['enum'][0]]
                    else:
                        example[field] = ['example']
                else:
                    example[field] = self._get_default_for_type(field_schema)
        
        return example
    
    def _get_default_for_type(self, field_schema: Dict[str, Any]) -> Any:
        """Get a default value for a field based on its schema."""
        field_type = field_schema.get('type')
        original_type = field_schema.get('_original_type')
        
        # Use provided example or placeholder first
        if 'example' in field_schema:
            return field_schema['example']
        if 'placeholder' in field_schema:
            return field_schema['placeholder']
        
        # For option fields, return the string value (not index)
        # The validation will convert it to index when needed
        if original_type == 'option' and 'enum' in field_schema and field_schema['enum']:
            # Return the first enum value as a string
            return field_schema['enum'][0]
        
        # Use first enum value if available
        if 'enum' in field_schema and field_schema['enum']:
            return field_schema['enum'][0]
        
        # Simple type defaults
        type_defaults = {
            'string': 'example',
            'number': 1.0,
            'integer': 1,
            'boolean': True,
            'array': [],
            'object': {},
            'null': None
        }
        
        # Check for minimum values for numbers
        if field_type in ('number', 'integer') and 'minimum' in field_schema:
            return field_schema['minimum']
        
        return type_defaults.get(field_type, None)
    
    def transform_input_for_api(self, input_data: Dict[str, Any], agent_name: str) -> Any:
        """
        Transform input data to match the API's expected format.
        
        Some APIs expect input_data as an array of field objects rather than a dictionary.
        This method detects and applies the appropriate transformation.
        
        Args:
            input_data: Dictionary of field values
            agent_name: Name of the agent (for logging)
            
        Returns:
            Transformed input data in the format expected by the API
        """
        # For now, return as-is. In the future, we can detect from schema
        # or API responses whether array format is needed
        return input_data
    
