"""Persistent stores for chats, plans, research reports, and the knowledge graph.

Each module owns one storage surface:

* :mod:`codeagents.stores.chat` — chat sessions on disk.
* :mod:`codeagents.stores.plan` — task plans.
* :mod:`codeagents.stores.research` — deep-research reports.
* :mod:`codeagents.stores.kg` — knowledge graph + Leiden communities.
"""

from codeagents.stores.chat import ChatStore, ChatSummary, default_chats_dir
from codeagents.stores.kg import Community, Entity, KGStore, Relation
from codeagents.stores.plan import (
    Plan,
    PlanLimitError,
    PlanNotFoundError,
    PlanStep,
    PlanStore,
    PlanSummary,
    default_plans_dir,
)
from codeagents.stores.research import ResearchReport, ResearchSection, ResearchStore

__all__ = [
    "ChatStore",
    "ChatSummary",
    "Community",
    "Entity",
    "KGStore",
    "Plan",
    "PlanLimitError",
    "PlanNotFoundError",
    "PlanStep",
    "PlanStore",
    "PlanSummary",
    "Relation",
    "ResearchReport",
    "ResearchSection",
    "ResearchStore",
    "default_chats_dir",
    "default_plans_dir",
]
