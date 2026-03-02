"""Asynchronous clients for Cosmos DB and Azure AI Search."""

import asyncio
import logging
from typing import Dict, Any, List, Optional

import aiohttp

logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)


class AsyncCosmosClientWrapper:
    """Asynchronous wrapper for Cosmos DB queries."""

    def __init__(
        self,
        endpoint: str,
        database: str,
        container: str,
        credential_key: str
    ):
        """
        Initialize Cosmos DB client wrapper.

        Args:
            endpoint: Cosmos DB endpoint URL.
            database: Database name.
            container: Container name partitioned by /cve_id.
            credential_key: Primary or secondary key for authentication.
        """
        self.endpoint = endpoint.rstrip("/")
        self.database = database
        self.container = container
        self.credential_key = credential_key
        logger.info(
            f"Initialized AsyncCosmosClientWrapper for {database}/{container}"
        )

    async def get_exact_match(self, cve_id: str) -> Optional[Dict[str, Any]]:
        """
        Query Cosmos DB for exact CVE match with successful patch outcome.

        Args:
            cve_id: CVE identifier (e.g., CVE-2024-1234).

        Returns:
            Dictionary containing the matched record if found and patch_outcome is "SUCCESS",
            None otherwise.
        """
        logger.debug(f"Querying Cosmos DB for exact match: {cve_id}")

        try:
            # Construct query URL for Cosmos DB
            query_url = (
                f"{self.endpoint}/dbs/{self.database}/colls/{self.container}/docs"
            )
            headers = {
                "Authorization": f"type=master&ver=1.0&sig={self.credential_key}",
                "x-ms-version": "2020-07-15",
                "Content-Type": "application/query+json"
            }

            query_body = {
                "query": "SELECT * FROM c WHERE c.cve_id = @cve_id AND c.patch_outcome = @outcome",
                "parameters": [
                    {"name": "@cve_id", "value": cve_id},
                    {"name": "@outcome", "value": "SUCCESS"}
                ]
            }

            async with aiohttp.ClientSession() as session:
                async with session.post(
                    query_url,
                    json=query_body,
                    headers=headers,
                    timeout=aiohttp.ClientTimeout(total=10)
                ) as response:
                    if response.status == 200:
                        data = await response.json()
                        documents = data.get("Documents", [])
                        if documents:
                            logger.info(f"Found exact match in Cosmos DB for {cve_id}")
                            return documents[0]
                        else:
                            logger.debug(f"No exact match found for {cve_id}")
                            return None
                    else:
                        error_msg = await response.text()
                        logger.warning(f"Cosmos DB query error {response.status}: {error_msg}")
                        return None

        except asyncio.TimeoutError:
            logger.error(f"Timeout querying Cosmos DB for {cve_id}")
            return None
        except aiohttp.ClientError as e:
            logger.error(f"Client error querying Cosmos DB: {e}")
            return None
        except Exception as e:
            logger.error(f"Unexpected error in Cosmos DB query: {e}")
            return None


class AsyncAISearchWrapper:
    """Asynchronous wrapper for Azure AI Search vector similarity queries."""

    SIMILARITY_THRESHOLD = 0.88
    TOP_RESULTS = 3

    def __init__(
        self,
        endpoint: str,
        index_name: str,
        api_key: str
    ):
        """
        Initialize Azure AI Search client wrapper.

        Args:
            endpoint: Azure AI Search service endpoint URL.
            index_name: Name of the search index.
            api_key: API key for authentication.
        """
        self.endpoint = endpoint.rstrip("/")
        self.index_name = index_name
        self.api_key = api_key
        logger.info(f"Initialized AsyncAISearchWrapper for index: {index_name}")

    async def get_semantic_matches(
        self,
        embedding: List[float]
    ) -> List[Dict[str, Any]]:
        """
        Perform vector similarity search in Azure AI Search.

        Args:
            embedding: 1536-dimensional embedding vector.

        Returns:
            List of top 3 matching records where cosine similarity >= 0.88,
            sorted by relevance score descending.
        """
        logger.debug("Performing semantic search in Azure AI Search")

        if not embedding or len(embedding) != 1536:
            logger.warning(f"Invalid embedding dimension: {len(embedding) if embedding else 0}")
            return []

        try:
            search_url = f"{self.endpoint}/indexes/{self.index_name}/docs/search?api-version=2024-07-01"
            headers = {
                "api-key": self.api_key,
                "Content-Type": "application/json"
            }

            search_body = {
                "vectors": [
                    {
                        "value": embedding,
                        "fields": "embedding_vector",
                        "k": self.TOP_RESULTS
                    }
                ],
                "select": "*",
                "top": self.TOP_RESULTS
            }

            async with aiohttp.ClientSession() as session:
                async with session.post(
                    search_url,
                    json=search_body,
                    headers=headers,
                    timeout=aiohttp.ClientTimeout(total=15)
                ) as response:
                    if response.status == 200:
                        result = await response.json()
                        documents = result.get("value", [])

                        # Filter by similarity threshold and score
                        filtered_docs = []
                        for doc in documents:
                            score = doc.get("@search.score", 0.0)
                            # AI Search returns similarity score; filter by threshold
                            if score >= self.SIMILARITY_THRESHOLD:
                                filtered_docs.append({
                                    "id": doc.get("id", ""),
                                    "cve_id": doc.get("cve_id", ""),
                                    "record_id": doc.get("record_id", ""),
                                    "patch_id": doc.get("patch_id", ""),
                                    "affected_package": doc.get("affected_package", ""),
                                    "patch_outcome": doc.get("patch_outcome", ""),
                                    "patch_diff": doc.get("patch_diff", ""),
                                    "recommended_strategy": doc.get("recommended_strategy", ""),
                                    "solutions_tried": doc.get("solutions_tried", []),
                                    "similarity_score": score
                                })

                        logger.info(f"Found {len(filtered_docs)} semantic matches above threshold")
                        return filtered_docs[:self.TOP_RESULTS]
                    else:
                        error_msg = await response.text()
                        logger.warning(f"AI Search error {response.status}: {error_msg}")
                        return []

        except asyncio.TimeoutError:
            logger.error("Timeout querying Azure AI Search")
            return []
        except aiohttp.ClientError as e:
            logger.error(f"Client error querying AI Search: {e}")
            return []
        except Exception as e:
            logger.error(f"Unexpected error in semantic search: {e}")
            return []
