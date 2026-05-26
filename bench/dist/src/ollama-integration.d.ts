/**
 * Ollama integration - Local model inference
 * Provides interface to call local models via Ollama API
 */
export interface OllamaConfig {
    baseUrl?: string;
    model: string;
    temperature?: number;
    topK?: number;
    topP?: number;
    repeat_penalty?: number;
}
export interface OllamaResponse {
    model: string;
    response: string;
    tokens_used: number;
    done: boolean;
}
export declare class OllamaClient {
    private client;
    private model;
    private temperature;
    constructor(config: OllamaConfig);
    /**
     * Call Ollama with a prompt and return response
     */
    call(prompt: string, systemPrompt?: string, options?: {
        temperature?: number;
        topK?: number;
        topP?: number;
    }): Promise<OllamaResponse>;
    /**
     * Check if Ollama is running and model is available
     */
    isAvailable(): Promise<boolean>;
    /**
     * Pull/download a model
     */
    pullModel(): Promise<void>;
    /**
     * Stream responses for long outputs
     */
    stream(prompt: string, systemPrompt?: string): AsyncGenerator<string, void, unknown>;
}
/**
 * List available local models
 */
export declare function listLocalModels(baseUrl?: string): Promise<string[]>;
/**
 * Check if Ollama is running
 */
export declare function isOllamaRunning(baseUrl?: string): Promise<boolean>;
//# sourceMappingURL=ollama-integration.d.ts.map