import {sendDeltaToStream, sendStateEventToStream, sendErrorMessage} from "../common/streams.js";
import {getLogger} from "../common/logging.js";
import { doesNotSupportImagesInstructions, additionalImageInstruction, getImageBase64Content } from "../datasource/datasources.js";
import { BedrockRuntimeClient, ConverseStreamCommand } from "@aws-sdk/client-bedrock-runtime";
import {trace} from "../common/trace.js";
import {extractKey} from "../datasource/datasources.js";


const logger = getLogger("bedrock");
const BLANK_MSG = "Intentionally Left Blank, please ignore";

// Model mapping: redirect legacy/invalid model IDs to verified working models
// All Claude 3.x models are LEGACY on Bedrock — redirect to Claude 4 family
const MODEL_V2_TO_V1_MAPPING = {
    // Legacy Claude 3.x -> Claude 4 Sonnet (verified working)
    "anthropic.claude-3-sonnet-20240229-v1:0": "us.anthropic.claude-sonnet-4-5-20250929-v1:0",
    "anthropic.claude-3-sonnet-20240229-v2:0": "us.anthropic.claude-sonnet-4-5-20250929-v1:0",
    "anthropic.claude-3-5-sonnet-20240620-v1:0": "us.anthropic.claude-sonnet-4-5-20250929-v1:0",
    "anthropic.claude-3-5-sonnet-20240620-v2:0": "us.anthropic.claude-sonnet-4-5-20250929-v1:0",
    "anthropic.claude-3-5-sonnet-20241022-v2:0": "us.anthropic.claude-sonnet-4-5-20250929-v1:0",
    "us.anthropic.claude-3-5-sonnet-20241022-v2:0": "us.anthropic.claude-sonnet-4-5-20250929-v1:0",
    "anthropic.claude-3-7-sonnet-20250219-v1:0": "us.anthropic.claude-sonnet-4-5-20250929-v1:0",
    "anthropic.claude-3-7-sonnet-20250219-v2:0": "us.anthropic.claude-sonnet-4-5-20250929-v1:0",
    "us.anthropic.claude-3-7-sonnet-20250219-v1:0": "us.anthropic.claude-sonnet-4-5-20250929-v1:0",
    // Legacy Haiku -> Haiku 4.5 (verified working)
    "anthropic.claude-3-haiku-20240307-v1:0": "us.anthropic.claude-haiku-4-5-20251001-v1:0",
    "anthropic.claude-3-haiku-20240307-v2:0": "us.anthropic.claude-haiku-4-5-20251001-v1:0",
    "anthropic.claude-3-5-haiku-20241022-v1:0": "us.anthropic.claude-haiku-4-5-20251001-v1:0",
    // Legacy Opus -> Opus 4.5 (verified working)
    "anthropic.claude-3-opus-20240229-v1:0": "us.anthropic.claude-opus-4-5-20251101-v1:0",
    "us.anthropic.claude-3-opus-20240229-v1:0": "us.anthropic.claude-opus-4-5-20251101-v1:0",
    // Claude 4 base IDs -> us. prefixed (verified working)
    "anthropic.claude-sonnet-4-20250514-v1:0": "us.anthropic.claude-sonnet-4-5-20250929-v1:0",
    "anthropic.claude-sonnet-4-20250514-v2:0": "us.anthropic.claude-sonnet-4-5-20250929-v1:0",
    "anthropic.claude-sonnet-4-5-20250929-v1:0": "us.anthropic.claude-sonnet-4-5-20250929-v1:0",
    "anthropic.claude-sonnet-4-5-20250929-v2:0": "us.anthropic.claude-sonnet-4-5-20250929-v1:0",
};

export const chatBedrock = async (chatBody, writable) => {

    let body = {...chatBody};
    const options = {...body.options}; 
    delete body.options; 
    const currentModel = options.model;
    
    // Map v2 models to v1 if needed
    const originalModelId = currentModel.id;
    if (MODEL_V2_TO_V1_MAPPING[currentModel.id]) {
        currentModel.id = MODEL_V2_TO_V1_MAPPING[currentModel.id];
        logger.info(`Mapped model from ${originalModelId} to ${currentModel.id}`);
    }

    const systemPrompts = [{"text": options.prompt.trim() || BLANK_MSG}];
    if (currentModel.systemPrompt.trim()) {
        systemPrompts.push({ "text": currentModel.systemPrompt });
    }

    const withoutSystemMessages = [];
    // options.prompt is a match for the first message in messages 
    for (const msg of body.messages) {
        if (msg.role === "system") {
                                      // avoid duplicate system prompts
            if (msg.content.trim() && msg.content !== options.prompt) systemPrompts.push({ "text": msg.content });
        } else {
            withoutSystemMessages.push(msg);
        }
    }
    const imageSources =  !options.dataSourceOptions?.disableDataSources ? body.imageSources : [];
    const combinedMessages = combineMessages(withoutSystemMessages, options.prompt);
    const sanitizedMessages = await sanitizeMessages(combinedMessages, imageSources, currentModel, writable);
    
    try {
        
        const region = process.env.DEP_REGION ?? 'us-east-1';
        const client = new BedrockRuntimeClient({ region: region}); 
        
        logger.debug("Initiating call to Bedrock");

        const maxModelTokens = options.model.outputTokenLimit;

        const maxTokens = body.max_tokens || 1000;
        const inferenceConfigs = {"temperature": options.temperature, 
                                  "maxTokens": maxTokens > maxModelTokens ? maxModelTokens : maxTokens, };
        
        const input = { modelId: currentModel.id,
                        messages: sanitizedMessages,
                        inferenceConfig: inferenceConfigs,
                        }

        if (currentModel.supportsSystemPrompts) {
            input.system = systemPrompts;
        } else {
            // Gather all text values from the system prompts list
            const systemPromptsText = systemPrompts.map(sp => sp.text).join("\n\n");
            const sanitizedMessagesCopy = [...sanitizedMessages];

            sanitizedMessagesCopy[sanitizedMessagesCopy.length -1].content[0].text +=
            `Recall your custom instructions are: ${systemPromptsText}`;

            input.messages = sanitizedMessagesCopy;
        }

        trace(options.requestId, ["Bedrock"], {modelId : currentModel.id, originalModelId: originalModelId, data: input})

        const response = await client.send( new ConverseStreamCommand(input) );
        const { messageStream } = response.stream.options;
        const decoder = new TextDecoder();
        for await (const chunk of messageStream) {
            const jsonString = decoder.decode(chunk.body);
            // Parse the JSON string to an object
            const message = JSON.parse(jsonString);
            sendDeltaToStream(writable, "answer", message);
        }
        writable.end();

        writable.on('finish', () => {
            logger.debug('All data has been written to writable stream');
        });

        writable.on('error', (error) => {
            logger.error('Error with Writable stream: ', error);
            const statusCode = error.code || error.statusCode;
            const statusText = error.message || error.name || 'Stream Error';
            sendErrorMessage(writable, statusCode, statusText);
            reject(error);
        });
         
    } catch (error) {
        if (error.message || error.$response?.message) console.log("Error invoking Bedrock API:", error.message || error.$response?.message);
        logger.error(`Error invoking Bedrock chat for model ${currentModel.id}: `, error);
        sendErrorMessage(writable, error.$metadata?.httpStatusCode, error.$response?.reason);
    }
}


function combineMessages(oldMessages, failSafeUserMessage) {
    if (!oldMessages || oldMessages.length === 0) return oldMessages;
    let messages = [];
    const delimiter = "\n_________________________\n";
    
    const newestMessage = oldMessages[oldMessages.length - 1]
    if (newestMessage['role'] === 'user') oldMessages[oldMessages.length - 1]['content'] =
        `${delimiter}${newestMessage['content']}`
    
    let i = -1;
    let j = 0;
    while (j < oldMessages.length) {
        const curMessageRole = oldMessages[j]['role'];
        const oldContent = oldMessages[j]['content'];
        if (!oldContent) oldMessages[j]['content'] = "NA Intentionally left empty, disregard and continue";
        if (messages.length == 0 || (messages[i]['role'] !== curMessageRole)) {
            oldMessages[j]['content'] = oldMessages[j]['content'].trimEnd() // remove white space, final messages cause error if not
            messages.push(oldMessages[j]);
            i += 1;
        } else if (messages[i]['role'] === oldMessages[j]['role']) {
            messages[i]['content'] += delimiter + oldContent;
        } 
        j += 1;
    }

    if (messages.length === 0 || (messages[0]['role'] !== 'user')) {
        messages = [{'role': 'user', 'content': failSafeUserMessage}, ...messages];
    } 

    return messages;
}


async function sanitizeMessages(messages, imageSources, model, responseStream) {
    if (!messages) return messages;

    const containsImages = imageSources && imageSources.length > 0;

    if (!model.supportsImages && containsImages) {
        messages.slice(-1)[0].content += doesNotSupportImagesInstructions(model.name)
    }

    let updatedMessages = [
        ...(messages.map(m => {
            return { "role": m['role'],
                     "content": [
                        { "text":  m['content'].trim() || BLANK_MSG }
                    ]}
            }))
    ];

    if (model.supportsImages && containsImages) {
        updatedMessages = await includeImageSources(imageSources, updatedMessages, responseStream); 
    }
    return updatedMessages;

}

async function includeImageSources(dataSources, messages, responseStream) {
    if (!dataSources || dataSources.length === 0) return messages;

    const retrievedImages = [];

    let imageMessageContent = [[]];
    let listIdx = 0;
    for (let i = 0; i < dataSources.length; i++) {
        const ds = dataSources[i];
        const encoded_image = await getImageBase64Content(ds);
        if (encoded_image) {
            retrievedImages.push({...ds, contentKey: extractKey(ds.id)});
            // only 20 per content allowed
            if (imageMessageContent[listIdx].length > 19) {
                listIdx++;
                imageMessageContent.push([])
            }
            imageMessageContent[listIdx].push({
                "image": {
                    "format": ds.type.split('/')[1], 
                    "source": {
                        "bytes": Uint8Array.from(atob(encoded_image), char => char.charCodeAt(0))
                    }
                }
            })
        } else {
            logger.info("Failed to get base64 encoded image: ", ds);
        }
    }

    if (retrievedImages.length > 0) {
        sendStateEventToStream(responseStream, {
            sources: { images: { sources: retrievedImages} }
          });
    }

    const msgLen = messages.length - 1;
    let content = messages[msgLen]['content'];
    content.push({ "text": additionalImageInstruction});
    content = [...content, ...imageMessageContent[0]];
    messages[msgLen]['content'] = content;
    if (listIdx > 0) {
        imageMessageContent.slice(1).forEach(contents => {
            messages.push({
                "role": "user",
                "content": contents
            })
        })
    }
    return messages;
}
    




   
