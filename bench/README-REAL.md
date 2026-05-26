# Real Benchmarking: Ollama + Langfuse Integration

Fast local model benchmarking with full observability. Run circuit design agents against local LLMs (Mistral, Llama, etc.) with Langfuse tracing.

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                    Benchmark Scenarios                       │
│  (12 circuit design tasks across 4 difficulty levels)        │
└────────────────┬────────────────────────────────────────────┘
                 │
        ┌────────┴────────┐
        │                 │
   ┌────▼──────┐    ┌────▼──────┐
   │   Ollama  │    │  Langfuse │
   │  (Local   │    │ (Tracing &│
   │   LLM)    │    │  Analytics)
   └────┬──────┘    └────┬──────┘
        │                │
        ├────────┬───────┘
        │        │
   ┌────▼────────▼──────┐
   │  Benchmark Runner  │
   │  (Agent Executor)  │
   └────┬───────────────┘
        │
   ┌────▼──────────────┐
   │ Mock KiCad Server │
   │(In-memory state)  │
   └───────────────────┘
```

## Quick Start

### 1. Install Ollama

Download from https://ollama.ai or use:
```bash
# macOS
brew install ollama

# Linux/Windows
# Download from ollama.ai
```

### 2. Start Ollama Server

```bash
ollama serve
# Runs on http://localhost:11434
```

### 3. Pull a Model

```bash
# Fast (1B) - great for testing
ollama pull tinyllama

# Balanced (7B)
ollama pull mistral
ollama pull neural-chat

# High quality (13B+, needs GPU)
ollama pull orca-mini
```

### 4. Run Real Benchmarks

```bash
cd bench
npm install
npm run build

# Run with default mistral model
npm run bench:real

# Run with fast tinyllama
npm run bench:real:tiny

# Run only easy scenarios
npm run bench:real:easy

# Save results to file
npm run bench:real:save

# Verbose output with detailed results
npm run bench:real:verbose
```

## Features

### 1. Local Model Execution
- **No API keys needed** - runs entirely on your machine
- **Zero latency** - instant inference with local models
- **Reproducible** - same circuit state every run
- **Cost-free** - benchmark at scale with zero API costs

### 2. Langfuse Integration
- **Full trace visibility** - see every agent decision and tool call
- **Token accounting** - track input/output tokens per test
- **Error tracking** - automatic error logging and categorization
- **Performance analytics** - latency, success rates, tool usage stats

### 3. Tool Execution Layer
- **Automatic tool parsing** - extracts tool calls from agent responses
- **Mock MCP server** - executes against in-memory circuit state
- **Failure detection** - logs which tools fail and why
- **Success tracking** - verifies circuit state against criteria

## Command Examples

### Basic Usage

```bash
# All tests with default model
npm run bench:real

# Specific difficulty level
npm run bench:real:easy
npm run bench:real --difficulty moderate
npm run bench:real --difficulty hard

# Specific circuit
npm run bench:real -- --circuit led_blink.kicad_sch
npm run bench:real -- --circuit uart_interface.kicad_sch

# Specific agent mode
npm run bench:real -- --agents executor
npm run bench:real -- --agents pipeline
npm run bench:real -- --agents planner,executor,verifier
```

### Advanced Usage

```bash
# Use neural-chat model
npm run bench:real -- --model neural-chat

# Custom Ollama server
npm run bench:real -- --baseUrl http://192.168.1.100:11434

# Disable Langfuse (no tracing)
npm run bench:real -- --no-langfuse

# Verbose output with test details
npm run bench:real -- --verbose

# Save results as JSON
npm run bench:real -- --output my_results.json

# Combine options
npm run bench:real -- \
  --model tinyllama \
  --difficulty easy \
  --agents executor,pipeline \
  --verbose \
  --output results.json
```

## Using Langfuse

### Set Up Langfuse (Optional)

If you want full trace visibility:

1. Sign up at https://cloud.langfuse.com
2. Create a project
3. Get your public/secret keys
4. Set environment variables:

```bash
export LANGFUSE_PUBLIC_KEY=pk_...
export LANGFUSE_SECRET_KEY=sk_...
```

### View Traces

Traces are automatically sent to Langfuse when configured. View them at:
```
https://cloud.langfuse.com/projects/<your-project>/traces
```

Each test appears as a trace with:
- **Generations** - LLM API calls with prompt/response
- **Spans** - Tool executions (connect_net_to_pin, add_wire, etc.)
- **Metadata** - Scenario ID, agent mode, circuit, difficulty
- **Metrics** - Tokens, latency, success/failure

### Disable Langfuse

If you don't want tracing, use `--no-langfuse`:
```bash
npm run bench:real -- --no-langfuse
```

## Model Selection

### Recommended Models

| Model | Size | Speed | Quality | Use Case |
|-------|------|-------|---------|----------|
| **tinyllama** | 1B | ⚡⚡⚡ | ⭐⭐ | Fast iteration/testing |
| **mistral** | 7B | ⚡⚡ | ⭐⭐⭐⭐ | Balanced benchmark |
| **neural-chat** | 7B | ⚡⚡ | ⭐⭐⭐⭐ | Chat-optimized |
| **orca-mini** | 3B | ⚡⚡⚡ | ⭐⭐⭐ | Good reasoning |
| **dolphin-mixtral** | 46.7B | ⚡ | ⭐⭐⭐⭐⭐ | Highest quality (GPU) |

### Pulling Models

```bash
# Quick pulls
ollama pull tinyllama    # 1GB
ollama pull mistral      # 4GB
ollama pull neural-chat  # 4GB
ollama pull orca-mini    # 2GB

# High quality (requires GPU)
ollama pull dolphin-mixtral  # 96GB
ollama pull orca-13b         # 7GB
```

## Output & Results

### Console Output

```
🚀 Starting real benchmark suite with Ollama
🤖 Model: mistral
📋 Scenarios: 3
🧠 Agent modes: planner, executor, verifier, pipeline
📊 Langfuse: enabled
Total tests: 12

[100%] Completed

================================================================================
BENCHMARK RESULTS (Ollama + Langfuse)
================================================================================

📊 Summary:
  Timestamp: 2026-03-17T01:15:30.123Z
  Model: mistral
  Total tests: 12
  Passed: 10 (83.3%)
  Failed: 2
  Avg tokens/test: 1850
  Total cost: $0.00
  Tool calls: 45 (88.9% success)

🤖 Agent Mode Breakdown:
  executor     3/3 (100.0%) - 2100 tokens, 15 tools
  pipeline     2/3 (66.7%) - 2800 tokens, 20 tools
  planner      3/3 (100.0%) - 1200 tokens, 3 tools
  verifier     2/3 (66.7%) - 1400 tokens, 7 tools

📈 Top Performing Scenarios:
  voltage_reg_input              3/4 (75%)
  led_blink_power                3/4 (75%)
  uart_mcu_side                  2/4 (50%)

================================================================================
```

### JSON Output

Save detailed results:
```bash
npm run bench:real:save
```

Results file structure:
```json
{
  "timestamp": "2026-03-17T...",
  "model": "mistral",
  "scenarios_run": 3,
  "agent_modes": ["planner", "executor", "verifier", "pipeline"],
  "results": [
    {
      "scenario_id": "led_blink_power",
      "agent_mode": "executor",
      "succeeded": true,
      "criteria_met": 4,
      "criteria_total": 4,
      "tokens_used": 1850,
      "duration_ms": 3200,
      "cost_usd": 0,
      "errors": [],
      "notes": "4/4 criteria - 12/12 tools",
      "tool_calls_made": 12,
      "tool_calls_successful": 12
    }
  ],
  "summary": {
    "total_tests": 12,
    "passed": 10,
    "failed": 2,
    "avg_success_rate": 0.833,
    "avg_tokens_per_test": 1850,
    "total_cost_usd": 0,
    "total_tool_calls": 45,
    "tool_success_rate": 0.889
  }
}
```

## Troubleshooting

### "Ollama not running"

```
❌ Error: Ollama not running at http://localhost:11434
```

**Fix:** Start Ollama server
```bash
ollama serve
```

### Model Not Found

```
📥 Model not found, pulling: mistral
```

**Fix:** Wait for model to download (first run takes time depending on model size)

### Out of Memory

If Ollama crashes with OOM:
- Use a smaller model (tinyllama vs mistral)
- Close other applications
- Run with `ollama serve --gpu=false` (CPU-only, slower)

### Langfuse Connection Issues

If traces aren't sending:
- Check internet connection
- Verify keys with `echo $LANGFUSE_PUBLIC_KEY`
- Use `--no-langfuse` to disable
- Check Langfuse status at cloud.langfuse.com

## Performance Tips

### For Fast Iteration
```bash
npm run bench:real:easy -- --model tinyllama --agents executor
```

- **Easy scenarios only** - 3 tests instead of 12
- **TinyLlama** - 1B model runs in <100ms per test
- **Executor only** - skip planning/verification overhead
- **Total time:** ~30 seconds

### For Comprehensive Testing
```bash
npm run bench:real -- --model mistral --verbose --output results.json
```

- **All scenarios** - 12 tests
- **All agents** - 4 modes = 48 total tests
- **Mistral** - balanced speed/quality
- **Total time:** 5-10 minutes

### Batch Testing

```bash
#!/bin/bash
# Test multiple models
for model in tinyllama mistral neural-chat; do
  npm run bench:real -- --model $model --output results-$model.json
done
```

## Workflow: Iterating on Agent Prompts

1. **Baseline** - establish current performance:
```bash
npm run bench:real:easy -- --output baseline.json
```

2. **Modify prompts** - edit `src/agent-configs.ts` (EXECUTOR_CONFIG, etc.)

3. **Rebuild and test**:
```bash
npm run build
npm run bench:real:easy -- --output new.json
```

4. **Compare**:
```bash
# Visual diff
diff baseline.json new.json

# Or extract metrics
cat baseline.json | jq '.summary'
cat new.json | jq '.summary'
```

5. **Iterate** - repeat steps 2-4

## Architecture Details

### Ollama Client (`ollama-integration.ts`)
- Wraps Ollama API for easy model calling
- Handles connection, pulls, and streaming
- Token count estimation (words × 1.3)

### Tool Executor (`tool-executor.ts`)
- Parses tool calls from agent responses (XML or JSON)
- Executes against mock MCP server
- Returns structured results

### Langfuse Tracer (`langfuse-integration.ts`)
- Wraps Langfuse client for observability
- Logs generations, spans, and metadata
- Automatic error tracking

### Benchmark Runner (`benchmark-runner-ollama.ts`)
- Orchestrates scenario → agent → execution → verification
- Manages tracing throughout
- Collects metrics and formats results

## File Structure

```
bench/
├── src/
│   ├── mock-mcp-server.ts          # In-memory circuit state
│   ├── test-circuits.ts            # 4 test circuit fixtures
│   ├── benchmark-scenarios.ts      # 12 benchmark scenarios
│   ├── agent-configs.ts            # 4 agent modes
│   ├── tool-executor.ts            # Tool parsing/execution
│   ├── ollama-integration.ts       # Ollama client wrapper
│   ├── langfuse-integration.ts     # Langfuse observability
│   ├── benchmark-runner.ts         # Mock benchmark runner
│   ├── benchmark-runner-ollama.ts  # Real benchmark runner
│   ├── cli.ts                      # Mock CLI
│   └── cli-real.ts                 # Real CLI
├── dist/                           # Compiled JavaScript
├── circuits/                       # Test circuits
├── results/                        # Benchmark output
├── package.json
├── tsconfig.json
└── README-REAL.md
```

## Next Steps

1. ✅ Run with mock benchmarks (`npm run bench`)
2. ✅ Run with real models (`npm run bench:real`)
3. ⏳ Analyze Langfuse traces for insights
4. ⏳ Iterate on agent prompts based on results
5. ⏳ Add more circuit designs and test scenarios
6. ⏳ Integrate into CI/CD for regression testing

---

**Questions?** Check the main README.md for mock benchmarking or see troubleshooting above.
