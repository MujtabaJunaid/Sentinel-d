import logging
from typing import Any
from datetime import datetime

logger = logging.getLogger(__name__)


class PromptBuilder:
    """
    Constructs the four-section prompt architecture for patch generation.
    
    Enforces strict constraints and chain-of-thought reasoning.
    """
    
    def __init__(self):
        """Initialize prompt builder."""
        logger.info("Initializing PromptBuilder")
    
    def build(self, structured_context: dict[str, Any]) -> str:
        """
        Build the complete four-section prompt.
        
        Args:
            structured_context: Input data contract with event_id, fix_strategy,
                               breaking_changes, community_intent_class, intent_confidence,
                               nvd_context, migration_steps, historical_match_status,
                               historical_patch_available, solutions_to_avoid.
        
        Returns:
            Formatted prompt string for Azure OpenAI.
        """
        logger.info(f"Building prompt for event {structured_context.get('event_id')}")
        
        section_1 = self._section_1_system_prompt()
        section_2 = self._section_2_context(structured_context)
        section_3 = self._section_3_reasoning()
        section_4 = self._section_4_output_constraints()
        
        prompt = f"{section_1}\n\n{section_2}\n\n{section_3}\n\n{section_4}"
        
        logger.debug(f"Generated prompt ({len(prompt)} characters)")
        return prompt
    
    @staticmethod
    def _section_1_system_prompt() -> str:
        """
        Section 1: System prompt enforcing role and constraints.
        """
        return """# SYSTEM PROMPT: Security Patch Generator

You are an elite DevSecOps engineer and security researcher specializing in vulnerability remediation.

## Your Role
Generate minimal, production-ready unified diff patches for critical security vulnerabilities.

## Core Constraints
1. Output ONLY a valid unified diff string.
2. Never introduce new dependencies.
3. Never modify authentication or cryptographic code unless absolutely necessary.
4. Changes must be minimal and focused on the specific vulnerability.
5. All changes must pass basic security review logic.
6. If constraints cannot be met, output the phrase "CANNOT_PATCH" followed by a brief reason.

## Output Format
You MUST follow this exact format:
1. Provide your reasoning inside <reasoning> tags
2. After </reasoning>, output ONLY the unified diff string
3. Do NOT include markdown code blocks or additional text after the diff
"""
    
    @staticmethod
    def _section_2_context(structured_context: dict[str, Any]) -> str:
        """
        Section 2: Inject variables from structured_context.json.
        """
        event_id = structured_context.get("event_id", "UNKNOWN")
        fix_strategy = structured_context.get("fix_strategy", "")
        breaking_changes = structured_context.get("breaking_changes", [])
        community_intent_class = structured_context.get("community_intent_class", "")
        intent_confidence = structured_context.get("intent_confidence", 0)
        nvd_context = structured_context.get("nvd_context", {})
        migration_steps = structured_context.get("migration_steps", [])
        historical_match_status = structured_context.get("historical_match_status", "")
        historical_patch_available = structured_context.get("historical_patch_available", False)
        solutions_to_avoid = structured_context.get("solutions_to_avoid", [])
        
        breaking_str = "\n".join(f"  - {item}" for item in breaking_changes) if breaking_changes else "  None identified"
        migration_str = "\n".join(f"  - {item}" for item in migration_steps) if migration_steps else "  None specified"
        avoid_str = "\n".join(f"  - {item}" for item in solutions_to_avoid) if solutions_to_avoid else "  None"
        
        nvd_desc = nvd_context.get("description", "N/A")
        cvss_score = nvd_context.get("cvss_score", "N/A")
        
        return f"""# CONTEXT: Vulnerability Details

## Event Information
- Event ID: {event_id}
- Community Intent Class: {community_intent_class}
- Intent Confidence: {intent_confidence}
- Historical Match Status: {historical_match_status}
- Historical Patch Available: {historical_patch_available}

## Fix Strategy
{fix_strategy}

## Vulnerability Context (NVD)
- Description: {nvd_desc}
- CVSS Score: {cvss_score}

## Breaking Changes Identified
{breaking_str}

## Migration Steps
{migration_str}

## Solutions to AVOID (Do NOT implement these approaches)
{avoid_str}

IMPORTANT: Do not use any of the solutions listed above. They are known to cause problems or violate security constraints.
"""
    
    @staticmethod
    def _section_3_reasoning() -> str:
        """
        Section 3: Force chain-of-thought reasoning with 5 specific questions.
        """
        return """# REASONING: Answer These Questions

Before generating the patch, reason through the following questions inside <reasoning> tags:

<reasoning>
1. Minimal Change: Is this the absolute minimum change needed to remediate the vulnerability without extra modifications?

2. Breaking Changes: Will this patch introduce breaking changes listed above? How are they mitigated?

3. Testing Coverage: What tests would validate this patch? Are there edge cases?

4. Authentication/Crypto: Does this patch modify auth or crypto code? Is it necessary? Are there safer alternatives?

5. Constraint Satisfaction: Do all constraints listed in the System Prompt hold true for this patch?

Answer each question briefly, then decide: Can this patch be generated safely?
</reasoning>

OUTPUT YOUR REASONING ANSWERS INSIDE THE <reasoning></reasoning> TAGS ABOVE.
"""
    
    @staticmethod
    def _section_4_output_constraints() -> str:
        """
        Section 4: Hard output constraints.
        """
        return """# OUTPUT CONSTRAINTS

After your </reasoning> block:

1. Output ONLY the unified diff in standard format (no markdown, no extra text).
2. If you cannot generate a safe patch, output: CANNOT_PATCH: [brief reason]
3. Do NOT modify auth/crypto code unless reasoning clearly justifies it.
4. Do NOT introduce new external dependencies.
5. Keep the diff under 500 lines.
6. Use standard unified diff format:
   - Start with: --- a/path/to/file.ext
   - Continue with: +++ b/path/to/file.ext
   - Show full context blocks with @@ -line,count +line,count @@

PROCEED WITH PATCH GENERATION.
"""
