/**
 * Langfuse integration - LLM observability and tracing
 * Tracks agent execution, token usage, latency, and errors
 */
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
export declare class LangfuseTracer {
    private client;
    private enabled;
    constructor(config: LangfuseConfig);
    /**
     * Start a trace for an agent execution
     */
    startTrace(metadata: TraceMetadata): import("langfuse-core").LangfuseTraceClient | null;
    /**
     * Log a generation (LLM call)
     */
    logGeneration(trace: any, input: string, output: string, metadata: {
        model: string;
        tokens_used: number;
        temperature?: number;
        duration_ms?: number;
    }): void;
    /**
     * Log a span (tool execution, verification, etc.)
     */
    logSpan(trace: any, name: string, input: any, output: any, duration_ms: number, metadata?: Record<string, any>): void;
    /**
     * Log success/failure of scenario
     */
    endTrace(trace: any, metadata: {
        succeeded: boolean;
        criteria_met: number;
        criteria_total: number;
        errors?: string[];
    }): void;
    /**
     * Flush pending traces to Langfuse
     */
    flush(): Promise<void>;
    /**
     * Check if Langfuse is enabled
     */
    isEnabled(): boolean;
}
/**
 * Create a simple trace context manager
 */
export interface TraceContext {
    trace: any;
    startTime: number;
    metadata: TraceMetadata;
}
export declare function createTraceContext(tracer: LangfuseTracer, metadata: TraceMetadata): TraceContext;
export declare function endTraceContext(tracer: LangfuseTracer, context: TraceContext, result: {
    succeeded: boolean;
    criteria_met: number;
    criteria_total: number;
    errors?: string[];
}): number;
//# sourceMappingURL=langfuse-integration.d.ts.map