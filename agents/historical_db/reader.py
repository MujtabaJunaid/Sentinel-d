"""Historical DB reader orchestrator for two-stage lookup."""

import asyncio
import logging
from typing import Dict, Any, List, Optional
from datetime import datetime

from agents.historical_db.embeddings import EmbeddingService
from agents.historical_db.clients import AsyncCosmosClientWrapper, AsyncAISearchWrapper

logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)


class HistoricalDBReader:
    """
    Orchestrates two-stage lookup: exact match followed by semantic match.
    
    Returns structured historical_match.json output conforming to data contract.
    """

    def __init__(
        self,
        cosmos_client: AsyncCosmosClientWrapper,
        ai_search_client: AsyncAISearchWrapper,
        embedding_service: EmbeddingService
    ):
        """
        Initialize Historical DB Reader.

        Args:
            cosmos_client: Async Cosmos DB client wrapper.
            ai_search_client: Async Azure AI Search client wrapper.
            embedding_service: Embedding service for semantic search.
        """
        self.cosmos_client = cosmos_client
        self.ai_search_client = ai_search_client
        self.embedding_service = embedding_service
        logger.info("Initialized HistoricalDBReader")

    async def lookup(
        self,
        event_id: str,
        cve_id: str,
        description: str,
        affected_package: str
    ) -> Dict[str, Any]:
        """
        Perform two-stage historical lookup.

        Args:
            event_id: Unique event identifier.
            cve_id: CVE identifier (e.g., CVE-2024-1234).
            description: CVE description text.
            affected_package: Package name affected by vulnerability.

        Returns:
            Dictionary containing historical_match.json output conforming to data contract.
        """
        logger.info(f"Starting historical lookup for event_id: {event_id}, cve_id: {cve_id}")

        # Stage 1: Exact Match
        logger.debug("Stage 1: Attempting exact match lookup")
        exact_match = await self.cosmos_client.get_exact_match(cve_id)

        if exact_match:
            logger.info(f"Exact match found for {cve_id}")
            return self._build_exact_match_response(event_id, cve_id, exact_match)

        # Stage 2: Semantic Match (if no exact match)
        logger.debug("Stage 2: No exact match found, attempting semantic search")
        combined_text = f"{description} {affected_package}"
        
        try:
            embedding = await self.embedding_service.embed_text(combined_text)
            semantic_matches = await self.ai_search_client.get_semantic_matches(embedding)

            if semantic_matches:
                logger.info(f"Semantic matches found for {cve_id}")
                return self._build_semantic_match_response(
                    event_id, cve_id, semantic_matches
                )
        except Exception as e:
            logger.warning(f"Semantic search failed: {e}")

        # Fallback: No Match
        logger.info(f"No historical match found for {cve_id}")
        return self._build_no_match_response(event_id, cve_id)

    def _build_exact_match_response(
        self,
        event_id: str,
        cve_id: str,
        exact_match: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Build response for exact match.

        Args:
            event_id: Event identifier.
            cve_id: CVE identifier.
            exact_match: Exact match record from Cosmos DB.

        Returns:
            Historical match dictionary conforming to data contract.
        """
        # Extract solutions_tried where outcome is FAILED
        all_solutions = exact_match.get("solutions_tried", [])
        solutions_tried_previously = [
            {
                "strategy": sol.get("strategy", ""),
                "outcome": sol.get("outcome", ""),
                "failure_reason": sol.get("failure_reason", "")
            }
            for sol in all_solutions
            if sol.get("outcome") == "FAILED"
        ]

        response: Dict[str, Any] = {
            "event_id": event_id,
            "lookup_status": "EXACT_MATCH",
            "match_confidence": 1.0,
            "matched_cve_id": cve_id,
            "matched_record_id": exact_match.get("record_id", ""),
            "recommended_strategy": exact_match.get("recommended_strategy", ""),
            "historical_patch_diff": exact_match.get("patch_diff", ""),
            "previous_outcome": exact_match.get("patch_outcome", ""),
            "solutions_tried_previously": solutions_tried_previously,
            "replay_eligible": True,
            "replay_ineligible_reason": None,
            "timestamp": datetime.utcnow().isoformat()
        }

        logger.debug(f"Built exact match response for event_id: {event_id}")
        return response

    def _build_semantic_match_response(
        self,
        event_id: str,
        cve_id: str,
        semantic_matches: List[Dict[str, Any]]
    ) -> Dict[str, Any]:
        """
        Build response for semantic match.

        Args:
            event_id: Event identifier.
            cve_id: CVE identifier.
            semantic_matches: List of semantic match records from AI Search.

        Returns:
            Historical match dictionary conforming to data contract.
        """
        best_match = semantic_matches[0]
        all_solutions = best_match.get("solutions_tried", [])

        # Extract failed strategies for context
        failed_strategies = [
            {
                "strategy": sol.get("strategy", ""),
                "outcome": sol.get("outcome", ""),
                "failure_reason": sol.get("failure_reason", "")
            }
            for sol in all_solutions
            if sol.get("outcome") == "FAILED"
        ]

        response: Dict[str, Any] = {
            "event_id": event_id,
            "lookup_status": "SEMANTIC_MATCH",
            "match_confidence": best_match.get("similarity_score", 0.88),
            "matched_cve_id": best_match.get("cve_id", ""),
            "matched_record_id": best_match.get("record_id", ""),
            "recommended_strategy": best_match.get("recommended_strategy", ""),
            "historical_patch_diff": best_match.get("patch_diff", ""),
            "previous_outcome": best_match.get("patch_outcome", "UNKNOWN"),
            "solutions_tried_previously": failed_strategies,
            "replay_eligible": best_match.get("patch_outcome") == "SUCCESS",
            "replay_ineligible_reason": (
                None if best_match.get("patch_outcome") == "SUCCESS"
                else f"Previous outcome: {best_match.get('patch_outcome')}"
            ),
            "timestamp": datetime.utcnow().isoformat()
        }

        logger.debug(f"Built semantic match response for event_id: {event_id}")
        return response

    def _build_no_match_response(
        self,
        event_id: str,
        cve_id: str
    ) -> Dict[str, Any]:
        """
        Build response for no match.

        Args:
            event_id: Event identifier.
            cve_id: CVE identifier.

        Returns:
            Historical match dictionary conforming to data contract.
        """
        response: Dict[str, Any] = {
            "event_id": event_id,
            "lookup_status": "NO_MATCH",
            "match_confidence": 0.0,
            "matched_cve_id": "",
            "matched_record_id": "",
            "recommended_strategy": "",
            "historical_patch_diff": "",
            "previous_outcome": "",
            "solutions_tried_previously": [],
            "replay_eligible": False,
            "replay_ineligible_reason": "No historical record found",
            "timestamp": datetime.utcnow().isoformat()
        }

        logger.debug(f"Built no-match response for event_id: {event_id}")
        return response
