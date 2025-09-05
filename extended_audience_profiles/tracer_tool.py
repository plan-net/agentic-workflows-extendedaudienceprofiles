"""
Tracer Tool for Agent Reasoning

This module provides a tool that allows agents to share their thoughts and reasoning
with users through the kodosumi UI tracer.
"""
from pydantic import BaseModel, Field
from agents import FunctionTool, RunContextWrapper
from typing import Any


class ThinkOutLoudArgs(BaseModel):
    """Arguments for the think_out_loud tool."""
    thought: str = Field(description="What you're thinking, observing, or doing")


class TracerTool:
    """
    A tool that allows agents to share their thoughts and reasoning with users
    through the kodosumi UI tracer.
    """
    
    def __init__(self, tracer):
        """
        Initialize the TracerTool with a kodosumi Tracer instance.
        
        Args:
            tracer: The kodosumi Tracer for sending updates to the UI
        """
        self.tracer = tracer
        
    def create_tool(self) -> FunctionTool:
        """
        Create a FunctionTool that agents can use to share their thoughts.
        
        Returns:
            FunctionTool configured for thinking out loud
        """
        async def think_out_loud(ctx: RunContextWrapper[Any], args: str) -> str:
            """
            Send a thought or observation to the user interface.
            Uses sync method inside async function for compatibility.
            
            Args:
                ctx: The run context (provided by the agent framework)
                args: JSON string containing the thought to share
                
            Returns:
                Confirmation message
            """
            try:
                parsed = ThinkOutLoudArgs.model_validate_json(args)
                # Use markdown_sync even in async function to avoid event loop issues
                self.tracer.markdown_sync(f"💭 {parsed.thought}")
                return "Thought shared with user"
            except Exception as e:
                # If there's an error, still try to share something
                self.tracer.markdown_sync(f"💭 [Agent thinking... error sharing full thought: {str(e)}]")
                return f"Error sharing thought: {str(e)}"
            
        # Get the schema and ensure additionalProperties is false
        schema = ThinkOutLoudArgs.model_json_schema()
        schema["additionalProperties"] = False
        
        return FunctionTool(
            name="think_out_loud",
            description=(
                "Share your current thoughts, reasoning, observations, or actions with the user. "
                "Use this to make your thinking process transparent. Be natural and conversational."
            ),
            params_json_schema=schema,
            on_invoke_tool=think_out_loud
        )
