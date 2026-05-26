/**
 * Mock KiCad MCP Server for benchmarking
 * Provides in-memory circuit state, instant responses, deterministic behavior
 */
export interface Pin {
    pin: string;
    name: string;
    connected_to?: string[];
}
export interface Component {
    ref: string;
    value: string;
    footprint: string;
    pins: Pin[];
    properties: Map<string, string>;
    x: number;
    y: number;
}
export interface Net {
    name: string;
    pins: string[];
}
export interface CircuitState {
    filename: string;
    components: Map<string, Component>;
    nets: Map<string, Net>;
    wires: Map<string, {
        from: string;
        to: string;
    }>;
    labels: Map<string, {
        x: number;
        y: number;
        net: string;
    }>;
}
export declare class MockKiCadMCP {
    private circuits;
    private wireCounter;
    private labelCounter;
    /**
     * Initialize a circuit with basic schematic state
     */
    initCircuit(filename: string, components: Component[], nets: Net[]): CircuitState;
    /**
     * Export entire circuit to JSON representation
     */
    exportSchematicToJson(filename: string): any;
    /**
     * Add a component to the circuit
     */
    addComponent(filename: string, component: Component): {
        success: boolean;
        ref: string;
    };
    /**
     * Connect a pin to a net (or create new net if it doesn't exist)
     */
    connectNetToPin(filename: string, netName: string, componentRef: string, pinNum: string): {
        success: boolean;
        reason?: string;
    };
    /**
     * Connect two pins together directly
     */
    connectPinToPin(filename: string, ref1: string, pin1: string, ref2: string, pin2: string): {
        success: boolean;
        reason?: string;
    };
    /**
     * Add a wire between two points
     */
    addWire(filename: string, startX: number, startY: number, endX: number, endY: number): {
        success: boolean;
        wire_id: string;
    };
    /**
     * Add a global label to a net
     */
    addGlobalLabel(filename: string, netName: string, x: number, y: number): {
        success: boolean;
        label_id?: string;
    };
    /**
     * Get component by reference
     */
    getComponent(filename: string, ref: string): Component | undefined;
    /**
     * Check if a net has all required pins connected
     */
    isNetComplete(filename: string, netName: string): boolean;
    /**
     * Get all nets
     */
    getNets(filename: string): Net[];
    /**
     * Get circuit state for inspection
     */
    getCircuitState(filename: string): CircuitState | undefined;
    /**
     * Reset circuit to initial state
     */
    reset(filename: string): void;
}
//# sourceMappingURL=mock-mcp-server.d.ts.map