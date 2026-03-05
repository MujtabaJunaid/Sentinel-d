"""Shared retry utility with exponential backoff.

Wraps any async function with automatic retry on transient Azure/HTTP failures.
"""

import asyncio
import logging
from typing import Any, Callable, Optional, Set

logger = logging.getLogger(__name__)

DEFAULT_RETRY_CODES: Set[int] = {429, 503, 504}


def is_retryable(exc: Exception, retry_codes: Set[int]) -> bool:
    """Check if an exception is retryable based on status code or message."""
    status = getattr(exc, "status_code", None) or getattr(exc, "code", None)
    if isinstance(status, int) and status in retry_codes:
        return True

    message = str(exc).lower()
    retryable_phrases = [
        "too many requests",
        "service unavailable",
        "gateway timeout",
        "connection reset",
        "timed out",
    ]
    return any(phrase in message for phrase in retryable_phrases)


async def with_retry(
    fn: Callable[..., Any],
    *args: Any,
    max_attempts: int = 3,
    base_delay_s: float = 1.0,
    max_delay_s: float = 30.0,
    retry_codes: Optional[Set[int]] = None,
    label: str = "operation",
    **kwargs: Any,
) -> Any:
    """Execute an async function with exponential backoff retry.

    Args:
        fn: Async callable to execute.
        *args: Positional arguments passed to fn.
        max_attempts: Maximum number of attempts (default 3).
        base_delay_s: Base delay in seconds (doubled each retry).
        max_delay_s: Maximum delay cap in seconds.
        retry_codes: HTTP status codes to retry on.
        label: Label for log messages.
        **kwargs: Keyword arguments passed to fn.

    Returns:
        Result of fn(*args, **kwargs).
    """
    if retry_codes is None:
        retry_codes = DEFAULT_RETRY_CODES

    last_error: Optional[Exception] = None

    for attempt in range(1, max_attempts + 1):
        try:
            return await fn(*args, **kwargs)
        except Exception as exc:
            last_error = exc

            if attempt >= max_attempts or not is_retryable(exc, retry_codes):
                raise

            delay = min(base_delay_s * (2 ** (attempt - 1)), max_delay_s)
            logger.warning(
                "Retry %d/%d for %s (delay=%.1fs): %s",
                attempt,
                max_attempts,
                label,
                delay,
                str(exc),
            )
            await asyncio.sleep(delay)

    raise last_error  # type: ignore[misc]
