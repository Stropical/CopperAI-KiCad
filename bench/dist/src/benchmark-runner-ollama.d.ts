/**
 * Real benchmark runner using Ollama + Langfuse
 * Executes scenarios against local LLM with full observability
 */
import { BenchmarkScenario } from './benchmark-scenarios.js';
import { AgentMode } from './agent-configs.js';
export interface RealBenchmarkResult {
    scenario_id: string;
    agent_mode: AgentMode;
    succeeded: boolean;
    criteria_met: number;
    criteria_total: number;
    tokens_used: number;
    duration_ms: number;
    cost_usd: number;
    errors: string[];
    notes: string;
    tool_calls_made: number;
    tool_calls_successful: number;
}
export interface RealBenchmarkSuite {
    timestamp: string;
    model: string;
    scenarios_run: number;
    agent_modes: AgentMode[];
    results: RealBenchmarkResult[];
    summary: {
        total_tests: number;
        passed: number;
        failed: number;
        avg_success_rate: number;
        avg_tokens_per_test: number;
        total_cost_usd: number;
        total_tool_calls: number;
        tool_success_rate: number;
    };
}
/**
 * Run real benchmarks using Ollama
 */
export declare function runRealBenchmarks(options?: {
    model: string;
    baseUrl?: string;
    scenarios?: BenchmarkScenario[];
    agent_modes?: AgentMode[];
    scenarios_filter?: (s: BenchmarkScenario) => boolean;
    langfuseEnabled?: boolean;
}): Promise<RealBenchmarkSuite>;
/**
 * Format real results for display
 */
export declare function formatRealResults(suite: RealBenchmarkSuite): string;
//# sourceMappingURL=benchmark-runner-ollama.d.ts.map