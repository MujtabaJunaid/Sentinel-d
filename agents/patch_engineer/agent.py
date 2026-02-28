import asyncio
import json
import logging
import os
from base64 import b64encode, b64decode
from dataclasses import dataclass
from typing import Any, Optional

import aiohttp

logger = logging.getLogger(__name__)


@dataclass
class PatchEngineering:
    cve_id: str
    target_file: str
    new_version: str
    telemetry_data: str
    test_results: str
    version_bump_logic: str


class PatchEngineerAgent:
    """Executes security patches via GitHub API with full async/await support."""

    def __init__(self):
        self.pat_token = os.getenv("GITHUB_PAT_TOKEN")
        if not self.pat_token:
            raise ValueError("GITHUB_PAT_TOKEN environment variable not set")

        self.github_api_url = "https://api.github.com"
        self.repo_owner = os.getenv("GITHUB_REPO_OWNER", "")
        self.repo_name = os.getenv("GITHUB_REPO_NAME", "")

        if not self.repo_owner or not self.repo_name:
            raise ValueError("GITHUB_REPO_OWNER or GITHUB_REPO_NAME not set")

        self.headers = {
            "Authorization": f"Bearer {self.pat_token}",
            "Accept": "application/vnd.github.v3+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }

    async def apply_patch(self, payload: dict[str, Any]) -> str:
        """
        Execute full patch workflow: branch, commit, PR.

        Args:
            payload: Dictionary with cve_id, target_file, new_version, telemetry, test_results, version_bump_logic.

        Returns:
            PR URL string.

        Raises:
            Exception: On GitHub API errors or workflow failures.
        """
        try:
            patch = PatchEngineering(
                cve_id=payload["cve_id"],
                target_file=payload["target_file"],
                new_version=payload["new_version"],
                telemetry_data=payload.get("telemetry_data", ""),
                test_results=payload.get("test_results", ""),
                version_bump_logic=payload.get("version_bump_logic", ""),
            )

            logger.info(f"Starting patch workflow for {patch.cve_id}")

            async with aiohttp.ClientSession() as session:
                default_branch = await self._get_default_branch(session)
                logger.info(f"Default branch: {default_branch}")

                branch_name = f"security/sentinel-d-{patch.cve_id}"
                branch_sha = await self._get_branch_sha(session, default_branch)
                logger.info(f"Creating branch {branch_name} at {branch_sha}")

                await self._create_branch(session, branch_name, branch_sha)

                pr_url = await self._commit_and_create_pr(session, patch, branch_name, default_branch)
                logger.info(f"PR created successfully: {pr_url}")

                return pr_url

        except Exception as e:
            logger.error(f"Patch workflow failed: {str(e)}")
            raise

    async def _get_default_branch(self, session: aiohttp.ClientSession) -> str:
        """Retrieve default branch name."""
        url = f"{self.github_api_url}/repos/{self.repo_owner}/{self.repo_name}"
        try:
            async with session.get(url, headers=self.headers) as resp:
                if resp.status != 200:
                    raise Exception(f"Failed to fetch repo info: {resp.status}")
                data = await resp.json()
                return data["default_branch"]
        except Exception as e:
            logger.error(f"Error fetching default branch: {str(e)}")
            raise

    async def _get_branch_sha(self, session: aiohttp.ClientSession, branch: str) -> str:
        """Get SHA of branch head."""
        url = f"{self.github_api_url}/repos/{self.repo_owner}/{self.repo_name}/refs/heads/{branch}"
        try:
            async with session.get(url, headers=self.headers) as resp:
                if resp.status != 200:
                    raise Exception(f"Failed to fetch branch SHA: {resp.status}")
                data = await resp.json()
                return data["object"]["sha"]
        except Exception as e:
            logger.error(f"Error fetching branch SHA: {str(e)}")
            raise

    async def _create_branch(self, session: aiohttp.ClientSession, branch_name: str, sha: str) -> None:
        """Create new branch."""
        url = f"{self.github_api_url}/repos/{self.repo_owner}/{self.repo_name}/git/refs"
        payload = {"ref": f"refs/heads/{branch_name}", "sha": sha}

        try:
            async with session.post(url, headers=self.headers, json=payload) as resp:
                if resp.status not in [200, 201]:
                    error_msg = await resp.text()
                    raise Exception(f"Failed to create branch: {resp.status} - {error_msg}")
                logger.info(f"Branch created: {branch_name}")
        except Exception as e:
            logger.error(f"Error creating branch: {str(e)}")
            raise

    async def _get_file_sha(self, session: aiohttp.ClientSession, file_path: str, branch: str) -> str:
        """Get SHA of file on branch."""
        url = f"{self.github_api_url}/repos/{self.repo_owner}/{self.repo_name}/contents/{file_path}"
        params = {"ref": branch}

        try:
            async with session.get(url, headers=self.headers, params=params) as resp:
                if resp.status != 200:
                    raise Exception(f"File not found: {resp.status}")
                data = await resp.json()
                return data["sha"]
        except Exception as e:
            logger.error(f"Error fetching file SHA: {str(e)}")
            raise

    async def _get_file_content(self, session: aiohttp.ClientSession, file_path: str, branch: str) -> str:
        """Get file content and decode it from base64."""
        url = f"{self.github_api_url}/repos/{self.repo_owner}/{self.repo_name}/contents/{file_path}"
        params = {"ref": branch}

        try:
            async with session.get(url, headers=self.headers, params=params) as resp:
                if resp.status != 200:
                    raise Exception(f"Failed to fetch file: {resp.status}")
                data = await resp.json()
                
                # GitHub returns base64 encoded content, often with newlines
                encoded_content = data.get("content", "")
                if not encoded_content:
                    return ""
                    
                # Decode the base64 back to a standard UTF-8 string
                decoded_content = b64decode(encoded_content).decode('utf-8')
                return decoded_content
                
        except Exception as e:
            logger.error(f"Error fetching file content: {str(e)}")
            raise

    async def _update_file(
        self,
        session: aiohttp.ClientSession,
        file_path: str,
        new_content: str,
        branch: str,
        commit_message: str,
    ) -> None:
        """Update file with new version."""
        url = f"{self.github_api_url}/repos/{self.repo_owner}/{self.repo_name}/contents/{file_path}"

        try:
            file_sha = await self._get_file_sha(session, file_path, branch)

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
                logger.info(f"File updated: {file_path}")
        except Exception as e:
            logger.error(f"Error updating file: {str(e)}")
            raise

    async def _create_pull_request(
        self, session: aiohttp.ClientSession, branch_name: str, default_branch: str, pr_body: str, cve_id: str
    ) -> str:
        """Create pull request."""
        url = f"{self.github_api_url}/repos/{self.repo_owner}/{self.repo_name}/pulls"

        payload = {
            "title": f"Security Patch: {cve_id} (Sentinel-D)",
            "head": branch_name,
            "base": default_branch,
            "body": pr_body,
        }

        try:
            async with session.post(url, headers=self.headers, json=payload) as resp:
                if resp.status not in [200, 201]:
                    error_msg = await resp.text()
                    raise Exception(f"Failed to create PR: {resp.status} - {error_msg}")
                data = await resp.json()
                return data["html_url"]
        except Exception as e:
            logger.error(f"Error creating pull request: {str(e)}")
            raise

    async def _commit_and_create_pr(
        self, session: aiohttp.ClientSession, patch: PatchEngineering, branch_name: str, default_branch: str
    ) -> str:
        """Commit changes and create PR."""
        try:
            target_file = patch.target_file
            current_content = await self._get_file_content(session, target_file, branch_name)

            new_content = current_content.replace(
                "version_placeholder", patch.new_version
            )

            commit_msg = f"Security patch: Update version for {patch.cve_id}"
            await self._update_file(session, target_file, new_content, branch_name, commit_msg)

            from agents.patch_engineer.pr_generator import PullRequestGenerator

            pr_gen = PullRequestGenerator()
            pr_body = pr_gen.generate(
                {
                    "cve_id": patch.cve_id,
                    "sre_telemetry": patch.telemetry_data,
                    "sandbox_test_results": patch.test_results,
                    "version_bump_logic": patch.version_bump_logic,
                }
            )

            pr_url = await self._create_pull_request(session, branch_name, default_branch, pr_body, patch.cve_id)
            return pr_url

        except Exception as e:
            logger.error(f"Error in commit and PR creation: {str(e)}")
            raise
