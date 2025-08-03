# CLAUDE.md

Development principles for Extended Audience Profiles - a multi-agent orchestration system using Masumi Network.

## Core Development Principles

### 1. ALWAYS Build Generalized Solutions
```python
# ❌ WRONG: Agent-specific code
if agent_name == "ask-the-crowd":
    transformed_data["survey_mode"] = [0]

# ✅ RIGHT: Schema-driven transformation
if original_type == 'option' and 'enum' in field_schema:
    index = field_schema['enum'].index(value)
    transformed_data[field] = [index]
```

### 2. Budget Strategy is Sacred
- **Orchestrator**: MUST spend 30-50% of total budget
- **Refinement**: MUST use 100% of remaining budget
- **Never** leave budget unspent in refinement phase

### 3. Quality Over Quantity
- Consolidator should select best insights, not list everything
- Focus on narrative quality, not data dumps
- "Statistics are starting points for insight, not endpoints"

### 4. Token Awareness
- Always check token limits before processing
- Implement smart truncation (remove sections, not arbitrary cuts)
- Model limits: gpt-4.1 (1M+), o3-mini (200k)

## Key Patterns

### State Management
```python
# Use Ray Actor pattern for distributed state
actor = StateActor.remote(budget_config, agents_config)
await actor.update_tasks.remote(job_id, task_updates)
```

### Citation Format
```
[Original Source, Year][via: agent_name]
Example: [Nielsen, 2023][via: advanced-web-research]
```

### Exception Hierarchy
```python
ExtendedAudienceProfilesError (base)
├── BudgetExceededError
├── AgentNotFoundError
├── ValidationError
└── MasumiNetworkError
```

### Schema Validation
- Get schema first: `get_agent_input_schema()`
- Validate against schema
- Transform enums to indices for option fields
- Handle array types properly

## Architecture Rules

1. **5-Phase Workflow**: Orchestration → Collection → Refinement → Deep-Dive → Synthesis
2. **External Prompts**: Keep in `prompts/` directory, never hardcode
3. **Unified State**: Single StateActor manages jobs + budget
4. **Async Everything**: Use async/await for all I/O operations
5. **Type Safety**: Always use type hints and dataclasses

## Common Pitfalls

- **Don't** create agent-specific validation logic
- **Don't** ignore actual_cost from purchase responses
- **Don't** wait for job completion in tools (return immediately)
- **Don't** remove citations during truncation
- **Don't** create files unless explicitly requested

## Testing Approach
```python
# Mock at the right level
with patch('extended_audience_profiles.masumi.MasumiClient') as mock:
    # Test business logic, not external APIs
```

## System Architecture

### Layer Hierarchy
```
Kodosumi Service (query.py)
    ↓
Orchestration Engine (agent.py)
    ↓
Masumi Network (masumi.py)
    ↓
State/Storage (state.py, storage.py)
```
**Rule**: Never bypass layers (e.g., don't call Masumi from query.py)

### Job Lifecycle
```
Submit → Background Poll → Store → Synthesize
```
- Tools return immediately after submission
- Polling runs async with exponential backoff (10s → 60s)
- Results stored immediately upon completion

### Masumi API Flow
```python
# Always this exact sequence:
response = await client.start_job(agent_name, input_data)
purchase = await client.create_purchase(response, input_data, agent_name)
# Then poll in background - NEVER wait in tool
status = await client.poll_job_status(job_id, agent_name)
```

### Budget State Machine
```
reserve → execute → record_cost | release_on_error
```
- Use actual_cost from purchase response
- ALWAYS release reservation on ANY error

## Agent Constraints

- **GWI**: Short prompts (5-15 words best), Chat ID for follow-ups
- **Ask-the-crowd**: EU-only, statements not questions, age 18+
- **Web Research**: No limits, best for trends/context

## Storage Patterns

```
data/results/jobs/{job_id}/
├── {agent_name}/{masumi_job_id}.md  # NOT result.md
├── consolidated/profile.md
└── metadata.json
```

## Error Recovery

- Continue with partial results (graceful degradation)
- Classify errors: transient (retry) vs permanent (fail)
- Single agent failure ≠ job failure

## Quick Reference

- Models: gpt-4.1 (all agents)
- Prices: GWI (3 USDM), Web Research (2.5 USDM), Ask-the-crowd (5 USDM)
- Commands: `just start`, `just test`, `just stop`