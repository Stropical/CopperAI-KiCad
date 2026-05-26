/**
 * Ollama integration - Local model inference
 * Provides interface to call local models via Ollama API
 */
import { Ollama } from 'ollama';
export class OllamaClient {
    constructor(config) {
        const baseUrl = config.baseUrl || 'http://localhost:11434';
        this.client = new Ollama({ host: baseUrl });
        this.model = config.model;
        this.temperature = config.temperature ?? 0.7;
    }
    /**
     * Call Ollama with a prompt and return response
     */
    async call(prompt, systemPrompt, options) {
        try {
            const fullPrompt = systemPrompt
                ? `${systemPrompt}\n\nUser Query:\n${prompt}`
                : prompt;
            const response = await this.client.generate({
                model: this.model,
                prompt: fullPrompt,
                stream: false,
                options: {
                    temperature: options?.temperature ?? this.temperature,
                    top_k: options?.topK,
                    top_p: options?.topP,
                    num_predict: 1000,
                },
            });
            // Count approximate tokens (rough estimate: 1 word ≈ 1.3 tokens)
            const words = response.response.split(/\s+/).length;
            const tokensUsed = Math.round(words * 1.3);
            return {
                model: this.model,
                response: response.response,
                tokens_used: tokensUsed,
                done: response.done,
            };
        }
        catch (error) {
            throw new Error(`Ollama error: ${error instanceof Error ? error.message : String(error)}`);
        }
    }
    /**
     * Check if Ollama is running and model is available
     */
    async isAvailable() {
        try {
            const response = await this.client.list();
            return response.models.some(m => m.name.includes(this.model));
        }
        catch {
            return false;
        }
    }
    /**
     * Pull/download a model
     */
    async pullModel() {
        try {
            console.log(`📥 Pulling model: ${this.model}`);
            await this.client.pull({ model: this.model });
            console.log(`✅ Model ready: ${this.model}`);
        }
        catch (error) {
            throw new Error(`Failed to pull model ${this.model}: ${error instanceof Error ? error.message : String(error)}`);
        }
    }
    /**
     * Stream responses for long outputs
     */
    async *stream(prompt, systemPrompt) {
        const fullPrompt = systemPrompt
            ? `${systemPrompt}\n\nUser Query:\n${prompt}`
            : prompt;
        const response = await this.client.generate({
            model: this.model,
            prompt: fullPrompt,
            stream: true,
            options: {
                temperature: this.temperature,
                num_predict: 2000,
            },
        });
        // Handle streaming response
        for await (const chunk of response) {
            yield chunk.response;
        }
    }
}
/**
 * List available local models
 */
export async function listLocalModels(baseUrl = 'http://localhost:11434') {
    try {
        const client = new Ollama({ host: baseUrl });
        const response = await client.list();
        return response.models.map(m => m.name);
    }
    catch {
        return [];
    }
}
/**
 * Check if Ollama is running
 */
export async function isOllamaRunning(baseUrl = 'http://localhost:11434') {
    try {
        const client = new Ollama({ host: baseUrl });
        await client.list();
        return true;
    }
    catch {
        return false;
    }
}
//# sourceMappingURL=ollama-integration.js.map