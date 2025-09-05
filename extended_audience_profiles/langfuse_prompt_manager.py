"""
Langfuse Prompt Management for Extended Audience Profiles
This module provides utilities for managing AI agent prompts using Langfuse.
"""

from langfuse import Langfuse
import os
import logging
from typing import Dict, Optional, List
from datetime import datetime
from dotenv import load_dotenv
from pathlib import Path

load_dotenv()

# Set up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("eap_prompt_manager")


class ExtendedAudienceProfilesPromptManager:
    """
    A utility class for managing Extended Audience Profiles agent prompts with Langfuse.
    This class provides methods for synchronizing prompts between
    local files and the Langfuse prompt management system.
    """
    # Agent name mapping for consistency
    AGENT_DISPLAY_NAMES = {
        "orchestrator": "Audience Profile Orchestrator",
        "refinement": "Audience Profile Refinement", 
        "consolidator": "Audience Profile Consolidator"
    }
    
    def __init__(self):
        """Initialize the PromptManager with a Langfuse client."""
        # Check if Langfuse environment variables are set
        if not os.getenv("LANGFUSE_PUBLIC_KEY") or not os.getenv("LANGFUSE_SECRET_KEY"):
            logger.warning("Langfuse API keys not set. Some functionality may be limited.")
            self.langfuse = None
            return
            
        # Initialize Langfuse client
        try:
            self.langfuse = Langfuse()
            # Try to validate authentication
            auth_status = self.langfuse.auth_check()
            if auth_status:
                logger.info("Successfully authenticated with Langfuse")
            else:
                logger.warning("Langfuse authentication check returned False")
        except Exception as e:
            logger.warning(f"Failed to authenticate with Langfuse: {e}")
            self.langfuse = None
    
    def upload_prompts_to_langfuse(self, prompts: Dict[str, str], label: str = "production") -> Dict[str, int]:
        """
        Upload Extended Audience Profiles prompts to Langfuse.
        
        Args:
            prompts: Dictionary of {agent_name: prompt_text}
            label: Label to apply to prompts (default: "production")
            
        Returns:
            dict: A dictionary of {agent_name: prompt_version} for created prompts
        """
        if not self.langfuse:
            logger.error("Langfuse client not initialized")
            return {}
            
        created_prompts = {}
        
        for agent_name, prompt_text in prompts.items():
            try:
                # Check if prompt already exists
                try:
                    existing_prompt = self.langfuse.get_prompt(agent_name, label=label)
                    logger.info(f"Prompt {agent_name} already exists (version {existing_prompt.version})")
                    # Create a new version
                    created_prompt = self.langfuse.create_prompt(
                        name=agent_name,
                        type="text",
                        prompt=prompt_text,
                        labels=[label],
                        config={
                            "model": "gpt-4.1",
                            "framework": "openai-agents-sdk",
                            "updated_at": datetime.now().isoformat(),
                            "project": "extended-audience-profiles"
                        }
                    )
                    created_prompts[agent_name] = created_prompt.version
                    logger.info(f"Created new version of prompt {agent_name} (version {created_prompt.version})")
                except:
                    # Create new prompt
                    created_prompt = self.langfuse.create_prompt(
                        name=agent_name,
                        type="text",
                        prompt=prompt_text,
                        labels=[label],
                        config={
                            "model": "gpt-4.1",
                            "framework": "openai-agents-sdk",
                            "created_at": datetime.now().isoformat(),
                            "project": "extended-audience-profiles"
                        }
                    )
                    created_prompts[agent_name] = created_prompt.version
                    logger.info(f"Created new prompt {agent_name} with label '{label}' (version {created_prompt.version})")
            except Exception as e:
                logger.error(f"Failed to create prompt {agent_name}: {e}")
        
        return created_prompts
    
    def get_prompts_from_langfuse(self, agent_names: List[str], label: str = "production") -> Dict[str, str]:
        """
        Fetch prompts from Langfuse for Extended Audience Profiles agents.
        
        Args:
            agent_names: List of agent names to fetch
            label: Label to use when fetching prompts (default: "production")
            
        Returns:
            dict: Dictionary of {agent_name: prompt_text}
        """
        if not self.langfuse:
            logger.error("Langfuse client not initialized")
            return {}
            
        prompts = {}
        
        logger.info(f"Fetching {len(agent_names)} prompts from Langfuse (label: '{label}')...")
        
        for agent_name in agent_names:
            try:
                langfuse_prompt = self.langfuse.get_prompt(agent_name, label=label)
                prompts[agent_name] = langfuse_prompt.prompt
                logger.info(f"  ✓ {agent_name} (v{langfuse_prompt.version})")
            except Exception as e:
                logger.warning(f"  ✗ {agent_name} - Not found with label '{label}': {e}")
        
        logger.info(f"Found {len(prompts)}/{len(agent_names)} prompts")
        return prompts
    
    def sync_local_prompts_to_langfuse(self, label: str = "production") -> Dict[str, int]:
        """
        Sync local Extended Audience Profiles prompts to Langfuse.
        
        Args:
            label: Label to apply to prompts
            
        Returns:
            dict: Created prompt versions
        """
        # Load prompts from files
        prompts_dir = Path(__file__).parent / "prompts"
        
        prompts = {}
        prompt_files = {
            "orchestrator": "orchestrator.txt",
            "refinement": "refinement.txt",
            "consolidator": "consolidator.txt"
        }
        
        for agent_name, filename in prompt_files.items():
            try:
                with open(prompts_dir / filename, "r") as f:
                    prompts[agent_name] = f.read()
                    logger.info(f"Loaded prompt for {agent_name} from {filename}")
            except Exception as e:
                logger.error(f"Failed to load prompt {filename}: {e}")
        
        if prompts:
            logger.info(f"Uploading {len(prompts)} prompts to Langfuse...")
            return self.upload_prompts_to_langfuse(prompts, label)
        else:
            logger.error("No prompts loaded")
            return {}
    
    def create_dynamic_agent_with_langfuse_prompt(
        self, 
        agent_name: str,
        tools: List,
        label: str = "production"
    ):
        """
        Create an agent with prompt fetched from Langfuse.
        
        Args:
            agent_name: Name in Langfuse (e.g., "orchestrator")
            tools: List of tools for the agent
            label: Langfuse label to use
            
        Returns:
            Agent instance with Langfuse prompt
        """
        if not self.langfuse:
            logger.error("Langfuse client not initialized")
            return None
            
        from agents import Agent
        
        try:
            # Fetch prompt from Langfuse
            langfuse_prompt = self.langfuse.get_prompt(agent_name, label=label)
            prompt_text = langfuse_prompt.prompt

            display_name = self.AGENT_DISPLAY_NAMES.get(
                agent_name,
                f"Audience {agent_name.replace('_', ' ').title()}"
            )
            
            logger.info(f"Creating {display_name} agent with Langfuse prompt v{langfuse_prompt.version}")
            
            # Create agent with Langfuse prompt
            agent = Agent(
                name=display_name,
                instructions=prompt_text,
                tools=tools,
                model="gpt-4.1"
            )
            
            return agent
            
        except Exception as e:
            logger.error(f"Failed to create agent {agent_name} with Langfuse prompt: {e}")
            raise
    
    def get_comparison_report(self, label: str = "production") -> str:
        """
        Compare local prompts with Langfuse versions.
        
        Args:
            label: Langfuse label to compare against
            
        Returns:
            str: Comparison report
        """
        if not self.langfuse:
            return "Langfuse client not initialized"
            
        # Load local prompts
        prompts_dir = Path(__file__).parent / "prompts"
        local_prompts = {}
        
        prompt_files = {
            "orchestrator": "orchestrator.txt",
            "refinement": "refinement.txt", 
            "consolidator": "consolidator.txt"
        }
        
        for agent_name, filename in prompt_files.items():
            try:
                with open(prompts_dir / filename, "r") as f:
                    local_prompts[agent_name] = f.read()
            except Exception as e:
                logger.error(f"Failed to load prompt {filename}: {e}")
        
        report = []
        report.append(f"\n{'='*60}")
        report.append("PROMPT COMPARISON REPORT - Extended Audience Profiles")
        report.append(f"{'='*60}\n")
        
        for agent_name, local_prompt in local_prompts.items():
            report.append(f"Agent: {agent_name}")
            report.append("-" * 40)
            
            try:
                langfuse_prompt = self.langfuse.get_prompt(agent_name, label=label)
                remote_prompt = langfuse_prompt.prompt
                
                if local_prompt.strip() == remote_prompt.strip():
                    report.append(f"✅ In sync (version {langfuse_prompt.version})")
                else:
                    report.append(f"❌ Out of sync")
                    report.append(f"   Langfuse version: {langfuse_prompt.version}")
                    report.append(f"   Local length: {len(local_prompt)} chars")
                    report.append(f"   Remote length: {len(remote_prompt)} chars")
                    
                    # Show first difference
                    for i, (l, r) in enumerate(zip(local_prompt, remote_prompt)):
                        if l != r:
                            report.append(f"   First difference at char {i}")
                            break
                            
            except Exception as e:
                report.append(f"⚠️  Not found in Langfuse: {e}")
            
            report.append("")
        
        return "\n".join(report)


# Utility functions for easy access
def upload_current_prompts(label: str = "production") -> Dict[str, int]:
    """Upload current Extended Audience Profiles prompts to Langfuse."""
    manager = ExtendedAudienceProfilesPromptManager()
    return manager.sync_local_prompts_to_langfuse(label)


def get_prompts_from_langfuse(label: str = "production") -> Dict[str, str]:
    """Get Extended Audience Profiles prompts from Langfuse."""
    manager = ExtendedAudienceProfilesPromptManager()
    agent_names = ["orchestrator", "refinement", "consolidator"]
    return manager.get_prompts_from_langfuse(agent_names, label)


def compare_prompts(label: str = "production"):
    """Compare local prompts with Langfuse versions."""
    manager = ExtendedAudienceProfilesPromptManager()
    print(manager.get_comparison_report(label))
