"""Asynchronous clients for Cosmos DB and Azure AI Search."""

import asyncio
import logging
import os
from typing import Dict, Any, List, Optional

from azure.cosmos.aio import CosmosClient, ContainerProxy
from azure.core.credentials import AzureKeyCredential
from azure.search.documents.aio import SearchClient

logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)


class AsyncCosmosClientWrapper:
    """Asynchronous wrapper for Cosmos DB queries using Azure SDK."""

    def __init__(self):
        """
        Initialize Cosmos DB client wrapper from environment variables.
        
        Reads: COSMOS_DB_ENDPOINT, COSMOS_DB_READ_KEY, COSMOS_DB_NAME, COSMOS_CONTAINER_NAME
        """
        self.endpoint = os.getenv("COSMOS_DB_ENDPOINT")
        self.read_key = os.getenv("COSMOS_DB_READ_KEY")
        self.database_name = os.getenv("COSMOS_DB_NAME", "sentinel")
        self.container_name = os.getenv("COSMOS_CONTAINER_NAME", "cve_patches")
        
        if not self.endpoint or not self.read_key:
            raise ValueError(
                "COSMOS_DB_ENDPOINT and COSMOS_DB_READ_KEY environment variables required"
            )
        
        self.client = None
        self.container: ContainerProxy = None
        logger.info(
            f"Initialized AsyncCosmosClientWrapper for {self.database_name}/{self.container_name}"
        )

    async def __aenter__(self):
        """Async context manager entry."""
        self.client = CosmosClient(self.endpoint, credential=AzureKeyCredential(self.read_key))
        database = self.client.get_database_client(self.database_name)
        self.container = database.get_container_client(self.container_name)
        return self

    async def __aexit__(self, *args):
        """Async context manager exit."""
        if self.client:
            await self.client.close()

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
            if not self.container:
                raise RuntimeError("Cosmos DB container not initialized. Use async with context.")
            
            # Query for exact CVE match with SUCCESS outcome
            query = "SELECT * FROM c WHERE c.cve_id = @cve_id AND c.patch_outcome = @outcome"
            
            async for item in self.container.query_items(
                query=query,
                parameters=[
                    {"name": "@cve_id", "value": cve_id},
                    {"name": "@outcome", "value": "SUCCESS"}
                ],
                max_item_count=1
            ):
                logger.info(f"Found exact match in Cosmos DB for {cve_id}")
                return item
            
            logger.debug(f"No exact match found for {cve_id}")
            return None

        except Exception as e:
            logger.error(f"Error querying Cosmos DB: {str(e)}", exc_info=True)
            return None


class AsyncAISearchWrapper:
    """Asynchronous wrapper for Azure AI Search vector similarity queries."""

    SIMILARITY_THRESHOLD = 0.88
    TOP_RESULTS = 3

    def __init__(self):
        """
        Initialize Azure AI Search client wrapper from environment variables.
        
        Reads: AI_SEARCH_ENDPOINT, AI_SEARCH_API_KEY, AI_SEARCH_INDEX_NAME
        """
        self.endpoint = os.getenv("AI_SEARCH_ENDPOINT")
        self.api_key = os.getenv("AI_SEARCH_API_KEY")
        self.index_name = os.getenv("AI_SEARCH_INDEX_NAME", "cve-patches-index")
        
        if not self.endpoint or not self.api_key:
            raise ValueError(
                "AI_SEARCH_ENDPOINT and AI_SEARCH_API_KEY environment variables required"
            )
        
        credential = AzureKeyCredential(self.api_key)
        self.client = SearchClient(
            endpoint=self.endpoint,
            index_name=self.index_name,
            credential=credential
        )
        logger.info(f"Initialized AsyncAISearchWrapper for index: {self.index_name}")

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
            # Perform vector search using Azure SDK
            # Note: SearchClient.search_async() requires async context
            results = []
            
            # Use synchronous client wrapped in async context
            # This is simplified for production; consider using httpx for true async
            async def _search():
                search_results = self.client.search(
                    search_text="",  # Empty text for pure vector search
                    vector=embedding,
                    vector_fields="cve_description_embedding",
                    k=self.TOP_RESULTS,
                    select=["id", "cve_id", "record_id", "patch_id", "affected_package",
                            "patch_outcome", "patch_diff", "recommended_strategy", 
                            "solutions_tried"],
                )
                
                docs = []
                async for result in search_results:
                    score = result.get("@search.score", 0.0)
                    if score >= self.SIMILARITY_THRESHOLD:
                        docs.append({
                            "id": result.get("id", ""),
                            "cve_id": result.get("cve_id", ""),
                            "record_id": result.get("record_id", ""),
                            "patch_id": result.get("patch_id", ""),
                            "affected_package": result.get("affected_package", ""),
                            "patch_outcome": result.get("patch_outcome", ""),
                            "patch_diff": result.get("patch_diff", ""),
                            "recommended_strategy": result.get("recommended_strategy", ""),
                            "solutions_tried": result.get("solutions_tried", []),
                            "similarity_score": score
                        })
                return docs
            
            # Run search in thread pool to avoid blocking
            import concurrent.futures
            loop = asyncio.get_event_loop()
            with concurrent.futures.ThreadPoolExecutor() as pool:
                docs = await loop.run_in_executor(
                    pool,
                    lambda: self.client.search(
                        search_text="",
                        vector=embedding,
                        vector_fields="cve_description_embedding",
                        k=self.TOP_RESULTS,
                        select=["id", "cve_id", "record_id", "patch_id", "affected_package",
                                "patch_outcome", "patch_diff", "recommended_strategy", 
                                "solutions_tried"],
                    )
                )
            
            filtered_docs = []
            for result in docs:
                score = result.get("@search.score", 0.0)
                if score >= self.SIMILARITY_THRESHOLD:
                    filtered_docs.append({
                        "id": result.get("id", ""),
                        "cve_id": result.get("cve_id", ""),
                        "record_id": result.get("record_id", ""),
                        "patch_id": result.get("patch_id", ""),
                        "affected_package": result.get("affected_package", ""),
                        "patch_outcome": result.get("patch_outcome", ""),
                        "patch_diff": result.get("patch_diff", ""),
                        "recommended_strategy": result.get("recommended_strategy", ""),
                        "solutions_tried": result.get("solutions_tried", []),
                        "similarity_score": score
                    })
            
            logger.info(f"Found {len(filtered_docs)} semantic matches above threshold {self.SIMILARITY_THRESHOLD}")
            return filtered_docs[:self.TOP_RESULTS]

        except Exception as e:
            logger.error(f"Error in semantic search: {str(e)}", exc_info=True)
            return []
