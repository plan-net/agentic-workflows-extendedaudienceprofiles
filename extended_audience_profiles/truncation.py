"""Smart truncation strategies for managing large research results."""

import re
from enum import Enum
from typing import Dict, List, Tuple, Optional
from dataclasses import dataclass
import logging

from .token_utils import TokenCounter

logger = logging.getLogger(__name__)


class TruncationStrategy(Enum):
    """Available truncation strategies."""
    PROPORTIONAL = "proportional"  # Each result gets proportional share
    PRIORITY_BASED = "priority"  # Prioritize certain agents/results
    SUMMARY_FALLBACK = "summary"  # Fall back to summaries for large results
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


class SmartTruncator:
    """Handles intelligent truncation of research results."""
    
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
        self.counter = TokenCounter()
    
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
        elif strategy == TruncationStrategy.SMART_SECTIONS:
            return self._truncate_smart_sections(results, token_budget)
        elif strategy == TruncationStrategy.SUMMARY_FALLBACK:
            return self._truncate_with_summary(results, token_budget)
        else:
            return self._truncate_priority_based(results, token_budget)
    
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
            self.counter.estimate_tokens(data.get('result', ''), self.model)
            for data in results.values()
        )
        
        if total_tokens <= token_budget:
            # No truncation needed
            for job_id, data in results.items():
                truncated_results[job_id] = data.copy()
                truncation_metadata[job_id] = TruncationResult(
                    truncated_text=data.get('result', ''),
                    original_tokens=self.counter.estimate_tokens(data.get('result', ''), self.model),
                    truncated_tokens=self.counter.estimate_tokens(data.get('result', ''), self.model),
                    was_truncated=False,
                    strategy_used=TruncationStrategy.PROPORTIONAL
                )
            return truncated_results, truncation_metadata
        
        # Calculate proportional budget for each result
        for job_id, data in results.items():
            result_text = data.get('result', '')
            original_tokens = self.counter.estimate_tokens(result_text, self.model)
            
            # Proportional share of budget
            result_budget = int((original_tokens / total_tokens) * token_budget)
            
            # Truncate to fit budget
            truncated_text = self._truncate_to_token_limit(result_text, result_budget)
            
            truncated_results[job_id] = data.copy()
            truncated_results[job_id]['result'] = truncated_text
            
            truncation_metadata[job_id] = TruncationResult(
                truncated_text=truncated_text,
                original_tokens=original_tokens,
                truncated_tokens=self.counter.estimate_tokens(truncated_text, self.model),
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
            original_tokens = self.counter.estimate_tokens(result_text, self.model)
            
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
                
                truncated_tokens = self.counter.estimate_tokens(truncated_text, self.model)
                
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
            
            current_tokens = self.counter.estimate_tokens(working_text, self.model)
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
        if self.counter.estimate_tokens(working_text, self.model) > token_budget:
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
        tokens = self.counter.estimate_tokens(text, self.model)
        
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
            
            truncated_tokens = self.counter.estimate_tokens(truncated, self.model)
            
            if truncated_tokens <= token_limit:
                best_length = mid
                left = mid + 1
            else:
                right = mid - 1
        
        if preserve_ending:
            return text[:best_length//2] + "\n\n[... content truncated ...]\n\n" + text[-(best_length//2):]
        else:
            return text[:best_length] + "\n\n[... content truncated ...]"
    
    def _truncate_with_summary(
        self,
        results: Dict[str, Dict],
        token_budget: int
    ) -> Tuple[Dict[str, Dict], Dict[str, TruncationResult]]:
        """Truncate by replacing large results with summaries."""
        # This would require AI summarization - for now, fall back to smart sections
        logger.info("Summary truncation not yet implemented, using smart sections instead")
        return self._truncate_smart_sections(results, token_budget)
    
    def _truncate_priority_based(
        self,
        results: Dict[str, Dict],
        token_budget: int
    ) -> Tuple[Dict[str, Dict], Dict[str, TruncationResult]]:
        """Truncate based on agent priority."""
        # For now, use proportional as default
        return self._truncate_proportional(results, token_budget)