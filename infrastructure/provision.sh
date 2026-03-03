#!/usr/bin/env bash
# Sentinel-D Day 1 — Azure Resource Provisioning
# Run from repo root: bash infrastructure/provision.sh
# Prerequisites: az login completed, subscription selected
set -euo pipefail

RESOURCE_GROUP="sentinel-d-rg"
LOCATION="eastus2"
SB_NAMESPACE="sentinel-d-sb"
FUNC_APP="sentinel-d-webhook"
FUNC_STORAGE="sentineldfuncstore"
COSMOS_ACCOUNT="sentinel-d-cosmos"
COSMOS_DB="sentinel-d-db"
COSMOS_CONTAINER="remediation-history"
SEARCH_SERVICE="sentinel-d-search"
TABLE_STORAGE="sentineldtables"
APPINSIGHTS="sentinel-d-insights"
LOG_WORKSPACE="sentinel-d-logs"

echo "=== 1/7 Resource Group ==="
az group create \
  --name "$RESOURCE_GROUP" \
  --location "$LOCATION"

echo "=== 2/7 Service Bus (Standard) + Queue ==="
az servicebus namespace create \
  --name "$SB_NAMESPACE" \
  --resource-group "$RESOURCE_GROUP" \
  --location "$LOCATION" \
  --sku Standard

az servicebus queue create \
  --name "vulnerability-events" \
  --namespace-name "$SB_NAMESPACE" \
  --resource-group "$RESOURCE_GROUP" \
  --lock-duration "PT5M" \
  --max-delivery-count 10 \
  --dead-lettering-on-message-expiration true \
  --default-message-time-to-live "P14D"

echo "=== 3/7 Function App (Consumption, Node.js 20) ==="
az storage account create \
  --name "$FUNC_STORAGE" \
  --resource-group "$RESOURCE_GROUP" \
  --location "$LOCATION" \
  --sku Standard_LRS

az functionapp create \
  --name "$FUNC_APP" \
  --resource-group "$RESOURCE_GROUP" \
  --storage-account "$FUNC_STORAGE" \
  --consumption-plan-location "$LOCATION" \
  --runtime node \
  --runtime-version 20 \
  --functions-version 4 \
  --os-type Linux

echo "=== 4/7 Cosmos DB (Core API, Serverless) ==="
az cosmosdb create \
  --name "$COSMOS_ACCOUNT" \
  --resource-group "$RESOURCE_GROUP" \
  --locations regionName="$LOCATION" \
  --capabilities EnableServerless \
  --kind GlobalDocumentDB

az cosmosdb sql database create \
  --account-name "$COSMOS_ACCOUNT" \
  --resource-group "$RESOURCE_GROUP" \
  --name "$COSMOS_DB"

az cosmosdb sql container create \
  --account-name "$COSMOS_ACCOUNT" \
  --resource-group "$RESOURCE_GROUP" \
  --database-name "$COSMOS_DB" \
  --name "$COSMOS_CONTAINER" \
  --partition-key-path "/cve_id"

echo "=== 5/7 Azure AI Search (Basic) ==="
az search service create \
  --name "$SEARCH_SERVICE" \
  --resource-group "$RESOURCE_GROUP" \
  --location "$LOCATION" \
  --sku basic

echo "=== 6/7 Table Storage ==="
az storage account create \
  --name "$TABLE_STORAGE" \
  --resource-group "$RESOURCE_GROUP" \
  --location "$LOCATION" \
  --sku Standard_LRS

TABLE_CONN=$(az storage account show-connection-string \
  --name "$TABLE_STORAGE" \
  --resource-group "$RESOURCE_GROUP" \
  --query connectionString -o tsv)

az storage table create --name "deferredbacklog" --connection-string "$TABLE_CONN"
az storage table create --name "auditlog" --connection-string "$TABLE_CONN"

echo "=== 7/7 Application Insights ==="
az monitor log-analytics workspace create \
  --workspace-name "$LOG_WORKSPACE" \
  --resource-group "$RESOURCE_GROUP" \
  --location "$LOCATION"

LOG_WORKSPACE_ID=$(az monitor log-analytics workspace show \
  --workspace-name "$LOG_WORKSPACE" \
  --resource-group "$RESOURCE_GROUP" \
  --query id -o tsv)

az monitor app-insights component create \
  --app "$APPINSIGHTS" \
  --resource-group "$RESOURCE_GROUP" \
  --location "$LOCATION" \
  --workspace "$LOG_WORKSPACE_ID"

echo ""
echo "=== Configure Function App Settings ==="
FUNC_ENDPOINT=$(az functionapp show \
  --name "$FUNC_APP" \
  --resource-group "$RESOURCE_GROUP" \
  --query defaultHostName -o tsv)

az functionapp config appsettings set \
  --name "$FUNC_APP" \
  --resource-group "$RESOURCE_GROUP" \
  --settings \
    SERVICE_BUS_NAMESPACE="$SB_NAMESPACE" \
    SERVICE_BUS_QUEUE_NAME="vulnerability-events"

echo ""
echo "=== Provisioning Complete ==="
echo "Function endpoint: https://$FUNC_ENDPOINT"
echo "Verify: az resource list -g $RESOURCE_GROUP -o table"
