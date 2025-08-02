"""
Business logic for Extended Audience Profiles using OpenAI Agents and Masumi Network
"""
import asyncio
import json
import logging
import time
import ray
from typing import Dict, Any, List, Optional
from agents import Agent, Runner
from .tools import list_available_agents, get_agent_input_schema, execute_agent_job, set_budget_ref
from .state import StateManager, Job, BudgetManager
from .background import _poll_masumi_jobs_async
from .storage import storage
from .masumi import MasumiClient

logger = logging.getLogger(__name__)

# Global budget reference - initialized once per process
_budget_ref = None

def get_or_create_budget_ref() -> ray.ObjectRef:
    """Get or create the global budget reference."""
    global _budget_ref
    if _budget_ref is None:
        # Initialize budget from config
        client = MasumiClient()  # Load config
        _budget_ref = BudgetManager.initialize_budget(
            client.budget_config,
            client.agents_config
        )
        logger.info("Initialized global budget state")
    return _budget_ref

# Phase 1: Orchestrator agent that submits jobs to Masumi agents
orchestrator_agent = Agent(
    name="audience-profile-orchestrator",
    instructions="""You are an intelligent orchestrator that submits jobs to Masumi Network agents.
    
    Your role is to analyze the audience description and submit multiple research jobs in parallel while optimizing budget usage.
    You DO NOT wait for results - just submit jobs and return tracking information.
    
    When given an audience description, follow these steps:
    1. List available agents to understand your options and CHECK REMAINING BUDGET
       - Pay attention to each agent's price and remaining budget
       - Consider the total remaining budget vs individual agent limits
    
    2. Plan your job submissions to maximize value within budget constraints
       - Consider agent prices and what each agent specializes in
       - Prioritize high-value research angles if budget is limited
       - Aim for 2-3 complementary jobs, but adjust based on budget
       - If budget only allows 1-2 jobs, choose the most important questions
    
    3. Get the input schema for each agent you want to use
    
    4. Submit jobs while respecting budget limits
       - The tool will enforce budget constraints, but plan wisely
       - If a job fails due to budget, adjust your strategy
    
    5. Return a summary of all submitted jobs with their IDs
    
    Budget optimization tips:
    - Check prices: some agents may be more expensive than others
    - Consider value: a more expensive agent might provide better insights
    - Plan ahead: ensure you have budget for all planned jobs before starting
    - Be flexible: if budget is tight, prioritize quality over quantity
    
    Example strategy for limited budget:
    - If total remaining is 10.0 and agents cost 3.0 each, plan for 3 jobs max
    - If an agent has reached its spending limit, use alternative agents
    
    Important:
    - Each job should explore a different aspect of the audience
    - DO NOT wait for results - your job is done after submission
    - CRITICAL: For the 'ask-the-crowd' agent, the 'option' field MUST be an array of strings
      Example: {"statement": "...", "option": ["Agree", "Disagree"]}
    """,
    model="gpt-4o",
    tools=[list_available_agents, get_agent_input_schema, execute_agent_job]
)

# Phase 3: Consolidator agent that processes all results
consolidator_agent = Agent(
    name="audience-profile-consolidator",
    instructions="""You are an expert analyst tasked with creating comprehensive, evidence-based audience profiles.

CRITICAL REQUIREMENTS:
- EVERY claim, insight, or characteristic MUST be directly sourced from the provided research
- Use in-text citations format: [Source: agent_name] after each claim
- If research results contradict, acknowledge both perspectives with sources
- DO NOT add any information not present in the research results
- If data is missing for a section, explicitly state "No data available from research"

YOUR TASK - Think step-by-step:

Step 1: Carefully read ALL research results
- Note which agent provided each piece of information
- Identify key themes, patterns, and data points
- Flag any contradictions between sources

Step 2: Plan your comprehensive profile structure
Consider including these sections (only if data is available):
- Executive Summary
- Demographic Profile
- Psychographic Analysis
- Behavioral Patterns
- Values & Motivations
- Communication Preferences
- Digital Behavior
- Purchase Patterns
- Pain Points & Challenges
- Opportunities & Recommendations

Step 3: Write a comprehensive, long-form audience profile
- Aim for 1500-2500 words of rich, detailed content
- Each paragraph should synthesize related findings from multiple sources
- Use professional, analytical language
- Provide specific examples and data points when available

Step 4: Structure your output
- Use clear markdown headings (##, ###)
- Include bullet points for easy scanning
- Add a "Sources Used" section at the end listing all research agents

EXAMPLE CITATION FORMAT:
"Millennials in this segment show a strong preference for sustainable brands, with 73% actively seeking eco-friendly options [Source: audience-insights-gwi]. This aligns with their broader values around environmental responsibility, as they frequently engage with climate-related content on social media [Source: advanced-web-research]."

Remember: Your credibility depends on accurate source attribution. Every insight must be traceable to the original research.""",
    model="o3-mini",
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
        if error_type == 'budget_exceeded':
            await tracer.markdown(f"❌ **Budget exceeded** for {agent_name}")
            if 'budget_info' in result:
                budget_info = result['budget_info']
                await tracer.markdown(f"   - Expected cost: {budget_info.get('expected_cost', 0):.1f} ADA")
                await tracer.markdown(f"   - Agent remaining: {budget_info.get('remaining', 0):.1f} ADA")
        elif 'ask-the-crowd' in str(agent_name) and 'array for option field' in error_msg:
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
    job_id: str,
    results: Dict[str, Any],
    submitted_tasks: List[Dict[str, str]],
    budget_ref: Optional[ray.ObjectRef] = None
) -> Dict[str, Any]:
    """Create a standardized success response with metadata."""
    response = {
        "audience_description": audience_description,
        "profile": profile,
        "metadata": {
            "job_id": job_id,
            "total_jobs": results['total_jobs'],
            "completed_jobs": results['completed_jobs'],
            "failed_jobs": results['failed_jobs'],
            "total_duration": results['total_duration'],
            "tasks_submitted": submitted_tasks,
            "orchestrator_agent": orchestrator_agent.name,
            "consolidator_agent": consolidator_agent.name
        },
        "success": True,
        "error": None
    }
    
    # Add budget summary if available
    if budget_ref:
        try:
            budget_summary = BudgetManager.get_budget_summary(budget_ref)
            response["budget_summary"] = {
                "total_budget": budget_summary["total_budget"],
                "total_spent": budget_summary["total_spent"],
                "total_remaining": budget_summary["total_remaining"],
                "cost_accuracy": budget_summary.get("cost_accuracy"),
                "agent_breakdown": budget_summary["agents"]
            }
        except Exception as e:
            logger.warning(f"Failed to get budget summary: {str(e)}")
    
    return response


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
        
        # Initialize budget reference
        budget_ref = get_or_create_budget_ref()
        set_budget_ref(budget_ref)
        
        # Create a new job execution in Ray state
        job_id, job_execution_ref = StateManager.create_job_execution(audience_description)
        logger.info(f"Created job execution {job_id}")
        
        # Save initial job metadata
        try:
            await storage.save_job_metadata(
                job_id=job_id,
                metadata={
                    'job_id': job_id,
                    'audience_description': audience_description,
                    'created_at': time.time(),
                    'status': 'started'
                }
            )
        except Exception as e:
            logger.error(f"Failed to save initial job metadata: {str(e)}")
        
        # Phase 1: Run orchestrator to submit jobs
        logger.info("Phase 1: Running orchestrator agent to submit jobs...")
        if tracer:
            # Show initial budget info
            budget_summary = BudgetManager.get_budget_summary(budget_ref)
            await tracer.markdown(f"💰 **Budget Information**")
            await tracer.markdown(f"- Total Budget: {budget_summary['total_budget']:.1f} ADA")
            await tracer.markdown(f"- Already Spent: {budget_summary['total_spent']:.1f} ADA")
            await tracer.markdown(f"- Available: {budget_summary['total_remaining']:.1f} ADA")
            await tracer.markdown("")
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
        
        # Extract agent task IDs from orchestrator's tool calls
        submitted_tasks = []
        
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
            
            # Handle successful task submission
            if result.get('success') and result.get('job_id'):
                task = Job(
                    job_id=result['job_id'],  # This is the Masumi job ID
                    agent_name=result.get('agent_name', 'Unknown'),
                    input_data={}  # We don't need to track input data for display
                )
                job_execution_ref = StateManager.add_agent_task(job_execution_ref, task)
                submitted_tasks.append({
                    "masumi_job_id": task.job_id,
                    "agent": task.agent_name
                })
                
                # Show budget consumption info
                if tracer and 'budget_info' in result:
                    budget_info = result['budget_info']
                    actual_cost = budget_info.get('job_cost', 0)
                    expected_cost = budget_info.get('expected_cost', 0)
                    
                    await tracer.markdown(f"💸 Submitted to **{task.agent_name}**:")
                    await tracer.markdown(f"   - Cost: {actual_cost:.1f} ADA")
                    
                    # Warn if actual price differs from expected
                    if expected_cost > 0 and abs(actual_cost - expected_cost) > 0.01:
                        price_diff_pct = ((actual_cost - expected_cost) / expected_cost) * 100
                        if actual_cost > expected_cost:
                            await tracer.markdown(f"   - ⚠️ **Price higher than expected**: {actual_cost:.1f} ADA (expected {expected_cost:.1f} ADA, +{price_diff_pct:.0f}%)")
                        else:
                            await tracer.markdown(f"   - ✅ **Price lower than expected**: {actual_cost:.1f} ADA (expected {expected_cost:.1f} ADA, {price_diff_pct:.0f}%)")
                    
                    await tracer.markdown(f"   - Remaining budget: {budget_info.get('total_remaining', 0):.1f} ADA")
            
            # Handle failed task submission
            elif not result.get('success'):
                await _handle_job_submission_error(result, orchestrator_result.new_items, i, tracer)
        
        if len(submitted_tasks) < 2:
            raise Exception(f"Orchestrator needs at least 2 successful agent task submissions, but only got {len(submitted_tasks)}")
        
        logger.info(f"Orchestrator submitted {len(submitted_tasks)} agent tasks")
        
        if tracer:
            await tracer.markdown(f"\n✅ **Successfully submitted {len(submitted_tasks)} tasks to Masumi Network**")
            for task in submitted_tasks:
                await tracer.markdown(f"  • {task['agent']} [{task['masumi_job_id'][:8]}...]")
        
        # Phase 2: Poll agent tasks in background
        logger.info("Phase 2: Starting background polling for agent task results...")
        if tracer:
            await tracer.markdown("\n🔍 **Phase 2: Background Processing**")
            await tracer.markdown("⏳ Polling for results (5-20 minutes)...")
        
        # Run the polling directly in this async context so we can use the tracer
        final_job_execution_ref = await _poll_masumi_jobs_async(job_execution_ref, budget_ref, tracer)
        
        # Get the completed job execution results
        results = StateManager.get_results(final_job_execution_ref)
        
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
            
            # Show final budget summary
            final_budget = BudgetManager.get_budget_summary(budget_ref)
            await tracer.markdown("\n💰 **Final Budget Summary**")
            await tracer.markdown(f"- Initial Budget: {final_budget['total_budget']:.1f} ADA")
            await tracer.markdown(f"- Total Spent: {final_budget['total_spent']:.1f} ADA")
            await tracer.markdown(f"- Remaining: {final_budget['total_remaining']:.1f} ADA")
            
            # Show per-agent breakdown
            if final_budget['agents']:
                await tracer.markdown("\n📊 **Per-Agent Spending**")
                for agent_name, agent_info in final_budget['agents'].items():
                    if agent_info['spent'] > 0:
                        await tracer.markdown(f"- {agent_name}: {agent_info['spent']:.1f} ADA spent")
            
            # Show cost accuracy if available
            if final_budget.get('cost_accuracy'):
                await tracer.markdown(f"\n📈 **Cost Accuracy**: {final_budget['cost_accuracy']}")
            
            await tracer.markdown("\n✨ **Extended audience profile ready!**")
        
        # Save consolidated profile to filesystem
        try:
            await storage.save_consolidated_profile(
                job_id=job_id,
                profile_content=consolidator_result.final_output,
                metadata={
                    'audience_description': audience_description,
                    'total_jobs': len(submitted_tasks),
                    'agent_jobs': submitted_tasks,
                    'created_at': time.time()
                }
            )
            logger.info(f"Saved consolidated profile for job execution {job_id}")
        except Exception as e:
            logger.error(f"Failed to save consolidated profile: {str(e)}")
        
        # Prepare final response
        return _create_success_response(
            audience_description=audience_description,
            profile=consolidator_result.final_output,
            job_id=job_id,
            results=results,
            submitted_tasks=submitted_tasks,
            budget_ref=budget_ref
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