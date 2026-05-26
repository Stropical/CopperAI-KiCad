#!/usr/bin/env node
/**
 * Real benchmark CLI - Ollama + Langfuse integration
 */
import fs from 'fs';
import path from 'path';
import { runRealBenchmarks, formatRealResults, } from './benchmark-runner-ollama.js';
import { benchmarkScenarios, getScenariosByDifficulty, getScenariosByCircuit } from './benchmark-scenarios.js';
import { ALL_MODES } from './agent-configs.js';
import { generateHtmlReport } from './html-report-generator.js';
import { MockKiCadMCP } from './mock-mcp-server.js';
import { createLedBlinkCircuit, createVoltageRegulatorCircuit, createUartInterfaceCircuit, createLedMatrixCircuit, } from './test-circuits.js';
function printUsage() {
    console.log(`
circuit-bench-real - KiCad Agent Benchmarking with Ollama + Langfuse

REQUIRES:
  - Ollama running (ollama serve)
  - Model available locally (ollama pull <model>)
  - Optional: Langfuse keys (LANGFUSE_PUBLIC_KEY, LANGFUSE_SECRET_KEY)

USAGE:
  circuit-bench-real [OPTIONS]

OPTIONS:
  --model <name>         Ollama model to use (default: mistral)
  --baseUrl <url>        Ollama base URL (default: http://localhost:11434)
  --difficulty <level>   Filter by difficulty (easy, moderate, hard)
  --circuit <name>       Filter by circuit
  --agents <list>        Comma-separated agent list
  --output <file>        Save results to JSON
  --output-html <file>   Save results as interactive HTML report
  --no-langfuse          Disable Langfuse tracing
  --verbose              Print detailed results
  --help                 Show this message

EXAMPLES:
  # Run with default mistral model
  circuit-bench-real

  # Use a faster model
  circuit-bench-real --model tinyllama

  # Test only easy scenarios
  circuit-bench-real --difficulty easy

  # Test with specific model and save results
  circuit-bench-real --model neural-chat --output results.json

AVAILABLE MODELS:
  mistral           - 7B, balanced quality/speed
  neural-chat       - 7B, optimized for chat
  tinyllama         - 1B, very fast
  orca-mini         - 3B, good reasoning
  dolphin-mixtral   - 46.7B, high quality (requires GPU)

To pull a model:
  ollama pull mistral
  ollama pull neural-chat
  ollama pull tinyllama
`);
}
function parseArgs(args) {
    const options = {};
    for (let i = 0; i < args.length; i++) {
        const arg = args[i];
        if (arg === '--help') {
            printUsage();
            process.exit(0);
        }
        else if (arg === '--model' && i + 1 < args.length) {
            options.model = args[++i];
        }
        else if (arg === '--baseUrl' && i + 1 < args.length) {
            options.baseUrl = args[++i];
        }
        else if (arg === '--difficulty' && i + 1 < args.length) {
            options.difficulty = args[++i];
        }
        else if (arg === '--circuit' && i + 1 < args.length) {
            options.circuit = args[++i];
        }
        else if (arg === '--agents' && i + 1 < args.length) {
            options.agents = args[++i].split(',').map(a => a.trim());
        }
        else if (arg === '--output' && i + 1 < args.length) {
            options.output = args[++i];
        }
        else if (arg === '--output-html' && i + 1 < args.length) {
            options.outputHtml = args[++i];
        }
        else if (arg === '--no-langfuse') {
            options.noLangfuse = true;
        }
        else if (arg === '--verbose') {
            options.verbose = true;
        }
    }
    return options;
}
async function main() {
    const args = process.argv.slice(2);
    if (args.includes('--help') || args.length === 0) {
        if (args.length === 0) {
            console.log('circuit-bench-real - KiCad Agent Benchmarking with Ollama + Langfuse\n');
        }
    }
    const options = parseArgs(args);
    // Determine which scenarios to run
    let scenariosToRun = benchmarkScenarios;
    if (options.difficulty) {
        scenariosToRun = getScenariosByDifficulty(options.difficulty);
        console.log(`📋 Filtering by difficulty: ${options.difficulty}`);
    }
    if (options.circuit) {
        scenariosToRun = getScenariosByCircuit(options.circuit);
        console.log(`📋 Filtering by circuit: ${options.circuit}`);
    }
    // Determine which agents to test
    const agentModes = options.agents || ALL_MODES;
    console.log(`🤖 Agent modes: ${agentModes.join(', ')}\n`);
    try {
        // Initialize circuits for visualization
        const mcp = new MockKiCadMCP();
        const circuits = new Map();
        circuits.set('led_blink', createLedBlinkCircuit(mcp));
        circuits.set('voltage_regulator', createVoltageRegulatorCircuit(mcp));
        circuits.set('uart_interface', createUartInterfaceCircuit(mcp));
        circuits.set('led_matrix', createLedMatrixCircuit(mcp));
        // Run real benchmarks
        const suite = await runRealBenchmarks({
            model: options.model || 'mistral',
            baseUrl: options.baseUrl,
            scenarios: scenariosToRun,
            agent_modes: agentModes,
            langfuseEnabled: !options.noLangfuse,
        });
        // Print results
        console.log(formatRealResults(suite));
        // Optionally print detailed results
        if (options.verbose) {
            console.log('\n' + '='.repeat(80));
            console.log('DETAILED RESULTS');
            console.log('='.repeat(80) + '\n');
            for (const result of suite.results) {
                const status = result.succeeded ? '✅' : '❌';
                console.log(`${status} ${result.scenario_id.padEnd(30)} ${result.agent_mode.padEnd(12)} ` +
                    `${result.criteria_met}/${result.criteria_total} criteria - ${result.tokens_used} tokens`);
                if (result.errors.length > 0) {
                    console.log(`   Errors: ${result.errors.join(', ')}`);
                }
            }
        }
        // Save JSON results if requested
        if (options.output) {
            const outputPath = path.resolve(options.output);
            fs.writeFileSync(outputPath, JSON.stringify(suite, null, 2));
            console.log(`\n💾 JSON results saved to ${outputPath}`);
        }
        // Generate HTML report if requested
        if (options.outputHtml) {
            const htmlPath = path.resolve(options.outputHtml);
            const circuitStateMap = new Map();
            // Get circuit states
            for (const [key, _] of circuits.entries()) {
                const state = mcp.getCircuitState(`${key}.kicad_sch`);
                if (state) {
                    circuitStateMap.set(key, state);
                }
            }
            const html = generateHtmlReport(suite, circuitStateMap, {
                title: `Circuit Benchmark Report - ${suite.model}`,
                includeCircuits: true,
                includeDetailed: true,
                theme: 'light',
            });
            fs.writeFileSync(htmlPath, html);
            console.log(`\n📊 HTML report saved to ${htmlPath}`);
            console.log(`   Open in browser: file://${path.resolve(htmlPath)}`);
        }
        // Print Langfuse info
        if (!options.noLangfuse) {
            console.log(`\n📊 View traces at: https://cloud.langfuse.com`);
        }
    }
    catch (error) {
        const message = error instanceof Error ? error.message : String(error);
        console.error(`\n❌ Error: ${message}`);
        if (message.includes('Ollama not running')) {
            console.log('\n💡 Fix: Start Ollama with: ollama serve');
        }
        process.exit(1);
    }
}
main();
//# sourceMappingURL=cli-real.js.map