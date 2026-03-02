import asyncio
import json
import logging
import os
import re
from typing import Any, Optional
from datetime import datetime

import aiohttp

from agents.patch_generator.prompt_builder import PromptBuilder
from agents.patch_generator.confidence_scorer import ConfidenceScorer

logger = logging.getLogger(__name__)


class PatchGeneratorAgent:
    """
    Orchestrator for patch generation via Microsoft Foundry (Azure OpenAI).
    
    Coordinates prompt building, LLM invocation, parsing, scoring, and output.
    """
    
    def __init__(self):
        """Initialize patch generator agent."""
        self.foundry_endpoint = os.getenv(
            "AZURE_OPENAI_ENDPOINT",
            "https://{resource}.openai.azure.com/"
        )
        self.foundry_api_key = os.getenv("AZURE_OPENAI_API_KEY")
        self.foundry_api_version = os.getenv("AZURE_OPENAI_API_VERSION", "2024-08-01-preview")
        self.model_deployment = os.getenv("AZURE_OPENAI_DEPLOYMENT_ID", "gpt-4o")
        
        if not self.foundry_api_key:
            raise ValueError("AZURE_OPENAI_API_KEY environment variable not set")
        
        self.prompt_builder = PromptBuilder()
        self.confidence_scorer = ConfidenceScorer()
        
        logger.info(f"Initialized PatchGeneratorAgent with model: {self.model_deployment}")
    
    async def generate(self, structured_context: dict[str, Any]) -> dict[str, Any]:
        """
        Generate a security patch from structured context.
        
        Args:
            structured_context: Input data contract with vulnerability and fix details.
        
        Returns:
            candidate_patch.json dictionary with generated patch or failure reason.
        
        Raises:
            Exception: On API errors or parsing failures.
        """
        event_id = structured_context.get("event_id", "UNKNOWN")
        
        try:
            logger.info(f"Starting patch generation for event {event_id}")
            
            # Step 1: Build prompt
            prompt = self.prompt_builder.build(structured_context)
            
            # Step 2: Call Microsoft Foundry (Azure OpenAI)
            logger.info("Calling Microsoft Foundry (GPT-4o)")
            response_text = await self._call_foundry(prompt)
            
            # Step 3: Parse response
            reasoning_chain, diff_string, cannot_patch_reason = self._parse_response(response_text)
            
            # Step 4: Handle CANNOT_PATCH
            if cannot_patch_reason:
                logger.warning(f"Model cannot patch: {cannot_patch_reason}")
                return self._build_cannot_patch_output(event_id, cannot_patch_reason)
            
            # Step 5: Check for auth/crypto modifications
            touches_auth_crypto = self._check_auth_crypto_files(diff_string)
            if touches_auth_crypto:
                logger.warning("Patch touches auth/crypto files")
            
            # Step 6: Calculate file modifications and line count
            files_modified = self._extract_modified_files(diff_string)
            lines_changed = self._count_changed_lines(diff_string)
            
            # Step 7: Score the patch
            llm_confidence = self.confidence_scorer.score(
                llm_log_prob=0.9,  # Assume reasonable confidence from successful generation
                diff_string=diff_string,
                reasoning_chain=reasoning_chain,
                structured_context=structured_context,
            )
            
            # Step 8: Build output
            output = self._build_candidate_patch_output(
                event_id=event_id,
                diff_string=diff_string,
                files_modified=files_modified,
                lines_changed=lines_changed,
                touches_auth_crypto=touches_auth_crypto,
                llm_confidence=llm_confidence,
                reasoning_chain=reasoning_chain,
            )
            
            logger.info(f"Patch generated successfully for {event_id} (confidence: {llm_confidence:.3f})")
            return output
        
        except Exception as e:
            logger.error(f"Patch generation failed for {event_id}: {str(e)}")
            raise
    
    async def _call_foundry(self, prompt: str) -> str:
        """
        Call Microsoft Foundry (Azure OpenAI) via aiohttp.
        
        Args:
            prompt: Complete four-section prompt.
        
        Returns:
            Response text from the model.
        """
        url = (
            f"{self.foundry_endpoint}/openai/deployments/{self.model_deployment}"
            f"/chat/completions?api-version={self.foundry_api_version}"
        )
        
        headers = {
            "api-key": self.foundry_api_key,
            "Content-Type": "application/json",
        }
        
        payload = {
            "messages": [
                {
                    "role": "user",
                    "content": prompt,
                }
            ],
            "max_tokens": 4096,
            "temperature": 0.2,  # Low temperature for consistency
            "top_p": 0.95,
        }
        
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(url, headers=headers, json=payload, timeout=aiohttp.ClientTimeout(total=60)) as resp:
                    if resp.status != 200:
                        error_text = await resp.text()
                        raise Exception(f"Foundry API error: {resp.status} - {error_text}")
                    
                    data = await resp.json()
                    response_text = data["choices"][0]["message"]["content"]
                    
                    logger.debug(f"Received response ({len(response_text)} chars)")
                    return response_text
        
        except asyncio.TimeoutError:
            raise Exception("Foundry API request timeout")
        except Exception as e:
            logger.error(f"Foundry API call failed: {str(e)}")
            raise
    
    @staticmethod
    def _parse_response(response_text: str) -> tuple[str, str, Optional[str]]:
        """
        Parse model response to extract reasoning, diff, and status.
        
        Returns:
            Tuple of (reasoning_chain, diff_string, cannot_patch_reason)
        """
        logger.debug("Parsing model response")
        
        # Check for CANNOT_PATCH
        if "CANNOT_PATCH" in response_text.upper():
            match = re.search(r'CANNOT_PATCH[:\s]+(.+?)(?:\n|$)', response_text, re.IGNORECASE)
            reason = match.group(1).strip() if match else "Unknown reason"
            logger.info(f"Model output CANNOT_PATCH: {reason}")
            return "", "", reason
        
        # Extract reasoning block
        reasoning_match = re.search(
            r'<reasoning>(.*?)</reasoning>',
            response_text,
            re.DOTALL | re.IGNORECASE
        )
        reasoning_chain = reasoning_match.group(1).strip() if reasoning_match else ""
        
        # Extract diff (everything after </reasoning>)
        if reasoning_match:
            diff_start_pos = reasoning_match.end()
            diff_string = response_text[diff_start_pos:].strip()
        else:
            # Fallback: treat entire response as diff if no reasoning block
            diff_string = response_text.strip()
        
        # Clean up diff: remove markdown code blocks if present
        diff_string = re.sub(r'^```.*?\n', '', diff_string)  # Remove opening code block
        diff_string = re.sub(r'\n```$', '', diff_string)      # Remove closing code block
        
        logger.debug(f"Extracted reasoning ({len(reasoning_chain)} chars) and diff ({len(diff_string)} chars)")
        
        return reasoning_chain, diff_string, None
    
    @staticmethod
    def _check_auth_crypto_files(diff_string: str) -> bool:
        """
        Check if diff modifies authentication or cryptographic files.
        
        Uses regex to detect common patterns in file paths.
        """
        auth_crypto_patterns = [
            r'auth',
            r'crypto',
            r'security',
            r'password',
            r'secret',
            r'token',
            r'ssl',
            r'tls',
            r'encryption',
        ]
        
        for line in diff_string.split("\n"):
            # Check file paths in diff headers
            if line.startswith("---") or line.startswith("+++"):
                file_path = line.split()[1] if len(line.split()) > 1 else ""
                file_path_lower = file_path.lower()
                
                for pattern in auth_crypto_patterns:
                    if re.search(pattern, file_path_lower):
                        logger.info(f"Detected auth/crypto file: {file_path}")
                        return True
        
        return False
    
    @staticmethod
    def _extract_modified_files(diff_string: str) -> list[str]:
        """Extract list of modified files from diff."""
        files = []
        
        for line in diff_string.split("\n"):
            if line.startswith("+++"):
                # Extract file path: +++ b/path/to/file.ext
                match = re.match(r'\+\+\+ b/(.+)', line)
                if match:
                    files.append(match.group(1))
        
        return files
    
    @staticmethod
    def _count_changed_lines(diff_string: str) -> int:
        """Count number of changed lines (additions + deletions)."""
        count = 0
        
        for line in diff_string.split("\n"):
            if line.startswith("+") and not line.startswith("+++"):
                count += 1
            elif line.startswith("-") and not line.startswith("---"):
                count += 1
        
        return count
    
    @staticmethod
    def _build_cannot_patch_output(event_id: str, cannot_patch_reason: str) -> dict[str, Any]:
        """Build output for CANNOT_PATCH scenario."""
        return {
            "event_id": event_id,
            "status": "CANNOT_PATCH",
            "source": "FOUNDRY",
            "diff": "",
            "files_modified": [],
            "lines_changed": 0,
            "touches_auth_crypto": False,
            "llm_confidence": 0.0,
            "reasoning_chain": f"Model determined patch is not feasible: {cannot_patch_reason}",
            "model_id": "gpt-4o",
            "cannot_patch_reason": cannot_patch_reason,
            "timestamp": datetime.utcnow().isoformat() + "Z",
        }
    
    @staticmethod
    def _build_candidate_patch_output(
        event_id: str,
        diff_string: str,
        files_modified: list[str],
        lines_changed: int,
        touches_auth_crypto: bool,
        llm_confidence: float,
        reasoning_chain: str,
    ) -> dict[str, Any]:
        """Build output for successful patch generation."""
        return {
            "event_id": event_id,
            "status": "PATCH_GENERATED",
            "source": "FOUNDRY",
            "diff": diff_string,
            "files_modified": files_modified,
            "lines_changed": lines_changed,
            "touches_auth_crypto": touches_auth_crypto,
            "llm_confidence": llm_confidence,
            "reasoning_chain": reasoning_chain,
            "model_id": "gpt-4o",
            "cannot_patch_reason": None,
            "timestamp": datetime.utcnow().isoformat() + "Z",
        }


async def main():
    """Example usage."""
    agent = PatchGeneratorAgent()
    
    structured_context = {
        "event_id": "CVE-2024-0001",
        "fix_strategy": "Update dependency version to 2.5.1",
        "breaking_changes": ["API endpoint change"],
        "community_intent_class": "SECURITY_UPDATE",
        "intent_confidence": 0.95,
        "nvd_context": {
            "description": "SQL injection vulnerability in query builder",
            "cvss_score": 9.1,
        },
        "migration_steps": ["Update version", "Run migrations"],
        "historical_match_status": "EXACT_MATCH",
        "historical_patch_available": True,
        "solutions_to_avoid": ["Monkey patching", "Global monkey patch"],
    }
    
    try:
        result = await agent.generate(structured_context)
        print(json.dumps(result, indent=2))
    except Exception as e:
        logger.error(f"Failed: {str(e)}")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(main())
