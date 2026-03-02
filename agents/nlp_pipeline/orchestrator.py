"""Orchestrator for NLP Context Pipeline."""

import asyncio
import logging
from typing import Dict, Any, List
from datetime import datetime

from agents.nlp_pipeline.fetchers import NVDFetcher, StackOverflowFetcher
from agents.nlp_pipeline.ml_models import EntityExtractor, IntentClassifier

logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)


class NLPContextOrchestrator:
    """
    Orchestrates fetchers and ML models to assemble the final security context.
    
    Execution flow:
    1. Accept webhook_payload.json dictionary
    2. Run NVDFetcher and StackOverflowFetcher in parallel using asyncio.gather
    3. Pass NVD text sequentially to EntityExtractor
    4. Pass Stack Overflow text sequentially to IntentClassifier
    5. Assemble and return structured_context.json dictionary
    """

    PIPELINE_VERSION = "1.0.0"

    def __init__(self, nvd_api_key: Optional[str] = None):
        """
        Initialize the orchestrator with fetchers and ML models.

        Args:
            nvd_api_key: Optional API key for NVD API.
        """
        self.nvd_fetcher = NVDFetcher(api_key=nvd_api_key)
        self.stackoverflow_fetcher = StackOverflowFetcher()
        self.entity_extractor = EntityExtractor()
        self.intent_classifier = IntentClassifier()
        logger.info("Initialized NLPContextOrchestrator")

    async def process(self, webhook_payload: Dict[str, Any]) -> Dict[str, Any]:
        """
        Process webhook payload and generate structured context.

        Args:
            webhook_payload: Input dictionary containing:
                - event_id: Unique event identifier
                - cve_id: CVE identifier (e.g., CVE-2024-1234)
                - affected_package: Package name affected by vulnerability
                - current_version: Current version of the package
                - fix_version_range: Version range where fix is available

        Returns:
            Dictionary containing structured_context with all required fields.
        """
        logger.info(f"Processing webhook for event_id: {webhook_payload.get('event_id')}")

        event_id: str = webhook_payload.get("event_id", "")
        cve_id: str = webhook_payload.get("cve_id", "")
        affected_package: str = webhook_payload.get("affected_package", "")

        # Step 1 & 2: Run NVDFetcher and StackOverflowFetcher in parallel
        logger.debug(f"Fetching data in parallel for CVE {cve_id} and package {affected_package}")
        nvd_data, stackoverflow_data = await asyncio.gather(
            self.nvd_fetcher.fetch(cve_id),
            self.stackoverflow_fetcher.fetch(affected_package),
            return_exceptions=True
        )

        # Handle potential exceptions from gather
        if isinstance(nvd_data, Exception):
            logger.error(f"Exception from NVD fetcher: {nvd_data}")
            nvd_data = {}
        if isinstance(stackoverflow_data, Exception):
            logger.error(f"Exception from StackOverflow fetcher: {stackoverflow_data}")
            stackoverflow_data = {}

        # Extract raw text from API responses for ML processing
        nvd_text = self._extract_nvd_text(nvd_data) if nvd_data else ""
        stackoverflow_text = self._extract_stackoverflow_text(stackoverflow_data) if stackoverflow_data else ""

        # Step 3: Pass NVD text sequentially to EntityExtractor
        logger.debug("Extracting entities from NVD text")
        breaking_changes, migration_steps = self.entity_extractor.extract(nvd_text)

        # Step 4: Pass Stack Overflow text sequentially to IntentClassifier
        logger.debug("Classifying community intent from StackOverflow text")
        community_intent_class, intent_confidence = self.intent_classifier.classify(stackoverflow_text)

        # Step 5: Assemble structured context
        structured_context = self._assemble_context(
            event_id=event_id,
            cve_id=cve_id,
            affected_package=affected_package,
            nvd_data=nvd_data,
            breaking_changes=breaking_changes,
            migration_steps=migration_steps,
            community_intent_class=community_intent_class,
            intent_confidence=intent_confidence,
            webhook_payload=webhook_payload
        )

        logger.info(f"Successfully assembled context for event_id: {event_id}")
        return structured_context



    def _extract_stackoverflow_text(self, stackoverflow_data: Dict[str, Any]) -> str:
        """
        Extract text from Stack Overflow API response.

        Args:
            stackoverflow_data: Response from Stack Exchange API.

        Returns:
            Concatenated text for NLP processing.
        """
        if not stackoverflow_data or "items" not in stackoverflow_data:
            return ""

        text_parts = []
        for item in stackoverflow_data.get("items", [])[:5]:  # Top 5 results
            text_parts.append(item.get("title", ""))
            text_parts.append(item.get("body", ""))

        return " ".join(text_parts)

    def _assemble_context(
        self,
        event_id: str,
        cve_id: str,
        affected_package: str,
        nvd_data: Dict[str, Any],
        breaking_changes: List[Dict[str, Any]],
        migration_steps: List[str],
        community_intent_class: str,
        intent_confidence: float,
        webhook_payload: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Assemble the final structured context dictionary.

        Args:
            event_id: Event identifier.
            cve_id: CVE identifier.
            affected_package: Package name.
            nvd_data: NVD API response.
            breaking_changes: List of detected breaking changes.
            migration_steps: List of migration steps.
            community_intent_class: Classified community intent.
            intent_confidence: Confidence score for intent classification.
            webhook_payload: Original webhook payload.

        Returns:
            Structured context dictionary matching the output schema.
        """
        structured_context: Dict[str, Any] = {
            "event_id": event_id,
            "fix_strategy": self._determine_fix_strategy(community_intent_class),
            "breaking_changes": breaking_changes,
            "community_intent_class": community_intent_class,
            "intent_confidence": intent_confidence,
            "nvd_context": {
                "cvss_score": self._extract_cvss_score(nvd_data),
                "attack_vector": "NETWORK",
                "auth_required": False
            },
            "migration_steps": migration_steps,
            # Mock values for historical DB integration (to be updated later)
            "historical_match_status": "NO_HISTORICAL_MATCH",
            "historical_patch_available": False,
            "historical_record_id": None,
            "solutions_to_avoid": [
                "Avoid using deprecated parseXML() function",
                "Do not downgrade to versions before 2.5.0",
                "Avoid parallel execution without proper locking mechanism"
            ],
            "pipeline_version": self.PIPELINE_VERSION,
            "timestamp": datetime.utcnow().isoformat()
        }

        return structured_context

    def _determine_fix_strategy(self, community_intent_class: str) -> str:
        """
        Determine recommended fix strategy based on intent classification.

        Args:
            community_intent_class: Classified community intent.

        Returns:
            Recommended fix strategy.
        """
        strategy_map = {
            "API_MIGRATION": "MIGRATE_TO_NEW_API",
            "VERSION_PIN": "PIN_COMPATIBLE_VERSION",
            "SECURITY_FIX": "APPLY_SECURITY_PATCH_IMMEDIATELY",
            "PERFORMANCE_OPTIMIZATION": "UPDATE_FOR_PERFORMANCE",
            "DEPENDENCY_UPDATE": "UPDATE_DEPENDENCY",
            "BREAKING_CHANGE_MIGRATION": "MAJOR_VERSION_MIGRATION",
            "ROLLBACK_REQUIRED": "EVALUATE_ROLLBACK"
        }
        return strategy_map.get(community_intent_class, "EVALUATE_OPTIONS")

    def _extract_cvss_score(self, nvd_data: Dict[str, Any]) -> float:
        """
        Extract CVSS score from NVD 2.0 data.

        Args:
            nvd_data: Response from NVD API.

        Returns:
            CVSS base score as float, or 0.0 if not found.
        """
        try:
            vulnerabilities = nvd_data.get("vulnerabilities", [])
            if vulnerabilities and len(vulnerabilities) > 0:
                vuln = vulnerabilities[0]
                cve_metrics = vuln.get("cve", {}).get("metrics", {})

                # Try cvssMetricV31 first, then fall back to cvssMetricV30
                for metric_key in ["cvssMetricV31", "cvssMetricV30"]:
                    if metric_key in cve_metrics:
                        metric_list = cve_metrics[metric_key]
                        if metric_list and len(metric_list) > 0:
                            cvss_data = metric_list[0]
                            base_score = cvss_data.get("cvssData", {}).get("baseScore", 0.0)
                            return float(base_score)
        except (KeyError, IndexError, TypeError, ValueError):
            pass
        return 0.0

    def _extract_nvd_text(self, nvd_data: Dict[str, Any]) -> str:
        """
        Extract descriptive text from NVD 2.0 vulnerabilities data.

        Args:
            nvd_data: Response from NVD API.

        Returns:
            Concatenated description text for NLP processing.
        """
        try:
            vulnerabilities = nvd_data.get("vulnerabilities", [])
            if vulnerabilities and len(vulnerabilities) > 0:
                vuln = vulnerabilities[0]
                descriptions = vuln.get("cve", {}).get("descriptions", [])
                if descriptions:
                    for desc_obj in descriptions:
                        desc_value = desc_obj.get("value", "")
                        if desc_value:
                            return desc_value
        except (KeyError, IndexError, TypeError):
            pass
        return ""


# Type import
from typing import Optional
