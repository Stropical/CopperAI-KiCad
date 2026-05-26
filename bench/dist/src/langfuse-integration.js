/**
 * Langfuse integration - LLM observability and tracing
 * Tracks agent execution, token usage, latency, and errors
 */
import { Langfuse } from 'langfuse';
/**
 * LangfuseTracer wraps Langfuse client with convenience methods
 */
export class LangfuseTracer {
    constructor(config) {
        this.client = null;
        this.enabled = false;
        this.enabled = config.enabled !== false;
        if (this.enabled) {
            try {
                this.client = new Langfuse({
                    publicKey: config.publicKey || process.env.LANGFUSE_PUBLIC_KEY,
                    secretKey: config.secretKey || process.env.LANGFUSE_SECRET_KEY,
                    baseUrl: config.baseUrl,
                });
            }
            catch (error) {
                console.warn('⚠️ Langfuse disabled - keys not configured');
                this.enabled = false;
            }
        }
    }
    /**
     * Start a trace for an agent execution
     */
    startTrace(metadata) {
        if (!this.enabled || !this.client)
            return null;
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
    logGeneration(trace, input, output, metadata) {
        if (!this.enabled || !this.client || !trace)
            return;
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
    logSpan(trace, name, input, output, duration_ms, metadata) {
        if (!this.enabled || !this.client || !trace)
            return;
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
    endTrace(trace, metadata) {
        if (!this.enabled || !this.client || !trace)
            return;
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
    async flush() {
        if (this.enabled && this.client) {
            await this.client.flush();
        }
    }
    /**
     * Check if Langfuse is enabled
     */
    isEnabled() {
        return this.enabled;
    }
}
export function createTraceContext(tracer, metadata) {
    return {
        trace: tracer.startTrace(metadata),
        startTime: Date.now(),
        metadata,
    };
}
export function endTraceContext(tracer, context, result) {
    const duration_ms = Date.now() - context.startTime;
    tracer.endTrace(context.trace, result);
    return duration_ms;
}
//# sourceMappingURL=langfuse-integration.js.map