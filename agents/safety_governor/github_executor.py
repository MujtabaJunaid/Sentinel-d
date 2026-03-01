import asyncio
import logging
import os
from base64 import b64encode, b64decode
from dataclasses import dataclass
from typing import Any, Optional
from datetime import datetime
import re

import aiohttp

logger = logging.getLogger(__name__)


@dataclass
class GitHubExecutorConfig:
    """Configuration for GitHub executor."""
    pat_token: str
    repo_owner: str
    repo_name: str
    github_api_url: str = "https://api.github.com"


@dataclass
class ExecutionResult:
    """Result of GitHub operation execution."""
    success: bool
    url: str  # PR or Issue URL
    message: str


class UnifiedDiffParser:
    """Parses unified diff format and applies patches to files."""
    
    @staticmethod
    def parse_diff(diff_string: str) -> dict[str, dict[str, Any]]:
        """
        Parse unified diff string into file changes.
        
        Returns:
            Dictionary mapping file paths to {'additions', 'deletions', 'content'}.
        """
        files = {}
        current_file = None
        current_additions = []
        current_deletions = []
        
        for line in diff_string.split("\n"):
            if line.startswith("--- "):
                pass  # Skip old file marker
            elif line.startswith("+++ "):
                # Extract target file path
                match = re.match(r"\+\+\+ b/(.+)", line)
                if match:
                    current_file = match.group(1)
                    files[current_file] = {
                        "additions": [],
                        "deletions": [],
                        "operations": [],
                    }
            elif current_file and line.startswith("+") and not line.startswith("+++"):
                # Addition (skip the leading '+')
                files[current_file]["additions"].append(line[1:])
                files[current_file]["operations"].append(("add", line[1:]))
            elif current_file and line.startswith("-") and not line.startswith("---"):
                # Deletion (skip the leading '-')
                files[current_file]["deletions"].append(line[1:])
                files[current_file]["operations"].append(("del", line[1:]))
        
        return files
    
    @staticmethod
    def apply_patch(original_content: str, additions: list[str], deletions: list[str]) -> str:
        """
        Apply patch additions/deletions to original content.
        
        Note: This is a simplified parser. For complex diffs, a proper difflib-based
        approach would be more robust. This implementation handles simple additions/deletions.
        """
        lines = original_content.split("\n")
        result_lines = []
        deletion_set = set(deletions)
        
        for line in lines:
            if line not in deletion_set:
                result_lines.append(line)
        
        # Append additions at the end (simplified approach)
        result_lines.extend(additions)
        
        return "\n".join(result_lines)


class GitHubExecutor:
    """
    Executes GitHub operations based on safety decision.
    
    Handles PR creation with auto-approval labels, branch management,
    and Issue escalation for sensitive patches.
    """
    
    def __init__(self, config: GitHubExecutorConfig):
        """Initialize GitHub executor with credentials."""
        self.config = config
        self.headers = {
            "Authorization": f"Bearer {self.config.pat_token}",
            "Accept": "application/vnd.github.v3+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        logger.info(f"Initialized GitHub executor for {config.repo_owner}/{config.repo_name}")
    
    @staticmethod
    def from_environment() -> "GitHubExecutor":
        """Create executor from environment variables."""
        pat_token = os.getenv("GITHUB_PAT_TOKEN")
        repo_owner = os.getenv("GITHUB_REPO_OWNER")
        repo_name = os.getenv("GITHUB_REPO_NAME")
        
        if not all([pat_token, repo_owner, repo_name]):
            raise ValueError(
                "Missing required environment variables: "
                "GITHUB_PAT_TOKEN, GITHUB_REPO_OWNER, GITHUB_REPO_NAME"
            )
        
        config = GitHubExecutorConfig(
            pat_token=pat_token,
            repo_owner=repo_owner,
            repo_name=repo_name,
        )
        return GitHubExecutor(config)
    
    async def execute(
        self,
        tier: str,
        composite_score: float,
        candidate_patch: dict[str, Any],
        validation_bundle: dict[str, Any],
        visual_regression: bool,
    ) -> ExecutionResult:
        """
        Execute GitHub operation based on decision tier.
        
        Args:
            tier: Routing tier (HIGH, MEDIUM, LOW, BLOCKED).
            composite_score: Safety score (0-1).
            candidate_patch: Patch data with cve_id, diff, reasoning_chain.
            validation_bundle: Test results with test counts.
            visual_regression: Whether visual regression was detected.
        
        Returns:
            ExecutionResult with success status and URL.
        """
        try:
            cve_id = candidate_patch.get("cve_id")
            if not cve_id:
                raise ValueError("cve_id not provided in candidate_patch")
            
            logger.info(f"Executing {tier} action for {cve_id}")
            
            if tier in ("HIGH", "MEDIUM"):
                return await self._execute_pr_workflow(
                    cve_id=cve_id,
                    tier=tier,
                    composite_score=composite_score,
                    candidate_patch=candidate_patch,
                    validation_bundle=validation_bundle,
                    visual_regression=visual_regression,
                )
            elif tier == "LOW":
                return await self._execute_issue_workflow(
                    cve_id=cve_id,
                    composite_score=composite_score,
                    candidate_patch=candidate_patch,
                    validation_bundle=validation_bundle,
                )
            elif tier == "BLOCKED":
                raise Exception(f"Patch {cve_id} is BLOCKED and cannot be executed")
            else:
                raise ValueError(f"Unknown tier: {tier}")
        
        except Exception as e:
            logger.error(f"Execution failed: {str(e)}")
            raise
    
    async def _execute_pr_workflow(
        self,
        cve_id: str,
        tier: str,
        composite_score: float,
        candidate_patch: dict[str, Any],
        validation_bundle: dict[str, Any],
        visual_regression: bool,
    ) -> ExecutionResult:
        """Execute full PR workflow: branch, diff, commit, PR."""
        async with aiohttp.ClientSession() as session:
            try:
                # Get default branch
                default_branch = await self._get_default_branch(session)
                logger.info(f"Target branch: {default_branch}")
                
                # Create feature branch
                branch_name = f"security/sentinel-d-{cve_id}"
                branch_sha = await self._get_branch_sha(session, default_branch)
                await self._create_branch(session, branch_name, branch_sha)
                logger.info(f"Created branch: {branch_name}")
                
                # Apply diff to files
                diff_string = candidate_patch.get("diff", "")
                if diff_string:
                    await self._apply_diff_to_files(session, branch_name, diff_string, cve_id)
                
                # Create PR with appropriate label
                label = "sentinel/auto-approve" if tier == "HIGH" else "sentinel/needs-review"
                pr_body = self._build_pr_body(
                    cve_id=cve_id,
                    composite_score=composite_score,
                    candidate_patch=candidate_patch,
                    validation_bundle=validation_bundle,
                    visual_regression=visual_regression,
                )
                
                pr_url = await self._create_pull_request(
                    session=session,
                    branch_name=branch_name,
                    base_branch=default_branch,
                    cve_id=cve_id,
                    pr_body=pr_body,
                    label=label,
                )
                
                logger.info(f"PR created: {pr_url}")
                return ExecutionResult(
                    success=True,
                    url=pr_url,
                    message=f"PR created with label '{label}'",
                )
            
            except Exception as e:
                logger.error(f"PR workflow failed: {str(e)}")
                raise
    
    async def _execute_issue_workflow(
        self,
        cve_id: str,
        composite_score: float,
        candidate_patch: dict[str, Any],
        validation_bundle: dict[str, Any],
    ) -> ExecutionResult:
        """Execute Issue workflow for escalation."""
        async with aiohttp.ClientSession() as session:
            try:
                issue_body = self._build_issue_body(
                    cve_id=cve_id,
                    composite_score=composite_score,
                    candidate_patch=candidate_patch,
                    validation_bundle=validation_bundle,
                )
                
                issue_url = await self._create_issue(
                    session=session,
                    cve_id=cve_id,
                    body=issue_body,
                    label="sentinel/escalate",
                )
                
                logger.info(f"Issue created: {issue_url}")
                return ExecutionResult(
                    success=True,
                    url=issue_url,
                    message="Escalation issue created",
                )
            
            except Exception as e:
                logger.error(f"Issue workflow failed: {str(e)}")
                raise
    
    async def _get_default_branch(self, session: aiohttp.ClientSession) -> str:
        """Get repository default branch."""
        url = f"{self.config.github_api_url}/repos/{self.config.repo_owner}/{self.config.repo_name}"
        
        async with session.get(url, headers=self.headers) as resp:
            if resp.status != 200:
                raise Exception(f"Failed to fetch repo info: {resp.status}")
            data = await resp.json()
            return data["default_branch"]
    
    async def _get_branch_sha(self, session: aiohttp.ClientSession, branch: str) -> str:
        """Get SHA of branch head."""
        url = f"{self.config.github_api_url}/repos/{self.config.repo_owner}/{self.config.repo_name}/refs/heads/{branch}"
        
        async with session.get(url, headers=self.headers) as resp:
            if resp.status != 200:
                raise Exception(f"Failed to fetch branch SHA: {resp.status}")
            data = await resp.json()
            return data["object"]["sha"]
    
    async def _create_branch(self, session: aiohttp.ClientSession, branch_name: str, sha: str) -> None:
        """Create new branch."""
        url = f"{self.config.github_api_url}/repos/{self.config.repo_owner}/{self.config.repo_name}/git/refs"
        payload = {"ref": f"refs/heads/{branch_name}", "sha": sha}
        
        async with session.post(url, headers=self.headers, json=payload) as resp:
            if resp.status not in [200, 201]:
                error_msg = await resp.text()
                raise Exception(f"Failed to create branch: {resp.status} - {error_msg}")
    
    async def _get_file_sha_and_content(
        self,
        session: aiohttp.ClientSession,
        file_path: str,
        branch: str,
    ) -> tuple[str, str]:
        """Get SHA and decoded content of file on branch."""
        url = f"{self.config.github_api_url}/repos/{self.config.repo_owner}/{self.config.repo_name}/contents/{file_path}"
        params = {"ref": branch}
        
        async with session.get(url, headers=self.headers, params=params) as resp:
            if resp.status != 200:
                raise Exception(f"File not found: {file_path} ({resp.status})")
            data = await resp.json()
            
            file_sha = data["sha"]
            encoded_content = data.get("content", "")
            
            # Decode base64 content
            try:
                decoded_content = b64decode(encoded_content).decode('utf-8')
            except Exception as e:
                logger.warning(f"Failed to decode file {file_path}: {str(e)}")
                decoded_content = ""
            
            return file_sha, decoded_content
    
    async def _apply_diff_to_files(
        self,
        session: aiohttp.ClientSession,
        branch_name: str,
        diff_string: str,
        cve_id: str,
    ) -> None:
        """Apply unified diff to target files."""
        try:
            parsed_diff = UnifiedDiffParser.parse_diff(diff_string)
            
            for file_path, changes in parsed_diff.items():
                logger.info(f"Applying patch to {file_path}")
                
                # Get current file content
                file_sha, current_content = await self._get_file_sha_and_content(
                    session, file_path, branch_name
                )
                
                # Apply diff operations
                new_content = UnifiedDiffParser.apply_patch(
                    current_content,
                    changes.get("additions", []),
                    changes.get("deletions", []),
                )
                
                # Commit changes
                await self._update_file(
                    session=session,
                    file_path=file_path,
                    new_content=new_content,
                    file_sha=file_sha,
                    branch=branch_name,
                    commit_message=f"Security patch: Apply fix for {cve_id}",
                )
        
        except Exception as e:
            logger.error(f"Failed to apply diff: {str(e)}")
            raise
    
    async def _update_file(
        self,
        session: aiohttp.ClientSession,
        file_path: str,
        new_content: str,
        file_sha: str,
        branch: str,
        commit_message: str,
    ) -> None:
        """Update file via GitHub API."""
        url = f"{self.config.github_api_url}/repos/{self.config.repo_owner}/{self.config.repo_name}/contents/{file_path}"
        
        encoded_content = b64encode(new_content.encode()).decode()
        
        payload = {
            "message": commit_message,
            "content": encoded_content,
            "sha": file_sha,
            "branch": branch,
        }
        
        async with session.put(url, headers=self.headers, json=payload) as resp:
            if resp.status not in [200, 201]:
                error_msg = await resp.text()
                raise Exception(f"Failed to update file: {resp.status} - {error_msg}")
    
    async def _create_pull_request(
        self,
        session: aiohttp.ClientSession,
        branch_name: str,
        base_branch: str,
        cve_id: str,
        pr_body: str,
        label: str,
    ) -> str:
        """Create pull request and apply label."""
        url = f"{self.config.github_api_url}/repos/{self.config.repo_owner}/{self.config.repo_name}/pulls"
        
        payload = {
            "title": f"Security Patch: {cve_id} (Sentinel-D)",
            "head": branch_name,
            "base": base_branch,
            "body": pr_body,
        }
        
        async with session.post(url, headers=self.headers, json=payload) as resp:
            if resp.status not in [200, 201]:
                error_msg = await resp.text()
                raise Exception(f"Failed to create PR: {resp.status} - {error_msg}")
            data = await resp.json()
            pr_url = data["html_url"]
            pr_number = data["number"]
        
        # Apply label
        await self._apply_label(session, pr_number, label)
        
        return pr_url
    
    async def _apply_label(
        self,
        session: aiohttp.ClientSession,
        issue_number: int,
        label: str,
    ) -> None:
        """Apply label to PR/Issue."""
        url = f"{self.config.github_api_url}/repos/{self.config.repo_owner}/{self.config.repo_name}/issues/{issue_number}/labels"
        
        payload = {"labels": [label]}
        
        async with session.post(url, headers=self.headers, json=payload) as resp:
            if resp.status not in [200, 201]:
                logger.warning(f"Failed to apply label: {resp.status}")
    
    async def _create_issue(
        self,
        session: aiohttp.ClientSession,
        cve_id: str,
        body: str,
        label: str,
    ) -> str:
        """Create GitHub Issue for escalation."""
        url = f"{self.config.github_api_url}/repos/{self.config.repo_owner}/{self.config.repo_name}/issues"
        
        payload = {
            "title": f"Security Review Required: {cve_id} (Sentinel-D)",
            "body": body,
        }
        
        async with session.post(url, headers=self.headers, json=payload) as resp:
            if resp.status not in [200, 201]:
                error_msg = await resp.text()
                raise Exception(f"Failed to create issue: {resp.status} - {error_msg}")
            data = await resp.json()
            issue_url = data["html_url"]
            issue_number = data["number"]
        
        # Apply escalation label
        await self._apply_label(session, issue_number, label)
        
        return issue_url
    
    def _build_pr_body(
        self,
        cve_id: str,
        composite_score: float,
        candidate_patch: dict[str, Any],
        validation_bundle: dict[str, Any],
        visual_regression: bool,
    ) -> str:
        """Build PR description with diagnostic information."""
        tests_passed = validation_bundle.get("tests_passed", 0)
        tests_failed = validation_bundle.get("tests_failed", 0)
        total_tests = tests_passed + tests_failed
        test_pass_rate = (tests_passed / total_tests * 100) if total_tests > 0 else 0
        
        reasoning_chain = candidate_patch.get("reasoning_chain", "N/A")
        
        visual_status = "🔴 REGRESSION DETECTED" if visual_regression else "✅ PASSED"
        
        pr_body = f"""# Security Patch: {cve_id} (Sentinel-D)

## Safety Assessment

- **Composite Safety Score**: {composite_score:.3f} / 1.000
- **Test Pass Rate**: {test_pass_rate:.1f}% ({tests_passed}/{total_tests})
- **SSIM Visual Regression**: {visual_status}

## Test Results

| Metric | Value |
|--------|-------|
| Tests Passed | {tests_passed} |
| Tests Failed | {tests_failed} |
| Coverage Before | {validation_bundle.get('coverage_before', 0):.1f}% |
| Coverage After | {validation_bundle.get('coverage_after', 0):.1f}% |

## Reasoning Chain

```
{reasoning_chain}
```

## Patch Details

Generated by Sentinel-D Safety Governor on {datetime.utcnow().isoformat()}Z

---

**Deployment Strategy**
1. Merge to default branch when approval obtained
2. CI/CD pipeline triggers automatically
3. Deploy to staging first (24h monitoring)
4. Production rollout with auto-rollback triggers

---

*This PR was automatically generated by Sentinel-D Security Patch Orchestration System*
"""
        return pr_body
    
    def _build_issue_body(
        self,
        cve_id: str,
        composite_score: float,
        candidate_patch: dict[str, Any],
        validation_bundle: dict[str, Any],
    ) -> str:
        """Build Issue description for escalation."""
        reasoning_chain = candidate_patch.get("reasoning_chain", "N/A")
        
        issue_body = f"""# Sentinel-D Security Review Escalation: {cve_id}

## Diagnostic Bundle

- **Composite Safety Score**: {composite_score:.3f} / 1.000
- **Escalation Reason**: Sensitive code changes or lower confidence score
- **Generated**: {datetime.utcnow().isoformat()}Z

## Test Results

| Metric | Value |
|--------|-------|
| Tests Passed | {validation_bundle.get('tests_passed', 0)} |
| Tests Failed | {validation_bundle.get('tests_failed', 0)} |
| Coverage Change | {validation_bundle.get('coverage_before', 0):.1f}% → {validation_bundle.get('coverage_after', 0):.1f}% |

## Reasoning Chain

```
{reasoning_chain}
```

## Required Actions

1. Security team: Review patch reasoning and implications
2. Validate: Confirm test results and coverage metrics
3. Decision: Approve for PR creation or request additional changes
4. Approval: Comment `@sentinel-d approve` to proceed

## Diff

```diff
{candidate_patch.get('diff', 'N/A')}
```

---

*Escalated by Sentinel-D for manual security review*
"""
        return issue_body
