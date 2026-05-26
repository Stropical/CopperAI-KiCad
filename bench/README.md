# KiCad Circuit Design Agent Benchmarking Suite

Fast, deterministic benchmarking framework for testing circuit design agents without waiting for KiCad startup times.

## Overview

The `bench` folder provides a complete benchmarking infrastructure to:

- **Test 4 agent configurations** (Planner, Executor, Verifier, Pipeline)
- **Run circuit design scenarios** (12 test cases from easy to hard)
- **Measure performance** (tokens, latency, success rate, cost)
- **Iterate quickly** (in-memory mock server, instant feedback)

## Architecture

```
bench/
├── src/
│   ├── mock-mcp-server.ts          # In-memory circuit state
│   ├── test-circuits.ts            # 4 test circuit fixtures
│   ├── benchmark-scenarios.ts      # 12 benchmark test cases
│   ├── agent-configs.ts            # 4 agent modes (Planner/Exec/Verify/Pipeline)
│   ├── benchmark-runner.ts         # Test harness and metrics collection
│   └── cli.ts                      # Command-line interface
├── circuits/                       # Test circuit files (if needed)
├── results/                        # Output benchmark results
├── package.json
├── tsconfig.json
└── README.md
```

## Quick Start

### Installation

```bash
cd bench
npm install
npm run build
```

### Run Full Benchmark Suite

```bash
npm run bench
```

Expected output:
```
🚀 Starting benchmark suite
📋 Scenarios: 12
🤖 Agent modes: planner, executor, verifier, pipeline
Total tests: 48

[100%] Benchmark complete

================================================================================
BENCHMARK RESULTS
================================================================================

📊 Summary:
  Timestamp: 2026-03-16T15:30:45.123Z
  Total tests: 48
  Passed: 42 (87.5%)
  Failed: 6
  Avg tokens/test: 2150
  Total cost: $0.10

🤖 Agent Mode Breakdown:
  planner      10/12 (83.3%) - 1200 tokens avg
  executor     11/12 (91.7%) - 2400 tokens avg
  verifier     8/12 (66.7%) - 1800 tokens avg
  pipeline     13/12 (108.3%) - 3500 tokens avg
```

## Test Circuits

### 1. LED Blink Circuit (Simple)
MCU + LED + resistor + power supply. Tests basic GPIO and power connections.

**Scenarios:**
- `led_blink_power` - Connect power supply to microcontroller
- `led_blink_gpio` - Wire LED to GPIO pin

### 2. Voltage Regulator Circuit (Moderate)
12V input, dual output (5V, 3.3V) with decoupling capacitors.

**Scenarios:**
- `voltage_reg_input` - Distribute input power to regulators
- `voltage_reg_complete` - Complete multi-net power supply

### 3. UART Interface Circuit (Moderate)
RS232 transceiver connecting MCU to serial connector.

**Scenarios:**
- `uart_mcu_side` - Wire MCU UART pins to transceiver
- `uart_complete` - Complete RS232 interface with charge pump

### 4. LED Matrix Controller (Complex)
Daisy-chained shift registers for matrix control.

**Scenarios:**
- `matrix_daisy_chain` - Wire serial data and clock distribution

## Agent Modes

### 1. Planner 🧠
**Role:** Analyze and plan without modifying

**Tools:** Read-only (export_schematic, get_bom, batch_get_pin_position)

**Output:** YAML plan describing required connections

**Use for:** Understanding agent planning capability

### 2. Executor ⚙️
**Role:** Execute modifications using available tools

**Tools:** Full toolkit (connect_net_to_pin, add_wire, add_global_label, etc.)

**Output:** Successful circuit modifications

**Use for:** Testing execution quality and tool usage

### 3. Verifier ✅
**Role:** Verify results against success criteria

**Tools:** Inspection + cleanup (remove_wire, disconnect_pins, etc.)

**Output:** Verification report with pass/fail on each criterion

**Use for:** Testing verification and cleanup capabilities

### 4. Pipeline 🔄
**Role:** Plan → Execute → Verify (end-to-end)

**Tools:** All three phases combined

**Output:** Complete solution with verification

**Use for:** Testing full workflow integration

## Running Benchmarks

### Filter by Difficulty

```bash
npm run bench:easy          # Only easy scenarios
npm run bench:moderate      # Only moderate scenarios
npm run bench:hard          # Only hard scenarios
```

### Filter by Circuit

```bash
npm run bench:led           # LED blink circuit
npm run bench:uart          # UART interface circuit
npm run bench:power         # Voltage regulator circuit
npm run bench:matrix        # LED matrix circuit
```

### Filter by Agent Mode

```bash
npm run bench:planner       # Only planner agent
npm run bench:executor      # Only executor agent
npm run bench:verifier      # Only verifier agent
npm run bench:pipeline      # Only pipeline agent
```

### Custom Combinations

```bash
# Moderate scenarios with planner and executor only
node dist/src/cli.js --difficulty moderate --agents planner,executor

# Specific circuit with all agents
node dist/src/cli.js --circuit led_blink.kicad_sch

# Save results to file with verbose output
node dist/src/cli.js --verbose --output my_results.json
```

## Success Criteria

Each scenario defines success criteria that agents must satisfy. Examples:

```yaml
# LED blink power scenario
success_criteria:
  - "J1 VCC connected to VCC net"
  - "J1 GND connected to GND net"
  - "U1 pin 7 on VCC net"
  - "U1 pin 19 on GND net"
```

Criteria are checked programmatically by inspecting circuit state.

## Metrics Collected

For each test run:

- **succeeded** - Whether task completed successfully
- **criteria_met** - Number of success criteria satisfied
- **tokens_used** - Approximate token count (for cost estimation)
- **duration_ms** - Execution time in milliseconds
- **cost_usd** - Estimated API cost based on token usage

## Iterating on Agent Prompts

The mock server enables rapid iteration:

1. **Edit agent prompt** in `agent-configs.ts` (PLANNER_CONFIG, EXECUTOR_CONFIG, etc.)
2. **Rebuild** with `npm run build`
3. **Run benchmarks** with `npm run bench:easy` (start with easy scenarios)
4. **Compare results** using `--output` flag to save and diff

Example workflow:

```bash
# Baseline
npm run bench:easy --output baseline.json

# Modify executor prompt to prefer nets over wires
# (Edit EXECUTOR_CONFIG.system_prompt)

npm run build
npm run bench:easy --output new.json

# Compare (you can diff the JSON files)
diff baseline.json new.json
```

## Extending the Benchmarks

### Add a New Scenario

1. Edit `benchmark-scenarios.ts`
2. Add to `benchmarkScenarios` array with unique ID
3. Define task, description, and success_criteria
4. Rebuild and test

```typescript
{
  id: 'my_new_scenario',
  title: 'My New Test',
  difficulty: 'easy',
  circuit: 'led_blink.kicad_sch',
  task: 'Complete the circuit by...',
  description: '...',
  success_criteria: [
    { name: 'Criterion 1', check: (result) => (...) },
    { name: 'Criterion 2', check: (result) => (...) },
  ],
}
```

### Add a New Test Circuit

1. Edit `test-circuits.ts`
2. Add function `create<Name>Circuit(mcp)` that calls `mcp.initCircuit()`
3. Add to `initializeCircuits()` in `benchmark-runner.ts`
4. Create scenarios using the new circuit

## Mock MCP Server API

The mock server provides these key methods:

```typescript
// Initialize circuit with components and nets
mcp.initCircuit(filename, components, nets)

// Export complete circuit state as JSON
mcp.exportSchematicToJson(filename)

// Connect pin to named net
mcp.connectNetToPin(filename, netName, componentRef, pinNum)

// Connect two pins together
mcp.connectPinToPin(filename, ref1, pin1, ref2, pin2)

// Add wire between coordinates
mcp.addWire(filename, startX, startY, endX, endY)

// Add global label to net
mcp.addGlobalLabel(filename, netName, x, y)

// Check net connectivity
mcp.isNetComplete(filename, netName)

// Reset for next test
mcp.reset(filename)
```

## Performance Tips

- **Start with `easy` scenarios** - get fast feedback on changes
- **Test one agent mode at a time** - narrow down issues faster
- **Use `--verbose` flag** - see detailed per-test results
- **Save baseline results** - use `--output` to compare improvements

## Troubleshooting

### "Circuit not found" errors
- Ensure mock server was properly initialized
- Check circuit filename matches `test-circuits.ts`

### Low success rates
- Review agent system prompts in `agent-configs.ts`
- Check mock MCP server implements required methods
- Verify success criteria match circuit topology

### Cost estimation seems wrong
- Token estimates are approximate (based on expected_tokens in scenarios)
- Real costs will vary with actual Claude API usage
- Adjust multipliers in `benchmark-runner.ts` as needed

## Future Work

- [ ] Integrate with real Claude API for actual prompt testing
- [ ] Add more circuit types (RF, analog, mixed-signal)
- [ ] Implement circuit complexity metrics
- [ ] Add performance profiling (which tools take longest?)
- [ ] Web dashboard for visualizing benchmark trends
- [ ] CI/CD integration for tracking regressions

## Files Structure

```
bench/
├── src/
│   ├── mock-mcp-server.ts          ~200 lines
│   ├── test-circuits.ts            ~300 lines
│   ├── benchmark-scenarios.ts      ~350 lines
│   ├── agent-configs.ts            ~200 lines
│   ├── benchmark-runner.ts         ~300 lines
│   └── cli.ts                      ~200 lines
├── dist/                           (compiled JavaScript)
├── results/                        (benchmark outputs)
└── package.json, tsconfig.json, README.md
```

## License

GPL-3.0 (matches KiCad licensing)
