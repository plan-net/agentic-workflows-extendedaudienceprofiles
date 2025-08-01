"""
Tools for OpenAI Agents - includes Masumi Network integration tools
"""
from typing import Dict, Any, List, Optional
from agents import function_tool
from .masumi import MasumiClient
import ray
import logging
from pydantic import BaseModel

logger = logging.getLogger(__name__)


# Pydantic models for strict schema compliance
class AgentInfo(BaseModel):
    name: str
    description: str
    endpoint: str
    budget_remaining: float
    budget_spent: float
    max_budget: str


class ListAgentsResponse(BaseModel):
    success: bool
    total_budget: float
    total_spent: float
    total_remaining: float
    available_agents: List[AgentInfo]
    error: Optional[str] = None


class SchemaProperty(BaseModel):
    type: str
    description: str
    example: Optional[str] = None
    enum: Optional[List[str]] = None
    minimum: Optional[float] = None
    maximum: Optional[float] = None
    minLength: Optional[int] = None
    maxLength: Optional[int] = None


class SchemaResponse(BaseModel):
    success: bool
    agent_name: Optional[str] = None
    schema: Optional[Dict[str, Any]] = None  # Still need Any for nested schema
    instructions: Optional[str] = None
    error: Optional[str] = None


class BudgetInfo(BaseModel):
    job_cost: str
    agent_remaining: float
    total_remaining: float
    agent_spent: Optional[float] = None
    total_spent: Optional[float] = None
    remaining: Optional[float] = None


class JobExecutionResponse(BaseModel):
    success: bool
    job_id: Optional[str] = None
    agent_name: Optional[str] = None
    result: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = None
    budget_info: Optional[BudgetInfo] = None
    error: Optional[str] = None
    error_type: Optional[str] = None


@function_tool
async def list_available_agents() -> Dict[str, Any]:
    """
    Get list of available Masumi agents with descriptions and budget information.
    
    Returns:
        Dict containing available agents and budget summary
    """
    try:
        client = MasumiClient()
        available_agents = client.get_available_agents()
        budget_summary = client.get_budget_summary()
        
        # Format agent information for the AI to understand
        agents_info = []
        for agent in available_agents:
            agent_budget = budget_summary['agents'].get(agent['name'], {})
            agents_info.append({
                'name': agent['name'],
                'description': agent.get('description', 'No description'),
                'endpoint': agent.get('endpoint', 'N/A'),
                'budget_remaining': agent_budget.get('remaining', 0),
                'budget_spent': agent_budget.get('spent', 0),
                'max_budget': agent_budget.get('max_budget', 'unlimited')
            })
        
        return {
            'success': True,
            'total_budget': budget_summary['total_budget'],
            'total_spent': budget_summary['total_spent'],
            'total_remaining': budget_summary['total_remaining'],
            'available_agents': agents_info
        }
    except Exception as e:
        logger.error(f"Error listing agents: {str(e)}")
        return {
            'success': False,
            'error': str(e),
            'available_agents': []
        }


@function_tool
async def get_agent_input_schema(agent_name: str) -> Dict[str, Any]:
    """
    Get the input schema for a specific Masumi agent.
    
    Args:
        agent_name: Name of the agent to get schema for
        
    Returns:
        Dict containing the input schema and requirements
    """
    try:
        client = MasumiClient()
        
        # Check if agent exists
        agent_config = client.get_agent_config(agent_name)
        if not agent_config:
            return {
                'success': False,
                'error': f"Agent '{agent_name}' not found in configuration",
                'schema': None
            }
        
        # Get schema from the agent
        schema = await client.get_agent_schema(agent_name)
        
        return {
            'success': True,
            'agent_name': agent_name,
            'schema': schema,
            'instructions': f"Format your input according to the schema for {agent_name}"
        }
    except Exception as e:
        logger.error(f"Error getting schema for {agent_name}: {str(e)}")
        return {
            'success': False,
            'error': str(e),
            'schema': None
        }


@function_tool(strict_mode=False)
async def execute_agent_job(agent_name: str, input_data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Submit a job to a Masumi agent without waiting for completion.
    Returns immediately with job tracking information.
    
    Args:
        agent_name: Name of the agent to execute
        input_data: Input data formatted according to the agent's schema
        
    Returns:
        Dict containing job_id and submission status (not the result)
    """
    try:
        client = MasumiClient()
        
        # Check budget before starting
        remaining_budget = client.get_agent_remaining_budget(agent_name)
        if remaining_budget <= 0:
            return {
                'success': False,
                'error': f"No remaining budget for agent '{agent_name}'",
                'job_id': None,
                'error_type': 'budget_exceeded',
                'budget_info': {
                    'agent_spent': client.get_agent_spending(agent_name),
                    'total_spent': client.get_total_spending(),
                    'remaining': 0
                }
            }
        
        # Step 1: Start the job
        logger.info(f"Starting job with agent: {agent_name}")
        logger.info(f"Input data: {input_data}")
        job_response = await client.start_job(agent_name, input_data)
        job_id = job_response['job_id']
        payment_id = job_response['payment_id']
        
        logger.info(f"Job started with ID: {job_id}, payment ID: {payment_id}")
        
        # Step 2: Create purchase to complete payment
        logger.info(f"Creating purchase for job {job_id}")
        purchase_result = await client.create_purchase(job_response, input_data, agent_name)
        
        if not purchase_result.get('success'):
            return {
                'success': False,
                'error': 'Failed to complete purchase for job',
                'job_id': job_id,
                'error_type': 'payment_error'
            }
        
        # Return immediately without waiting
        logger.info(f"Job {job_id} submitted successfully, payment completed")
        
        return {
            'success': True,
            'job_id': job_id,
            'agent_name': agent_name,
            'status': 'submitted',
            'message': f"Job submitted to {agent_name}. Will complete in 5-20 minutes.",
            'budget_info': {
                'job_cost': purchase_result.get('actual_cost', 'unknown'),
                'agent_remaining': client.get_agent_remaining_budget(agent_name),
                'total_remaining': client.get_remaining_budget()
            }
        }
        
    except ValueError as ve:
        # Validation errors
        logger.warning(f"Validation error for {agent_name}: {str(ve)}")
        return {
            'success': False,
            'error': str(ve),
            'result': None,
            'error_type': 'validation_error'
        }
    except RuntimeError as re:
        # API/Network errors
        logger.error(f"Runtime error for {agent_name}: {str(re)}")
        return {
            'success': False,
            'error': str(re),
            'result': None,
            'error_type': 'api_error'
        }
    except Exception as e:
        # Other unexpected errors
        logger.error(f"Unexpected error executing job with {agent_name}: {str(e)}")
        return {
            'success': False,
            'error': str(e),
            'result': None,
            'error_type': 'unexpected_error'
        }