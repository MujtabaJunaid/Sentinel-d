"""Azure Service Bus consumer for the SRE Agent.

Subscribes to the vulnerability-events queue, processes each message through
the SRE Agent pipeline, and completes or abandons accordingly.
"""

import asyncio
import logging
import os
import signal
from typing import Any

from azure.identity.aio import DefaultAzureCredential
from azure.servicebus.aio import ServiceBusClient
from azure.servicebus import ServiceBusReceivedMessage
from dotenv import load_dotenv

from kql_generator import generate_kql
from kql_validator import validate_kql
from telemetry_query import query_telemetry
from classifier import classify
from router import route_classification

load_dotenv()

logger = logging.getLogger(__name__)

SB_NAMESPACE: str = os.environ.get("SERVICE_BUS_NAMESPACE", "")
SB_QUEUE: str = os.environ.get("SERVICE_BUS_QUEUE_NAME", "vulnerability-events")
WORKSPACE_ID: str = os.environ.get("APP_INSIGHTS_WORKSPACE_ID", "")

# Lock renewal interval: 4 minutes (lock duration is 5 min)
LOCK_RENEWAL_SECONDS: int = 4 * 60


async def process_event(event: dict[str, Any]) -> dict[str, Any]:
    """Process a single webhook payload through the SRE Agent pipeline.

    Generates KQL, validates it, queries telemetry, and classifies the result.

    Args:
        event: Parsed webhook_payload from Service Bus message body.

    Returns:
        A telemetry_classification dict conforming to the schema.

    Raises:
        ValueError: If KQL validation fails.
    """
    kql_query = await generate_kql(event["file_path"], event["affected_package"])

    validation = validate_kql(kql_query)
    if not validation["valid"]:
        raise ValueError(f"KQL validation failed: {validation['reason']}")

    telemetry_result = await query_telemetry(kql_query, WORKSPACE_ID)
    return classify(telemetry_result, event, kql_query)


async def _renew_lock(
    receiver: Any, message: ServiceBusReceivedMessage
) -> None:
    """Periodically renew the message lock until cancelled."""
    while True:
        await asyncio.sleep(LOCK_RENEWAL_SECONDS)
        try:
            await receiver.renew_message_lock(message)
        except Exception as exc:
            logger.error("Lock renewal failed for %s: %s", message.message_id, exc)


async def start_consumer() -> None:
    """Start the Service Bus consumer.

    Subscribes to the vulnerability-events queue, processes each message
    through the SRE Agent pipeline, and completes or abandons accordingly.
    """
    credential = DefaultAzureCredential()
    client = ServiceBusClient(
        fully_qualified_namespace=f"{SB_NAMESPACE}.servicebus.windows.net",
        credential=credential,
    )

    async with client:
        receiver = client.get_queue_receiver(queue_name=SB_QUEUE)
        async with receiver:
            logger.info("SRE Agent consumer listening on queue: %s", SB_QUEUE)

            # Graceful shutdown
            stop_event = asyncio.Event()
            loop = asyncio.get_running_loop()
            for sig in (signal.SIGINT, signal.SIGTERM):
                loop.add_signal_handler(sig, stop_event.set)

            while not stop_event.is_set():
                messages = await receiver.receive_messages(
                    max_message_count=1, max_wait_time=5
                )
                for message in messages:
                    renewal_task = asyncio.create_task(
                        _renew_lock(receiver, message)
                    )
                    try:
                        event = message.body
                        if isinstance(event, bytes):
                            import json
                            event = json.loads(event)

                        classification = await process_event(event)
                        logger.info(
                            "Classified event_id=%s as %s",
                            event.get("event_id"),
                            classification["status"],
                        )

                        # Route to downstream system based on classification
                        route_result = await route_classification(
                            classification, event
                        )
                        logger.info(
                            "Routed event_id=%s → %s (%s)",
                            event.get("event_id"),
                            route_result["destination"],
                            route_result["detail"],
                        )

                        await receiver.complete_message(message)
                    except Exception as exc:
                        logger.error(
                            "Processing failed for message %s: %s",
                            message.message_id,
                            exc,
                        )
                        await receiver.abandon_message(message)
                    finally:
                        renewal_task.cancel()

            logger.info("Shutting down SRE Agent consumer...")

    await credential.close()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    try:
        asyncio.run(start_consumer())
    except Exception as exc:
        logger.error("Consumer startup failed: %s", exc)
        raise SystemExit(1)
