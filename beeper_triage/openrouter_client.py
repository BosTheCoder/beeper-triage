"""OpenRouter client — now `draftkit`'s.

Kept as a re-export so `from .openrouter_client import OpenRouterMessage` keeps
working across this package and beeper-inbox's vendored copy. New code should
import from `draftkit` directly.
"""

from draftkit import OpenRouterClient, OpenRouterError, OpenRouterMessage

__all__ = ["OpenRouterClient", "OpenRouterError", "OpenRouterMessage"]
