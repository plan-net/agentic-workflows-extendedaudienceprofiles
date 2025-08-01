"""
Business logic for Extended Audience Profiles using OpenAI Agents and Masumi Network
"""
import asyncio
import json
import logging
from typing import Dict, Any, List, Optional
from agents import Agent, Runner
from .tools import list_available_agents, get_agent_input_schema, execute_agent_job
from .state import StateManager, Job
from .background import _poll_masumi_jobs_async

logger = logging.getLogger(__name__)

# Phase 1: Orchestrator agent that submits jobs to Masumi agents
orchestrator_agent = Agent(
    name="audience-profile-orchestrator",
    instructions="""You are an intelligent orchestrator that submits jobs to Masumi Network agents.
    
    Your role is to analyze the audience description and submit multiple research jobs in parallel.
    You DO NOT wait for results - just submit jobs and return tracking information.
    
    When given an audience description, follow these steps:
    1. List available agents to understand your options and check budgets
    2. Based on the audience description, decide which agents to use and what questions to ask
    3. Get the input schema for each agent you want to use
    4. Submit 2-3 different research jobs with complementary questions/angles
    5. Return a summary of all submitted jobs with their IDs
    
    Example strategy:
    - For "millennials interested in sustainable fashion":
      - Job 1: Research demographic characteristics and values
      - Job 2: Research shopping behaviors and brand preferences
      - Job 3: Research social media habits and influencers
    
    Important:
    - Submit jobs to different agents if appropriate, or multiple jobs to the same agent
    - Each job should explore a different aspect of the audience
    - DO NOT wait for results - your job is done after submission
    - Return all job IDs and what each job is researching
    - CRITICAL: For the 'ask-the-crowd' agent, the 'option' field MUST be an array of strings, not a single string
      Example: {"statement": "...", "option": ["Agree", "Disagree"]}
    """,
    model="gpt-4o",
    tools=[list_available_agents, get_agent_input_schema, execute_agent_job]
)

# Phase 3: Consolidator agent that processes all results
consolidator_agent = Agent(
    name="audience-profile-consolidator",
    instructions="""You are an expert at synthesizing research results into comprehensive audience profiles.
    
    You receive multiple research results about an audience and create a unified, actionable profile.
    
    Your task:
    1. Analyze all the research results provided
    2. Identify common themes and patterns
    3. Resolve any contradictions or conflicts in the data
    4. Create a comprehensive audience profile that includes:
       - Demographics and psychographics
       - Behaviors and preferences
       - Values and motivations
       - Communication preferences
       - Recommended engagement strategies
    
    Format the output as a well-structured report with clear sections and actionable insights.
    Focus on practical recommendations for reaching and engaging this audience.
    """,
    model="gpt-4o",
    tools=[]  # Consolidator doesn't need tools - just processes provided data
)



def _find_agent_name_from_previous_calls(items: List[Any], current_index: int) -> Optional[str]:
    """Look backwards to find agent name from execute_agent_job calls."""
    for j in range(current_index - 1, -1, -1):
        item = items[j]
        if (type(item).__name__ == 'ToolCallItem' and 
            hasattr(item, 'function') and 
            hasattr(item.function, 'name') and 
            item.function.name == 'execute_agent_job' and
            hasattr(item.function, 'arguments')):
            try:
                args = json.loads(item.function.arguments)
                return args.get('agent_name')
            except:
                pass
    return None


def _format_research_results(results: Dict[str, Any]) -> str:
    """Format research results for consolidator agent."""
    formatted_results = []
    for job_id, data in results.items():
        agent_name = data.get('agent_name', 'Unknown')
        question = data.get('input_data', {}).get('question', 'N/A')
        result = data.get('result', 'No result')
        
        formatted_results.append(
            f"Research from {agent_name}:\n"
            f"Question: {question}\n"
            f"Result: {result}"
        )
    
    return "\n\n".join(formatted_results)


async def _handle_job_submission_error(result: Dict[str, Any], items: List[Any], index: int, tracer) -> None:
    """Handle errors from job submission attempts."""
    agent_name = result.get('agent_name') or _find_agent_name_from_previous_calls(
        items, index
    ) or 'Unknown agent'
    
    error_msg = result.get('error', 'Unknown error')
    error_type = result.get('error_type', 'unknown')
    
    logger.error(f"Failed to submit job to {agent_name}: {error_msg} (type: {error_type})")
    
    if tracer:
        # Special handling for known error types
        if 'ask-the-crowd' in str(agent_name) and 'array for option field' in error_msg:
            await tracer.markdown(f"⚠️ Skipped {agent_name}: requires array format for options")
        else:
            await tracer.markdown(f"❌ Failed to submit to {agent_name}: {error_msg[:200]}... (type: {error_type})")


def _create_error_response(audience_description: str, error: str) -> Dict[str, Any]:
    """Create a standardized error response."""
    return {
        "audience_description": audience_description,
        "profile": None,
        "success": False,
        "error": error
    }


def _create_success_response(
    audience_description: str,
    profile: str,
    submission_id: str,
    results: Dict[str, Any],
    submitted_jobs: List[Dict[str, str]]
) -> Dict[str, Any]:
    """Create a standardized success response with metadata."""
    return {
        "audience_description": audience_description,
        "profile": profile,
        "metadata": {
            "submission_id": submission_id,
            "total_jobs": results['total_jobs'],
            "completed_jobs": results['completed_jobs'],
            "failed_jobs": results['failed_jobs'],
            "total_duration": results['total_duration'],
            "jobs_submitted": submitted_jobs,
            "orchestrator_agent": orchestrator_agent.name,
            "consolidator_agent": consolidator_agent.name
        },
        "success": True,
        "error": None
    }


async def generate_audience_profile(audience_description: str, tracer=None) -> Dict[str, Any]:
    """
    Generate an extended audience profile using fan-out/fan-in agent orchestration.
    
    Phase 1: Orchestrator submits multiple jobs to Masumi agents
    Phase 2: Background tasks poll for results
    Phase 3: Consolidator synthesizes all results
    
    Args:
        audience_description: Description of the target audience
        
    Returns:
        Dict with profile data and metadata
    """
    try:
        logger.info(f"Starting audience profile generation for: {audience_description}")
        
        # Create a new submission in Ray state
        submission_id, submission_ref = StateManager.create_submission(audience_description)
        logger.info(f"Created submission {submission_id}")
        
        # Phase 1: Run orchestrator to submit jobs
        logger.info("Phase 1: Running orchestrator agent to submit jobs...")
        if tracer:
            await tracer.markdown("🤖 Running orchestrator agent...")
        orchestrator_prompt = f"""
        Analyze this audience and submit research jobs to Masumi agents:
        
        Audience: {audience_description}
        
        Submit 2-3 complementary research jobs that explore different aspects of this audience.
        Return the job IDs and what each job is researching.
        """
        
        orchestrator_result = await Runner.run(
            orchestrator_agent,
            orchestrator_prompt
        )
        
        # Extract job IDs from orchestrator's tool calls
        submitted_jobs = []
        
        # Process tool call outputs from orchestrator
        for i, item in enumerate(orchestrator_result.new_items):
            # Skip if not a tool call output
            if type(item).__name__ != 'ToolCallOutputItem':
                continue
            
            # Get the actual tool output directly from item.output
            result = item.output
            
            # Skip if output is not a dictionary
            if not isinstance(result, dict):
                continue
            
            # Handle successful job submission
            if result.get('success') and result.get('job_id'):
                job = Job(
                    job_id=result['job_id'],
                    agent_name=result.get('agent_name', 'Unknown'),
                    input_data={}  # We don't need to track input data for display
                )
                submission_ref = StateManager.add_job(submission_ref, job)
                submitted_jobs.append({
                    "job_id": job.job_id,
                    "agent": job.agent_name
                })
            
            # Handle failed job submission
            elif not result.get('success'):
                await _handle_job_submission_error(result, orchestrator_result.new_items, i, tracer)
        
        if len(submitted_jobs) < 2:
            raise Exception(f"Orchestrator needs at least 2 successful job submissions, but only got {len(submitted_jobs)}")
        
        logger.info(f"Orchestrator submitted {len(submitted_jobs)} jobs")
        
        if tracer:
            await tracer.markdown(f"\n✅ **Successfully submitted {len(submitted_jobs)} jobs to Masumi Network**")
            for job in submitted_jobs:
                await tracer.markdown(f"  • {job['agent']} [{job['job_id'][:8]}...]")
        
        # Phase 2: Poll jobs in background
        logger.info("Phase 2: Starting background polling for job results...")
        if tracer:
            await tracer.markdown("\n🔍 **Phase 2: Background Processing**")
            await tracer.markdown("⏳ Polling for results (5-20 minutes)...")
        
        # Run the polling directly in this async context so we can use the tracer
        final_submission_ref = await _poll_masumi_jobs_async(submission_ref, tracer)
        
        # Get the completed submission
        results = StateManager.get_results(final_submission_ref)
        
        if not results['is_complete']:
            raise Exception("Not all jobs completed successfully")
        
        logger.info(f"All jobs complete. Successful: {results['completed_jobs']}, Failed: {results['failed_jobs']}")
        
        # Phase 3: Run consolidator to synthesize results
        logger.info("Phase 3: Running consolidator agent to synthesize results...")
        if tracer:
            await tracer.markdown("\n🧪 **Phase 3: Synthesis**")
            await tracer.markdown("🤖 Running consolidator agent...")
        
        # Prepare results for consolidator
        research_results = _format_research_results(results['results'])
        
        consolidator_prompt = f"""
        Create a comprehensive audience profile based on these research results:
        
        Original Audience Description: {audience_description}
        
        Research Results:
        {research_results}
        
        Synthesize all findings into a unified, actionable audience profile.
        """
        
        consolidator_result = await Runner.run(
            consolidator_agent,
            consolidator_prompt
        )
        
        if tracer:
            await tracer.markdown("✅ Profile synthesis complete")
            await tracer.markdown("\n✨ **Extended audience profile ready!**")
        
        # Prepare final response
        return _create_success_response(
            audience_description=audience_description,
            profile=consolidator_result.final_output,
            submission_id=submission_id,
            results=results,
            submitted_jobs=submitted_jobs
        )
        
    except Exception as e:
        logger.error(f"Error generating audience profile: {str(e)}")
        return _create_error_response(audience_description, str(e))


def generate_audience_profile_sync(audience_description: str) -> Dict[str, Any]:
    """
    Synchronous wrapper for generate_audience_profile.
    Used for compatibility with existing code.
    
    Args:
        audience_description: Description of the target audience
        
    Returns:
        Dict with profile data and metadata
    """
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        return loop.run_until_complete(generate_audience_profile(audience_description))
    finally:
        loop.close()