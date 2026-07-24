import { DynamoDBClient, GetItemCommand } from "@aws-sdk/client-dynamodb";
import { DynamoDBDocumentClient, QueryCommand, ScanCommand } from "@aws-sdk/lib-dynamodb";
import { extractParams } from "../common/handlers.js";
import { getLogger } from "../common/logging.js";

const logger = getLogger("mtd");
const client = new DynamoDBClient({});
const dynamoDB = DynamoDBDocumentClient.from(client);

const costDynamoTableName = process.env.COST_CALCULATIONS_DYNAMO_TABLE;
const historyCostDynamoTableName = process.env.HISTORY_COST_CALCULATIONS_DYNAMO_TABLE;

// REMOVED 2026-07-10 (security): the `handler` (POST /billing/mtd-cost) and
// `apiKeyUserCostHandler` (POST /billing/api-key-user-cost) endpoints were an
// authenticated IDOR -- both returned cost data keyed by an attacker-supplied
// body.data.email with no check that it matched the caller (params.user), so any
// logged-in user could read any user's spend. Neither had a live caller (the
// frontend doMtdCostOp was dead code; nothing called api-key-user-cost). Removed
// rather than retrofitted. The safe pattern remains below in
// listAllUserMtdCostsHandler (params.user + admin gate).
//
// The UI's own per-user cost call (op /list-user-mtd-costs) is now served by
// listUserMtdCostsHandler at the bottom of this file. It follows the same safe
// pattern: the identity it reports on comes ONLY from params.user (the verified
// token), never from the request body, and it takes no email/user input at all.

// Number of Query pages we will follow for a single user's cost rows. One row
// exists per accountInfo (`<coa>#<apiKeyOr'NA'>`); a page holds ~1MB of them, so
// this is a runaway guard, not a real limit.
const MAX_USER_COST_QUERY_PAGES = 10;

const toNumber = (value) => {
    const parsed = parseFloat(value);
    return Number.isFinite(parsed) ? parsed : 0;
};

// hourlyCost is written by common/accounting.js as a 24-element list indexed by
// UTC hour, and re-zeroed nightly by billing/reset.js. Older rows may predate
// the field or hold a short/garbage list, so always normalize to exactly 24
// numbers -- the frontend indexes hourlyCost[utcHour] directly.
const normalizeHourlyCost = (raw) => {
    const hourly = new Array(24).fill(0);
    if (Array.isArray(raw)) {
        for (let hour = 0; hour < 24; hour++) {
            hourly[hour] = toNumber(raw[hour]);
        }
    }
    return hourly;
};

export const listAllUserMtdCostsHandler = async (event, context, callback) => {
    const startTime = Date.now();
    logger.info("=== LIST ALL USER MTD COSTS REQUEST STARTED ===");
    
    try {
        logger.info("Extracting params from event");
        const params = await extractParams(event);

        if (params.statusCode) {
            logger.error("Failed to extract params", { statusCode: params.statusCode });
            return params; // This is an error response from extractParams
        }

        const { body, user } = params;
        logger.info("Request initiated by user", { user, requestBody: body });

        // Check if user is in Admin group by querying ADMIN_DYNAMODB_TABLE
        logger.info("Starting admin privilege verification");
        const adminTableName = process.env.ADMIN_DYNAMODB_TABLE;
        if (!adminTableName) {
            logger.error("ADMIN_DYNAMODB_TABLE environment variable is not set");
            return {
                statusCode: 500,
                body: JSON.stringify({ error: 'Server configuration error' }),
            };
        }

        try {
            const adminParams = {
                TableName: adminTableName,
                Key: {
                    config_id: { S: 'admins' }
                }
            };

            logger.info("Querying admin table", { tableName: adminTableName });
            const adminCommand = new GetItemCommand(adminParams);
            const adminResult = await client.send(adminCommand);

            if (!adminResult.Item) {
                logger.error("No admin configuration found in admin table");
                return {
                    statusCode: 500,
                    body: JSON.stringify({ error: 'Admin configuration not found' }),
                };
            }

            const adminConfig = adminResult.Item;
            const adminEmails = adminConfig.data?.L || [];
            logger.info("Retrieved admin configuration", { adminCount: adminEmails.length });
            
            // Check if current user's email is in the admin list
            const isAdmin = adminEmails.some(emailObj => 
                emailObj.S && emailObj.S.toLowerCase() === user.toLowerCase()
            );

            if (!isAdmin) {
                logger.warn("Unauthorized access attempt to admin endpoint", { user, isAdmin });
                return {
                    statusCode: 403,
                    body: JSON.stringify({ error: 'Access denied. Admin privileges required.' }),
                };
            }
            
            logger.info("Admin privileges verified successfully", { user });
        } catch (error) {
            logger.error("Error verifying admin privileges", { error: error.message, user });
            return {
                statusCode: 500,
                body: JSON.stringify({ error: 'Authorization check failed' }),
            };
        }

        // Extract pagination parameters
        const pageSize = body?.data?.pageSize || 50;
        const lastEvaluatedKey = body?.data?.lastEvaluatedKey || null;
        
        logger.info("Pagination parameters", { pageSize, hasLastEvaluatedKey: !!lastEvaluatedKey });

        if (pageSize > 100) {
            logger.warn("Page size too large", { pageSize });
            return {
                statusCode: 400,
                body: JSON.stringify({ error: 'Page size cannot exceed 100' }),
            };
        }

        // Try GSI first, if no results check if we need to backfill
        logger.info("Starting cost data retrieval from GSI");
        let result;
        let needsBackfill = false;
        const gsiStartTime = Date.now();

        try {
            // Query the GSI to get all cost records efficiently
            const queryParams = {
                TableName: costDynamoTableName,
                IndexName: 'record-type-user-index',
                KeyConditionExpression: 'record_type = :type',
                ExpressionAttributeValues: {
                    ':type': 'cost'
                },
                Limit: pageSize * 10, // Get more records to ensure we have enough users after aggregation
            };

            if (lastEvaluatedKey) {
                queryParams.ExclusiveStartKey = lastEvaluatedKey;
            }

            logger.info("Querying GSI", { 
                tableName: costDynamoTableName, 
                indexName: 'record-type-user-index',
                limit: queryParams.Limit,
                hasPaginationKey: !!lastEvaluatedKey
            });
            
            const queryCommand = new QueryCommand(queryParams);
            result = await dynamoDB.send(queryCommand);
            
            const gsiDuration = Date.now() - gsiStartTime;
            logger.info("GSI query completed", { 
                itemsFound: result.Items?.length || 0,
                duration: gsiDuration,
                hasNextPage: !!result.LastEvaluatedKey
            });
            
            // If no items found in GSI, check if there are records without record_type
            if (!result.Items || result.Items.length === 0) {
                logger.info("No records found in GSI, checking for legacy records without record_type");
                
                // Quick scan to see if there are any records at all
                const checkScanParams = {
                    TableName: costDynamoTableName,
                    Limit: 1 // Just check if any records exist
                };

                const checkScanCommand = new ScanCommand(checkScanParams);
                const checkResult = await dynamoDB.send(checkScanCommand);
                
                if (checkResult.Items && checkResult.Items.length > 0) {
                    needsBackfill = true;
                    logger.warn("Found legacy records without record_type, backfill needed", {
                        legacyRecordsFound: checkResult.Items.length
                    });
                } else {
                    logger.info("No cost records found in database at all");
                }
            }
        } catch (error) {
            logger.error("Error querying GSI", { 
                error: error.message, 
                tableName: costDynamoTableName,
                indexName: 'record-type-user-index'
            });
            needsBackfill = true;
        }

        // Auto-trigger backfill if needed (admin already verified)
        if (needsBackfill) {
            logger.info("Triggering automatic backfill for record_type field");
            const backfillStartTime = Date.now();
            
            try {
                // Import and run backfill function directly
                const { handler: backfillHandler } = await import('./backfill.js');
                const backfillResult = await backfillHandler({}, {});
                
                const backfillDuration = Date.now() - backfillStartTime;
                logger.info("Backfill completed successfully", { 
                    duration: backfillDuration,
                    result: backfillResult 
                });
                
                // Now retry the GSI query
                logger.info("Retrying GSI query after backfill");
                const retryQueryParams = {
                    TableName: costDynamoTableName,
                    IndexName: 'record-type-user-index',
                    KeyConditionExpression: 'record_type = :type',
                    ExpressionAttributeValues: {
                        ':type': 'cost'
                    },
                    Limit: pageSize * 10,
                };

                if (lastEvaluatedKey) {
                    retryQueryParams.ExclusiveStartKey = lastEvaluatedKey;
                }

                const retryQueryCommand = new QueryCommand(retryQueryParams);
                result = await dynamoDB.send(retryQueryCommand);
                
                logger.info("Post-backfill GSI query completed", { 
                    itemsFound: result.Items?.length || 0 
                });
                
            } catch (backfillError) {
                logger.error("Auto-backfill failed, falling back to table scan", { 
                    error: backfillError.message,
                    backfillDuration: Date.now() - backfillStartTime
                });
                
                // Fallback to scan if backfill fails
                const fallbackScanParams = {
                    TableName: costDynamoTableName,
                    Limit: pageSize * 10,
                };

                if (lastEvaluatedKey) {
                    fallbackScanParams.ExclusiveStartKey = lastEvaluatedKey;
                }

                logger.info("Executing fallback table scan");
                const fallbackScanCommand = new ScanCommand(fallbackScanParams);
                result = await dynamoDB.send(fallbackScanCommand);
                
                logger.info("Fallback scan completed", { 
                    itemsFound: result.Items?.length || 0 
                });
            }
        }

        // Aggregate costs by user
        logger.info("Starting cost aggregation by user");
        const aggregationStartTime = Date.now();
        const userCosts = {};
        let totalRecordsProcessed = 0;
        
        result.Items.forEach(item => {
            const email = item.id;
            const accountInfo = item.accountInfo || 'Unknown Account';
            const dailyCost = parseFloat(item.dailyCost) || 0;
            const monthlyCost = parseFloat(item.monthlyCost) || 0;
            
            if (!userCosts[email]) {
                userCosts[email] = {
                    email: email,
                    dailyCost: 0,
                    monthlyCost: 0,
                    totalCost: 0,
                    accounts: []
                };
            }
            
            userCosts[email].dailyCost += dailyCost;
            userCosts[email].monthlyCost += monthlyCost;
            
            // Add account information
            userCosts[email].accounts.push({
                accountInfo: accountInfo,
                dailyCost: dailyCost,
                monthlyCost: monthlyCost,
                totalCost: dailyCost + monthlyCost
            });
            
            totalRecordsProcessed++;
        });

        // Calculate total costs for each user
        Object.keys(userCosts).forEach(email => {
            userCosts[email].totalCost = userCosts[email].dailyCost + userCosts[email].monthlyCost;
        });

        // Convert to array and sort by total cost descending
        const userCostArray = Object.values(userCosts).sort((a, b) => b.totalCost - a.totalCost);
        
        const aggregationDuration = Date.now() - aggregationStartTime;
        logger.info("Cost aggregation completed", { 
            recordsProcessed: totalRecordsProcessed,
            uniqueUsers: userCostArray.length,
            aggregationDuration,
            topUserCost: userCostArray[0]?.totalCost || 0
        });

        const totalDuration = Date.now() - startTime;
        
        const response = {
            statusCode: 200,
            body: JSON.stringify({
                users: userCostArray,
                count: userCostArray.length,
                lastEvaluatedKey: result.LastEvaluatedKey || null,
                hasMore: !!result.LastEvaluatedKey
            }),
        };
        
        logger.info("=== LIST ALL USER MTD COSTS REQUEST COMPLETED ===", {
            totalDuration,
            usersReturned: userCostArray.length,
            recordsProcessed: totalRecordsProcessed,
            hasMoreData: !!result.LastEvaluatedKey,
            requestedBy: user
        });
        
        return response;
    } catch (error) {
        const totalDuration = Date.now() - startTime;
        logger.error("=== LIST ALL USER MTD COSTS REQUEST FAILED ===", {
            error: error.message,
            stack: error.stack,
            totalDuration,
            requestedBy: user || 'unknown'
        });
        return {
            statusCode: 500,
            body: JSON.stringify({ error: 'Internal server error' }),
        };
    }
};

/**
 * POST /billing/list-user-mtd-costs
 *
 * Returns the CALLER'S OWN month-to-date cost breakdown, in the shape the
 * frontend's UserMtdCosts interface expects (services/mtdCostService.ts).
 *
 * SECURITY: the user whose costs are returned is derived ONLY from
 * params.user, which extractParams sets from the verified Cognito access
 * token (or the API-key record). The request body is never read -- the
 * frontend sends `data: {}` and this handler must keep it that way. Two
 * endpoints in this file were removed in 2026-07 for taking the target email
 * from body.data.email (authenticated IDOR); do not reintroduce that pattern.
 * There is deliberately no way to ask this endpoint about another user --
 * cross-user reads live in listAllUserMtdCostsHandler behind the admin gate.
 *
 * No-data is a success case: a user who has never run a chat has no rows in
 * the cost table, and the UI treats a response without `.email` as a failure,
 * so we return zeros with the caller's email rather than a 404.
 */
/**
 * Mask the API-key segment of an accountInfo sort key before returning it.
 *
 * common/accounting.js writes accountInfo as `${coa}#${accessToken}` where
 * accessToken is the caller's RAW `amp-...` API key. Echoing that back in an
 * API response leaks a live credential into the browser, logs, and any cached
 * payload. We keep the COA (which the UI uses to attribute cost per account)
 * and replace the key with a stable, non-secret suffix hint.
 */
const redactAccountInfo = (raw) => {
    if (typeof raw !== 'string' || raw.length === 0) return 'Unknown Account';
    const hash = raw.indexOf('#');
    if (hash === -1) return raw;                       // no key segment present
    const coa = raw.slice(0, hash);
    const key = raw.slice(hash + 1);
    if (!key || key === 'NA') return `${coa}#NA`;      // no API key on this row
    // Keep only a short non-reversible tail so the UI can distinguish keys.
    const tail = key.slice(-4);
    return `${coa}#amp-***${tail}`;
};

export const listUserMtdCostsHandler = async (event, context, callback) => {
    const startTime = Date.now();
    let requestUser = 'unknown'; // kept in outer scope so the catch can log it
    logger.info("=== LIST USER MTD COSTS REQUEST STARTED ===");

    try {
        const params = await extractParams(event);

        if (params.statusCode) {
            logger.error("Failed to extract params", { statusCode: params.statusCode });
            return params; // This is an error response from extractParams
        }

        // Identity comes from the token ONLY. body is intentionally ignored.
        const user = params.user;

        if (!user || typeof user !== 'string') {
            logger.error("No usable user identity on verified request");
            return {
                statusCode: 401,
                body: JSON.stringify({ error: 'Unauthorized' }),
            };
        }
        requestUser = user;

        if (!costDynamoTableName) {
            logger.error("COST_CALCULATIONS_DYNAMO_TABLE environment variable is not set");
            return {
                statusCode: 500,
                body: JSON.stringify({ error: 'Server configuration error' }),
            };
        }

        // The cost table is keyed id(HASH) + accountInfo(RANGE) and accounting.js
        // writes id = the same token-derived user string, so a partition Query on
        // id returns exactly this caller's rows -- one per account/API key. No
        // scan, and no way to reach another partition.
        const items = [];
        let lastEvaluatedKey = null;
        let pagesFetched = 0;

        do {
            const queryParams = {
                TableName: costDynamoTableName,
                KeyConditionExpression: '#id = :user',
                ExpressionAttributeNames: { '#id': 'id' },
                ExpressionAttributeValues: { ':user': user },
            };

            if (lastEvaluatedKey) {
                queryParams.ExclusiveStartKey = lastEvaluatedKey;
            }

            const queryResult = await dynamoDB.send(new QueryCommand(queryParams));
            if (queryResult.Items && queryResult.Items.length > 0) {
                items.push(...queryResult.Items);
            }

            lastEvaluatedKey = queryResult.LastEvaluatedKey || null;
            pagesFetched++;
        } while (lastEvaluatedKey && pagesFetched < MAX_USER_COST_QUERY_PAGES);

        if (lastEvaluatedKey) {
            logger.warn("Stopped paginating user cost rows at page cap", {
                pagesFetched,
                rowsRead: items.length
            });
        }

        logger.info("User cost rows retrieved", {
            rowsRead: items.length,
            pagesFetched,
            duration: Date.now() - startTime
        });

        // Aggregate the per-account rows into the totals the UI renders.
        let dailyCost = 0;
        let monthlyCost = 0;
        const hourlyCost = new Array(24).fill(0);
        const accounts = [];
        let lastUpdated = null;

        items.forEach(item => {
            // Legacy rows predate record_type; anything explicitly tagged as
            // something other than a cost row is not spend and is skipped.
            if (item.record_type !== undefined && item.record_type !== 'cost') return;

            const rowDailyCost = toNumber(item.dailyCost);
            const rowMonthlyCost = toNumber(item.monthlyCost);
            const rowHourlyCost = normalizeHourlyCost(item.hourlyCost);

            dailyCost += rowDailyCost;
            monthlyCost += rowMonthlyCost;
            for (let hour = 0; hour < 24; hour++) {
                hourlyCost[hour] += rowHourlyCost[hour];
            }

            // The cost table has no timestamp today (only the history table
            // writes one), so this is normally null -- the UI already treats
            // lastUpdated/timestamp as nullable.
            const rowTimestamp = typeof item.timestamp === 'string' ? item.timestamp : null;
            if (rowTimestamp && (!lastUpdated || rowTimestamp > lastUpdated)) {
                lastUpdated = rowTimestamp;
            }

            accounts.push({
                // SECURITY: accounting.js builds this sort key as
                // `${coa}#${accessToken}` where accessToken is the RAW `amp-...`
                // API key. Returning it verbatim would echo the caller's live
                // API key secrets back in every cost response (and into any log
                // or browser cache that captures the payload). Mask the key
                // segment; the COA is preserved so the UI can still attribute
                // cost per account.
                accountInfo: redactAccountInfo(item.accountInfo),
                dailyCost: rowDailyCost,
                monthlyCost: rowMonthlyCost,
                totalCost: rowDailyCost + rowMonthlyCost,
                timestamp: rowTimestamp
            });
        });

        accounts.sort((a, b) => b.totalCost - a.totalCost);

        // monthlyCost holds the days already rolled up by billing/reset.js, so
        // month-to-date is prior days + today. Matches listAllUserMtdCostsHandler.
        const totalCost = dailyCost + monthlyCost;

        const response = {
            statusCode: 200,
            body: JSON.stringify({
                email: user,
                dailyCost,
                monthlyCost,
                totalCost,
                hourlyCost,
                accounts,
                lastUpdated,
                timestamp: new Date().toISOString()
            }),
        };

        logger.info("=== LIST USER MTD COSTS REQUEST COMPLETED ===", {
            totalDuration: Date.now() - startTime,
            accountsReturned: accounts.length,
            hasData: accounts.length > 0,
            requestedBy: user
        });

        return response;
    } catch (error) {
        logger.error("=== LIST USER MTD COSTS REQUEST FAILED ===", {
            error: error.message,
            stack: error.stack,
            totalDuration: Date.now() - startTime,
            requestedBy: requestUser
        });
        return {
            statusCode: 500,
            body: JSON.stringify({ error: 'Internal server error' }),
        };
    }
};