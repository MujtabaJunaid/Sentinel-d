#!/usr/bin/env bash
# Sentinel-D — Deploy 72-Hour Auto-Escalation Logic App
# Run from repo root: bash infrastructure/deploy-logic-app.sh
# Prerequisites:
#   - az login completed and correct subscription selected
#   - The following env vars set (or pass as arguments):
#       GITHUB_TOKEN              GitHub PAT with repo/issues:write scope
#       GITHUB_OWNER              Repository owner (e.g. "acme-corp")
#       GITHUB_REPO               Repository name (e.g. "demo-app")
#       APPINSIGHTS_APP_ID        Application Insights Application ID
#       APPINSIGHTS_API_KEY       Application Insights API key
#       SERVICE_BUS_NAMESPACE     Service Bus namespace name (without .servicebus.windows.net)
#       SECURITY_TEAM_LEAD_LOGIN  GitHub login of the security team lead
#
# Test procedure after deploy:
#   1. Create a test GitHub Issue with body containing event_id and label sentinel/dormant
#   2. Manually trigger a Logic App run:
#        az logic workflow trigger fire \
#          --resource-group sentinel-d-rg \
#          --workflow-name sentinel-d-auto-escalation \
#          --trigger-name "Recurrence"
#   3. Verify in Azure portal: Logic Apps → Runs history → latest run succeeded
#   4. Verify on GitHub: issue reassigned to $SECURITY_TEAM_LEAD_LOGIN
#      and comment "⚠️ 72-hour escalation" appears on the issue

set -euo pipefail

RESOURCE_GROUP="${RESOURCE_GROUP:-sentinel-d-rg}"

: "${GITHUB_TOKEN:?Must set GITHUB_TOKEN}"
: "${GITHUB_OWNER:?Must set GITHUB_OWNER}"
: "${GITHUB_REPO:?Must set GITHUB_REPO}"
: "${APPINSIGHTS_APP_ID:?Must set APPINSIGHTS_APP_ID}"
: "${APPINSIGHTS_API_KEY:?Must set APPINSIGHTS_API_KEY}"
: "${SERVICE_BUS_NAMESPACE:?Must set SERVICE_BUS_NAMESPACE}"
: "${SECURITY_TEAM_LEAD_LOGIN:?Must set SECURITY_TEAM_LEAD_LOGIN}"

echo "=== Deploying 72-hour Auto-Escalation Logic App ==="
echo "Resource group : $RESOURCE_GROUP"
echo "GitHub repo    : $GITHUB_OWNER/$GITHUB_REPO"
echo "Escalation lead: $SECURITY_TEAM_LEAD_LOGIN"
echo ""

az deployment group create \
  --resource-group "$RESOURCE_GROUP" \
  --template-file infrastructure/auto-escalation-logic-app.json \
  --parameters \
    githubToken="$GITHUB_TOKEN" \
    githubOwner="$GITHUB_OWNER" \
    githubRepo="$GITHUB_REPO" \
    appInsightsAppId="$APPINSIGHTS_APP_ID" \
    appInsightsApiKey="$APPINSIGHTS_API_KEY" \
    serviceBusNamespace="$SERVICE_BUS_NAMESPACE" \
    securityTeamLeadLogin="$SECURITY_TEAM_LEAD_LOGIN"

echo ""
echo "=== Deploy complete ==="
echo ""
echo "Verify:"
echo "  az logic workflow show --resource-group $RESOURCE_GROUP --name sentinel-d-auto-escalation"
echo ""
echo "Manual trigger test:"
echo "  az logic workflow trigger fire \\"
echo "    --resource-group $RESOURCE_GROUP \\"
echo "    --workflow-name sentinel-d-auto-escalation \\"
echo "    --trigger-name Recurrence"
