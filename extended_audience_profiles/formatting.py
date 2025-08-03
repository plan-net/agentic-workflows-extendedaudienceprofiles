"""
Formatting utilities for Extended Audience Profiles
"""
import asyncio
from typing import Dict, Any, List, Optional, Tuple

from .context_management import ContextManager, TruncationStrategy


def format_research_results(
    results: Dict[str, Any], 
    model: str = "o3-mini",
    tracer=None
) -> tuple[str, Dict[str, Any]]:
    """
    Format research results for consolidator agent with token management.
    
    Returns:
        Tuple of (formatted_results, token_metadata)
    """
    context_mgr = ContextManager(model)
    
    # First check if results fit within context
    fits, token_metadata = context_mgr.check_token_fit(results)
    
    # Log token analysis
    import logging
    logger = logging.getLogger(__name__)
    logger.info(f"Token analysis: {token_metadata['total_tokens']} tokens, "
                f"{token_metadata['percentage_used']:.1f}% of budget")
    
    # Display token info in tracer if available
    if tracer:
        asyncio.create_task(display_token_analysis(tracer, results, token_metadata))
    
    # If results don't fit, truncate them
    if not fits:
        logger.warning(f"Results exceed context window. Applying truncation strategy.")
        budget = context_mgr.get_token_budget()
        
        # Truncate results to fit
        truncated_results, truncation_info = context_mgr.truncate_results(
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


def format_results_for_refinement(
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


async def display_token_analysis(tracer, results: Dict[str, Any], token_metadata: Dict[str, Any]):
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


def find_agent_name_from_previous_calls(items: List[Any], current_index: int) -> Optional[str]:
    """
    Look backwards through tool call history to find the agent name.
    
    This function searches through previous tool calls to find the most recent
    execute_agent_job call and extracts the agent_name parameter from it.
    Used when an error occurs and we need to identify which agent failed.
    
    Args:
        items: List of tool call items from the conversation history
        current_index: Current position in the items list
        
    Returns:
        Optional[str]: Agent name if found, None otherwise
    """
    import json
    
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
            except (json.JSONDecodeError, AttributeError):
                pass
    return None