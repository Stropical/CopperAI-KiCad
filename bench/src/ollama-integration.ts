/**
 * Ollama integration - Local model inference
 * Provides interface to call local models via Ollama API
 */

import { Ollama } from 'ollama';

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

export class OllamaClient {
  private client: Ollama;
  private model: string;
  private temperature: number;

  constructor(config: OllamaConfig) {
    const baseUrl = config.baseUrl || 'http://localhost:11434';
    this.client = new Ollama({ host: baseUrl });
    this.model = config.model;
    this.temperature = config.temperature ?? 0.7;
  }

  /**
   * Call Ollama with a prompt and return response
   */
  async call(
    prompt: string,
    systemPrompt?: string,
    options?: { temperature?: number; topK?: number; topP?: number }
  ): Promise<OllamaResponse> {
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
    } catch (error) {
      throw new Error(`Ollama error: ${error instanceof Error ? error.message : String(error)}`);
    }
  }

  /**
   * Check if Ollama is running and model is available
   */
  async isAvailable(): Promise<boolean> {
    try {
      const response = await this.client.list();
      return response.models.some(m => m.name.includes(this.model));
    } catch {
      return false;
    }
  }

  /**
   * Pull/download a model
   */
  async pullModel(): Promise<void> {
    try {
      console.log(`📥 Pulling model: ${this.model}`);
      await this.client.pull({ model: this.model });
      console.log(`✅ Model ready: ${this.model}`);
    } catch (error) {
      throw new Error(
        `Failed to pull model ${this.model}: ${error instanceof Error ? error.message : String(error)}`
      );
    }
  }

  /**
   * Stream responses for long outputs
   */
  async *stream(prompt: string, systemPrompt?: string) {
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
export async function listLocalModels(baseUrl: string = 'http://localhost:11434'): Promise<string[]> {
  try {
    const client = new Ollama({ host: baseUrl });
    const response = await client.list();
    return response.models.map(m => m.name);
  } catch {
    return [];
  }
}

/**
 * Check if Ollama is running
 */
export async function isOllamaRunning(baseUrl: string = 'http://localhost:11434'): Promise<boolean> {
  try {
    const client = new Ollama({ host: baseUrl });
    await client.list();
    return true;
  } catch {
    return false;
  }
}
