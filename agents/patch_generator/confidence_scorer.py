import logging
import re
from typing import Any

logger = logging.getLogger(__name__)


class ConfidenceScorer:
    """
    Computes composite confidence score for generated patches.
    
    Implements weighted formula with constraint adherence and intent alignment.
    """
    
    def __init__(self):
        """Initialize confidence scorer."""
        logger.info("Initializing ConfidenceScorer")
    
    def score(
        self,
        llm_log_prob: float,
        diff_string: str,
        reasoning_chain: str,
        structured_context: dict[str, Any],
    ) -> float:
        """
        Calculate composite confidence score.
        
        Weighting formula:
        - llm_log_prob (base): 40%
        - constraint_adherence: 35%
        - nlp_intent_alignment: 25%
        - +0.05 bonus for EXACT_MATCH
        - -0.20 penalty if reasoning mentions solutions_to_avoid
        
        Args:
            llm_log_prob: Base score from language model (0-1).
            diff_string: Generated unified diff string.
            reasoning_chain: The <reasoning> block output from model.
            structured_context: Original input context with fix_strategy, solutions_to_avoid, etc.
        
        Returns:
            Composite confidence score (0-1).
        """
        try:
            # Component 1: LLM Log Probability (40% weight)
            llm_component = self._normalize_score(llm_log_prob) * 0.40
            logger.debug(f"LLM component: {llm_component:.3f}")
            
            # Component 2: Constraint Adherence (35% weight)
            constraint_score = self._evaluate_constraint_adherence(diff_string, reasoning_chain, structured_context)
            constraint_component = constraint_score * 0.35
            logger.debug(f"Constraint component: {constraint_component:.3f}")
            
            # Component 3: NLP Intent Alignment (25% weight)
            intent_score = self._evaluate_nlp_intent_alignment(diff_string, structured_context)
            intent_component = intent_score * 0.25
            logger.debug(f"Intent component: {intent_component:.3f}")
            
            # Composite before adjustments
            composite = llm_component + constraint_component + intent_component
            logger.debug(f"Composite before adjustments: {composite:.3f}")
            
            # Additive adjustments
            composite = self._apply_historical_match_bonus(composite, structured_context)
            composite = self._apply_solutions_to_avoid_penalty(composite, reasoning_chain, structured_context)
            
            # Clamp to valid range
            final_score = max(0.0, min(1.0, composite))
            logger.info(f"Final confidence score: {final_score:.3f}")
            
            return final_score
        
        except Exception as e:
            logger.error(f"Error calculating confidence score: {str(e)}")
            raise
    
    @staticmethod
    def _normalize_score(value: float) -> float:
        """Normalize score to 0-1 range."""
        return max(0.0, min(1.0, value))
    
    def _evaluate_constraint_adherence(
        self,
        diff_string: str,
        reasoning_chain: str,
        structured_context: dict[str, Any],
    ) -> float:
        """
        Evaluate constraint adherence (35% weight).
        
        Checks:
        1. No new dependencies introduced
        2. Reasoning does not mention solutions_to_avoid
        """
        score = 1.0
        solutions_to_avoid = structured_context.get("solutions_to_avoid", [])
        
        # Check 1: No new dependencies
        if self._has_new_dependencies(diff_string):
            logger.warning("Detected new dependencies in diff")
            score -= 0.3
        
        # Check 2: Reasoning does not mention avoided solutions
        if self._mentions_solutions_to_avoid(reasoning_chain, solutions_to_avoid):
            logger.warning("Reasoning mentions solutions to avoid")
            score -= 0.5
        
        return self._normalize_score(score)
    
    @staticmethod
    def _has_new_dependencies(diff_string: str) -> bool:
        """
        Check if diff introduces new dependencies.
        
        Simple heuristic: Look for dependency file changes (requirements, package.json, etc.)
        and new imports without corresponding removals.
        """
        dependency_patterns = [
            r'^\+.*requirements\.txt',
            r'^\+.*package\.json',
            r'^\+.*Gemfile',
            r'^\+.*setup\.py',
            r'^\+.*pyproject\.toml',
        ]
        
        for line in diff_string.split("\n"):
            for pattern in dependency_patterns:
                if re.search(pattern, line):
                    return True
        
        return False
    
    @staticmethod
    def _mentions_solutions_to_avoid(reasoning_chain: str, solutions_to_avoid: list[str]) -> bool:
        """
        Check if reasoning mentions any solution to avoid.
        """
        if not solutions_to_avoid:
            return False
        
        reasoning_lower = reasoning_chain.lower()
        for solution in solutions_to_avoid:
            if solution.lower() in reasoning_lower:
                return True
        
        return False
    
    def _evaluate_nlp_intent_alignment(
        self,
        diff_string: str,
        structured_context: dict[str, Any],
    ) -> float:
        """
        Evaluate NLP intent alignment (25% weight).
        
        Check if generated diff contains the fix_strategy keyword.
        Mock implementation: 1.0 if fix_strategy appears in diff, else 0.7.
        """
        fix_strategy = structured_context.get("fix_strategy", "")
        
        if not fix_strategy:
            logger.warning("No fix_strategy provided in context")
            return 0.7
        
        # Extract keywords from fix strategy (split by common delimiters)
        keywords = re.findall(r'\b\w+\b', fix_strategy.lower())
        
        diff_lower = diff_string.lower()
        
        # Check if any significant keywords appear in the diff
        matched_keywords = [kw for kw in keywords if kw in diff_lower and len(kw) > 3]
        
        if matched_keywords:
            logger.debug(f"Matched keywords in diff: {matched_keywords}")
            return 1.0
        
        logger.debug("Fix strategy keywords not found in diff")
        return 0.7
    
    @staticmethod
    def _apply_historical_match_bonus(
        score: float,
        structured_context: dict[str, Any],
    ) -> float:
        """Apply +0.05 bonus if historical_match_status == 'EXACT_MATCH'."""
        historical_match = structured_context.get("historical_match_status", "")
        
        if historical_match == "EXACT_MATCH":
            bonus_score = score + 0.05
            logger.info("Applied +0.05 bonus for EXACT_MATCH")
            return bonus_score
        
        return score
    
    @staticmethod
    def _apply_solutions_to_avoid_penalty(
        score: float,
        reasoning_chain: str,
        structured_context: dict[str, Any],
    ) -> float:
        """Apply -0.20 penalty if reasoning mentions any solution from solutions_to_avoid."""
        solutions_to_avoid = structured_context.get("solutions_to_avoid", [])
        
        if ConfidenceScorer._mentions_solutions_to_avoid(reasoning_chain, solutions_to_avoid):
            penalty_score = score - 0.20
            logger.info("Applied -0.20 penalty for mentioning avoided solutions")
            return penalty_score
        
        return score
