import logging
from dataclasses import dataclass
from typing import Any, Literal
from enum import Enum

logger = logging.getLogger(__name__)


class RoutingTier(Enum):
    """Routing tier definitions for safety decisions."""
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"
    BLOCKED = "BLOCKED"


@dataclass
class CandidatePatch:
    """Data contract for candidate patch input."""
    cve_id: str
    diff: str
    llm_confidence: float
    reasoning_chain: str
    touches_auth_crypto: bool


@dataclass
class ValidationBundle:
    """Data contract for validation bundle input."""
    tests_passed: int
    tests_failed: int
    coverage_before: float
    coverage_after: float
    visual_regression: bool


@dataclass
class SafetyDecision:
    """Output data contract for safety decision."""
    tier: RoutingTier
    composite_score: float
    action_required: Literal["PR", "Issue", "Reject"]
    reasoning: str


class DecisionEngine:
    """
    Calculates composite safety score and determines routing tier.
    
    Implements strict safety governance for automated security patches.
    """
    
    # Configuration thresholds
    COVERAGE_REGRESSION_THRESHOLD = 0.05  # 5% drop
    SCORE_HIGH_THRESHOLD = 0.85
    SCORE_MEDIUM_THRESHOLD = 0.70
    SCORE_LOW_THRESHOLD = 0.55
    
    def __init__(self):
        """Initialize decision engine."""
        logger.info("Initializing Safety Governor Decision Engine")
    
    def evaluate(
        self,
        candidate_patch: dict[str, Any],
        validation_bundle: dict[str, Any],
    ) -> SafetyDecision:
        """
        Evaluate patch safety and determine routing tier.
        
        Args:
            candidate_patch: Dictionary containing cve_id, diff, llm_confidence,
                             reasoning_chain, touches_auth_crypto.
            validation_bundle: Dictionary containing tests_passed, tests_failed,
                               coverage_before, coverage_after, visual_regression.
        
        Returns:
            SafetyDecision containing tier, composite_score, and action_required.
        
        Raises:
            ValueError: If required fields are missing or invalid.
        """
        try:
            # Parse and validate inputs
            patch = self._parse_candidate_patch(candidate_patch)
            validation = self._parse_validation_bundle(validation_bundle)
            
            logger.info(f"Evaluating patch {patch.cve_id} with confidence {patch.llm_confidence}")
            
            # Calculate composite safety score
            composite_score = self._calculate_composite_score(patch, validation)
            
            # Check for coverage regression penalty
            if self._has_coverage_regression(validation):
                logger.warning(
                    f"Coverage regression detected for {patch.cve_id}: "
                    f"{validation.coverage_before}% → {validation.coverage_after}%"
                )
                composite_score = self._apply_penalty(composite_score)
            
            # Determine routing tier
            decision = self._determine_tier(patch, validation, composite_score)
            logger.info(f"Decision for {patch.cve_id}: {decision.tier.value} (score: {composite_score:.3f})")
            
            return decision
            
        except ValueError as e:
            logger.error(f"Validation error in safety decision: {str(e)}")
            raise
        except Exception as e:
            logger.error(f"Unexpected error in decision evaluation: {str(e)}")
            raise
    
    def _parse_candidate_patch(self, data: dict[str, Any]) -> CandidatePatch:
        """Parse and validate candidate patch input."""
        required_fields = {"cve_id", "diff", "llm_confidence", "reasoning_chain", "touches_auth_crypto"}
        missing = required_fields - set(data.keys())
        
        if missing:
            raise ValueError(f"Missing required fields in candidate_patch: {missing}")
        
        if not 0 <= data["llm_confidence"] <= 1:
            raise ValueError(f"llm_confidence must be between 0 and 1, got {data['llm_confidence']}")
        
        return CandidatePatch(
            cve_id=data["cve_id"],
            diff=data["diff"],
            llm_confidence=data["llm_confidence"],
            reasoning_chain=data["reasoning_chain"],
            touches_auth_crypto=data["touches_auth_crypto"],
        )
    
    def _parse_validation_bundle(self, data: dict[str, Any]) -> ValidationBundle:
        """Parse and validate validation bundle input."""
        required_fields = {"tests_passed", "tests_failed", "coverage_before", "coverage_after", "visual_regression"}
        missing = required_fields - set(data.keys())
        
        if missing:
            raise ValueError(f"Missing required fields in validation_bundle: {missing}")
        
        if data["tests_passed"] < 0 or data["tests_failed"] < 0:
            raise ValueError("Test counts cannot be negative")
        
        if not 0 <= data["coverage_before"] <= 100 or not 0 <= data["coverage_after"] <= 100:
            raise ValueError("Coverage percentages must be between 0 and 100")
        
        return ValidationBundle(
            tests_passed=data["tests_passed"],
            tests_failed=data["tests_failed"],
            coverage_before=data["coverage_before"],
            coverage_after=data["coverage_after"],
            visual_regression=data["visual_regression"],
        )
    
    def _calculate_composite_score(
        self,
        patch: CandidatePatch,
        validation: ValidationBundle,
    ) -> float:
        """
        Calculate composite safety score.
        
        Score formula combines LLM confidence (40%) and test pass rate (60%).
        """
        # LLM confidence component (0-1 scale, 40% weight)
        llm_component = patch.llm_confidence * 0.40
        
        # Test pass rate component (0-1 scale, 60% weight)
        total_tests = validation.tests_passed + validation.tests_failed
        if total_tests == 0:
            test_pass_rate = 0.0
        else:
            test_pass_rate = validation.tests_passed / total_tests
        test_component = test_pass_rate * 0.60
        
        composite_score = llm_component + test_component
        
        logger.debug(
            f"Score breakdown: LLM={llm_component:.3f} + Tests={test_component:.3f} = {composite_score:.3f}"
        )
        
        return composite_score
    
    def _has_coverage_regression(self, validation: ValidationBundle) -> bool:
        """Check if coverage has regressed beyond threshold."""
        regression = validation.coverage_before - validation.coverage_after
        return regression > self.COVERAGE_REGRESSION_THRESHOLD
    
    def _apply_penalty(self, score: float) -> float:
        """Apply penalty for coverage regression."""
        penalty = 0.15  # 15% penalty for regression
        penalized_score = max(score - penalty, 0.0)
        logger.debug(f"Applied coverage regression penalty: {score:.3f} → {penalized_score:.3f}")
        return penalized_score
    
    def _determine_tier(
        self,
        patch: CandidatePatch,
        validation: ValidationBundle,
        composite_score: float,
    ) -> SafetyDecision:
        """Determine routing tier based on safety criteria."""
        
        # BLOCKED: Score too low or test failures detected
        if composite_score < self.SCORE_LOW_THRESHOLD or validation.tests_failed > 0:
            reason = f"Score {composite_score:.3f} below threshold"
            if validation.tests_failed > 0:
                reason = f"{validation.tests_failed} test failure(s)"
            
            return SafetyDecision(
                tier=RoutingTier.BLOCKED,
                composite_score=composite_score,
                action_required="Reject",
                reasoning=reason,
            )
        
        # LOW: Touches auth/crypto or low confidence, escalate for review
        if patch.touches_auth_crypto or composite_score < self.SCORE_MEDIUM_THRESHOLD:
            reason = "Auth/crypto code" if patch.touches_auth_crypto else f"Low confidence ({composite_score:.3f})"
            
            return SafetyDecision(
                tier=RoutingTier.LOW,
                composite_score=composite_score,
                action_required="Issue",
                reasoning=reason,
            )
        
        # MEDIUM: Needs human review (visual regression or medium confidence)
        if validation.visual_regression or composite_score < self.SCORE_HIGH_THRESHOLD:
            reason = "Visual regression detected" if validation.visual_regression else f"Score {composite_score:.3f} requires review"
            
            return SafetyDecision(
                tier=RoutingTier.MEDIUM,
                composite_score=composite_score,
                action_required="PR",
                reasoning=reason,
            )
        
        # HIGH: Auto-approve conditions met
        return SafetyDecision(
            tier=RoutingTier.HIGH,
            composite_score=composite_score,
            action_required="PR",
            reasoning=f"All criteria met (score: {composite_score:.3f})",
        )
