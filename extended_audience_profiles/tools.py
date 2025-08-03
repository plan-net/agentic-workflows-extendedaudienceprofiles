"""
Tools for OpenAI Agents - includes Masumi Network integration tools
"""
from typing import Dict, Any, List, Optional
from agents import function_tool
from .masumi import MasumiClient
from .state import get_state_actor
from .exceptions import AgentNotFoundError, MasumiNetworkError, ValidationError
import logging
import asyncio

logger = logging.getLogger(__name__)


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
        
        # Get budget summary from actor
        try:
            actor = get_state_actor()
            budget_summary = await actor.get_budget_summary.remote()
        except RuntimeError:
            # Actor not initialized yet
            budget_summary = {
                'total_budget': float('inf'),
                'total_spent': 0.0,
                'total_remaining': float('inf'),
                'agents': {}
            }
        
        # Format agent information for the AI to understand
        agents_info = []
        for agent in available_agents:
            agent_budget = budget_summary['agents'].get(agent['name'], {})
            agents_info.append({
                'name': agent['name'],
                'description': agent.get('description', 'No description'),
                'capabilities': agent.get('capabilities', []),
                'best_for': agent.get('best_for', ''),
                'example_prompts': agent.get('example_prompts', []),
                'limitations': agent.get('limitations', []),
                'endpoint': agent.get('endpoint', 'N/A'),
                'price': agent.get('price', 0.0),
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
    except (RuntimeError, AttributeError) as e:
        logger.error(f"Error accessing Masumi client: {str(e)}")
        return {
            'success': False,
            'error': f"Configuration error: {str(e)}",
            'available_agents': []
        }
    except Exception as e:
        logger.error(f"Unexpected error listing agents: {str(e)}")
        return {
            'success': False,
            'error': f"Unexpected error: {str(e)}",
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
        
        # Build example input
        example_input = client.build_schema_example(schema)
        
        # Build detailed field instructions
        field_instructions = []
        for field, field_schema in schema.get('properties', {}).items():
            field_type = field_schema.get('type', 'string')
            required = field in schema.get('required', [])
            
            instruction = f"- {field} ({field_type})"
            if required:
                instruction += " [REQUIRED]"
            else:
                instruction += " [optional]"
            
            if 'enum' in field_schema:
                # Check if this is an option field
                if field_schema.get('_original_type') == 'option':
                    instruction += f" - Valid values: {field_schema['enum']} (will be auto-converted to indices)"
                else:
                    instruction += f" - Must be one of: {field_schema['enum']}"
            if 'description' in field_schema:
                instruction += f" - {field_schema['description']}"
            
            field_instructions.append(instruction)
        
        return {
            'success': True,
            'agent_name': agent_name,
            'schema': schema,
            'example_input': example_input,
            'field_instructions': '\n'.join(field_instructions),
            'instructions': f"""To use {agent_name}, provide input_data as a dictionary with these fields:

{chr(10).join(field_instructions)}

Example valid input:
{example_input}

IMPORTANT: The input_data parameter must be a dictionary matching this schema, not a string."""
        }
    except AgentNotFoundError:
        # Already handled above
        raise
    except MasumiNetworkError as e:
        logger.error(f"Network error getting schema for {agent_name}: {str(e)}")
        return {
            'success': False,
            'error': str(e),
            'schema': None
        }
    except Exception as e:
        logger.error(f"Unexpected error getting schema for {agent_name}: {str(e)}")
        return {
            'success': False,
            'error': f"Unexpected error: {str(e)}",
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
        
        # Get agent config for price info
        agent_config = client.get_agent_config(agent_name)
        if not agent_config:
            return {
                'success': False,
                'error': f"Agent '{agent_name}' not found",
                'job_id': None,
                'error_type': 'agent_not_found'
            }
        
        # Check and reserve budget BEFORE starting the job
        expected_cost = agent_config.get('price', 0.0)
        try:
            actor = get_state_actor()
            can_afford, reason, budget_summary = await actor.reserve_budget.remote(agent_name, expected_cost)
            
            if not can_afford:
                # Get current budget state for error response
                agent_info = budget_summary['agents'].get(agent_name, {})
                
                return {
                    'success': False,
                    'error': reason,
                    'job_id': None,
                    'error_type': 'budget_exceeded',
                    'budget_info': {
                        'expected_cost': expected_cost,
                        'agent_spent': agent_info.get('spent', 0),
                        'total_spent': budget_summary['total_spent'],
                        'remaining': agent_info.get('remaining', 0)
                    }
                }
        except RuntimeError:
            # Actor not initialized, proceed without budget check
            logger.warning("StateActor not initialized, skipping budget check")
        
        # Get schema and validate input
        schema = None
        try:
            schema = await client.get_agent_schema(agent_name)
            
            # Validate and transform input data
            is_valid, errors, transformed_data = client.validate_input_against_schema(input_data, schema)
            
            if not is_valid:
                # Build helpful error message
                example = client.build_schema_example(schema)
                error_msg = f"Input validation failed for {agent_name}:\n"
                error_msg += "\n".join(f"  - {error}" for error in errors)
                error_msg += f"\n\nExpected format example:\n{example}"
                
                # Release budget reservation on validation error
                try:
                    actor = get_state_actor()
                    await actor.release_reservation.remote(agent_name, expected_cost)
                except RuntimeError:
                    pass
                
                return {
                    'success': False,
                    'error': error_msg,
                    'job_id': None,
                    'error_type': 'validation_error',
                    'schema': schema,
                    'example': example
                }
            
            # Use transformed data for the job
            input_data = transformed_data
            
        except (OSError, RuntimeError) as e:
            # Network or runtime errors getting schema - log but continue
            logger.warning(f"Could not validate schema for {agent_name}: {str(e)}")
            # Continue with original input_data
        
        # Step 1: Start the job
        logger.info(f"Starting job with agent: {agent_name}")
        logger.info(f"Input data: {input_data}")
        
        try:
            job_response = await client.start_job(agent_name, input_data)
            job_id = job_response['job_id']
            payment_id = job_response['payment_id']
            
            logger.info(f"Job started with ID: {job_id}, payment ID: {payment_id}")
            
        except ValueError as ve:
            # Validation error from API - provide helpful response
            logger.error(f"Job submission validation error: {str(ve)}")
            
            # Try to get schema for better error message
            example = None
            if schema:
                example = client.build_schema_example(schema)
            
            # Release budget reservation
            try:
                actor = get_state_actor()
                await actor.release_reservation.remote(agent_name, expected_cost)
            except RuntimeError:
                pass
            
            return {
                'success': False,
                'error': f"Input validation error: {str(ve)}",
                'job_id': None,
                'error_type': 'validation_error',
                'schema': schema,
                'example': example
            }
        
        # Step 2: Create purchase to complete payment
        logger.info(f"Creating purchase for job {job_id}")
        try:
            purchase_result = await client.create_purchase(job_response, input_data, agent_name)
            
            if not purchase_result.get('success'):
                # Release the budget reservation
                try:
                    actor = get_state_actor()
                    await actor.release_reservation.remote(agent_name, expected_cost)
                except RuntimeError:
                    pass
                
                return {
                    'success': False,
                    'error': 'Failed to complete purchase for job',
                    'job_id': job_id,
                    'error_type': 'payment_error'
                }
            
            # Record actual cost
            actual_cost = purchase_result.get('actual_cost', 0.0)
            logger.info(f"Purchase result for {agent_name}: actual_cost={actual_cost}, expected_cost={expected_cost}")
            
            # If actual_cost is 0, fall back to expected_cost
            if actual_cost == 0.0 and expected_cost > 0:
                logger.warning(f"Actual cost is 0, using expected cost {expected_cost} for {agent_name}")
                actual_cost = expected_cost
            
            if actual_cost > 0:
                try:
                    actor = get_state_actor()
                    logger.info(f"Recording cost: {actual_cost} USDM for {agent_name}")
                    await actor.record_cost.remote(agent_name, expected_cost, actual_cost)
                except RuntimeError:
                    logger.warning(f"StateActor not initialized, cannot record cost of {actual_cost}")
            else:
                logger.warning(f"Not recording cost - actual_cost={actual_cost}")
            
            # Return immediately without waiting
            logger.info(f"Job {job_id} submitted successfully, payment completed")
            
            # Get updated budget info after recording actual cost
            try:
                actor = get_state_actor()
                budget_summary = await actor.get_budget_summary.remote()
                agent_info = budget_summary['agents'].get(agent_name, {})
            except RuntimeError:
                budget_summary = None
                agent_info = {}
            
            return {
                'success': True,
                'job_id': job_id,
                'agent_name': agent_name,
                'input_data': input_data,
                'status': 'submitted',
                'message': f"Job submitted to {agent_name}. Will complete in 5-20 minutes.",
                'budget_info': {
                    'job_cost': actual_cost,
                    'expected_cost': expected_cost,
                    'agent_remaining': agent_info.get('remaining', 0),
                    'total_remaining': budget_summary['total_remaining'] if budget_summary else 0
                }
            }
            
        except (OSError, TimeoutError) as e:
            # Network or timeout errors
            logger.error(f"Network error during job execution: {str(e)}")
            # Release budget reservation on any error
            try:
                actor = get_state_actor()
                await actor.release_reservation.remote(agent_name, expected_cost)
            except RuntimeError:
                pass
            raise MasumiNetworkError("job execution", agent_name, e)
        
    except ValueError as ve:
        # Validation errors - these should have been caught earlier
        logger.warning(f"Unexpected validation error for {agent_name}: {str(ve)}")
        return {
            'success': False,
            'error': str(ve),
            'job_id': None,
            'error_type': 'validation_error'
        }
    except RuntimeError as re:
        # API/Network errors
        logger.error(f"Runtime error for {agent_name}: {str(re)}")
        return {
            'success': False,
            'error': str(re),
            'job_id': None,
            'error_type': 'api_error'
        }
    except (TypeError, AttributeError) as e:
        # Programming errors
        logger.error(f"Programming error executing job with {agent_name}: {str(e)}")
        return {
            'success': False,
            'error': f"Internal error: {str(e)}",
            'job_id': None,
            'error_type': 'internal_error'
        }