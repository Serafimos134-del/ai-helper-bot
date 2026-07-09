"""
core/container.py
Dependency container — singletons for db, AI analyzer, and consensus engine.
"""

from services.database import Database
from services.ai_trading import AITradingAnalyzer
from ai.consensus_engine import ConsensusEngine
from ai.orchestrator import AIOrchestrator

_db = None
_ai_analyzer = None
_consensus = None
_orchestrator = None


def get_db() -> Database:
    global _db
    if _db is None:
        _db = Database()
    return _db


def get_ai_analyzer() -> AITradingAnalyzer:
    global _ai_analyzer
    if _ai_analyzer is None:
        _ai_analyzer = AITradingAnalyzer()
    return _ai_analyzer


def get_consensus() -> ConsensusEngine:
    global _consensus
    if _consensus is None:
        _consensus = ConsensusEngine(get_ai_analyzer().provider)
    return _consensus


def get_orchestrator() -> AIOrchestrator:
    """Единая точка входа в AI Trading Core (см. ai/orchestrator.py) —
    предпочтительна для нового кода вместо прямого обращения к get_consensus()."""
    global _orchestrator
    if _orchestrator is None:
        _orchestrator = AIOrchestrator(get_consensus())
    return _orchestrator