"""Error-fixer environment for SchematicGym.

Provides a Gymnasium environment where agents (RL or LLM) fix ERC
violations in broken schematics.

Components
----------
- ``ERCFixerEnv``: Gymnasium environment (works for RL agents).
- ``LLMFixerWrapper``: Text interface for LLM agents.
- ``MCPBridge``: MCP tool bridge for top_dog agent compatibility.
- ``inject_errors``: Error injection into valid schematics.
"""

from .env import ERCFixerEnv, FixerAction
from .llm_wrapper import LLMFixerWrapper
from .mcp_bridge import MCPBridge
from .injectors import inject_errors, Fault
from .orchestrator import AgentOrchestrator, EpisodeReport, RandomPolicy, rule_based_llm
from .cleanup_tools import CleanupTools

__all__ = [
    "ERCFixerEnv",
    "FixerAction",
    "LLMFixerWrapper",
    "MCPBridge",
    "inject_errors",
    "Fault",
    "AgentOrchestrator",
    "EpisodeReport",
    "RandomPolicy",
    "rule_based_llm",
    "CleanupTools",
]
