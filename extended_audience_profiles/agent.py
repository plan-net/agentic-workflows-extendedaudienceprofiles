"""
Business logic for Extended Audience Profiles using OpenAI Agents and Masumi Network
"""
import asyncio
import logging
import time
from typing import Dict, Any, List, Optional
from agents import Agent, Runner
from .tools import list_available_agents, get_agent_input_schema, execute_agent_job
from .state import Task, init_state_actor, get_state_actor
from .background import _poll_masumi_jobs_async
from .storage import storage
from .masumi import MasumiClient
from .formatting import format_research_results, format_results_for_refinement, display_token_analysis
from .errors import handle_job_submission_error, create_error_response, create_success_response
from .exceptions import JobNotFoundError, StorageError

logger = logging.getLogger(__name__)

# Load prompts from files
def load_prompt(filename: str) -> str:
    """
    Load a prompt from the prompts directory.
    
    Reads prompt files from the prompts subdirectory relative to this module.
    Used to externalize agent instructions for easier maintenance.
    
    Args:
        filename: Name of the prompt file to load (e.g., 'orchestrator.txt')
        
    Returns:
        Content of the prompt file with whitespace stripped
        
    Raises:
        FileNotFoundError: If prompt file doesn't exist
    """
    from pathlib import Path
    prompt_path = Path(__file__).parent / "prompts" / filename
    with open(prompt_path, 'r') as f:
        return f.read().strip()


# Phase 1: Orchestrator agent that submits jobs to Masumi agents
orchestrator_agent = Agent(
    name="audience-profile-orchestrator",
    instructions=load_prompt("orchestrator.txt"),
    model="gpt-4.1",
    tools=[list_available_agents, get_agent_input_schema, execute_agent_job]
)

# Phase 3: Refinement agent that analyzes initial results and plans deeper research
refinement_agent = Agent(
    name="audience-profile-refinement",
    instructions=load_prompt("refinement.txt"),
    model="gpt-4.1",
    tools=[list_available_agents, get_agent_input_schema, execute_agent_job]
)

# Phase 5: Consolidator agent that processes all results
consolidator_agent = Agent(
    name="audience-profile-consolidator",
    instructions=load_prompt("consolidator.txt"),
    model="gpt-4.1",
    tools=[]  # Consolidator doesn't need tools - just processes provided data
)


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
        
        from .masumi import MasumiClient
        client = MasumiClient()
        actor = init_state_actor(client.budget_config, client.agents_config)
        
        job_id = await actor.create_job.remote(audience_description)
        logger.info(f"Created job {job_id}")
        
        # Verify job was created
        job = await actor.get_job.remote(job_id)
        if not job:
            raise Exception(f"Failed to create job - job {job_id} not found in state")
        logger.info(f"Job {job_id} verified in state")
        
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
        except StorageError:
            # Storage module already logged the error
            logger.warning(f"Failed to save initial job metadata for {job_id}")
        except Exception as e:
            logger.error(f"Unexpected error saving job metadata: {str(e)}")
        
        # Phase 1: Run orchestrator to submit jobs
        logger.info("Phase 1: Running orchestrator agent to submit jobs...")
        if tracer:
            # Show initial budget info
            budget_summary = await actor.get_budget_summary.remote()
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
                task = Task(
                    id=result['job_id'],  # This is the Masumi job ID
                    agent_name=result.get('agent_name', 'Unknown'),
                    input_data=result.get('input_data', {})
                )
                await actor.add_task.remote(job_id, task)
                submitted_tasks.append({
                    "masumi_job_id": task.id,
                    "agent": task.agent_name
                })
                
                # Show budget consumption info
                if tracer and 'budget_info' in result:
                    budget_info = result['budget_info']
                    actual_cost = budget_info.get('job_cost', 0)
                    expected_cost = budget_info.get('expected_cost', 0)
                    
                    await tracer.markdown(f"💸 Submitted to **{task.agent_name}**:")
                    
                    # Show cost and warn if actual price differs from expected
                    if expected_cost > 0 and abs(actual_cost - expected_cost) > 0.01:
                        price_diff_pct = ((actual_cost - expected_cost) / expected_cost) * 100
                        if actual_cost > expected_cost:
                            await tracer.markdown(f"   - Cost: {actual_cost:.1f} USDM")
                            await tracer.markdown(f"   - ⚠️ Price higher than expected: {actual_cost:.1f} USDM (expected {expected_cost:.1f} USDM, +{price_diff_pct:.0f}%)")
                        else:
                            await tracer.markdown(f"   - Cost: {actual_cost:.1f} USDM")
                            await tracer.markdown(f"   - ✅ Price lower than expected: {actual_cost:.1f} USDM (expected {expected_cost:.1f} USDM, {price_diff_pct:.0f}%)")
                    else:
                        await tracer.markdown(f"   - Cost: {actual_cost:.1f} USDM")
                    
                    await tracer.markdown(f"   - Remaining budget: {budget_info.get('total_remaining', 0):.1f} USDM")
            
            # Handle failed task submission
            elif not result.get('success'):
                await handle_job_submission_error(result, orchestrator_result.new_items, i, tracer)
        
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
        await _poll_masumi_jobs_async(actor, job_id, tracer)
        
        # Get the completed job execution results
        results = await actor.get_job_results.remote(job_id)
        
        # Check for error in results
        if 'error' in results:
            logger.error(f"Error getting job results: {results['error']}")
            raise Exception(f"Job error: {results['error']}")
        
        if not results.get('is_complete', False):
            raise Exception("Not all jobs completed successfully")
        
        logger.info(f"All jobs complete. Successful: {results['completed_jobs']}, Failed: {results['failed_jobs']}")
        
        # Phase 3: Run refinement agent to plan deeper research
        logger.info("Phase 3: Running refinement agent to analyze results and plan follow-up...")
        if tracer:
            await tracer.markdown("\n🔬 **Phase 3: Refinement Analysis**")
            
            # Show current budget status
            budget_summary = await actor.get_budget_summary.remote()
            await tracer.markdown(f"💰 **Remaining Budget**: {budget_summary['total_remaining']:.1f} USDM")
            await tracer.markdown("🤔 Analyzing first round results...")
        
        # Prepare results for refinement agent
        budget_summary = await actor.get_budget_summary.remote()
        refinement_prompt = format_results_for_refinement(
            results['results'],
            audience_description,
            budget_summary['total_remaining']
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
                task = Task(
                    id=result['job_id'],
                    agent_name=result.get('agent_name', 'Unknown'),
                    input_data=result.get('input_data', {})
                )
                await actor.add_task.remote(job_id, task)
                second_round_tasks.append({
                    "masumi_job_id": task.id,
                    "agent": task.agent_name
                })
                logger.info(f"Refinement: Successfully submitted task to {task.agent_name}")
                
                # Show budget info
                if tracer and 'budget_info' in result:
                    budget_info = result['budget_info']
                    actual_cost = budget_info.get('job_cost', 0)
                    
                    await tracer.markdown(f"💸 Submitted deep-dive to **{task.agent_name}**:")
                    await tracer.markdown(f"   - Cost: {actual_cost:.1f} USDM")
                    await tracer.markdown(f"   - Remaining: {budget_info.get('total_remaining', 0):.1f} USDM")
            
            # Handle errors
            elif result.get('error'):
                logger.error(f"Refinement submission error: {result}")
                await handle_job_submission_error(result, refinement_result.new_items, i, tracer)
        
        if tracer:
            await tracer.markdown(f"\n✅ **Submitted {len(second_round_tasks)} deep-dive tasks**")
        
        # Phase 4: Poll for second round results
        logger.info("Phase 4: Second round results phase...")
        if tracer:
            await tracer.markdown("\n🔍 **Phase 4: Deep-Dive Research**")
            
        if second_round_tasks:
            if tracer:
                await tracer.markdown("⏳ Polling for deep-dive results...")
            
            # Verify state before second polling
            job_summary = await actor.get_job_summary.remote(job_id)
            logger.info(f"Before second polling - Job {job_id}: {job_summary['total_tasks']} tasks")
            
            # Poll again for second round
            await _poll_masumi_jobs_async(actor, job_id, tracer)
            
            # Get all results (both rounds)
            results = await actor.get_job_results.remote(job_id)
            
            # Check for error in results
            if 'error' in results:
                logger.error(f"Error getting job results after second round: {results['error']}")
                raise Exception(f"Job error: {results['error']}")
            
            if not results.get('is_complete', False):
                raise Exception("Not all jobs completed successfully")
        else:
            if tracer:
                await tracer.markdown("ℹ️ No additional research needed - initial results were comprehensive")
        
        # Phase 5: Run consolidator to synthesize all results
        logger.info("Phase 5: Running consolidator agent to synthesize all results...")
        if tracer:
            await tracer.markdown("\n🧪 **Phase 5: Synthesis**")
            await tracer.markdown("🤖 Running consolidator agent with all results...")
        
        # Prepare results for consolidator with token management
        research_results, token_metadata = format_research_results(
            results['results'], 
            model="gpt-4.1",
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
            final_budget = await actor.get_budget_summary.remote()
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
        except StorageError:
            # Storage module already logged the error
            logger.warning("Failed to save consolidated profile to storage")
        except Exception as e:
            logger.error(f"Unexpected error saving consolidated profile: {str(e)}")
        
        # Prepare final response
        return create_success_response(
            audience_description=audience_description,
            profile=consolidator_result.final_output,
            job_id=job_id,
            results=results,
            submitted_tasks=submitted_tasks,
            second_round_tasks=second_round_tasks
        )
        
    except JobNotFoundError as e:
        logger.error(f"Job not found: {str(e)}")
        return create_error_response(audience_description, f"Job tracking error: {str(e)}")
    except (RuntimeError, AttributeError) as e:
        logger.error(f"Configuration or state error: {str(e)}")
        return create_error_response(audience_description, f"System configuration error: {str(e)}")
    except Exception as e:
        logger.error(f"Unexpected error generating audience profile: {str(e)}")
        return create_error_response(audience_description, f"Unexpected error: {str(e)}")


def generate_audience_profile_sync(audience_description: str) -> Dict[str, Any]:
    """
    Synchronous wrapper for generate_audience_profile.
    
    Creates a new event loop and runs the async generate_audience_profile
    function synchronously. Used for compatibility with synchronous code
    that cannot use async/await.
    
    Args:
        audience_description: Description of the target audience
        
    Returns:
        Dict with profile data and metadata
        
    Note:
        This creates a new event loop, so it should not be called from
        within an existing async context.
    """
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        return loop.run_until_complete(generate_audience_profile(audience_description))
    finally:
        loop.close()