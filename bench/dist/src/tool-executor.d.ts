/**
 * Tool executor - Parses and executes tool calls against mock MCP server
 */
import { MockKiCadMCP } from './mock-mcp-server.js';
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
export declare function parseToolCalls(response: string): ToolCall[];
/**
 * Execute tool calls against mock MCP server
 */
export declare class ToolExecutor {
    private mcp;
    constructor(mcp: MockKiCadMCP);
    execute(circuit: string, toolCall: ToolCall): Promise<ToolResult>;
    private addComponent;
    private generateDefaultPins;
    private batchConnect;
    private disconnectPins;
    private getBOM;
    private getBatchPinPositions;
}
//# sourceMappingURL=tool-executor.d.ts.map