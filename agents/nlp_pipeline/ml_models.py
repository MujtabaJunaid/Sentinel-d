"""ML model inference wrappers for NLP Context Pipeline."""

import logging
from typing import Dict, Any, List, Tuple

logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)


class EntityExtractor:
    """
    Mock wrapper for fine-tuned spaCy NER model.
    
    Extracts breaking changes and migration steps from raw text.
    """

    def __init__(self, model_name: str = "spacy-ner-model-v1"):
        """
        Initialize EntityExtractor.

        Args:
            model_name: Name of the NER model to use.
        """
        self.model_name = model_name
        logger.info(f"Initialized EntityExtractor with model: {model_name}")

    def extract(self, text: str) -> Tuple[List[Dict[str, Any]], List[str]]:
        """
        Extract breaking changes and migration steps from text.

        Args:
            text: Raw text from NVD or other sources.

        Returns:
            Tuple of (breaking_changes list, migration_steps list).
        """
        logger.debug(f"Extracting entities from text of length {len(text)}")

        # Mock implementation returning structured breaking changes
        breaking_changes = [
            {
                "entity": "API_CHANGE",
                "description": "Endpoint /api/v2/deprecated removed",
                "severity": "HIGH",
                "affected_functions": ["getUser()", "listUsers()"],
                "remediation": "Use /api/v3/users instead"
            },
            {
                "entity": "DEPENDENCY_REMOVAL",
                "description": "Legacy XML parsing library removed",
                "severity": "MEDIUM",
                "affected_functions": ["parseXML()", "validateSchema()"],
                "remediation": "Migrate to modern JSON-based APIs"
            }
        ]

        # Mock implementation returning migration steps
        migration_steps = [
            "Step 1: Review deprecated API endpoints in v2",
            "Step 2: Update all API calls to v3 endpoints",
            "Step 3: Test with new JSON serialization format",
            "Step 4: Update error handling for new exception types",
            "Step 5: Perform integration testing in staging environment",
            "Step 6: Deploy with feature flag for gradual rollout"
        ]

        logger.info(f"Extracted {len(breaking_changes)} breaking changes and {len(migration_steps)} migration steps")
        return breaking_changes, migration_steps


class IntentClassifier:
    """
    Mock wrapper for fine-tuned DistilBERT model.
    
    Classifies community intent from Stack Overflow and other sources.
    """

    INTENT_CLASSES = [
        "API_MIGRATION",
        "VERSION_PIN",
        "SECURITY_FIX",
        "PERFORMANCE_OPTIMIZATION",
        "DEPENDENCY_UPDATE",
        "BREAKING_CHANGE_MIGRATION",
        "ROLLBACK_REQUIRED"
    ]

    def __init__(self, model_name: str = "distilbert-intent-classifier-v1"):
        """
        Initialize IntentClassifier.

        Args:
            model_name: Name of the DistilBERT model to use.
        """
        self.model_name = model_name
        logger.info(f"Initialized IntentClassifier with model: {model_name}")

    def classify(
        self, text: str
    ) -> Tuple[str, float]:
        """
        Classify community intent from text.

        Args:
            text: Raw community text from Stack Overflow or other sources.

        Returns:
            Tuple of (intent_class, confidence_score) where confidence is 0.0-1.0.
        """
        logger.debug(f"Classifying intent from text of length {len(text)}")

        # Mock implementation: simulate classification with confidence
        # In production, this would call the actual DistilBERT model
        text_lower = text.lower()

        intent_scores: Dict[str, float] = {
            "API_MIGRATION": 0.0,
            "VERSION_PIN": 0.0,
            "SECURITY_FIX": 0.0,
            "PERFORMANCE_OPTIMIZATION": 0.0,
            "DEPENDENCY_UPDATE": 0.0,
            "BREAKING_CHANGE_MIGRATION": 0.0,
            "ROLLBACK_REQUIRED": 0.0,
        }

        # Simple keyword-based mock scoring
        if any(keyword in text_lower for keyword in ["api", "endpoint", "rest", "graphql"]):
            intent_scores["API_MIGRATION"] += 0.3
        if any(keyword in text_lower for keyword in ["version", "pin", "lock", "freeze"]):
            intent_scores["VERSION_PIN"] += 0.3
        if any(keyword in text_lower for keyword in ["security", "vulnerability", "cve", "patch"]):
            intent_scores["SECURITY_FIX"] += 0.4
        if any(keyword in text_lower for keyword in ["performance", "speed", "optimization", "latency"]):
            intent_scores["PERFORMANCE_OPTIMIZATION"] += 0.25
        if any(keyword in text_lower for keyword in ["dependency", "require", "import", "package"]):
            intent_scores["DEPENDENCY_UPDATE"] += 0.3
        if any(keyword in text_lower for keyword in ["breaking", "incompatible", "change", "migration"]):
            intent_scores["BREAKING_CHANGE_MIGRATION"] += 0.4
        if any(keyword in text_lower for keyword in ["rollback", "revert", "downgrade", "issue"]):
            intent_scores["ROLLBACK_REQUIRED"] += 0.35

        # Normalize scores and select highest
        max_intent = max(intent_scores, key=intent_scores.get)
        max_score = intent_scores[max_intent]

        # Ensure score is between 0 and 1, with fallback to mock value
        confidence = min(max(max_score, 0.65), 0.95) if max_score > 0 else 0.72

        logger.info(f"Classified intent as {max_intent} with confidence {confidence:.2f}")
        return max_intent, confidence
