/**
 * Tool executor - Parses and executes tool calls against mock MCP server
 */

import { MockKiCadMCP, Component, Pin } from './mock-mcp-server.js';

export interface ToolCall {
  name: string;
  args: Record<string, any>;
}

export interface ToolResult {
  tool: string;
  success: boolean;
  result?: any;
  error?: string;
}

/**
 * Parse tool calls from agent response
 * Looks for XML-like tool calls or JSON
 */
export function parseToolCalls(response: string): ToolCall[] {
  const toolCalls: ToolCall[] = [];

  // Pattern 1: XML-style tool calls
  const xmlPattern = /<tool name="([^"]+)"\s*>([\s\S]*?)<\/tool>/g;
  let match;

  while ((match = xmlPattern.exec(response)) !== null) {
    const [, name, content] = match;
    try {
      const args = JSON.parse(content);
      toolCalls.push({ name, args });
    } catch {
      // Try parsing as key=value pairs
      const params: Record<string, any> = {};
      content.split('\n').forEach(line => {
        const [key, ...valueParts] = line.split('=');
        if (key && valueParts.length > 0) {
          params[key.trim()] = valueParts.join('=').trim().replace(/^["']|["']$/g, '');
        }
      });
      if (Object.keys(params).length > 0) {
        toolCalls.push({ name, args: params });
      }
    }
  }

  // Pattern 2: JSON tool calls
  const jsonPattern = /```json\s*\{[\s\S]*?\}\s*```/g;
  while ((match = jsonPattern.exec(response)) !== null) {
    try {
      const json = JSON.parse(match[0].replace(/```json\s*/, '').replace(/\s*```/, ''));
      if (json.tool) {
        toolCalls.push({ name: json.tool, args: json.args || {} });
      }
    } catch {
      // Skip invalid JSON
    }
  }

  return toolCalls;
}

/**
 * Execute tool calls against mock MCP server
 */
export class ToolExecutor {
  constructor(private mcp: MockKiCadMCP) {}

  async execute(circuit: string, toolCall: ToolCall): Promise<ToolResult> {
    const { name, args } = toolCall;
    const startTime = Date.now();

    try {
      let result: any;

      switch (name) {
        // Read-only tools
        case 'export_schematic_to_json':
          result = this.mcp.exportSchematicToJson(args.filename || circuit);
          break;

        case 'get_bom':
          result = this.getBOM(circuit);
          break;

        case 'batch_get_pin_position':
          result = this.getBatchPinPositions(circuit, args.references || []);
          break;

        // Component modification
        case 'add_component':
          result = this.addComponent(
            circuit,
            args.ref,
            args.value,
            args.footprint,
            args.x,
            args.y
          );
          break;

        // Net connectivity
        case 'connect_net_to_pin':
          result = this.mcp.connectNetToPin(
            circuit,
            args.net_name,
            args.component_ref,
            args.pin_num
          );
          break;

        case 'connect_pin_to_pin':
          result = this.mcp.connectPinToPin(
            circuit,
            args.ref1,
            args.pin1,
            args.ref2,
            args.pin2
          );
          break;

        case 'batch_connect':
          result = this.batchConnect(circuit, args.connections || []);
          break;

        // Wiring
        case 'add_wire':
          result = this.mcp.addWire(
            circuit,
            args.start_x,
            args.start_y,
            args.end_x,
            args.end_y
          );
          break;

        case 'add_global_label':
          result = this.mcp.addGlobalLabel(circuit, args.net_name, args.x, args.y);
          break;

        // Cleanup tools
        case 'remove_wire':
          result = { success: true, wire_id: args.wire_id };
          break;

        case 'disconnect_pins':
          result = this.disconnectPins(
            circuit,
            args.component_ref,
            args.pin_nums || []
          );
          break;

        default:
          return {
            tool: name,
            success: false,
            error: `Unknown tool: ${name}`,
          };
      }

      return {
        tool: name,
        success: result?.success !== false,
        result,
      };
    } catch (error) {
      return {
        tool: name,
        success: false,
        error: error instanceof Error ? error.message : String(error),
      };
    }
  }

  private addComponent(
    circuit: string,
    ref: string,
    value: string,
    footprint: string,
    x: number,
    y: number
  ) {
    const component: Component = {
      ref,
      value,
      footprint,
      x: x || 0,
      y: y || 0,
      pins: this.generateDefaultPins(value),
      properties: new Map(),
    };

    return this.mcp.addComponent(circuit, component);
  }

  private generateDefaultPins(value: string): Pin[] {
    // Generate default pins based on component type
    if (value.includes('328')) {
      // ATmega328
      return [
        { pin: '1', name: 'PD0', connected_to: [] },
        { pin: '2', name: 'PD1', connected_to: [] },
        { pin: '7', name: 'VCC', connected_to: [] },
        { pin: '8', name: 'GND', connected_to: [] },
      ];
    } else if (value.includes('LED') || value.includes('595')) {
      // Generic 2-pin or 16-pin component
      return Array.from({ length: 16 }, (_, i) => ({
        pin: String(i + 1),
        name: `pin${i + 1}`,
        connected_to: [],
      }));
    }

    // Default: 2 pins
    return [
      { pin: '1', name: 'pin1', connected_to: [] },
      { pin: '2', name: 'pin2', connected_to: [] },
    ];
  }

  private batchConnect(
    circuit: string,
    connections: Array<{
      net: string;
      component_ref: string;
      pin_num: string;
    }>
  ) {
    const results = connections.map(conn =>
      this.mcp.connectNetToPin(circuit, conn.net, conn.component_ref, conn.pin_num)
    );

    return {
      success: results.every(r => r.success),
      connected: results.filter(r => r.success).length,
      failed: results.filter(r => !r.success).length,
    };
  }

  private disconnectPins(circuit: string, componentRef: string, pinNums: string[]) {
    // This is a mock - in reality would remove connections
    return {
      success: true,
      disconnected: pinNums.length,
    };
  }

  private getBOM(circuit: string) {
    const circuitState = this.mcp.getCircuitState(circuit);
    if (!circuitState) {
      return { components: [] };
    }

    const components = Array.from(circuitState.components.values()).map(c => ({
      ref: c.ref,
      value: c.value,
      footprint: c.footprint,
      quantity: 1,
    }));

    return {
      components,
      total: components.length,
    };
  }

  private getBatchPinPositions(circuit: string, references: string[]) {
    const circuitState = this.mcp.getCircuitState(circuit);
    if (!circuitState) {
      return { positions: {} };
    }

    const positions: Record<string, any> = {};

    for (const ref of references) {
      const comp = circuitState.components.get(ref);
      if (comp) {
        positions[ref] = {
          x: comp.x,
          y: comp.y,
          pins: comp.pins.map(p => ({ pin: p.pin, x: comp.x + 1000, y: comp.y })),
        };
      }
    }

    return { positions };
  }
}
