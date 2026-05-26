/**
 * Benchmark scenarios - circuit design tasks for agents
 */
export interface SuccessCriteria {
    name: string;
    check: (result: any) => boolean;
}
export interface BenchmarkScenario {
    id: string;
    title: string;
    difficulty: 'easy' | 'moderate' | 'hard';
    circuit: string;
    task: string;
    description: string;
    success_criteria: SuccessCriteria[];
    expected_tokens?: number;
    expected_duration_ms?: number;
}
export declare const benchmarkScenarios: BenchmarkScenario[];
/**
 * Get scenarios by difficulty
 */
export declare function getScenariosByDifficulty(difficulty: 'easy' | 'moderate' | 'hard'): BenchmarkScenario[];
/**
 * Get scenarios for a specific circuit
 */
export declare function getScenariosByCircuit(circuit: string): BenchmarkScenario[];
//# sourceMappingURL=benchmark-scenarios.d.ts.map