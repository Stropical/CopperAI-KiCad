/**
 * Benchmark runner - executes scenarios against agent configurations
 */
import { MockKiCadMCP } from './mock-mcp-server.js';
import { BenchmarkScenario } from './benchmark-scenarios.js';
import { AgentMode } from './agent-configs.js';
export interface BenchmarkResult {
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
}
export interface BenchmarkSuite {
    timestamp: string;
    scenarios_run: number;
    agent_modes: AgentMode[];
    results: BenchmarkResult[];
    summary: {
        total_tests: number;
        passed: number;
        failed: number;
        avg_success_rate: number;
        avg_tokens_per_test: number;
        total_cost_usd: number;
    };
}
/**
 * Initialize all test circuits
 */
export declare function initializeCircuits(mcp: MockKiCadMCP): Map<string, string>;
/**
 * Run benchmarks for specified scenarios and agent modes
 */
export declare function runBenchmarks(options?: {
    scenarios?: BenchmarkScenario[];
    agent_modes?: AgentMode[];
    scenarios_filter?: (s: BenchmarkScenario) => boolean;
}): Promise<BenchmarkSuite>;
/**
 * Format results for display
 */
export declare function formatResults(suite: BenchmarkSuite): string;
//# sourceMappingURL=benchmark-runner.d.ts.map