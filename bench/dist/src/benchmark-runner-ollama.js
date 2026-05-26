/**
 * Real benchmark runner using Ollama + Langfuse
 * Executes scenarios against local LLM with full observability
 */
import { MockKiCadMCP } from './mock-mcp-server.js';
import { OllamaClient, isOllamaRunning } from './ollama-integration.js';
import { LangfuseTracer, createTraceContext, endTraceContext } from './langfuse-integration.js';
import { benchmarkScenarios } from './benchmark-scenarios.js';
import { getAgentConfig, ALL_MODES } from './agent-configs.js';
import { ToolExecutor, parseToolCalls } from './tool-executor.js';
import { createLedBlinkCircuit, createVoltageRegulatorCircuit, createUartInterfaceCircuit, createLedMatrixCircuit, } from './test-circuits.js';
/**
 * Initialize all test circuits
 */
function initializeCircuits(mcp) {
    const circuits = new Map();
    circuits.set('led_blink', createLedBlinkCircuit(mcp));
    circuits.set('voltage_regulator', createVoltageRegulatorCircuit(mcp));
    circuits.set('uart_interface', createUartInterfaceCircuit(mcp));
    circuits.set('led_matrix', createLedMatrixCircuit(mcp));
    return circuits;
}
/**
 * Execute agent against scenario using Ollama
 */
async function executeAgent(mcp, scenario, agentConfig, ollamaClient, tracer, toolExecutor) {
    const startTime = Date.now();
    const traceContext = createTraceContext(tracer, {
        scenario_id: scenario.id,
        agent_mode: agentConfig.mode,
        circuit: scenario.circuit,
        difficulty: scenario.difficulty,
    });
    const errors = [];
    let toolCallsTotal = 0;
    let toolCallsSuccessful = 0;
    try {
        // Build the prompt
        const prompt = `
TASK: ${scenario.task}

CIRCUIT: ${scenario.circuit}

SUCCESS CRITERIA:
${scenario.success_criteria.map(c => `- ${c.name}`).join('\n')}

Please analyze the circuit and complete the task. When you call tools, use this format:

<tool name="tool_name">
{
  "param1": "value1",
  "param2": "value2"
}
</tool>

Start by exporting the schematic to understand the current state.
`;
        // Call Ollama
        const response = await ollamaClient.call(prompt, agentConfig.system_prompt, {
            temperature: agentConfig.temperature,
        });
        // Log generation
        tracer.logGeneration(traceContext.trace, prompt, response.response, {
            model: agentConfig.model,
            tokens_used: response.tokens_used,
            temperature: agentConfig.temperature,
        });
        // Parse and execute tool calls
        const toolCalls = parseToolCalls(response.response);
        toolCallsTotal = toolCalls.length;
        for (const toolCall of toolCalls) {
            try {
                const toolResult = await toolExecutor.execute(scenario.circuit, toolCall);
                if (toolResult.success) {
                    toolCallsSuccessful++;
                }
                else {
                    errors.push(`${toolCall.name}: ${toolResult.error}`);
                }
                // Log tool execution
                tracer.logSpan(traceContext.trace, `tool-${toolCall.name}`, toolCall.args, toolResult.result, 50, // Estimated time
                { success: toolResult.success });
            }
            catch (error) {
                errors.push(`Tool execution error: ${error instanceof Error ? error.message : String(error)}`);
            }
        }
        // Verify success criteria
        const circuitJson = mcp.exportSchematicToJson(scenario.circuit);
        let criteriaMetCount = 0;
        for (const criterion of scenario.success_criteria) {
            try {
                if (criterion.check(circuitJson)) {
                    criteriaMetCount++;
                }
            }
            catch {
                // Criterion check failed
            }
        }
        const succeeded = criteriaMetCount >= scenario.success_criteria.length * 0.7 && errors.length === 0;
        // End trace with results
        const duration_ms = endTraceContext(tracer, traceContext, {
            succeeded,
            criteria_met: criteriaMetCount,
            criteria_total: scenario.success_criteria.length,
            errors,
        });
        // Estimate cost
        const costUsd = (response.tokens_used / 1000000) * 0.001; // Rough estimate
        return {
            scenario_id: scenario.id,
            agent_mode: agentConfig.mode,
            succeeded,
            criteria_met: criteriaMetCount,
            criteria_total: scenario.success_criteria.length,
            tokens_used: response.tokens_used,
            duration_ms,
            cost_usd: costUsd,
            errors,
            notes: `${criteriaMetCount}/${scenario.success_criteria.length} criteria - ${toolCallsSuccessful}/${toolCallsTotal} tools`,
            tool_calls_made: toolCallsTotal,
            tool_calls_successful: toolCallsSuccessful,
        };
    }
    catch (error) {
        const duration_ms = Date.now() - startTime;
        const errorMsg = error instanceof Error ? error.message : String(error);
        errors.push(errorMsg);
        endTraceContext(tracer, traceContext, {
            succeeded: false,
            criteria_met: 0,
            criteria_total: scenario.success_criteria.length,
            errors,
        });
        return {
            scenario_id: scenario.id,
            agent_mode: agentConfig.mode,
            succeeded: false,
            criteria_met: 0,
            criteria_total: scenario.success_criteria.length,
            tokens_used: 0,
            duration_ms,
            cost_usd: 0,
            errors,
            notes: 'Exception during execution',
            tool_calls_made: 0,
            tool_calls_successful: 0,
        };
    }
}
/**
 * Run real benchmarks using Ollama
 */
export async function runRealBenchmarks(options = {
    model: 'mistral',
}) {
    const mcp = new MockKiCadMCP();
    const toolExecutor = new ToolExecutor(mcp);
    const tracer = new LangfuseTracer({
        enabled: options.langfuseEnabled !== false,
    });
    // Check Ollama is running
    const ollamaRunning = await isOllamaRunning(options.baseUrl);
    if (!ollamaRunning) {
        throw new Error(`❌ Ollama not running at ${options.baseUrl || 'http://localhost:11434'}\n` +
            'Start Ollama with: ollama serve');
    }
    const ollamaClient = new OllamaClient({
        baseUrl: options.baseUrl,
        model: options.model,
        temperature: 0.5,
    });
    // Check model is available
    const modelAvailable = await ollamaClient.isAvailable();
    if (!modelAvailable) {
        console.log(`📥 Model not found, pulling: ${options.model}`);
        await ollamaClient.pullModel();
    }
    initializeCircuits(mcp);
    const scenarios = options.scenarios || benchmarkScenarios;
    const filteredScenarios = options.scenarios_filter
        ? scenarios.filter(options.scenarios_filter)
        : scenarios;
    const agentModes = options.agent_modes || ALL_MODES;
    const results = [];
    console.log(`\n🚀 Starting real benchmark suite with Ollama`);
    console.log(`🤖 Model: ${options.model}`);
    console.log(`📋 Scenarios: ${filteredScenarios.length}`);
    console.log(`🧠 Agent modes: ${agentModes.join(', ')}`);
    console.log(`📊 Langfuse: ${tracer.isEnabled() ? 'enabled' : 'disabled'}`);
    console.log(`Total tests: ${filteredScenarios.length * agentModes.length}\n`);
    let testCount = 0;
    for (const scenario of filteredScenarios) {
        for (const mode of agentModes) {
            testCount++;
            const config = getAgentConfig(mode);
            const pct = Math.round((testCount / (filteredScenarios.length * agentModes.length)) * 100);
            process.stdout.write(`\r[${pct}%] ${scenario.id.padEnd(30)} ${mode.padEnd(10)}`);
            // Reset circuit for fresh start
            mcp.reset(scenario.circuit);
            initializeCircuits(mcp);
            const result = await executeAgent(mcp, scenario, config, ollamaClient, tracer, toolExecutor);
            results.push(result);
            // Small delay between tests
            await new Promise(resolve => setTimeout(resolve, 100));
        }
    }
    console.log('\n');
    // Flush Langfuse traces
    await tracer.flush();
    // Calculate summary
    const passed = results.filter(r => r.succeeded).length;
    const failed = results.length - passed;
    const avg_success_rate = passed / results.length;
    const avg_tokens = results.reduce((sum, r) => sum + r.tokens_used, 0) / results.length;
    const total_cost = results.reduce((sum, r) => sum + r.cost_usd, 0);
    const total_tool_calls = results.reduce((sum, r) => sum + r.tool_calls_made, 0);
    const tool_success_rate = total_tool_calls > 0
        ? results.reduce((sum, r) => sum + r.tool_calls_successful, 0) / total_tool_calls
        : 0;
    const summary = {
        total_tests: results.length,
        passed,
        failed,
        avg_success_rate,
        avg_tokens_per_test: Math.round(avg_tokens),
        total_cost_usd: Math.round(total_cost * 10000) / 10000,
        total_tool_calls,
        tool_success_rate,
    };
    return {
        timestamp: new Date().toISOString(),
        model: options.model,
        scenarios_run: filteredScenarios.length,
        agent_modes: agentModes,
        results,
        summary,
    };
}
/**
 * Format real results for display
 */
export function formatRealResults(suite) {
    const lines = [];
    lines.push('\n' + '='.repeat(80));
    lines.push('BENCHMARK RESULTS (Ollama + Langfuse)');
    lines.push('='.repeat(80));
    lines.push(`\n📊 Summary:`);
    lines.push(`  Timestamp: ${suite.timestamp}`);
    lines.push(`  Model: ${suite.model}`);
    lines.push(`  Total tests: ${suite.summary.total_tests}`);
    lines.push(`  Passed: ${suite.summary.passed} (${(suite.summary.avg_success_rate * 100).toFixed(1)}%)`);
    lines.push(`  Failed: ${suite.summary.failed}`);
    lines.push(`  Avg tokens/test: ${suite.summary.avg_tokens_per_test}`);
    lines.push(`  Total cost: $${suite.summary.total_cost_usd}`);
    lines.push(`  Tool calls: ${suite.summary.total_tool_calls} (${(suite.summary.tool_success_rate * 100).toFixed(1)}% success)`);
    lines.push(`\n🤖 Agent Mode Breakdown:`);
    const modeStats = new Map();
    for (const mode of suite.agent_modes) {
        const modeResults = suite.results.filter(r => r.agent_mode === mode);
        const modePassed = modeResults.filter(r => r.succeeded).length;
        modeStats.set(mode, { passed: modePassed, total: modeResults.length });
        const successRate = (modePassed / modeResults.length * 100).toFixed(1);
        const avgTokens = Math.round(modeResults.reduce((sum, r) => sum + r.tokens_used, 0) / modeResults.length);
        const avgTools = Math.round(modeResults.reduce((sum, r) => sum + r.tool_calls_made, 0) / modeResults.length);
        lines.push(`  ${mode.padEnd(12)} ${modePassed}/${modeResults.length} (${successRate}%) - ${avgTokens} tokens, ${avgTools} tools`);
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
//# sourceMappingURL=benchmark-runner-ollama.js.map