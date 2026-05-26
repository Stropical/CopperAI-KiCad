/**
 * HTML Report Generator - Creates interactive benchmark reports with circuit visualizations
 */
import { renderCircuitSVG } from './circuit-renderer.js';
/**
 * Generate HTML report from benchmark results
 */
export function generateHtmlReport(suite, circuits, options = {}) {
    const opts = {
        title: `Benchmark Results - ${suite.model}`,
        includeCircuits: true,
        includeDetailed: true,
        theme: 'light',
        ...options,
    };
    const bgColor = opts.theme === 'dark' ? '#1e1e1e' : '#ffffff';
    const textColor = opts.theme === 'dark' ? '#e0e0e0' : '#000000';
    const borderColor = opts.theme === 'dark' ? '#444' : '#ddd';
    const headerBg = opts.theme === 'dark' ? '#2d2d2d' : '#f5f5f5';
    let html = `<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>${opts.title}</title>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }

        body {
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            background-color: ${bgColor};
            color: ${textColor};
            line-height: 1.6;
            padding: 20px;
        }

        .container {
            max-width: 1400px;
            margin: 0 auto;
        }

        header {
            background-color: ${headerBg};
            padding: 30px;
            border-radius: 8px;
            margin-bottom: 30px;
            border: 1px solid ${borderColor};
        }

        h1 {
            font-size: 32px;
            margin-bottom: 10px;
        }

        .subtitle {
            color: #888;
            font-size: 14px;
        }

        .summary-grid {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
            gap: 15px;
            margin-top: 20px;
        }

        .summary-card {
            background-color: ${opts.theme === 'dark' ? '#3a3a3a' : '#f9f9f9'};
            padding: 15px;
            border-radius: 6px;
            border-left: 4px solid #4ecdc4;
        }

        .summary-card.success {
            border-left-color: #26de81;
        }

        .summary-card.warning {
            border-left-color: #f7b731;
        }

        .summary-card.error {
            border-left-color: #ff6b6b;
        }

        .metric-label {
            font-size: 12px;
            color: #888;
            text-transform: uppercase;
            margin-bottom: 5px;
        }

        .metric-value {
            font-size: 24px;
            font-weight: bold;
        }

        .agent-breakdown {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(250px, 1fr));
            gap: 15px;
            margin-bottom: 30px;
        }

        .agent-card {
            background-color: ${headerBg};
            padding: 20px;
            border-radius: 6px;
            border: 1px solid ${borderColor};
        }

        .agent-name {
            font-size: 16px;
            font-weight: bold;
            margin-bottom: 10px;
            text-transform: capitalize;
        }

        .success-bar {
            width: 100%;
            height: 24px;
            background-color: ${opts.theme === 'dark' ? '#444' : '#eee'};
            border-radius: 4px;
            overflow: hidden;
            margin-bottom: 8px;
        }

        .success-fill {
            height: 100%;
            background: linear-gradient(90deg, #26de81, #20c997);
            display: flex;
            align-items: center;
            justify-content: center;
            color: white;
            font-size: 12px;
            font-weight: bold;
        }

        .stats {
            font-size: 12px;
            color: #888;
            margin-top: 8px;
        }

        .results-section {
            background-color: ${headerBg};
            padding: 30px;
            border-radius: 8px;
            margin-bottom: 30px;
            border: 1px solid ${borderColor};
        }

        .results-section h2 {
            font-size: 20px;
            margin-bottom: 20px;
            padding-bottom: 10px;
            border-bottom: 2px solid ${borderColor};
        }

        .result-item {
            background-color: ${bgColor};
            padding: 15px;
            margin-bottom: 10px;
            border-radius: 6px;
            border-left: 4px solid #ddd;
            cursor: pointer;
            transition: all 0.3s ease;
        }

        .result-item:hover {
            transform: translateX(5px);
            border-left-color: #4ecdc4;
        }

        .result-item.success {
            border-left-color: #26de81;
        }

        .result-item.failed {
            border-left-color: #ff6b6b;
        }

        .result-header {
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-bottom: 8px;
        }

        .result-title {
            font-weight: bold;
        }

        .result-badge {
            display: inline-block;
            padding: 3px 8px;
            border-radius: 3px;
            font-size: 11px;
            font-weight: bold;
            background-color: #4ecdc4;
            color: white;
        }

        .result-badge.failed {
            background-color: #ff6b6b;
        }

        .result-details {
            font-size: 12px;
            color: #888;
            margin-top: 8px;
        }

        .circuit-container {
            background-color: ${headerBg};
            padding: 20px;
            border-radius: 6px;
            margin-top: 10px;
            display: none;
            border: 1px solid ${borderColor};
        }

        .circuit-container.active {
            display: block;
        }

        .circuit-svg {
            width: 100%;
            max-width: 1000px;
            margin: 0 auto;
            border: 1px solid ${borderColor};
            background-color: ${bgColor};
        }

        .circuit-text {
            background-color: ${bgColor};
            padding: 15px;
            border-radius: 4px;
            font-family: 'Courier New', monospace;
            font-size: 11px;
            overflow-x: auto;
            margin-top: 10px;
            white-space: pre;
        }

        table {
            width: 100%;
            border-collapse: collapse;
            margin-top: 15px;
        }

        th, td {
            padding: 10px;
            text-align: left;
            border-bottom: 1px solid ${borderColor};
        }

        th {
            background-color: ${opts.theme === 'dark' ? '#3a3a3a' : '#f5f5f5'};
            font-weight: bold;
        }

        tr:hover {
            background-color: ${opts.theme === 'dark' ? '#3a3a3a' : '#f9f9f9'};
        }

        footer {
            text-align: center;
            padding: 20px;
            color: #888;
            font-size: 12px;
        }
    </style>
</head>
<body>
    <div class="container">
        <header>
            <h1>${opts.title}</h1>
            <p class="subtitle">Generated on ${new Date().toLocaleString()}</p>

            <div class="summary-grid">
                <div class="summary-card success">
                    <div class="metric-label">Total Tests</div>
                    <div class="metric-value">${suite.summary.total_tests}</div>
                </div>
                <div class="summary-card success">
                    <div class="metric-label">Passed</div>
                    <div class="metric-value">${suite.summary.passed}</div>
                </div>
                <div class="summary-card ${suite.summary.failed > 0 ? 'error' : 'success'}">
                    <div class="metric-label">Failed</div>
                    <div class="metric-value">${suite.summary.failed}</div>
                </div>
                <div class="summary-card">
                    <div class="metric-label">Success Rate</div>
                    <div class="metric-value">${(suite.summary.avg_success_rate * 100).toFixed(1)}%</div>
                </div>
                <div class="summary-card">
                    <div class="metric-label">Avg Tokens</div>
                    <div class="metric-value">${suite.summary.avg_tokens_per_test}</div>
                </div>
                <div class="summary-card">
                    <div class="metric-label">Tool Success</div>
                    <div class="metric-value">${(suite.summary.tool_success_rate * 100).toFixed(1)}%</div>
                </div>
            </div>
        </header>

        <section class="results-section">
            <h2>🤖 Agent Mode Performance</h2>
            <div class="agent-breakdown">
`;
    // Agent breakdown
    for (const mode of suite.agent_modes) {
        const modeResults = suite.results.filter(r => r.agent_mode === mode);
        const modePassed = modeResults.filter(r => r.succeeded).length;
        const successRate = (modePassed / modeResults.length) * 100;
        const avgTokens = Math.round(modeResults.reduce((sum, r) => sum + r.tokens_used, 0) / modeResults.length);
        html += `                <div class="agent-card">
                    <div class="agent-name">${mode}</div>
                    <div class="success-bar">
                        <div class="success-fill" style="width: ${successRate}%">${modePassed}/${modeResults.length}</div>
                    </div>
                    <div class="stats">
                        <strong>${successRate.toFixed(1)}%</strong> success<br>
                        <strong>${avgTokens}</strong> avg tokens
                    </div>
                </div>
`;
    }
    html += `            </div>
        </section>

        <section class="results-section">
            <h2>📊 Test Results</h2>
            <table>
                <thead>
                    <tr>
                        <th>Scenario</th>
                        <th>Agent</th>
                        <th>Status</th>
                        <th>Criteria</th>
                        <th>Tokens</th>
                        <th>Duration</th>
                        <th>Tools</th>
                    </tr>
                </thead>
                <tbody>
`;
    // Results table
    for (const result of suite.results) {
        const statusIcon = result.succeeded ? '✅' : '❌';
        html += `                    <tr class="result-item ${result.succeeded ? 'success' : 'failed'}" onclick="toggleCircuit('${result.scenario_id}-${result.agent_mode}')">
                        <td><strong>${result.scenario_id}</strong></td>
                        <td>${result.agent_mode}</td>
                        <td>${statusIcon}</td>
                        <td>${result.criteria_met}/${result.criteria_total}</td>
                        <td>${result.tokens_used}</td>
                        <td>${result.duration_ms}ms</td>
                        <td>${result.tool_calls_successful}/${result.tool_calls_made}</td>
                    </tr>
`;
        // Circuit visualization (hidden by default)
        if (opts.includeCircuits) {
            const circuitKey = result.scenario_id.split('_').slice(0, 2).join('_');
            let circuitData = 'No circuit data';
            // Try to find matching circuit
            for (const [key, circuit] of circuits.entries()) {
                if (circuit.filename.includes(circuitKey)) {
                    const svg = renderCircuitSVG(circuit, { width: 1000, height: 600 });
                    circuitData = svg;
                    break;
                }
            }
            html += `                    <tr>
                        <td colspan="7">
                            <div class="circuit-container" id="circuit-${result.scenario_id}-${result.agent_mode}">
                                <h3>Circuit State: ${result.scenario_id}</h3>
                                <div class="circuit-svg">${circuitData}</div>
                            </div>
                        </td>
                    </tr>
`;
        }
    }
    html += `                </tbody>
            </table>
        </section>

        <footer>
            <p>🤖 Powered by Ollama + Langfuse | Circuit Benchmark Suite</p>
        </footer>
    </div>

    <script>
        function toggleCircuit(id) {
            const element = document.getElementById('circuit-' + id);
            if (element) {
                element.classList.toggle('active');
            }
        }
    </script>
</body>
</html>`;
    return html;
}
//# sourceMappingURL=html-report-generator.js.map