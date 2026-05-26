/**
 * Test circuit fixtures based on real KiCad projects
 * Simplified but realistic circuit designs for benchmarking
 */
import { MockKiCadMCP } from './mock-mcp-server';
/**
 * LED Blink Circuit - Simple
 * MCU + LED + current-limiting resistor + power
 */
export declare function createLedBlinkCircuit(mcp: MockKiCadMCP): string;
/**
 * Voltage Regulator Circuit - Moderate
 * 12V input, output 5V and 3.3V with filtering capacitors
 */
export declare function createVoltageRegulatorCircuit(mcp: MockKiCadMCP): string;
/**
 * UART Interface Circuit - Moderate
 * RS232 transceiver connecting to MCU
 */
export declare function createUartInterfaceCircuit(mcp: MockKiCadMCP): string;
/**
 * LED Matrix Driver - Complex
 * Using shift registers to control LED matrix
 */
export declare function createLedMatrixCircuit(mcp: MockKiCadMCP): string;
//# sourceMappingURL=test-circuits.d.ts.map