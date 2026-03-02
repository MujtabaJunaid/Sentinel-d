"""Embedding service for semantic search using Azure OpenAI."""

import asyncio
import logging
import os
from typing import List

import aiohttp

logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)


class EmbeddingService:
    """Generates 1536-dimensional vector embeddings for semantic search."""

    API_VERSION = "2024-08-01-preview"
    MODEL = "text-embedding-3-small"
    EMBEDDING_DIMENSION = 1536

    def __init__(
        self,
        api_endpoint: str,
        api_key: str
    ):
        """
        Initialize EmbeddingService.

        Args:
            api_endpoint: Azure OpenAI API endpoint URL.
            api_key: Azure OpenAI API key.
        """
        self.api_endpoint = api_endpoint.rstrip("/")
        self.api_key = api_key
        logger.info(f"Initialized EmbeddingService with endpoint: {self.api_endpoint}")

    async def embed_text(self, text: str) -> List[float]:
        """
        Generate embedding for input text using Azure OpenAI.

        Args:
            text: Raw text string (CVE description + package name).

        Returns:
            List of 1536 floats representing the embedding vector.

        Raises:
            RuntimeError: If API call fails or returns invalid response.
        """
        if not text or not text.strip():
            logger.warning("Empty text provided for embedding")
            return [0.0] * self.EMBEDDING_DIMENSION

        try:
            url = f"{self.api_endpoint}/openai/deployments/{self.MODEL}/embeddings"
            headers = {
                "api-key": self.api_key,
                "Content-Type": "application/json"
            }
            payload = {
                "input": text.strip(),
                "model": self.MODEL
            }

            async with aiohttp.ClientSession() as session:
                params = {"api-version": self.API_VERSION}
                async with session.post(
                    url,
                    json=payload,
                    headers=headers,
                    params=params,
                    timeout=aiohttp.ClientTimeout(total=15)
                ) as response:
                    if response.status == 200:
                        result = await response.json()
                        embedding = result.get("data", [{}])[0].get("embedding", [])
                        if embedding and len(embedding) == self.EMBEDDING_DIMENSION:
                            logger.debug(f"Successfully generated embedding for text length {len(text)}")
                            return embedding
                        else:
                            logger.error(f"Invalid embedding dimension: {len(embedding)}")
                            return [0.0] * self.EMBEDDING_DIMENSION
                    else:
                        error_msg = await response.text()
                        logger.error(f"Azure OpenAI API error {response.status}: {error_msg}")
                        raise RuntimeError(f"Embedding API returned status {response.status}")

        except asyncio.TimeoutError:
            logger.error("Timeout calling Azure OpenAI embedding API")
            raise RuntimeError("Embedding API timeout")
        except aiohttp.ClientError as e:
            logger.error(f"Client error calling embedding API: {e}")
            raise RuntimeError(f"Embedding API client error: {e}")
        except Exception as e:
            logger.error(f"Unexpected error generating embedding: {e}")
            raise RuntimeError(f"Embedding generation failed: {e}")
