# Amplify Backend Services Deployment Order

This document outlines the correct deployment order for all Amplify backend services based on their dependencies.

## Critical Deployment Issue Fixed

**Previous Error**: `No export named prod-RestApiRootResourceId found`

**Root Cause**: The `amplify-lambda-js` service was trying to import API Gateway resources that hadn't been created yet. The `amplify-lambda` service (which creates these resources) must be deployed first.

## Deployment Phases

### Phase 1: Independent Services
These services have no dependencies and can be deployed first:
- **llm-router** - Multi-provider LLM abstraction service

### Phase 2: Primary Infrastructure
This service creates the core infrastructure used by all other services:
- **amplify-lambda** - Creates:
  - API Gateway (exports `${stage}-RestApiId` and `${stage}-RestApiRootResourceId`)
  - 11 S3 buckets for various purposes
  - Core DynamoDB tables (chat-usage, accounts, cost-calculations, etc.)
  - DynamoDB stream for chat-usage table

### Phase 3: Secondary Infrastructure Services
These services create additional shared resources:
- **amplify-object-access** - Creates:
  - api-keys DynamoDB table
  - amplify-groups DynamoDB table
- **chat-billing** - Creates:
  - model-rates DynamoDB table
  - additional-charges table
  - history-usage table
- **amplify-lambda-admin** - Creates:
  - admin-configs DynamoDB table
  - admin-logs table

### Phase 4: Dependent Services
These services depend on resources created in earlier phases:
- **amplify-assistants** - Depends on:
  - API Gateway (from amplify-lambda)
  - api-keys and groups tables (from amplify-object-access)
  - billing table (from chat-billing)
- **amplify-lambda-js** - Depends on:
  - API Gateway exports (from amplify-lambda)
  - amplify-assistants service
- **amplify-lambda-api** - Depends on:
  - API Gateway (from amplify-lambda)
  - api-keys table (from amplify-object-access)
- **amplify-lambda-artifacts** - Depends on:
  - API Gateway (from amplify-lambda)
  - api-keys table (from amplify-object-access)
- **amplify-lambda-ops** - Depends on:
  - API Gateway (from amplify-lambda)
  - api-keys table (from amplify-object-access)
- **amplify-lambda-optimizer** - Depends on:
  - API Gateway (from amplify-lambda)
  - api-keys table (from amplify-object-access)
- **data-disclosure** - Depends on:
  - API Gateway (from amplify-lambda)
  - api-keys table (from amplify-object-access)
  - admin-configs table (from amplify-lambda-admin)
- **amplify-embedding** - Depends on:
  - API Gateway and S3 buckets (from amplify-lambda)
  - api-keys and groups tables (from amplify-object-access)
  - model-rates table (from chat-billing)
  - admin-configs table (from amplify-lambda-admin)

## Deployment Commands

### Option 1: Using Serverless Compose (Recommended)
```bash
# Deploy all services with correct dependency order
cd /Users/mgreen2/code/amplify/amplify-genai-backend
serverless deploy --stage prod
```

### Option 2: Manual Deployment
```bash
# Phase 1
cd llm-router && serverless deploy --stage prod && cd ..

# Phase 2
cd amplify-lambda && serverless deploy --stage prod && cd ..

# Phase 3 (can run in parallel)
cd amplify-object-access && serverless deploy --stage prod && cd ..
cd chat-billing && serverless deploy --stage prod && cd ..
cd amplify-lambda-admin && serverless deploy --stage prod && cd ..

# Phase 4 (after Phase 3 completes)
cd amplify-assistants && serverless deploy --stage prod && cd ..
cd amplify-lambda-js && serverless deploy --stage prod && cd ..
# ... deploy remaining services
```

## Validation

After deploying `amplify-lambda`, verify the exports exist:
```bash
# Check API Gateway exports
aws cloudformation list-exports --query "Exports[?Name=='prod-RestApiId']"
aws cloudformation list-exports --query "Exports[?Name=='prod-RestApiRootResourceId']"
```

## Common Issues

1. **Stage Mismatch**: Ensure all services are deployed with the same stage (dev/prod)
2. **Missing Exports**: If exports are missing, redeploy the amplify-lambda service
3. **Hard-coded API Gateway IDs**: Some services have hard-coded API Gateway IDs that may need updating

## Notes

- The `serverless-compose.yml` has been updated with correct dependencies
- All services default to 'dev' stage if not specified
- The cost-calculations DynamoDB table referenced by several services needs investigation (may be missing)