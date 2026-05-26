/**
 * Langfuse integration - LLM observability and tracing
 * Tracks agent execution, token usage, latency, and errors
 */

import { Langfuse } from 'langfuse';

export interface LangfuseConfig {
  publicKey?: string;
  secretKey?: string;
  baseUrl?: string;
  enabled?: boolean;
}

export interface TraceMetadata {
  scenario_id: string;
  agent_mode: string;
  circuit: string;
  difficulty: string;
  userId?: string;
  sessionId?: string;
  [key: string]: any;
}

/**
 * LangfuseTracer wraps Langfuse client with convenience methods
 */
export class LangfuseTracer {
  private client: Langfuse | null = null;
  private enabled: boolean = false;

  constructor(config: LangfuseConfig) {
    this.enabled = config.enabled !== false;

    if (this.enabled) {
      try {
        this.client = new Langfuse({
          publicKey: config.publicKey || process.env.LANGFUSE_PUBLIC_KEY,
          secretKey: config.secretKey || process.env.LANGFUSE_SECRET_KEY,
          baseUrl: config.baseUrl,
        });
      } catch (error) {
        console.warn('⚠️ Langfuse disabled - keys not configured');
        this.enabled = false;
      }
    }
  }

  /**
   * Start a trace for an agent execution
   */
  startTrace(metadata: TraceMetadata) {
    if (!this.enabled || !this.client) return null;

    return this.client.trace({
      name: `circuit-design-${metadata.agent_mode}`,
      userId: metadata.userId || 'bench-user',
      sessionId: metadata.sessionId || 'bench-session',
      metadata,
    });
  }

  /**
   * Log a generation (LLM call)
   */
  logGeneration(
    trace: any,
    input: string,
    output: string,
    metadata: {
      model: string;
      tokens_used: number;
      temperature?: number;
      duration_ms?: number;
    }
  ) {
    if (!this.enabled || !this.client || !trace) return;

    trace.generation({
      name: 'agent-generation',
      input: { content: input },
      output: { content: output },
      model: metadata.model,
      usage: {
        input: Math.round(metadata.tokens_used * 0.7), // Estimate input tokens
        output: Math.round(metadata.tokens_used * 0.3), // Estimate output tokens
      },
      metadata: {
        temperature: metadata.temperature,
        duration_ms: metadata.duration_ms,
      },
    });
  }

  /**
   * Log a span (tool execution, verification, etc.)
   */
  logSpan(
    trace: any,
    name: string,
    input: any,
    output: any,
    duration_ms: number,
    metadata?: Record<string, any>
  ) {
    if (!this.enabled || !this.client || !trace) return;

    trace.span({
      name,
      input,
      output,
      duration: duration_ms,
      metadata,
    });
  }

  /**
   * Log success/failure of scenario
   */
  endTrace(
    trace: any,
    metadata: {
      succeeded: boolean;
      criteria_met: number;
      criteria_total: number;
      errors?: string[];
    }
  ) {
    if (!this.enabled || !this.client || !trace) return;

    trace.update({
      metadata: {
        succeeded: metadata.succeeded,
        criteria_met: metadata.criteria_met,
        criteria_total: metadata.criteria_total,
        success_rate: `${(metadata.criteria_met / metadata.criteria_total * 100).toFixed(1)}%`,
        errors: metadata.errors?.join('; '),
      },
    });
  }

  /**
   * Flush pending traces to Langfuse
   */
  async flush(): Promise<void> {
    if (this.enabled && this.client) {
      await this.client.flush();
    }
  }

  /**
   * Check if Langfuse is enabled
   */
  isEnabled(): boolean {
    return this.enabled;
  }
}

/**
 * Create a simple trace context manager
 */
export interface TraceContext {
  trace: any;
  startTime: number;
  metadata: TraceMetadata;
}

export function createTraceContext(
  tracer: LangfuseTracer,
  metadata: TraceMetadata
): TraceContext {
  return {
    trace: tracer.startTrace(metadata),
    startTime: Date.now(),
    metadata,
  };
}

export function endTraceContext(
  tracer: LangfuseTracer,
  context: TraceContext,
  result: {
    succeeded: boolean;
    criteria_met: number;
    criteria_total: number;
    errors?: string[];
  }
) {
  const duration_ms = Date.now() - context.startTime;
  tracer.endTrace(context.trace, result);
  return duration_ms;
}
