"""Token counting and budget management utilities for context window management."""

import tiktoken
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


class TokenCounter:
    """Handles token counting for different models."""
    
    # Model to context window mapping
    MODEL_CONTEXT_WINDOWS = {
        "o3-mini": 200000,
        "gpt-4": 8192,
        "gpt-4-32k": 32768,
        "gpt-4-turbo": 128000,
        "gpt-4o": 128000,
        "gpt-4o-mini": 128000,
        "claude-3-opus": 200000,
        "claude-3-sonnet": 200000,
        "claude-3-haiku": 200000,
    }
    
    # Model to encoding mapping (for tiktoken)
    MODEL_ENCODINGS = {
        "o3-mini": "cl100k_base",  # Using GPT-4 encoding as approximation
        "gpt-4": "cl100k_base",
        "gpt-4-32k": "cl100k_base",
        "gpt-4-turbo": "cl100k_base",
        "gpt-4o": "cl100k_base",
        "gpt-4o-mini": "cl100k_base",
        "claude-3-opus": "cl100k_base",  # Approximation
        "claude-3-sonnet": "cl100k_base",  # Approximation
        "claude-3-haiku": "cl100k_base",  # Approximation
    }
    
    def __init__(self):
        self._encoders = {}
    
    def get_encoder(self, model: str) -> tiktoken.Encoding:
        """Get or create encoder for a model."""
        encoding_name = self.MODEL_ENCODINGS.get(model, "cl100k_base")
        
        if encoding_name not in self._encoders:
            self._encoders[encoding_name] = tiktoken.get_encoding(encoding_name)
        
        return self._encoders[encoding_name]
    
    def estimate_tokens(self, text: str, model: str = "o3-mini") -> int:
        """Estimate token count for a given text and model."""
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
    
    def get_context_window(self, model: str) -> int:
        """Get context window size for a model."""
        return self.MODEL_CONTEXT_WINDOWS.get(model, 128000)  # Default to GPT-4 size
    
    def get_token_budget(self, model: str) -> TokenBudget:
        """Get token budget for a model."""
        total_tokens = self.get_context_window(model)
        return TokenBudget(total_tokens=total_tokens)


def analyze_results_tokens(
    results: Dict[str, Dict],
    model: str = "o3-mini"
) -> Tuple[Dict[str, int], int]:
    """
    Analyze token usage for research results.
    
    Returns:
        Tuple of (per_result_tokens, total_tokens)
    """
    counter = TokenCounter()
    per_result_tokens = {}
    total_tokens = 0
    
    for job_id, data in results.items():
        result_text = data.get('result', '')
        tokens = counter.estimate_tokens(result_text, model)
        per_result_tokens[job_id] = tokens
        total_tokens += tokens
        
        logger.debug(f"Job {job_id}: {tokens} tokens")
    
    return per_result_tokens, total_tokens


def check_token_fit(
    results: Dict[str, Dict],
    model: str = "o3-mini"
) -> Tuple[bool, Dict[str, any]]:
    """
    Check if results fit within model's context window.
    
    Returns:
        Tuple of (fits_in_context, metadata)
    """
    counter = TokenCounter()
    budget = counter.get_token_budget(model)
    
    per_result_tokens, total_tokens = analyze_results_tokens(results, model)
    
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


def format_token_summary(metadata: Dict[str, any]) -> str:
    """Format token metadata for display."""
    lines = []
    lines.append(f"Total tokens: {metadata['total_tokens']:,}")
    lines.append(f"Available budget: {metadata['available_tokens']:,}")
    lines.append(f"Usage: {metadata['percentage_used']:.1f}%")
    
    if not metadata['fits_in_context']:
        lines.append("⚠️ EXCEEDS CONTEXT WINDOW")
    
    return "\n".join(lines)