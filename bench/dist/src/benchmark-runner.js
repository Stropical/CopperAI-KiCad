/**
 * Benchmark runner - executes scenarios against agent configurations
 */
import { MockKiCadMCP } from './mock-mcp-server.js';
import { benchmarkScenarios } from './benchmark-scenarios.js';
import { AgentMode, getAgentConfig, ALL_MODES } from './agent-configs.js';
import { createLedBlinkCircuit, createVoltageRegulatorCircuit, createUartInterfaceCircuit, createLedMatrixCircuit, } from './test-circuits.js';
/**
 * Initialize all test circuits
 */
export function initializeCircuits(mcp) {
    const circuits = new Map();
    circuits.set('led_blink', createLedBlinkCircuit(mcp));
    circuits.set('voltage_regulator', createVoltageRegulatorCircuit(mcp));
    circuits.set('uart_interface', createUartInterfaceCircuit(mcp));
    circuits.set('led_matrix', createLedMatrixCircuit(mcp));
    return circuits;
}
/**
 * Simulate agent execution and return metrics
 * This is a mock implementation - in real use, would call actual agent API
 */
async function simulateAgentExecution(mcp, scenario, agentConfig) {
    const startTime = Date.now();
    try {
        // Simulate agent thinking and execution
        // In reality, this would:
        // 1. Call Claude API with the scenario as prompt
        // 2. Parse tool calls and execute them against the mock MCP
        // 3. Return the final result
        // For now, simulate based on difficulty and agent mode
        const baseTokens = scenario.expected_tokens || 1000;
        const modeMultiplier = {
            [AgentMode.PLANNER]: 0.8,
            [AgentMode.EXECUTOR]: 1.2,
            [AgentMode.VERIFIER]: 0.9,
            [AgentMode.PIPELINE]: 2.0,
        };
        const tokensUsed = Math.round(baseTokens * (modeMultiplier[agentConfig.mode] || 1));
        const duration = Math.round((scenario.expected_duration_ms || 3000) * (modeMultiplier[agentConfig.mode] || 1));
        // Simulate success based on agent capability
        // Executor and Pipeline should be more successful than Planner/Verifier alone
        const successRates = {
            [AgentMode.PLANNER]: 0.3, // Planner is read-only, can't fulfill task
            [AgentMode.EXECUTOR]: 0.8, // Executor can do the work
            [AgentMode.VERIFIER]: 0.2, // Verifier alone can't do initial work
            [AgentMode.PIPELINE]: 0.95, // Pipeline with all three is most effective
        };
        const successRate = successRates[agentConfig.mode];
        const succeeded = Math.random() < successRate;
        // Check success criteria
        const circuitJson = mcp.exportSchematicToJson(scenario.circuit);
        let criteriaMetCount = 0;
        if (succeeded) {
            // Simulate meeting criteria based on success
            for (const criterion of scenario.success_criteria) {
                if (criterion.check(circuitJson)) {
                    criteriaMetCount++;
                }
            }
        }
        // Calculate cost (approximation)
        // Haiku: $0.80 per 1M input tokens, $4.00 per 1M output tokens
        const costUsd = (tokensUsed / 1000000) * 2.4; // average of input/output rates
        const duration_ms = Date.now() - startTime + duration; // add simulated execution time
        return {
            scenario_id: scenario.id,
            agent_mode: agentConfig.mode,
            succeeded,
            criteria_met: criteriaMetCount,
            criteria_total: scenario.success_criteria.length,
            tokens_used: tokensUsed,
            duration_ms,
            cost_usd: costUsd,
            errors: succeeded ? [] : ['Execution failed to meet criteria'],
            notes: `${agentConfig.mode} mode - ${criteriaMetCount}/${scenario.success_criteria.length} criteria met`,
        };
    }
    catch (error) {
        const duration_ms = Date.now() - startTime;
        return {
            scenario_id: scenario.id,
            agent_mode: agentConfig.mode,
            succeeded: false,
            criteria_met: 0,
            criteria_total: scenario.success_criteria.length,
            tokens_used: 0,
            duration_ms,
            cost_usd: 0,
            errors: [error instanceof Error ? error.message : String(error)],
            notes: 'Exception during execution',
        };
    }
}
/**
 * Run benchmarks for specified scenarios and agent modes
 */
export async function runBenchmarks(options = {}) {
    const mcp = new MockKiCadMCP();
    initializeCircuits(mcp);
    const scenarios = options.scenarios || benchmarkScenarios;
    const filteredScenarios = options.scenarios_filter
        ? scenarios.filter(options.scenarios_filter)
        : scenarios;
    const agentModes = options.agent_modes || ALL_MODES;
    const results = [];
    console.log(`\n🚀 Starting benchmark suite`);
    console.log(`📋 Scenarios: ${filteredScenarios.length}`);
    console.log(`🤖 Agent modes: ${agentModes.join(', ')}`);
    console.log(`Total tests: ${filteredScenarios.length * agentModes.length}\n`);
    let testCount = 0;
    for (const scenario of filteredScenarios) {
        for (const mode of agentModes) {
            testCount++;
            const config = getAgentConfig(mode);
            const pct = Math.round((testCount / (filteredScenarios.length * agentModes.length)) * 100);
            process.stdout.write(`\r[${pct}%] Running: ${scenario.id} (${mode})`);
            // Reset circuit for fresh start
            mcp.reset(scenario.circuit);
            initializeCircuits(mcp);
            const result = await simulateAgentExecution(mcp, scenario, config);
            results.push(result);
        }
    }
    console.log('\n');
    // Calculate summary
    const passed = results.filter(r => r.succeeded).length;
    const failed = results.length - passed;
    const avg_success_rate = passed / results.length;
    const avg_tokens = results.reduce((sum, r) => sum + r.tokens_used, 0) / results.length;
    const total_cost = results.reduce((sum, r) => sum + r.cost_usd, 0);
    const summary = {
        total_tests: results.length,
        passed,
        failed,
        avg_success_rate,
        avg_tokens_per_test: Math.round(avg_tokens),
        total_cost_usd: Math.round(total_cost * 10000) / 10000,
    };
    return {
        timestamp: new Date().toISOString(),
        scenarios_run: filteredScenarios.length,
        agent_modes: agentModes,
        results,
        summary,
    };
}
/**
 * Format results for display
 */
export function formatResults(suite) {
    const lines = [];
    lines.push('\n' + '='.repeat(80));
    lines.push('BENCHMARK RESULTS');
    lines.push('='.repeat(80));
    lines.push(`\n📊 Summary:`);
    lines.push(`  Timestamp: ${suite.timestamp}`);
    lines.push(`  Total tests: ${suite.summary.total_tests}`);
    lines.push(`  Passed: ${suite.summary.passed} (${(suite.summary.avg_success_rate * 100).toFixed(1)}%)`);
    lines.push(`  Failed: ${suite.summary.failed}`);
    lines.push(`  Avg tokens/test: ${suite.summary.avg_tokens_per_test}`);
    lines.push(`  Total cost: $${suite.summary.total_cost_usd}`);
    lines.push(`\n🤖 Agent Mode Breakdown:`);
    const modeStats = new Map();
    for (const mode of suite.agent_modes) {
        const modeResults = suite.results.filter(r => r.agent_mode === mode);
        const modePassed = modeResults.filter(r => r.succeeded).length;
        modeStats.set(mode, { passed: modePassed, total: modeResults.length });
        const successRate = (modePassed / modeResults.length * 100).toFixed(1);
        const avgTokens = Math.round(modeResults.reduce((sum, r) => sum + r.tokens_used, 0) / modeResults.length);
        lines.push(`  ${mode.padEnd(12)} ${modePassed}/${modeResults.length} (${successRate}%) - ${avgTokens} tokens avg`);
    }
    lines.push(`\n📈 Top Performing Scenarios:`);
    const scenarioStats = new Map();
    for (const result of suite.results) {
        if (!scenarioStats.has(result.scenario_id)) {
            scenarioStats.set(result.scenario_id, { passed: 0, total: 0 });
        }
        const stat = scenarioStats.get(result.scenario_id);
        stat.total++;
        if (result.succeeded)
            stat.passed++;
    }
    const scenarioEntries = Array.from(scenarioStats.entries())
        .sort((a, b) => (b[1].passed / b[1].total) - (a[1].passed / a[1].total))
        .slice(0, 5);
    for (const [scenarioId, stat] of scenarioEntries) {
        const rate = (stat.passed / stat.total * 100).toFixed(0);
        lines.push(`  ${scenarioId.padEnd(30)} ${stat.passed}/${stat.total} (${rate}%)`);
    }
    lines.push('\n' + '='.repeat(80));
    return lines.join('\n');
}
//# sourceMappingURL=benchmark-runner.js.map