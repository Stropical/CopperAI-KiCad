#!/usr/bin/env node

/**
 * Benchmark CLI - Command-line interface for running circuit design benchmarks
 */

import fs from 'fs';
import path from 'path';
import {
  runBenchmarks,
  formatResults,
  BenchmarkSuite,
} from './benchmark-runner.js';
import { benchmarkScenarios, getScenariosByDifficulty, getScenariosByCircuit } from './benchmark-scenarios.js';
import { AgentMode, ALL_MODES } from './agent-configs.js';

interface CliOptions {
  difficulty?: 'easy' | 'moderate' | 'hard';
  circuit?: string;
  agents?: AgentMode[];
  output?: string;
  verbose?: boolean;
}

function printUsage() {
  console.log(`
circuit-bench - KiCad Agent Benchmarking Tool

USAGE:
  circuit-bench [OPTIONS]

OPTIONS:
  --difficulty <level>    Filter scenarios by difficulty (easy, moderate, hard)
  --circuit <name>       Filter scenarios by circuit
  --agents <list>        Comma-separated list of agents to test
                         (planner,executor,verifier,pipeline)
  --output <file>        Save results to JSON file
  --verbose              Print detailed results
  --help                 Show this message

EXAMPLES:
  # Run all benchmarks
  circuit-bench

  # Test only easy scenarios
  circuit-bench --difficulty easy

  # Test specific circuit with all agents
  circuit-bench --circuit led_blink

  # Test only planner and executor against moderate scenarios
  circuit-bench --difficulty moderate --agents planner,executor

  # Save results to file
  circuit-bench --output results.json

SCENARIOS:
${benchmarkScenarios.map(s => `  ${s.id.padEnd(30)} (${s.difficulty})`).join('\n')}

AGENT MODES:
  planner    - Read-only analysis and planning
  executor   - Full execution with all tools
  verifier   - Verification and cleanup
  pipeline   - All three phases in sequence
`);
}

function parseArgs(args: string[]): CliOptions {
  const options: CliOptions = {};

  for (let i = 0; i < args.length; i++) {
    const arg = args[i];

    if (arg === '--help') {
      printUsage();
      process.exit(0);
    } else if (arg === '--difficulty' && i + 1 < args.length) {
      options.difficulty = args[++i] as any;
    } else if (arg === '--circuit' && i + 1 < args.length) {
      options.circuit = args[++i];
    } else if (arg === '--agents' && i + 1 < args.length) {
      options.agents = args[++i].split(',').map(a => a.trim() as AgentMode);
    } else if (arg === '--output' && i + 1 < args.length) {
      options.output = args[++i];
    } else if (arg === '--verbose') {
      options.verbose = true;
    }
  }

  return options;
}

async function main() {
  const args = process.argv.slice(2);

  if (args.includes('--help') || args.length === 0) {
    if (args.length === 0) {
      console.log('circuit-bench - KiCad Agent Benchmarking Tool\n');
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

  // Run benchmarks
  const suite = await runBenchmarks({
    scenarios: scenariosToRun,
    agent_modes: agentModes,
  });

  // Print results
  console.log(formatResults(suite));

  // Optionally print detailed results
  if (options.verbose) {
    console.log('\n' + '='.repeat(80));
    console.log('DETAILED RESULTS');
    console.log('='.repeat(80) + '\n');

    for (const result of suite.results) {
      const status = result.succeeded ? '✅' : '❌';
      console.log(
        `${status} ${result.scenario_id.padEnd(30)} ${result.agent_mode.padEnd(12)} ` +
          `${result.criteria_met}/${result.criteria_total} criteria - ${result.tokens_used} tokens`
      );

      if (result.errors.length > 0) {
        console.log(`   Errors: ${result.errors.join(', ')}`);
      }
    }
  }

  // Save to file if requested
  if (options.output) {
    const outputPath = path.resolve(options.output);
    fs.writeFileSync(outputPath, JSON.stringify(suite, null, 2));
    console.log(`\n💾 Results saved to ${outputPath}`);
  }
}

main().catch(error => {
  console.error('Error:', error);
  process.exit(1);
});
