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
from .token_utils import TokenCounter, check_token_fit, format_token_summary
from .truncation import SmartTruncator, TruncationStrategy

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
    
    Token window considerations:
    - The consolidator agent (o3-mini) has a 200k token context window
    - Each agent typically returns 500-2000 tokens of content
    - Plan your queries to balance breadth vs depth:
      - For simple topics: 3-4 focused queries work well
      - For complex topics: 2-3 deeper queries may be better
      - If you request very detailed research from many agents, results may be truncated
    
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

# Phase 3: Refinement agent that analyzes initial results and plans deeper research
refinement_agent = Agent(
    name="audience-profile-refinement",
    instructions="""You are an expert research analyst that reviews initial findings and identifies opportunities for deeper investigation.
    
    Your role is to analyze the initial research results and submit follow-up research jobs that:
    1. Fill gaps in the initial research
    2. Explore interesting patterns or unexpected findings
    3. Clarify contradictions or ambiguities
    4. Dive deeper into the most relevant aspects for the audience
    
    When given initial research results, follow these steps:
    1. Carefully read and analyze ALL initial findings
    2. Identify 3-5 key areas that need deeper investigation
    3. Check available budget and plan accordingly
    4. Submit focused, high-value research jobs
    
    Budget strategy:
    - You have access to ALL remaining budget
    - Prioritize quality and depth over quantity
    - If budget allows, explore multiple angles
    - Each follow-up should add significant value
    
    Research planning tips:
    - Avoid redundant questions already answered
    - Focus on actionable insights
    - Consider practical applications
    - Target specific demographics, behaviors, or preferences
    - Ask "what would be most valuable to know next?"
    
    Token considerations:
    - Be aware that all results (first + second round) go to synthesis
    - Plan research volume considering the 200k token limit
    - Quality over quantity if approaching limits
    
    Important:
    - Submit jobs and return immediately (don't wait for results)
    - Each job should have a clear purpose based on initial findings
    - Explain your reasoning for each follow-up research
    """,
    model="gpt-4o",
    tools=[list_available_agents, get_agent_input_schema, execute_agent_job]
)

# Phase 5: Consolidator agent that processes all results
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


def _format_research_results(
    results: Dict[str, Any], 
    model: str = "o3-mini",
    tracer=None
) -> tuple[str, Dict[str, Any]]:
    """
    Format research results for consolidator agent with token management.
    
    Returns:
        Tuple of (formatted_results, token_metadata)
    """
    # First check if results fit within context
    fits, token_metadata = check_token_fit(results, model)
    
    # Log token analysis
    logger.info(f"Token analysis: {token_metadata['total_tokens']} tokens, "
                f"{token_metadata['percentage_used']:.1f}% of budget")
    
    # Display token info in tracer if available
    if tracer:
        asyncio.create_task(_display_token_analysis(tracer, results, token_metadata))
    
    # If results don't fit, truncate them
    if not fits:
        logger.warning(f"Results exceed context window. Applying truncation strategy.")
        truncator = SmartTruncator(model)
        counter = TokenCounter()
        budget = counter.get_token_budget(model)
        
        # Truncate results to fit
        truncated_results, truncation_info = truncator.truncate_results(
            results,
            budget.available_tokens,
            strategy=TruncationStrategy.SMART_SECTIONS
        )
        
        # Update results with truncated versions
        for job_id, trunc_data in truncated_results.items():
            results[job_id] = trunc_data
        
        # Update metadata with truncation info
        token_metadata['truncation_applied'] = True
        token_metadata['truncation_info'] = {
            job_id: {
                'was_truncated': info.was_truncated,
                'original_tokens': info.original_tokens,
                'truncated_tokens': info.truncated_tokens,
                'sections_removed': info.sections_removed
            }
            for job_id, info in truncation_info.items()
        }
    else:
        token_metadata['truncation_applied'] = False
    
    # Format results as before
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
    
    return "\n\n".join(formatted_results), token_metadata


def _format_results_for_refinement(
    results: Dict[str, Any],
    audience_description: str,
    budget_remaining: float
) -> str:
    """
    Format first round research results for the refinement agent.
    
    Provides a structured summary that helps the refinement agent:
    - Understand what was already researched
    - Identify gaps and opportunities
    - Make budget-aware decisions
    """
    formatted = f"""Initial Research Results for: {audience_description}

Budget Status:
- Remaining Budget: {budget_remaining:.1f} USDM
- First Round Jobs: {len(results)}

Research Completed:
"""
    
    for job_id, data in results.items():
        agent_name = data.get('agent_name', 'Unknown')
        question = data.get('input_data', {}).get('question', 'N/A')
        result = data.get('result', 'No result')
        
        # Truncate result for refinement agent (just key findings)
        result_preview = result[:1000] + "..." if len(result) > 1000 else result
        
        formatted += f"""
Agent: {agent_name}
Question: {question}
Key Findings: {result_preview}
---
"""
    
    formatted += """
Based on these initial findings, identify:
1. What key questions remain unanswered?
2. What interesting patterns need deeper investigation?
3. What contradictions or ambiguities need clarification?
4. What specific audience segments need more research?

Submit follow-up research jobs that will provide the most value."""
    
    return formatted


async def _display_token_analysis(tracer, results: Dict[str, Any], token_metadata: Dict[str, Any]):
    """Display token analysis in tracer."""
    await tracer.markdown("\n📊 **Token Usage Analysis**")
    
    # Per-agent breakdown
    for job_id, tokens in token_metadata['per_result_tokens'].items():
        agent_name = results[job_id].get('agent_name', 'Unknown')
        await tracer.markdown(f"- {agent_name}: {tokens:,} tokens")
    
    # Total usage
    await tracer.markdown(f"\n**Total**: {token_metadata['total_tokens']:,} tokens "
                         f"({token_metadata['percentage_used']:.1f}% of budget)")
    
    # Status
    if token_metadata['fits_in_context']:
        await tracer.markdown("✅ All results fit within context window")
    else:
        await tracer.markdown("⚠️ Results truncated to fit context window")
        
        if 'truncation_info' in token_metadata:
            await tracer.markdown("\n**Truncation Details:**")
            for job_id, info in token_metadata['truncation_info'].items():
                if info['was_truncated']:
                    agent_name = results[job_id].get('agent_name', 'Unknown')
                    await tracer.markdown(f"- {agent_name}: {info['original_tokens']:,} → "
                                        f"{info['truncated_tokens']:,} tokens")
                    if info.get('sections_removed'):
                        await tracer.markdown(f"  Removed: {', '.join(info['sections_removed'])}")


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
                await tracer.markdown(f"   - Expected cost: {budget_info.get('expected_cost', 0):.1f} USDM")
                await tracer.markdown(f"   - Agent remaining: {budget_info.get('remaining', 0):.1f} USDM")
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
    second_round_tasks: List[Dict[str, str]] = None,
    budget_ref: Optional[ray.ObjectRef] = None
) -> Dict[str, Any]:
    """Create a standardized success response with metadata."""
    if second_round_tasks is None:
        second_round_tasks = []
    
    response = {
        "audience_description": audience_description,
        "profile": profile,
        "metadata": {
            "job_id": job_id,
            "total_jobs": results['total_jobs'],
            "completed_jobs": results['completed_jobs'],
            "failed_jobs": results['failed_jobs'],
            "total_duration": results['total_duration'],
            "first_round_tasks": submitted_tasks,
            "second_round_tasks": second_round_tasks,
            "tasks_submitted": submitted_tasks + second_round_tasks,  # All tasks for compatibility
            "orchestrator_agent": orchestrator_agent.name,
            "refinement_agent": refinement_agent.name,
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
            await tracer.markdown(f"- Total Budget: {budget_summary['total_budget']:.1f} USDM")
            await tracer.markdown(f"- Already Spent: {budget_summary['total_spent']:.1f} USDM")
            await tracer.markdown(f"- Available: {budget_summary['total_remaining']:.1f} USDM")
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
                    await tracer.markdown(f"   - Cost: {actual_cost:.1f} USDM")
                    
                    # Warn if actual price differs from expected
                    if expected_cost > 0 and abs(actual_cost - expected_cost) > 0.01:
                        price_diff_pct = ((actual_cost - expected_cost) / expected_cost) * 100
                        if actual_cost > expected_cost:
                            await tracer.markdown(f"   - ⚠️ **Price higher than expected**: {actual_cost:.1f} USDM (expected {expected_cost:.1f} USDM, +{price_diff_pct:.0f}%)")
                        else:
                            await tracer.markdown(f"   - ✅ **Price lower than expected**: {actual_cost:.1f} USDM (expected {expected_cost:.1f} USDM, {price_diff_pct:.0f}%)")
                    
                    await tracer.markdown(f"   - Remaining budget: {budget_info.get('total_remaining', 0):.1f} USDM")
            
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
        
        # Phase 3: Run refinement agent to plan deeper research
        logger.info("Phase 3: Running refinement agent to analyze results and plan follow-up...")
        if tracer:
            await tracer.markdown("\n🔬 **Phase 3: Refinement Analysis**")
            
            # Show current budget status
            budget_summary = BudgetManager.get_budget_summary(budget_ref)
            await tracer.markdown(f"💰 **Remaining Budget**: {budget_summary['total_remaining']:.1f} USDM")
            await tracer.markdown("🤔 Analyzing first round results...")
        
        # Prepare results for refinement agent
        refinement_prompt = _format_results_for_refinement(
            results['results'],
            audience_description,
            BudgetManager.get_budget_summary(budget_ref)['total_remaining']
        )
        
        refinement_result = await Runner.run(
            refinement_agent,
            refinement_prompt
        )
        
        # Extract second round task IDs
        second_round_tasks = []
        
        # Process refinement agent's tool calls
        for i, item in enumerate(refinement_result.new_items):
            if type(item).__name__ != 'ToolCallOutputItem':
                continue
            
            result = item.output
            
            if not isinstance(result, dict):
                continue
            
            # Handle successful task submission
            if result.get('success') and result.get('job_id'):
                task = Job(
                    job_id=result['job_id'],
                    agent_name=result.get('agent_name', 'Unknown'),
                    input_data={},
                    round=2  # Mark as second round
                )
                job_execution_ref = StateManager.add_agent_task(job_execution_ref, task)
                second_round_tasks.append({
                    "masumi_job_id": task.job_id,
                    "agent": task.agent_name
                })
                
                # Show budget info
                if tracer and 'budget_info' in result:
                    budget_info = result['budget_info']
                    actual_cost = budget_info.get('job_cost', 0)
                    
                    await tracer.markdown(f"💸 Submitted deep-dive to **{task.agent_name}**:")
                    await tracer.markdown(f"   - Cost: {actual_cost:.1f} USDM")
                    await tracer.markdown(f"   - Remaining: {budget_info.get('total_remaining', 0):.1f} USDM")
            
            # Handle errors
            elif result.get('error'):
                await _handle_job_submission_error(result, refinement_result.new_items, i, tracer)
        
        if tracer:
            await tracer.markdown(f"\n✅ **Submitted {len(second_round_tasks)} deep-dive tasks**")
        
        # Phase 4: Poll for second round results
        if second_round_tasks:
            logger.info("Phase 4: Polling for second round results...")
            if tracer:
                await tracer.markdown("\n🔍 **Phase 4: Second Round Results**")
                await tracer.markdown("⏳ Polling for deep-dive results...")
            
            # Poll again for second round
            final_job_execution_ref = await _poll_masumi_jobs_async(job_execution_ref, budget_ref, tracer)
            
            # Get all results (both rounds)
            results = StateManager.get_results(final_job_execution_ref)
            
            if not results['is_complete']:
                raise Exception("Not all jobs completed successfully")
        
        # Phase 5: Run consolidator to synthesize all results
        logger.info("Phase 5: Running consolidator agent to synthesize all results...")
        if tracer:
            await tracer.markdown("\n🧪 **Phase 5: Synthesis**")
            await tracer.markdown("🤖 Running consolidator agent with all results...")
        
        # Prepare results for consolidator with token management
        research_results, token_metadata = _format_research_results(
            results['results'], 
            model="o3-mini",
            tracer=tracer
        )
        
        # Store token metadata for later
        token_info = {
            'total_tokens': token_metadata['total_tokens'],
            'percentage_used': token_metadata['percentage_used'],
            'truncation_applied': token_metadata.get('truncation_applied', False)
        }
        
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
            
            # Show token usage summary
            await tracer.markdown(f"\n🔤 **Token Usage Summary**")
            await tracer.markdown(f"- Input tokens: {token_info['total_tokens']:,}")
            await tracer.markdown(f"- Context usage: {token_info['percentage_used']:.1f}%")
            if token_info['truncation_applied']:
                await tracer.markdown("- ⚠️ Some content was truncated to fit context window")
            
            # Show final budget summary
            final_budget = BudgetManager.get_budget_summary(budget_ref)
            await tracer.markdown("\n💰 **Final Budget Summary**")
            await tracer.markdown(f"- Initial Budget: {final_budget['total_budget']:.1f} USDM")
            await tracer.markdown(f"- Total Spent: {final_budget['total_spent']:.1f} USDM")
            await tracer.markdown(f"- Remaining: {final_budget['total_remaining']:.1f} USDM")
            
            # Show per-agent breakdown
            if final_budget['agents']:
                await tracer.markdown("\n📊 **Per-Agent Spending**")
                for agent_name, agent_info in final_budget['agents'].items():
                    if agent_info['spent'] > 0:
                        await tracer.markdown(f"- {agent_name}: {agent_info['spent']:.1f} USDM spent")
            
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
                    'total_jobs': len(submitted_tasks) + len(second_round_tasks),
                    'first_round_jobs': submitted_tasks,
                    'second_round_jobs': second_round_tasks,
                    'agent_jobs': submitted_tasks + second_round_tasks,  # All jobs for compatibility
                    'created_at': time.time(),
                    'token_info': token_info
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
            second_round_tasks=second_round_tasks,
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