from dm_agent.tools.base import Tool, ToolContext
from dm_agent.tools.core import TOOLS as CORE_TOOLS
from dm_agent.tools.knowledge import TOOLS as KNOWLEDGE_TOOLS

TOOLS: list[Tool] = [*CORE_TOOLS, *KNOWLEDGE_TOOLS]
TOOLS_BY_NAME: dict[str, Tool] = {t.name: t for t in TOOLS}
TOOL_DEFINITIONS = [t.definition for t in TOOLS]

__all__ = ["TOOLS", "TOOLS_BY_NAME", "TOOL_DEFINITIONS", "Tool", "ToolContext"]
