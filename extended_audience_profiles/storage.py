"""
Storage module for persisting Masumi agent results to filesystem
"""
import os
import json
import asyncio
import aiofiles
from pathlib import Path
from datetime import datetime
from typing import Dict, Any, List, Optional
import logging

logger = logging.getLogger(__name__)


class ResultStorage:
    """Manages storage of agent results and consolidated profiles"""
    
    def __init__(self, base_dir: Optional[str] = None):
        """
        Initialize storage with base directory.
        
        Args:
            base_dir: Base directory for storing results (defaults to env var or "data/results")
        """
        # Use environment variable if available, otherwise use provided or default
        if base_dir is None:
            base_dir = os.getenv('RESULTS_STORAGE_DIR', 'data/results')
        
        self.base_dir = Path(base_dir)
        self.jobs_dir = self.base_dir / "jobs"
        self.index_file = self.base_dir / "index.json"
        
        # Configuration from environment
        self.keep_days = int(os.getenv('RESULTS_KEEP_DAYS', '90'))
        self.compress_after_days = int(os.getenv('RESULTS_COMPRESS_AFTER_DAYS', '30'))
        
        # Create directories if they don't exist
        self._ensure_directories()
        
        logger.info(f"ResultStorage initialized with base_dir: {self.base_dir}")
    
    def _ensure_directories(self) -> None:
        """Ensure all required directories exist"""
        self.base_dir.mkdir(parents=True, exist_ok=True)
        self.jobs_dir.mkdir(exist_ok=True)
    
    def _get_job_dir(self, job_id: str) -> Path:
        """Get directory path for a job"""
        return self.jobs_dir / job_id
    
    def _get_agent_dir(self, job_id: str, agent_name: str) -> Path:
        """Get agent-specific directory for a job"""
        # Clean agent name for filesystem
        safe_agent_name = agent_name.replace('/', '-').replace(' ', '_')
        return self._get_job_dir(job_id) / safe_agent_name
    
    def _get_consolidated_dir(self, job_id: str) -> Path:
        """Get consolidated directory for a job"""
        return self._get_job_dir(job_id) / "consolidated"
    
    async def _write_json(self, file_path: Path, data: Dict[str, Any]) -> None:
        """Write JSON data to file"""
        async with aiofiles.open(file_path, 'w', encoding='utf-8') as f:
            await f.write(json.dumps(data, indent=2))
    
    async def _read_json(self, file_path: Path) -> Dict[str, Any]:
        """Read JSON data from file"""
        if not file_path.exists():
            return {}
        async with aiofiles.open(file_path, 'r', encoding='utf-8') as f:
            return json.loads(await f.read())
    
    async def _write_markdown(self, file_path: Path, content: str) -> None:
        """Write markdown content to file"""
        async with aiofiles.open(file_path, 'w', encoding='utf-8') as f:
            await f.write(content)
    
    async def _read_markdown(self, file_path: Path) -> str:
        """Read markdown content from file"""
        if not file_path.exists():
            return ""
        async with aiofiles.open(file_path, 'r', encoding='utf-8') as f:
            return await f.read()
    
    async def save_agent_result(
        self,
        job_id: str,
        masumi_job_id: str,
        agent_name: str,
        content: str,
        metadata: Dict[str, Any]
    ) -> None:
        """
        Save an individual agent's result.
        
        Args:
            job_id: ID of the job execution
            masumi_job_id: ID of the Masumi network job
            agent_name: Name of the agent
            content: The agent's response content
            metadata: Additional metadata (input_data, timestamps, etc.)
        """
        try:
            # Ensure directories exist
            agent_dir = self._get_agent_dir(job_id, agent_name)
            agent_dir.mkdir(parents=True, exist_ok=True)
            
            # File paths - use masumi_job_id for markdown filename
            md_file = agent_dir / f"{masumi_job_id}.md"
            json_file = agent_dir / "metadata.json"
            
            # Format markdown content
            question = metadata.get('input_data', {}).get('question', 'N/A')
            timestamp = datetime.now().isoformat()
            md_content = f"""# {agent_name} Research Results

**Job ID**: {masumi_job_id}
**Question**: {question}
**Generated**: {timestamp}

---

{content}
"""
            
            # Write markdown file
            await self._write_markdown(md_file, md_content)
            
            # Load existing metadata if it exists
            existing_metadata = await self._read_json(json_file)
            if not existing_metadata:
                existing_metadata = {"agent_name": agent_name, "jobs": []}
            # Ensure jobs array exists (for backward compatibility)
            if 'jobs' not in existing_metadata:
                existing_metadata['jobs'] = []
            
            # Prepare job metadata
            job_metadata = {
                "masumi_job_id": masumi_job_id,
                "input": metadata.get('input_data', {}),
                "started_at": datetime.fromtimestamp(metadata.get('started_at', 0)).isoformat() if metadata.get('started_at') else None,
                "completed_at": datetime.fromtimestamp(metadata.get('completed_at', 0)).isoformat() if metadata.get('completed_at') else None,
                "duration_seconds": metadata.get('duration', 0),
                "status": "completed"
            }
            
            # Add hash fields if present
            if 'hashes' in metadata:
                job_metadata['hashes'] = metadata['hashes']
            
            # Add new job to the jobs array
            existing_metadata['jobs'].append(job_metadata)
            
            # Write updated JSON metadata
            await self._write_json(json_file, existing_metadata)
            
            logger.info(f"Saved agent result: {md_file}")
            
            # Update job metadata
            await self._update_job_metadata(job_id, masumi_job_id, agent_name)
            
        except Exception as e:
            logger.error(f"Error saving agent result: {str(e)}")
            raise
    
    async def save_consolidated_profile(
        self,
        job_id: str,
        profile_content: str,
        metadata: Dict[str, Any]
    ) -> None:
        """
        Save the consolidated profile.
        
        Args:
            job_id: ID of the job execution
            profile_content: The consolidated profile content
            metadata: Additional metadata (audience_description, job info, etc.)
        """
        try:
            # Ensure directories exist
            consolidated_dir = self._get_consolidated_dir(job_id)
            consolidated_dir.mkdir(parents=True, exist_ok=True)
            
            # File paths
            profile_file = consolidated_dir / "profile.md"
            summary_file = consolidated_dir / "summary.json"
            
            # Format profile content
            audience = metadata.get('audience_description', 'N/A')
            total_jobs = metadata.get('total_jobs', 0)
            timestamp = datetime.now().isoformat()
            
            md_content = f"""# Extended Audience Profile
## Audience: {audience}
## Generated: {timestamp}
## Based on: {total_jobs} research sources

---

{profile_content}

---
*Generated by Extended Audience Profiles using Masumi Network*
"""
            
            # Write profile markdown
            await self._write_markdown(profile_file, md_content)
            
            # Prepare summary
            summary = {
                "job_id": job_id,
                "audience_description": audience,
                "generated_at": timestamp,
                "total_agents": total_jobs,
                "agent_executions": metadata.get('agent_jobs', []),
                "profile_file": str(profile_file.relative_to(self.base_dir))
            }
            
            # Write summary JSON
            await self._write_json(summary_file, summary)
            
            logger.info(f"Saved consolidated profile: {profile_file}")
            
            # Update master index
            await self._update_master_index(job_id, metadata)
            
        except Exception as e:
            logger.error(f"Error saving consolidated profile: {str(e)}")
            raise
    
    async def save_job_metadata(
        self,
        job_id: str,
        metadata: Dict[str, Any]
    ) -> None:
        """
        Save or update job metadata.
        
        Args:
            job_id: ID of the job execution
            metadata: Job metadata
        """
        try:
            job_dir = self._get_job_dir(job_id)
            job_dir.mkdir(parents=True, exist_ok=True)
            
            metadata_file = job_dir / "metadata.json"
            
            # Load existing metadata if it exists
            existing_metadata = await self._read_json(metadata_file)
            
            # Update metadata
            existing_metadata.update(metadata)
            existing_metadata['last_updated'] = datetime.now().isoformat()
            
            # Write metadata
            await self._write_json(metadata_file, existing_metadata)
            
        except Exception as e:
            logger.error(f"Error saving submission metadata: {str(e)}")
            raise
    
    async def _update_job_metadata(
        self,
        job_id: str,
        masumi_job_id: str,
        agent_name: str
    ) -> None:
        """Update job metadata when an agent completes"""
        metadata_file = self._get_job_dir(job_id) / "metadata.json"
        
        # Load existing metadata
        metadata = await self._read_json(metadata_file)
        
        # Update agents list
        if 'completed_agents' not in metadata:
            metadata['completed_agents'] = []
        
        metadata['completed_agents'].append({
            'masumi_job_id': masumi_job_id,
            'agent_name': agent_name,
            'completed_at': datetime.now().isoformat()
        })
        
        # Save updated metadata
        await self.save_job_metadata(job_id, metadata)
    
    async def _update_master_index(
        self,
        job_id: str,
        metadata: Dict[str, Any]
    ) -> None:
        """Update the master index file"""
        # Load existing index
        index = await self._read_json(self.index_file)
        if not isinstance(index, list):
            index = []
        
        # Add or update job entry
        entry = {
            'job_id': job_id,
            'audience_description': metadata.get('audience_description', 'N/A'),
            'created_at': metadata.get('created_at', datetime.now().isoformat()),
            'total_agents': metadata.get('total_jobs', 0),
            'status': 'completed'
        }
        
        # Remove existing entry if present (handle both old and new format)
        index = [e for e in index if e.get('job_id', e.get('submission_id')) != job_id]
        index.append(entry)
        
        # Sort by creation date (newest first)
        index.sort(key=lambda x: x['created_at'], reverse=True)
        
        # Write updated index
        await self._write_json(self.index_file, index)
    
    async def get_job_results(self, job_id: str) -> Dict[str, Any]:
        """
        Retrieve all results for a job.
        
        Args:
            job_id: ID of the job execution
            
        Returns:
            Dictionary containing all results and metadata
        """
        job_dir = self._get_job_dir(job_id)
        if not job_dir.exists():
            return {}
        
        results = {
            'job_id': job_id,
            'metadata': {},
            'agent_results': [],
            'consolidated_profile': None
        }
        
        # Load metadata
        metadata_file = job_dir / "metadata.json"
        results['metadata'] = await self._read_json(metadata_file)
        
        # Load agent results from individual agent directories
        if job_dir.exists():
            for agent_dir in job_dir.iterdir():
                if agent_dir.is_dir() and agent_dir.name != "consolidated":
                    metadata_file = agent_dir / "metadata.json"
                    agent_metadata = await self._read_json(metadata_file)
                    if agent_metadata:
                        
                        # Handle new format with jobs array
                        if 'jobs' in agent_metadata:
                            # New format: iterate through all jobs
                            for job in agent_metadata['jobs']:
                                job_result = {
                                    'agent_name': agent_metadata['agent_name'],
                                    'masumi_job_id': job['masumi_job_id'],
                                    'input': job.get('input', {}),
                                    'started_at': job.get('started_at'),
                                    'completed_at': job.get('completed_at'),
                                    'duration_seconds': job.get('duration_seconds'),
                                    'status': job.get('status', 'unknown'),
                                    'hashes': job.get('hashes', {})
                                }
                                
                                # Load corresponding markdown
                                md_file = agent_dir / f"{job['masumi_job_id']}.md"
                                content = await self._read_markdown(md_file)
                                if content:
                                    job_result['content'] = content
                                
                                results['agent_results'].append(job_result)
                        else:
                            # Old format: single job (backward compatibility)
                            md_file = agent_dir / "result.md"
                            content = await self._read_markdown(md_file)
                            if content:
                                agent_metadata['content'] = content
                            
                            results['agent_results'].append(agent_metadata)
        
        # Load consolidated profile
        profile_file = self._get_consolidated_dir(job_id) / "profile.md"
        content = await self._read_markdown(profile_file)
        if content:
            results['consolidated_profile'] = content
        
        return results
    
    async def list_all_jobs(self) -> List[Dict[str, Any]]:
        """
        List all job executions.
        
        Returns:
            List of job summaries
        """
        index = await self._read_json(self.index_file)
        return index if isinstance(index, list) else []


# Singleton instance
storage = ResultStorage()