import ray
import fastapi
import asyncio
from kodosumi.core import Launch, ServeAPI, InputsError, Tracer
from kodosumi.core import forms as F
from kodosumi import dtypes
from ray import serve
from .agent import generate_audience_profile

app = ServeAPI()


# Form model for input validation
profile_model = F.Model(
    F.Markdown("""
    # Extended Audience Profiles
    Generate comprehensive audience profiles using advanced web research capabilities.
    """),
    F.Break(),
    F.InputArea(
        label="Audience Description", 
        name="audience_description",
        placeholder="Describe your target audience (e.g., 'Millennials interested in sustainable fashion')",
        required=True
    ),
    F.Submit("Generate Profile"),
    F.Cancel("Cancel")
)




@app.enter(
    path="/",
    model=profile_model,
    summary="Extended Audience Profiles",
    description="Generate comprehensive audience profiles using Masumi Network agents",
    tags=["AI", "Masumi", "Audience Research"],
    version="1.0.0"
)
async def enter(request: fastapi.Request, inputs: dict):
    """
    Endpoint function for handling audience profile generation requests.
    
    Main entry point for the Extended Audience Profiles service. Validates
    input and launches the profile generation workflow.
    
    Args:
        request: FastAPI request object
        inputs: Dictionary containing 'audience_description' field
        
    Returns:
        Launch object that executes the profile generation
        
    Raises:
        InputsError: If audience_description is missing or empty
    """
    # Parse and cleanse inputs
    audience_description = inputs.get("audience_description", "").strip()
    
    # Validate inputs
    error = InputsError()
    if not audience_description:
        error.add(audience_description="Please enter an audience description.")
    if error.has_errors():
        raise error
    
    # Launch execution
    return Launch(
        request,
        run_profile_generation,
        inputs={
            "audience_description": audience_description
        }
    )


@serve.deployment
@serve.ingress(app)
class ExtendedAudienceProfiles: 
    pass

fast_app = ExtendedAudienceProfiles.bind()


@app.get("/health")
async def health():
    """
    Health check endpoint.
    
    Simple health check endpoint for monitoring and load balancer checks.
    
    Returns:
        Dict with status 'healthy' and service name
    """
    return {"status": "healthy", "service": "extended-audience-profiles"}


async def run_profile_generation(inputs: dict, tracer: Tracer):
    """
    Execute the profile generation and stream results via tracer.
    
    Main workflow execution function that runs the 5-phase profile generation
    process and streams progress updates through the tracer interface.
    
    Args:
        inputs: Dictionary containing 'audience_description'
        tracer: Kodosumi tracer for streaming progress updates
        
    Note:
        This function is executed asynchronously by the Launch system
        and streams all output through the tracer for real-time updates.
    """
    audience_description = inputs.get("audience_description", "")
    
    # Show progress
    await tracer.markdown(f"**Target Audience:** {audience_description}")
    
    await tracer.markdown("\n🚀 **Phase 1: Orchestration**")
    
    # Execute the profile generation directly (not in Ray remote)
    # This allows us to use the tracer directly
    result = await generate_audience_profile(audience_description, tracer)
    
    if not result["success"]:
        await tracer.markdown(f"❌ **Error:** {result['error']}")
        return dtypes.Markdown(body=f"## Error\n\n{result['error']}")
    
    
    # Extract metadata
    metadata = result.get("metadata", {})
    
    # Format result for admin panel
    formatted_result = f"## Extended Audience Profile\n\n"
    formatted_result += f"**Target Audience:** {audience_description}\n\n"
    formatted_result += "---\n\n"
    formatted_result += result["profile"]
    
    # Add metadata footer
    formatted_result += "\n\n---\n"
    formatted_result += f"*Generated via Masumi Network*\n"
    formatted_result += f"- Job ID: `{metadata.get('job_id', 'N/A')}`\n"
    
    if metadata:
        formatted_result += f"- Total Jobs: {metadata.get('total_jobs', 'N/A')}\n"
        formatted_result += f"- Completed: {metadata.get('completed_jobs', 'N/A')}\n"
        formatted_result += f"- Failed: {metadata.get('failed_jobs', 'N/A')}\n"
        formatted_result += f"- Duration: {metadata.get('total_duration', 'N/A'):.1f}s\n"
    
    return dtypes.Markdown(body=formatted_result)