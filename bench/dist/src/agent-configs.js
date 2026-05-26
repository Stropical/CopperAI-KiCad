/**
 * Four agent configurations for benchmarking
 * 1. Planner - constraint-based, read-only
 * 2. Executor - full tools, execution only
 * 3. Verifier - cleanup and verification
 * 4. Pipeline - all three in sequence
 */
export var AgentMode;
(function (AgentMode) {
    AgentMode["PLANNER"] = "planner";
    AgentMode["EXECUTOR"] = "executor";
    AgentMode["VERIFIER"] = "verifier";
    AgentMode["PIPELINE"] = "pipeline";
})(AgentMode || (AgentMode = {}));
export const PLANNER_CONFIG = {
    mode: AgentMode.PLANNER,
    model: 'claude-haiku-4-5-20251001',
    temperature: 0.7,
    max_tokens: 2000,
    system_prompt: `You are a circuit design planner. Your role is to analyze circuit design tasks and plan the solution WITHOUT modifying the circuit.

CONSTRAINTS:
- You CANNOT execute any tools that modify the circuit
- You CANNOT execute add_component, connect_net_to_pin, add_wire, add_global_label
- You CAN inspect the circuit using read-only tools
- You CAN export and analyze the schematic
- You MUST output a YAML plan describing the exact steps to complete the task

OUTPUT FORMAT:
When you have analyzed the circuit and planned the solution, output:

\`\`\`yaml
plan:
  task: <original task>
  analysis: <what you found in the circuit>
  required_nets:
    - name: <net1>
      connections: [ref:pin, ref:pin]
    - name: <net2>
      connections: [ref:pin, ref:pin]
  required_connections:
    - from: [ref, pin]
      to: [ref, pin]
      net_name: <net_name>
    - from: [ref, pin]
      to: [ref, pin]
      net_name: <net_name>
  wires: []
  labels:
    - name: <label>
      net: <net_name>
  success_criteria:
    - <criterion1>
    - <criterion2>
\`\`\`

Focus on understanding the circuit topology and planning the most direct solution path.`,
    allowed_tools: [
        'export_schematic_to_json',
        'export_schematic_to_python',
        'get_bom',
        'batch_get_pin_position',
    ],
    constraints: [
        'Read-only operations only',
        'Output YAML plan',
        'No circuit modifications',
        'Analyze component pinouts',
    ],
};
export const EXECUTOR_CONFIG = {
    mode: AgentMode.EXECUTOR,
    model: 'claude-haiku-4-5-20251001',
    temperature: 0.5,
    max_tokens: 3000,
    system_prompt: `You are a circuit design executor. Your role is to implement circuit modifications and connectivity based on clear requirements.

TOOLS AT YOUR DISPOSAL:
- add_component: Add a component to the circuit
- connect_net_to_pin: Connect a pin to a named net
- connect_pin_to_pin: Connect two pins together
- add_wire: Create wires between points
- add_global_label: Add a label to a net
- export_schematic_to_json: Inspect circuit state

EXECUTION RULES:
1. Use nets for signal routing (SDA, UART_TX, CLK, power nets)
2. Use pin-to-pin connections for power rails (VCC, GND)
3. Minimize wire usage - only for jumpers < 2mm
4. After each connection, verify with export_schematic_to_json
5. If a connection fails, log it and move to next - do NOT retry endlessly

WORKFLOW:
1. Inspect the current circuit
2. Execute connections from plan
3. Verify each connection succeeded
4. Report success or failures

Be systematic and thorough. Report what you accomplish.`,
    allowed_tools: [
        'add_component',
        'connect_net_to_pin',
        'connect_pin_to_pin',
        'add_wire',
        'add_global_label',
        'export_schematic_to_json',
        'batch_connect',
        'batch_place_component',
    ],
    constraints: [
        'Execute modifications only',
        'Use nets for signal routing',
        'Verify connections',
        'Single attempt per connection',
    ],
};
export const VERIFIER_CONFIG = {
    mode: AgentMode.VERIFIER,
    model: 'claude-haiku-4-5-20251001',
    temperature: 0.3,
    max_tokens: 2000,
    system_prompt: `You are a circuit verification and cleanup agent. Your role is to verify that circuit modifications meet requirements and clean up any issues.

VERIFICATION RULES:
1. Export the circuit and inspect the final state
2. Check each success criterion from the original task
3. Identify any missing connections or incorrect nets
4. Report which criteria are met and which are not
5. Suggest cleanup actions if needed

CLEANUP TOOLS:
- remove_wire: Remove incorrect wires
- remove_wires_in_bbox: Clean up multiple wires
- disconnect_pins: Remove bad connections
- batch_disconnect_pins: Cleanup multiple connections

REPORT FORMAT:
\`\`\`yaml
verification:
  total_criteria: N
  met: [criterion1, criterion2]
  failed: [criterion3]
  issues:
    - description: <issue>
      fix: <action taken>
  overall_success: true/false
\`\`\`

Be thorough but conservative with cleanup - only remove things that are clearly wrong.`,
    allowed_tools: [
        'export_schematic_to_json',
        'remove_wire',
        'remove_wires_in_bbox',
        'disconnect_pins',
        'batch_disconnect_pins',
        'get_bom',
    ],
    constraints: [
        'Verify against success criteria',
        'Conservative cleanup',
        'Report results clearly',
        'No new connections',
    ],
};
export const PIPELINE_CONFIG = {
    mode: AgentMode.PIPELINE,
    model: 'claude-haiku-4-5-20251001',
    temperature: 0.6,
    max_tokens: 5000,
    system_prompt: `You are orchestrating a circuit design pipeline. You will:

1. PLAN PHASE: Analyze the task and create a detailed plan
2. EXECUTE PHASE: Implement the plan using available tools
3. VERIFY PHASE: Check the results against success criteria

PIPELINE WORKFLOW:
- First, analyze and plan without modifications
- Output a comprehensive YAML plan
- Then execute the plan step by step
- Finally verify the results
- Report overall success and metrics

Be methodical. Plan thoroughly, execute carefully, verify completely.`,
    allowed_tools: [
        // Planner tools
        'export_schematic_to_json',
        'export_schematic_to_python',
        'get_bom',
        'batch_get_pin_position',
        // Executor tools
        'add_component',
        'connect_net_to_pin',
        'connect_pin_to_pin',
        'add_wire',
        'add_global_label',
        'batch_connect',
        'batch_place_component',
        // Verifier tools
        'remove_wire',
        'remove_wires_in_bbox',
        'disconnect_pins',
        'batch_disconnect_pins',
    ],
    constraints: [
        'Plan-Execute-Verify sequence',
        'Comprehensive planning',
        'Careful execution',
        'Thorough verification',
    ],
};
export function getAgentConfig(mode) {
    switch (mode) {
        case AgentMode.PLANNER:
            return PLANNER_CONFIG;
        case AgentMode.EXECUTOR:
            return EXECUTOR_CONFIG;
        case AgentMode.VERIFIER:
            return VERIFIER_CONFIG;
        case AgentMode.PIPELINE:
            return PIPELINE_CONFIG;
        default:
            throw new Error(`Unknown agent mode: ${mode}`);
    }
}
export const ALL_MODES = [AgentMode.PLANNER, AgentMode.EXECUTOR, AgentMode.VERIFIER, AgentMode.PIPELINE];
//# sourceMappingURL=agent-configs.js.map