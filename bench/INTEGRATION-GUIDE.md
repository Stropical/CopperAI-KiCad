# Integration Guide: Ollama + Langfuse Benchmarking

Complete guide to running the real benchmarking suite with local LLMs.

## 1. Setup Checklist

### Prerequisites
- [ ] Node.js 18+ installed
- [ ] npm installed
- [ ] Ollama installed (https://ollama.ai)
- [ ] Internet connection (for initial model downloads)

### Optional
- [ ] Langfuse account (https://cloud.langfuse.com) - for trace visualization
- [ ] GPU available - for faster inference (8GB+ VRAM recommended)

## 2. Installation Steps

### Step 1: Install Ollama

**macOS:**
```bash
brew install ollama
```

**Linux:**
```bash
curl https://ollama.ai/install.sh | sh
```

**Windows:**
Download from https://ollama.ai

### Step 2: Start Ollama Server

```bash
ollama serve
```

Output should show:
```
Listening on localhost:11434
```

Leave this running in a terminal.

### Step 3: Pull Models

```bash
# Quick test model (1GB)
ollama pull tinyllama

# Recommended model (4GB)
ollama pull mistral

# (Optional) Chat-optimized
ollama pull neural-chat
```

### Step 4: Build Benchmarks

```bash
cd bench
npm install
npm run build
```

### Step 5: (Optional) Configure Langfuse

If you want trace visibility:

1. Sign up at https://cloud.langfuse.com
2. Create a project
3. Copy your keys

4. Set environment variables:
```bash
export LANGFUSE_PUBLIC_KEY=pk_your_key_here
export LANGFUSE_SECRET_KEY=sk_your_key_here
```

Or add to `.env`:
```bash
LANGFUSE_PUBLIC_KEY=pk_...
LANGFUSE_SECRET_KEY=sk_...
```

## 3. Running Benchmarks

### Quick Start (2 minutes)

```bash
npm run bench:real:easy -- --model tinyllama
```

This runs:
- 3 easy scenarios
- All 4 agent modes
- 12 total tests
- With fast TinyLlama model

### Recommended Start (5 minutes)

```bash
npm run bench:real -- --difficulty easy --model mistral
```

This runs:
- 3 easy scenarios
- All 4 agent modes
- 12 total tests
- With balanced Mistral model

### Full Benchmark Suite (20+ minutes)

```bash
npm run bench:real -- --model mistral --verbose
```

This runs:
- 12 scenarios (all difficulties)
- 4 agent modes each
- 48 total tests
- With verbose output

## 4. Understanding Results

### Success Criteria

Each scenario checks that the agent correctly:
- Created required nets (VCC, GND, signal nets)
- Connected all component pins
- Placed components (if needed)
- Added labels for clarity

### Metrics

**Passed:** Agent met ≥70% of success criteria with no errors

**Tokens:** Estimated token count for cost calculation

**Duration:** How long the test took to run

**Tool Calls:** Which MCP tools were used and if they succeeded

### Interpreting Results

```
executor     3/3 (100.0%) - 2100 tokens, 15 tools
↑             ↑            ↑           ↑
Agent mode   Tests passed  Avg tokens  Avg tools used

✅ Executor mode: 100% success = reliable tool execution
⚠️ Verifier mode: 66% success = verification challenges
📊 Pipeline mode: 15 tools = planner+executor+verifier
```

## 5. Comparing Models

### TinyLlama vs Mistral vs Neural-Chat

```bash
# Test each model
npm run bench:real:easy -- --model tinyllama --output tiny.json
npm run bench:real:easy -- --model mistral --output mistral.json
npm run bench:real:easy -- --model neural-chat --output neural.json

# Compare results
cat tiny.json | jq '.summary'
cat mistral.json | jq '.summary'
cat neural.json | jq '.summary'
```

**Example Output:**
```json
// TinyLlama
{ "passed": 8, "avg_tokens": 1200, "avg_duration_ms": 500 }

// Mistral
{ "passed": 10, "avg_tokens": 2100, "avg_duration_ms": 2500 }

// Neural-Chat
{ "passed": 10, "avg_tokens": 2300, "avg_duration_ms": 2800 }
```

**Trade-offs:**
- **TinyLlama**: Fastest, least accurate
- **Mistral**: Balanced
- **Neural-Chat**: Better at task completion, slower
- **Dolphin-Mixtral**: Highest quality, requires GPU

## 6. Iterating on Agent Prompts

### Workflow

1. **Establish baseline:**
```bash
npm run bench:real:easy -- --model mistral --output baseline.json
```

2. **Edit prompt** (example):
```typescript
// src/agent-configs.ts - EXECUTOR_CONFIG

system_prompt: `You are a circuit design executor...
// Change this:
- Use nets for signal routing (SDA, UART_TX, CLK, power nets)

// To this:
- ALWAYS use nets for signal routing, NEVER use wires
- Prefer connect_net_to_pin over add_wire
- One net per signal, group related signals together
`
```

3. **Rebuild:**
```bash
npm run build
```

4. **Test:**
```bash
npm run bench:real:easy -- --model mistral --output new.json
```

5. **Compare:**
```bash
# Extract key metrics
jq '.summary' baseline.json
jq '.summary' new.json

# Or full diff
diff <(jq '.summary' baseline.json) <(jq '.summary' new.json)
```

6. **Iterate** - repeat steps 2-5

### Metric to Watch

```json
"summary": {
  "passed": 10,           // ← Higher is better
  "avg_tokens": 1850,     // ← Lower is better (cheaper)
  "tool_success_rate": 0.889,  // ← Higher is better
  "total_cost_usd": 0           // ← Zero for local models!
}
```

## 7. Using Langfuse

### View Traces

After running with Langfuse enabled:

1. Go to https://cloud.langfuse.com
2. Select your project
3. Click "Traces"
4. See each test as a trace with:
   - Prompt sent to model
   - Model response
   - Tool calls made
   - Execution time
   - Token usage

### Debug Agent Issues

If an agent fails, check Langfuse trace:

1. Find the failed test in traces
2. Expand the trace to see:
   - **Input**: Exact prompt sent
   - **Output**: Model's response
   - **Spans**: Each tool call and result
   - **Errors**: What went wrong

3. Refine the prompt based on what you see

### Export Traces

```bash
# Click "Export" in Langfuse UI
# Or use API:
curl -H "Authorization: Bearer $LANGFUSE_API_KEY" \
  https://cloud.langfuse.com/api/projects/traces
```

## 8. Common Issues & Fixes

### Ollama Connection Error

```
❌ Error: Ollama not running at http://localhost:11434
```

**Fix:**
```bash
# In another terminal:
ollama serve
```

### Model Not Found

```
📥 Model not found, pulling: mistral
(waits for download...)
```

**Fix:** This is normal on first run. Wait for model to download.

**Speed up:** Pre-pull models:
```bash
ollama pull mistral mistral
ollama pull tinyllama
ollama pull neural-chat
```

### Out of Memory

```
Error: oom killer
```

**Fix 1:** Use smaller model
```bash
npm run bench:real:easy -- --model tinyllama
```

**Fix 2:** Run on CPU only (slower)
```bash
ollama serve --gpu=false
```

**Fix 3:** Free up system RAM
```bash
# macOS
memory_pressure # check current usage
killall -9 Chrome  # close large apps
```

### Langfuse Keys Not Working

```
⚠️ Langfuse disabled - keys not configured
```

**Fix:** Check environment variables
```bash
echo $LANGFUSE_PUBLIC_KEY
echo $LANGFUSE_SECRET_KEY
```

If empty, set them:
```bash
export LANGFUSE_PUBLIC_KEY=pk_...
export LANGFUSE_SECRET_KEY=sk_...
```

Or disable Langfuse:
```bash
npm run bench:real -- --no-langfuse
```

## 9. Performance Optimization

### For Development (Quick Feedback)

```bash
npm run bench:real:easy -- --model tinyllama --agents executor
```

- Easy scenarios only (3 tests)
- TinyLlama (1B, ~500ms per test)
- Executor mode only (no planning/verification)
- **Total time: ~2 minutes**

### For CI/CD (Regression Testing)

```bash
npm run bench:real:easy -- --model mistral --output ci-results.json
```

- Easy scenarios only (3 tests)
- Mistral (7B, more reliable)
- All agents (catch regressions)
- Save results for comparison
- **Total time: ~5 minutes**

### For Comprehensive Testing

```bash
npm run bench:real -- --model mistral --verbose --output full-results.json
```

- All 12 scenarios
- All 4 agent modes = 48 tests
- Mistral for quality
- Verbose output
- **Total time: 20+ minutes**

### For GPU-Accelerated (High Quality)

```bash
ollama pull dolphin-mixtral
npm run bench:real -- --model dolphin-mixtral
```

- Requires GPU (8GB+ VRAM)
- Highest accuracy
- Slower inference
- Best for final evaluation

## 10. Production Deployment

### Automated Testing

```bash
# .github/workflows/bench.yml
name: Circuit Benchmark
on: [push, pull_request]
jobs:
  benchmark:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v3
      - uses: actions/setup-node@v3
        with:
          node-version: 18
      - run: |
          cd bench
          npm install
          npm run build
          # Run quick tests (skip real model)
          npm run bench
```

### Docker Deployment

```dockerfile
FROM node:18

# Install Ollama
RUN curl https://ollama.ai/install.sh | sh

# Setup bench
WORKDIR /app
COPY bench /app
RUN npm install && npm run build

# Run benchmarks
CMD ["npm", "run", "bench:real"]
```

### Monitoring & Alerts

Use Langfuse webhooks to alert on:
- Success rate drops below threshold
- Average tokens exceed budget
- Tool failures increase
- Latency increases

## 11. Next Steps

1. ✅ Run `npm run bench:real:easy` - verify setup works
2. ✅ Compare models (TinyLlama vs Mistral)
3. ⏳ Modify agent prompts based on results
4. ⏳ Set up Langfuse for trace analysis
5. ⏳ Integrate into your development workflow
6. ⏳ Run full benchmarks for comprehensive evaluation

## 12. Support & Resources

### Documentation
- Main README: `README.md`
- Real benchmarking: `README-REAL.md`
- Ollama docs: https://ollama.ai/docs
- Langfuse docs: https://docs.langfuse.com

### Troubleshooting Checklist
- [ ] Ollama running (`ollama serve` in another terminal)
- [ ] Model available (`ollama list`)
- [ ] Port not blocked (`curl http://localhost:11434`)
- [ ] Node.js 18+ (`node --version`)
- [ ] Dependencies installed (`npm install`)
- [ ] TypeScript compiled (`npm run build`)

### Examples & Recipes

**Run multiple models in sequence:**
```bash
#!/bin/bash
for model in tinyllama mistral neural-chat; do
  echo "Testing $model..."
  npm run bench:real:easy -- --model $model --output bench-$model.json
done
```

**Compare agent modes:**
```bash
npm run bench:real:easy -- --agents planner --output modes-planner.json
npm run bench:real:easy -- --agents executor --output modes-executor.json
npm run bench:real:easy -- --agents verifier --output modes-verifier.json
npm run bench:real:easy -- --agents pipeline --output modes-pipeline.json
```

**Monitor in real-time:**
```bash
npm run bench:real -- --verbose | tee bench-$(date +%Y%m%d-%H%M%S).log
```

---

**Ready to benchmark?** Start with:
```bash
npm run bench:real:easy
```
