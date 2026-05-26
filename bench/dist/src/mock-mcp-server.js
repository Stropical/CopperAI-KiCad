/**
 * Mock KiCad MCP Server for benchmarking
 * Provides in-memory circuit state, instant responses, deterministic behavior
 */
export class MockKiCadMCP {
    constructor() {
        this.circuits = new Map();
        this.wireCounter = 0;
        this.labelCounter = 0;
    }
    /**
     * Initialize a circuit with basic schematic state
     */
    initCircuit(filename, components, nets) {
        const circuit = {
            filename,
            components: new Map(components.map(c => [c.ref, c])),
            nets: new Map(nets.map(n => [n.name, n])),
            wires: new Map(),
            labels: new Map(),
        };
        this.circuits.set(filename, circuit);
        return circuit;
    }
    /**
     * Export entire circuit to JSON representation
     */
    exportSchematicToJson(filename) {
        const circuit = this.circuits.get(filename);
        if (!circuit) {
            throw new Error(`Circuit not found: ${filename}`);
        }
        return {
            filename: circuit.filename,
            components: Array.from(circuit.components.values()).map(c => ({
                ref: c.ref,
                value: c.value,
                footprint: c.footprint,
                position: { x: c.x, y: c.y },
                pins: c.pins.map(p => ({
                    pin: p.pin,
                    name: p.name,
                    connected_to: p.connected_to || [],
                })),
                properties: Object.fromEntries(c.properties),
            })),
            nets: Array.from(circuit.nets.values()).map(n => ({
                name: n.name,
                pins: n.pins,
            })),
            wires: Array.from(circuit.wires.values()),
            labels: Array.from(circuit.labels.values()),
        };
    }
    /**
     * Add a component to the circuit
     */
    addComponent(filename, component) {
        const circuit = this.circuits.get(filename);
        if (!circuit) {
            throw new Error(`Circuit not found: ${filename}`);
        }
        if (circuit.components.has(component.ref)) {
            return { success: false, ref: component.ref }; // component already exists
        }
        circuit.components.set(component.ref, component);
        return { success: true, ref: component.ref };
    }
    /**
     * Connect a pin to a net (or create new net if it doesn't exist)
     */
    connectNetToPin(filename, netName, componentRef, pinNum) {
        const circuit = this.circuits.get(filename);
        if (!circuit) {
            return { success: false, reason: "Circuit not found" };
        }
        const component = circuit.components.get(componentRef);
        if (!component) {
            return { success: false, reason: `Component not found: ${componentRef}` };
        }
        const pin = component.pins.find(p => p.pin === pinNum);
        if (!pin) {
            return { success: false, reason: `Pin not found: ${componentRef}:${pinNum}` };
        }
        // Create net if doesn't exist
        if (!circuit.nets.has(netName)) {
            circuit.nets.set(netName, { name: netName, pins: [] });
        }
        const net = circuit.nets.get(netName);
        const pinId = `${componentRef}:${pinNum}`;
        if (!net.pins.includes(pinId)) {
            net.pins.push(pinId);
        }
        if (!pin.connected_to) {
            pin.connected_to = [];
        }
        if (!pin.connected_to.includes(netName)) {
            pin.connected_to.push(netName);
        }
        return { success: true };
    }
    /**
     * Connect two pins together directly
     */
    connectPinToPin(filename, ref1, pin1, ref2, pin2) {
        const circuit = this.circuits.get(filename);
        if (!circuit) {
            return { success: false, reason: "Circuit not found" };
        }
        const comp1 = circuit.components.get(ref1);
        const comp2 = circuit.components.get(ref2);
        if (!comp1 || !comp2) {
            return { success: false, reason: "Component not found" };
        }
        const p1 = comp1.pins.find(p => p.pin === pin1);
        const p2 = comp2.pins.find(p => p.pin === pin2);
        if (!p1 || !p2) {
            return { success: false, reason: "Pin not found" };
        }
        // Create implicit net
        const netName = `${ref1}_${pin1}_to_${ref2}_${pin2}`;
        return this.connectNetToPin(filename, netName, ref1, pin1) &&
            this.connectNetToPin(filename, netName, ref2, pin2)
            ? { success: true }
            : { success: false, reason: "Could not connect pins" };
    }
    /**
     * Add a wire between two points
     */
    addWire(filename, startX, startY, endX, endY) {
        const circuit = this.circuits.get(filename);
        if (!circuit) {
            throw new Error(`Circuit not found: ${filename}`);
        }
        const wireId = `wire_${this.wireCounter++}`;
        circuit.wires.set(wireId, {
            from: `${startX},${startY}`,
            to: `${endX},${endY}`,
        });
        return { success: true, wire_id: wireId };
    }
    /**
     * Add a global label to a net
     */
    addGlobalLabel(filename, netName, x, y) {
        const circuit = this.circuits.get(filename);
        if (!circuit) {
            throw new Error(`Circuit not found: ${filename}`);
        }
        const labelId = `label_${this.labelCounter++}`;
        circuit.labels.set(labelId, { x, y, net: netName });
        return { success: true, label_id: labelId };
    }
    /**
     * Get component by reference
     */
    getComponent(filename, ref) {
        return this.circuits.get(filename)?.components.get(ref);
    }
    /**
     * Check if a net has all required pins connected
     */
    isNetComplete(filename, netName) {
        const circuit = this.circuits.get(filename);
        if (!circuit)
            return false;
        const net = circuit.nets.get(netName);
        if (!net)
            return false;
        return net.pins.length > 0;
    }
    /**
     * Get all nets
     */
    getNets(filename) {
        const circuit = this.circuits.get(filename);
        if (!circuit)
            return [];
        return Array.from(circuit.nets.values());
    }
    /**
     * Get circuit state for inspection
     */
    getCircuitState(filename) {
        return this.circuits.get(filename);
    }
    /**
     * Reset circuit to initial state
     */
    reset(filename) {
        this.circuits.delete(filename);
        this.wireCounter = 0;
        this.labelCounter = 0;
    }
}
//# sourceMappingURL=mock-mcp-server.js.map