/**
 * Four agent configurations for benchmarking
 * 1. Planner - constraint-based, read-only
 * 2. Executor - full tools, execution only
 * 3. Verifier - cleanup and verification
 * 4. Pipeline - all three in sequence
 */
export declare enum AgentMode {
    PLANNER = "planner",
    EXECUTOR = "executor",
    VERIFIER = "verifier",
    PIPELINE = "pipeline"
}
export interface AgentConfig {
    mode: AgentMode;
    model: string;
    temperature: number;
    max_tokens: number;
    system_prompt: string;
    allowed_tools: string[];
    constraints: string[];
}
export declare const PLANNER_CONFIG: AgentConfig;
export declare const EXECUTOR_CONFIG: AgentConfig;
export declare const VERIFIER_CONFIG: AgentConfig;
export declare const PIPELINE_CONFIG: AgentConfig;
export declare function getAgentConfig(mode: AgentMode): AgentConfig;
export declare const ALL_MODES: AgentMode[];
//# sourceMappingURL=agent-configs.d.ts.map