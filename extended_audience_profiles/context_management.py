"""
Context window management - token counting and truncation strategies
"""
import re
import tiktoken
from enum import Enum
from typing import Dict, List, Tuple, Optional
from dataclasses import dataclass
import logging

logger = logging.getLogger(__name__)


@dataclass
class TokenBudget:
    """Manages token budget for model context windows."""
    total_tokens: int
    system_tokens: int = 5000  # Reserved for system prompt and instructions
    output_buffer: int = 5000  # Reserved for model output
    safety_margin: int = 10000  # Safety buffer to prevent edge cases
    
    @property
    def available_tokens(self) -> int:
        """Calculate tokens available for research results."""
        return self.total_tokens - self.system_tokens - self.output_buffer - self.safety_margin
    
    @property
    def reserved_tokens(self) -> int:
        """Total reserved tokens."""
        return self.system_tokens + self.output_buffer + self.safety_margin


class TruncationStrategy(Enum):
    """Available truncation strategies."""
    PROPORTIONAL = "proportional"  # Each result gets proportional share
    SMART_SECTIONS = "smart"  # Remove less important sections first


@dataclass
class TruncationResult:
    """Result of a truncation operation."""
    truncated_text: str
    original_tokens: int
    truncated_tokens: int
    was_truncated: bool
    strategy_used: TruncationStrategy
    sections_removed: List[str] = None


class ContextManager:
    """Unified context window management - handles token counting and truncation."""
    
    # Model to context window mapping
    MODEL_CONTEXT_WINDOWS = {
        "o3-mini": 200000,
        "gpt-4": 8192,
        "gpt-4-32k": 32768,
        "gpt-4-turbo": 128000,
        "gpt-4o": 128000,
        "gpt-4o-mini": 128000,
    }
    
    # Model to encoding mapping (for tiktoken)
    MODEL_ENCODINGS = {
        "o3-mini": "cl100k_base",  # Using GPT-4 encoding as approximation
        "gpt-4": "cl100k_base",
        "gpt-4-32k": "cl100k_base",
        "gpt-4-turbo": "cl100k_base",
        "gpt-4o": "cl100k_base",
        "gpt-4o-mini": "cl100k_base",
    }
    
    # Section patterns to identify different parts of content
    SECTION_PATTERNS = {
        'executive_summary': r'(?i)(?:executive summary|summary|overview).*?(?=\n#|\n##|\Z)',
        'key_findings': r'(?i)(?:key findings|main findings|highlights).*?(?=\n#|\n##|\Z)',
        'methodology': r'(?i)(?:methodology|methods|approach).*?(?=\n#|\n##|\Z)',
        'detailed_analysis': r'(?i)(?:detailed analysis|deep dive|in-depth).*?(?=\n#|\n##|\Z)',
        'examples': r'(?i)(?:examples?|case studies?|scenarios?).*?(?=\n#|\n##|\Z)',
        'recommendations': r'(?i)(?:recommendations?|next steps?|action items?).*?(?=\n#|\n##|\Z)',
        'citations': r'(?i)(?:references?|citations?|sources?).*?(?=\n#|\n##|\Z)',
    }
    
    # Priority order for sections (higher priority = keep longer)
    SECTION_PRIORITY = [
        'citations',  # Never remove citations
        'executive_summary',
        'key_findings',
        'recommendations',
        'detailed_analysis',
        'methodology',
        'examples',
    ]
    
    def __init__(self, model: str = "o3-mini"):
        self.model = model
        self._encoders = {}
    
    def get_encoder(self, model: str) -> tiktoken.Encoding:
        """Get or create encoder for a model."""
        encoding_name = self.MODEL_ENCODINGS.get(model, "cl100k_base")
        
        if encoding_name not in self._encoders:
            self._encoders[encoding_name] = tiktoken.get_encoding(encoding_name)
        
        return self._encoders[encoding_name]
    
    def estimate_tokens(self, text: str, model: str = None) -> int:
        """Estimate token count for a given text and model."""
        if model is None:
            model = self.model
            
        if not text:
            return 0
        
        try:
            encoder = self.get_encoder(model)
            tokens = encoder.encode(text)
            return len(tokens)
        except Exception as e:
            logger.warning(f"Error counting tokens with tiktoken: {e}. Using approximation.")
            # Fallback: approximate 1 token ≈ 4 characters
            return len(text) // 4
    
    def get_context_window(self, model: str = None) -> int:
        """Get context window size for a model."""
        if model is None:
            model = self.model
        return self.MODEL_CONTEXT_WINDOWS.get(model, 128000)  # Default to GPT-4 size
    
    def get_token_budget(self, model: str = None) -> TokenBudget:
        """Get token budget for a model."""
        if model is None:
            model = self.model
        total_tokens = self.get_context_window(model)
        return TokenBudget(total_tokens=total_tokens)
    
    def analyze_results_tokens(self, results: Dict[str, Dict]) -> Tuple[Dict[str, int], int]:
        """
        Analyze token usage for research results.
        
        Returns:
            Tuple of (per_result_tokens, total_tokens)
        """
        per_result_tokens = {}
        total_tokens = 0
        
        for job_id, data in results.items():
            result_text = data.get('result', '')
            tokens = self.estimate_tokens(result_text)
            per_result_tokens[job_id] = tokens
            total_tokens += tokens
            
            logger.debug(f"Job {job_id}: {tokens} tokens")
        
        return per_result_tokens, total_tokens
    
    def check_token_fit(self, results: Dict[str, Dict]) -> Tuple[bool, Dict[str, any]]:
        """
        Check if results fit within model's context window.
        
        Returns:
            Tuple of (fits_in_context, metadata)
        """
        budget = self.get_token_budget()
        per_result_tokens, total_tokens = self.analyze_results_tokens(results)
        
        fits = total_tokens <= budget.available_tokens
        
        metadata = {
            'total_tokens': total_tokens,
            'available_tokens': budget.available_tokens,
            'total_context': budget.total_tokens,
            'reserved_tokens': budget.reserved_tokens,
            'percentage_used': (total_tokens / budget.available_tokens * 100) if budget.available_tokens > 0 else 0,
            'fits_in_context': fits,
            'per_result_tokens': per_result_tokens
        }
        
        return fits, metadata
    
    def truncate_results(
        self,
        results: Dict[str, Dict],
        token_budget: int,
        strategy: TruncationStrategy = TruncationStrategy.SMART_SECTIONS
    ) -> Tuple[Dict[str, Dict], Dict[str, TruncationResult]]:
        """
        Truncate research results to fit within token budget.
        
        Returns:
            Tuple of (truncated_results, truncation_metadata)
        """
        if strategy == TruncationStrategy.PROPORTIONAL:
            return self._truncate_proportional(results, token_budget)
        else:  # SMART_SECTIONS
            return self._truncate_smart_sections(results, token_budget)
    
    def _truncate_proportional(
        self,
        results: Dict[str, Dict],
        token_budget: int
    ) -> Tuple[Dict[str, Dict], Dict[str, TruncationResult]]:
        """Truncate each result proportionally."""
        truncated_results = {}
        truncation_metadata = {}
        
        # Calculate total tokens
        total_tokens = sum(
            self.estimate_tokens(data.get('result', ''))
            for data in results.values()
        )
        
        if total_tokens <= token_budget:
            # No truncation needed
            for job_id, data in results.items():
                truncated_results[job_id] = data.copy()
                truncation_metadata[job_id] = TruncationResult(
                    truncated_text=data.get('result', ''),
                    original_tokens=self.estimate_tokens(data.get('result', '')),
                    truncated_tokens=self.estimate_tokens(data.get('result', '')),
                    was_truncated=False,
                    strategy_used=TruncationStrategy.PROPORTIONAL
                )
            return truncated_results, truncation_metadata
        
        # Calculate proportional budget for each result
        for job_id, data in results.items():
            result_text = data.get('result', '')
            original_tokens = self.estimate_tokens(result_text)
            
            # Proportional share of budget
            result_budget = int((original_tokens / total_tokens) * token_budget)
            
            # Truncate to fit budget
            truncated_text = self._truncate_to_token_limit(result_text, result_budget)
            
            truncated_results[job_id] = data.copy()
            truncated_results[job_id]['result'] = truncated_text
            
            truncation_metadata[job_id] = TruncationResult(
                truncated_text=truncated_text,
                original_tokens=original_tokens,
                truncated_tokens=self.estimate_tokens(truncated_text),
                was_truncated=len(truncated_text) < len(result_text),
                strategy_used=TruncationStrategy.PROPORTIONAL
            )
        
        return truncated_results, truncation_metadata
    
    def _truncate_smart_sections(
        self,
        results: Dict[str, Dict],
        token_budget: int
    ) -> Tuple[Dict[str, Dict], Dict[str, TruncationResult]]:
        """Truncate by removing less important sections first."""
        truncated_results = {}
        truncation_metadata = {}
        
        current_total = 0
        
        for job_id, data in results.items():
            result_text = data.get('result', '')
            original_tokens = self.estimate_tokens(result_text)
            
            # Calculate remaining budget for this result
            remaining_budget = token_budget - current_total
            
            if original_tokens <= remaining_budget:
                # Fits without truncation
                truncated_results[job_id] = data.copy()
                truncation_metadata[job_id] = TruncationResult(
                    truncated_text=result_text,
                    original_tokens=original_tokens,
                    truncated_tokens=original_tokens,
                    was_truncated=False,
                    strategy_used=TruncationStrategy.SMART_SECTIONS
                )
                current_total += original_tokens
            else:
                # Need to truncate - remove sections by priority
                truncated_text, sections_removed = self._remove_sections_by_priority(
                    result_text,
                    remaining_budget
                )
                
                truncated_tokens = self.estimate_tokens(truncated_text)
                
                truncated_results[job_id] = data.copy()
                truncated_results[job_id]['result'] = truncated_text
                
                truncation_metadata[job_id] = TruncationResult(
                    truncated_text=truncated_text,
                    original_tokens=original_tokens,
                    truncated_tokens=truncated_tokens,
                    was_truncated=True,
                    strategy_used=TruncationStrategy.SMART_SECTIONS,
                    sections_removed=sections_removed
                )
                current_total += truncated_tokens
        
        return truncated_results, truncation_metadata
    
    def _remove_sections_by_priority(
        self,
        text: str,
        token_budget: int
    ) -> Tuple[str, List[str]]:
        """Remove sections from text based on priority until it fits budget."""
        sections_removed = []
        working_text = text
        
        # Start from lowest priority sections
        for section in reversed(self.SECTION_PRIORITY):
            if section == 'citations':
                # Never remove citations
                continue
            
            current_tokens = self.estimate_tokens(working_text)
            if current_tokens <= token_budget:
                break
            
            # Try to remove this section
            pattern = self.SECTION_PATTERNS.get(section)
            if pattern:
                matches = list(re.finditer(pattern, working_text, re.DOTALL))
                if matches:
                    # Remove the section
                    for match in reversed(matches):  # Remove from end to preserve positions
                        working_text = (
                            working_text[:match.start()] +
                            f"\n[{section.replace('_', ' ').title()} section removed for space]\n" +
                            working_text[match.end():]
                        )
                    sections_removed.append(section)
        
        # If still too long, do hard truncation but preserve ending
        if self.estimate_tokens(working_text) > token_budget:
            working_text = self._truncate_to_token_limit(working_text, token_budget, preserve_ending=True)
            sections_removed.append('content_truncated')
        
        return working_text, sections_removed
    
    def _truncate_to_token_limit(
        self,
        text: str,
        token_limit: int,
        preserve_ending: bool = False
    ) -> str:
        """Truncate text to fit within token limit."""
        tokens = self.estimate_tokens(text)
        
        if tokens <= token_limit:
            return text
        
        # Binary search for the right truncation point
        left, right = 0, len(text)
        best_length = 0
        
        while left <= right:
            mid = (left + right) // 2
            
            if preserve_ending:
                # Keep beginning and end
                truncated = text[:mid//2] + "\n\n[... content truncated ...]\n\n" + text[-(mid//2):]
            else:
                truncated = text[:mid] + "\n\n[... content truncated ...]"
            
            truncated_tokens = self.estimate_tokens(truncated)
            
            if truncated_tokens <= token_limit:
                best_length = mid
                left = mid + 1
            else:
                right = mid - 1
        
        if preserve_ending:
            return text[:best_length//2] + "\n\n[... content truncated ...]\n\n" + text[-(best_length//2):]
        else:
            return text[:best_length] + "\n\n[... content truncated ...]"


def format_token_summary(metadata: Dict[str, any]) -> str:
    """Format token metadata for display."""
    lines = []
    lines.append(f"Total tokens: {metadata['total_tokens']:,}")
    lines.append(f"Available budget: {metadata['available_tokens']:,}")
    lines.append(f"Usage: {metadata['percentage_used']:.1f}%")
    
    if not metadata['fits_in_context']:
        lines.append("⚠️ EXCEEDS CONTEXT WINDOW")
    
    return "\n".join(lines)