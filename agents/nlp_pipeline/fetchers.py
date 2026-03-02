"""Asynchronous fetchers for external APIs."""

import asyncio
import logging
import json
from typing import Dict, Any, Optional
from datetime import datetime, timedelta
import aiohttp
import hashlib

logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)


class NVDFetcher:
    """Fetches security data from NVD 2.0 API with local response caching."""

    BASE_URL = "https://services.nvd.nist.gov/rest/json/cves/2.0"
    CACHE_DURATION = timedelta(hours=24)

    def __init__(self, api_key: Optional[str] = None):
        """
        Initialize NVDFetcher.

        Args:
            api_key: Optional NVD API key for higher rate limits.
        """
        self.api_key = api_key
        self.cache: Dict[str, tuple[Dict[str, Any], datetime]] = {}

    async def fetch(self, cve_id: str) -> Dict[str, Any]:
        """
        Fetch CVE data from NVD API with caching.

        Args:
            cve_id: CVE identifier (e.g., CVE-2024-1234).

        Returns:
            Dictionary containing CVE details or empty dict on failure.
        """
        cache_key = hashlib.md5(cve_id.encode()).hexdigest()

        # Check cache
        if cache_key in self.cache:
            cached_data, timestamp = self.cache[cache_key]
            if datetime.utcnow() - timestamp < self.CACHE_DURATION:
                logger.debug(f"Cache hit for CVE {cve_id}")
                return cached_data

        try:
            async with aiohttp.ClientSession() as session:
                params = {"cveId": cve_id}
                if self.api_key:
                    params["apiKey"] = self.api_key

                async with session.get(
                    self.BASE_URL,
                    params=params,
                    timeout=aiohttp.ClientTimeout(total=10)
                ) as response:
                    if response.status == 200:
                        data = await response.json()
                        # Cache the response
                        self.cache[cache_key] = (data, datetime.utcnow())
                        logger.info(f"Successfully fetched NVD data for {cve_id}")
                        return data
                    else:
                        logger.warning(f"NVD API error {response.status} for {cve_id}")
                        return {}
        except asyncio.TimeoutError:
            logger.error(f"Timeout fetching NVD data for {cve_id}")
            return {}
        except aiohttp.ClientError as e:
            logger.error(f"Client error fetching NVD data for {cve_id}: {e}")
            return {}
        except Exception as e:
            logger.error(f"Unexpected error fetching NVD data for {cve_id}: {e}")
            return {}


class StackOverflowFetcher:
    """Fetches context from Stack Exchange API v2.3."""

    BASE_URL = "https://api.stackexchange.com/2.3/search/advanced"
    SITE = "stackoverflow"

    def __init__(self):
        """Initialize StackOverflowFetcher."""
        pass

    async def fetch(self, affected_package: str, limit: int = 5) -> Dict[str, Any]:
        """
        Fetch Stack Overflow answers for a package with filtering by score.

        Args:
            affected_package: Package name to search for.
            limit: Maximum number of top-scored answers to return.

        Returns:
            Dictionary containing Stack Overflow search results or empty dict on failure.
        """
        try:
            async with aiohttp.ClientSession() as session:
                params = {
                    "q": affected_package,
                    "sort": "votes",
                    "order": "desc",
                    "site": self.SITE,
                    "pagesize": limit,
                }

                async with session.get(
                    self.BASE_URL,
                    params=params,
                    timeout=aiohttp.ClientTimeout(total=10)
                ) as response:
                    if response.status == 200:
                        data = await response.json()
                        logger.info(f"Successfully fetched Stack Overflow data for {affected_package}")
                        return data
                    else:
                        logger.warning(f"Stack Exchange API error {response.status} for {affected_package}")
                        return {}
        except asyncio.TimeoutError:
            logger.error(f"Timeout fetching Stack Overflow data for {affected_package}")
            return {}
        except aiohttp.ClientError as e:
            logger.error(f"Client error fetching Stack Overflow data for {affected_package}: {e}")
            return {}
        except Exception as e:
            logger.error(f"Unexpected error fetching Stack Overflow data for {affected_package}: {e}")
            return {}
