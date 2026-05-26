/**
 * Circuit renderer - Converts circuit state to SVG visualization
 */
import { CircuitState } from './mock-mcp-server.js';
export interface RenderOptions {
    width?: number;
    height?: number;
    showLabels?: boolean;
    showPins?: boolean;
    highlightNets?: string[];
}
/**
 * Render a circuit as SVG
 */
export declare function renderCircuitSVG(circuit: CircuitState, options?: RenderOptions): string;
/**
 * Render a simple text representation of the circuit
 */
export declare function renderCircuitText(circuit: CircuitState): string;
//# sourceMappingURL=circuit-renderer.d.ts.map