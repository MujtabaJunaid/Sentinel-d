"""Three-way router for SRE Agent classification results.

Routes events based on telemetry classification status:
    ACTIVE   → publish to ``nlp-pipeline-input`` Service Bus topic
    DORMANT  → create GitHub Decision Gate issue
    DEFERRED → write to deferred backlog (Azure Table Storage)
"""

import asyncio
import json
import logging
import os
import subprocess
from typing import Any

from azure.identity.aio import DefaultAzureCredential
from azure.servicebus.aio import ServiceBusClient
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

SB_NAMESPACE: str = os.environ.get("SERVICE_BUS_NAMESPACE", "")
NLP_TOPIC: str = os.environ.get("NLP_PIPELINE_TOPIC", "nlp-pipeline-input")

# Path to the Safety Governor create-decision-issue.js script
_SAFETY_GOVERNOR_DIR = os.path.join(
    os.path.dirname(__file__), "..", "safety-governor"
)


async def route_classification(
    classification: dict[str, Any],
    original_event: dict[str, Any],
    historical_match: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Route a classified event to the appropriate downstream system.

    Args:
        classification: telemetry_classification dict from classifier.
        original_event: The original webhook_payload dict.
        historical_match: Optional historical DB lookup result.

    Returns:
        Dict with routing result: ``{status, destination, detail}``.
    """
    status = classification["status"]

    if status == "ACTIVE":
        return await _route_active(classification, original_event)
    elif status == "DORMANT":
        return await _route_dormant(classification, original_event, historical_match)
    elif status == "DEFERRED":
        return await _route_deferred(classification, original_event)
    else:
        logger.error("Unknown classification status: %s", status)
        return {
            "status": status,
            "destination": "UNKNOWN",
            "detail": f"Unrecognised status: {status}",
        }


async def _route_active(
    classification: dict[str, Any],
    original_event: dict[str, Any],
) -> dict[str, Any]:
    """Publish ACTIVE event to the nlp-pipeline-input Service Bus topic."""
    if not SB_NAMESPACE:
        raise EnvironmentError("SERVICE_BUS_NAMESPACE is required for ACTIVE routing")

    message_body = {
        "event_id": original_event["event_id"],
        "classification": classification,
        "webhook_payload": original_event,
    }

    credential = DefaultAzureCredential()
    client = ServiceBusClient(
        fully_qualified_namespace=f"{SB_NAMESPACE}.servicebus.windows.net",
        credential=credential,
    )

    try:
        async with client:
            sender = client.get_topic_sender(topic_name=NLP_TOPIC)
            async with sender:
                from azure.servicebus import ServiceBusMessage

                msg = ServiceBusMessage(
                    body=json.dumps(message_body),
                    application_properties={
                        "source": "sre-agent",
                        "event_id": original_event["event_id"],
                        "status": "ACTIVE",
                    },
                )
                await sender.send_messages(msg)

        logger.info(
            "Published ACTIVE event %s to topic %s",
            original_event["event_id"],
            NLP_TOPIC,
        )
    finally:
        await credential.close()

    return {
        "status": "ACTIVE",
        "destination": f"topic:{NLP_TOPIC}",
        "detail": f"Published to {NLP_TOPIC}",
    }


async def _route_dormant(
    classification: dict[str, Any],
    original_event: dict[str, Any],
    historical_match: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Create a GitHub Decision Gate issue for DORMANT events.

    Calls the existing Node.js create-decision-issue.js via subprocess
    to reuse the Octokit-based issue creation logic.
    """
    issue_input = json.dumps({
        "classification": classification,
        "webhook_payload": original_event,
        "historical_match": historical_match,
    })

    script_path = os.path.join(_SAFETY_GOVERNOR_DIR, "create-decision-issue.js")

    # Create a thin Node.js wrapper that accepts JSON from stdin
    wrapper_script = f"""
    const {{ createDecisionIssue }} = require('{script_path}');
    let data = '';
    process.stdin.on('data', chunk => data += chunk);
    process.stdin.on('end', async () => {{
      try {{
        const input = JSON.parse(data);
        const result = await createDecisionIssue(
          input.classification,
          input.historical_match,
          input.webhook_payload
        );
        process.stdout.write(JSON.stringify(result));
      }} catch (err) {{
        process.stderr.write(err.message);
        process.exit(1);
      }}
    }});
    """

    try:
        proc = subprocess.run(
            ["node", "-e", wrapper_script],
            input=issue_input,
            capture_output=True,
            text=True,
            timeout=30,
            cwd=_SAFETY_GOVERNOR_DIR,
        )

        if proc.returncode != 0:
            logger.error("Decision issue creation failed: %s", proc.stderr)
            return {
                "status": "DORMANT",
                "destination": "github-issue",
                "detail": f"Issue creation failed: {proc.stderr}",
            }

        result = json.loads(proc.stdout) if proc.stdout else {}
        logger.info(
            "Created decision issue #%s for event %s",
            result.get("issueNumber"),
            original_event["event_id"],
        )

        return {
            "status": "DORMANT",
            "destination": "github-issue",
            "detail": f"Issue #{result.get('issueNumber')} created: {result.get('issueUrl', '')}",
        }

    except subprocess.TimeoutExpired:
        logger.error("Decision issue creation timed out for %s", original_event["event_id"])
        return {
            "status": "DORMANT",
            "destination": "github-issue",
            "detail": "Issue creation timed out",
        }


async def _route_deferred(
    classification: dict[str, Any],
    original_event: dict[str, Any],
) -> dict[str, Any]:
    """Write DEFERRED event to the backlog table storage.

    Calls the existing Node.js backlog-writer.js via subprocess.
    """
    from datetime import datetime, timedelta, timezone

    defer_until = (datetime.now(timezone.utc) + timedelta(days=30)).isoformat()

    backlog_writer_path = os.path.join(
        os.path.dirname(__file__), "..", "historical-db", "backlog-writer.js"
    )

    wrapper_script = f"""
    const {{ writeDeferred }} = require('{backlog_writer_path}');
    writeDeferred(
      '{original_event["event_id"]}',
      '{original_event["cve_id"]}',
      '{defer_until}',
      'Auto-deferred by SRE Agent classification'
    ).then(() => {{
      process.stdout.write(JSON.stringify({{ success: true }}));
    }}).catch(err => {{
      process.stderr.write(err.message);
      process.exit(1);
    }});
    """

    try:
        proc = subprocess.run(
            ["node", "-e", wrapper_script],
            capture_output=True,
            text=True,
            timeout=30,
        )

        if proc.returncode != 0:
            logger.error("Backlog write failed: %s", proc.stderr)
            return {
                "status": "DEFERRED",
                "destination": "backlog",
                "detail": f"Backlog write failed: {proc.stderr}",
            }

        logger.info(
            "Wrote deferred event %s to backlog (until %s)",
            original_event["event_id"],
            defer_until,
        )

        return {
            "status": "DEFERRED",
            "destination": "backlog",
            "detail": f"Deferred until {defer_until}",
        }

    except subprocess.TimeoutExpired:
        logger.error("Backlog write timed out for %s", original_event["event_id"])
        return {
            "status": "DEFERRED",
            "destination": "backlog",
            "detail": "Backlog write timed out",
        }
