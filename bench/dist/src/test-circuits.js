/**
 * Test circuit fixtures based on real KiCad projects
 * Simplified but realistic circuit designs for benchmarking
 */
/**
 * LED Blink Circuit - Simple
 * MCU + LED + current-limiting resistor + power
 */
export function createLedBlinkCircuit(mcp) {
    const filename = 'led_blink.kicad_sch';
    const components = [
        // ATmega328 microcontroller
        {
            ref: 'U1',
            value: 'ATmega328P',
            footprint: 'DIP-28_W7.62mm',
            x: 50000,
            y: 50000,
            pins: [
                { pin: '1', name: 'PD0', connected_to: [] },
                { pin: '2', name: 'PD1', connected_to: [] },
                { pin: '19', name: 'GND', connected_to: [] },
                { pin: '20', name: 'AVCC', connected_to: [] },
                { pin: '7', name: 'VCC', connected_to: [] },
            ],
            properties: new Map(),
        },
        // LED
        {
            ref: 'LED1',
            value: 'RED_LED',
            footprint: 'LED_0805',
            x: 100000,
            y: 50000,
            pins: [
                { pin: '+', name: 'ANODE', connected_to: [] },
                { pin: '-', name: 'CATHODE', connected_to: [] },
            ],
            properties: new Map(),
        },
        // Current-limiting resistor
        {
            ref: 'R1',
            value: '220Ω',
            footprint: 'R_0805',
            x: 75000,
            y: 50000,
            pins: [
                { pin: '1', name: '1', connected_to: [] },
                { pin: '2', name: '2', connected_to: [] },
            ],
            properties: new Map(),
        },
        // Power supply
        {
            ref: 'J1',
            value: 'USB_POWER',
            footprint: 'USB_Micro_B',
            x: 20000,
            y: 50000,
            pins: [
                { pin: 'VCC', name: '5V', connected_to: [] },
                { pin: 'GND', name: 'GND', connected_to: [] },
            ],
            properties: new Map(),
        },
    ];
    const nets = [
        { name: 'VCC', pins: [] },
        { name: 'GND', pins: [] },
        { name: 'GPIO_PD0', pins: [] },
    ];
    mcp.initCircuit(filename, components, nets);
    return filename;
}
/**
 * Voltage Regulator Circuit - Moderate
 * 12V input, output 5V and 3.3V with filtering capacitors
 */
export function createVoltageRegulatorCircuit(mcp) {
    const filename = 'voltage_regulator.kicad_sch';
    const components = [
        // Input connector
        {
            ref: 'J1',
            value: 'DC_JACK',
            footprint: 'DC_Jack_5.5mm',
            x: 20000,
            y: 60000,
            pins: [
                { pin: '+', name: '+12V', connected_to: [] },
                { pin: '-', name: 'GND', connected_to: [] },
            ],
            properties: new Map(),
        },
        // 5V regulator
        {
            ref: 'U1',
            value: 'LM7805',
            footprint: 'TO-220',
            x: 60000,
            y: 70000,
            pins: [
                { pin: '1', name: 'IN', connected_to: [] },
                { pin: '2', name: 'GND', connected_to: [] },
                { pin: '3', name: 'OUT', connected_to: [] },
            ],
            properties: new Map(),
        },
        // 3.3V regulator
        {
            ref: 'U2',
            value: 'AMS1117-3.3',
            footprint: 'SOT-223',
            x: 60000,
            y: 40000,
            pins: [
                { pin: '1', name: 'IN', connected_to: [] },
                { pin: '2', name: 'GND', connected_to: [] },
                { pin: '3', name: 'OUT', connected_to: [] },
            ],
            properties: new Map(),
        },
        // Input capacitor
        {
            ref: 'C1',
            value: '10µF',
            footprint: 'C_0805',
            x: 45000,
            y: 60000,
            pins: [
                { pin: '+', name: '+', connected_to: [] },
                { pin: '-', name: '-', connected_to: [] },
            ],
            properties: new Map(),
        },
        // 5V output capacitor
        {
            ref: 'C2',
            value: '10µF',
            footprint: 'C_0805',
            x: 75000,
            y: 70000,
            pins: [
                { pin: '+', name: '+', connected_to: [] },
                { pin: '-', name: '-', connected_to: [] },
            ],
            properties: new Map(),
        },
        // 3.3V output capacitor
        {
            ref: 'C3',
            value: '10µF',
            footprint: 'C_0805',
            x: 75000,
            y: 40000,
            pins: [
                { pin: '+', name: '+', connected_to: [] },
                { pin: '-', name: '-', connected_to: [] },
            ],
            properties: new Map(),
        },
    ];
    const nets = [
        { name: 'VIN_12V', pins: [] },
        { name: 'VCC_5V', pins: [] },
        { name: 'VCC_3V3', pins: [] },
        { name: 'GND', pins: [] },
    ];
    mcp.initCircuit(filename, components, nets);
    return filename;
}
/**
 * UART Interface Circuit - Moderate
 * RS232 transceiver connecting to MCU
 */
export function createUartInterfaceCircuit(mcp) {
    const filename = 'uart_interface.kicad_sch';
    const components = [
        // Microcontroller
        {
            ref: 'U1',
            value: 'STM32F103',
            footprint: 'LQFP-48',
            x: 40000,
            y: 50000,
            pins: [
                { pin: '1', name: 'VCC', connected_to: [] },
                { pin: '24', name: 'GND', connected_to: [] },
                { pin: '10', name: 'USART1_TX', connected_to: [] },
                { pin: '11', name: 'USART1_RX', connected_to: [] },
            ],
            properties: new Map(),
        },
        // UART transceiver (MAX232)
        {
            ref: 'U2',
            value: 'MAX232',
            footprint: 'DIP-16',
            x: 100000,
            y: 50000,
            pins: [
                { pin: '1', name: 'C1+', connected_to: [] },
                { pin: '2', name: 'V+', connected_to: [] },
                { pin: '3', name: 'C1-', connected_to: [] },
                { pin: '4', name: 'C2+', connected_to: [] },
                { pin: '5', name: 'C2-', connected_to: [] },
                { pin: '6', name: 'GND', connected_to: [] },
                { pin: '11', name: 'T1IN', connected_to: [] },
                { pin: '12', name: 'T1OUT', connected_to: [] },
                { pin: '13', name: 'R1IN', connected_to: [] },
                { pin: '14', name: 'R1OUT', connected_to: [] },
                { pin: '15', name: 'VCC', connected_to: [] },
                { pin: '16', name: 'VCC', connected_to: [] },
            ],
            properties: new Map(),
        },
        // Charge pump capacitors
        {
            ref: 'C1',
            value: '1µF',
            footprint: 'C_0603',
            x: 70000,
            y: 60000,
            pins: [
                { pin: '+', name: '+', connected_to: [] },
                { pin: '-', name: '-', connected_to: [] },
            ],
            properties: new Map(),
        },
        {
            ref: 'C2',
            value: '1µF',
            footprint: 'C_0603',
            x: 70000,
            y: 40000,
            pins: [
                { pin: '+', name: '+', connected_to: [] },
                { pin: '-', name: '-', connected_to: [] },
            ],
            properties: new Map(),
        },
        // RS232 connector
        {
            ref: 'J1',
            value: 'DB9_RS232',
            footprint: 'DSUB_9',
            x: 150000,
            y: 50000,
            pins: [
                { pin: '2', name: 'RX', connected_to: [] },
                { pin: '3', name: 'TX', connected_to: [] },
                { pin: '5', name: 'GND', connected_to: [] },
            ],
            properties: new Map(),
        },
    ];
    const nets = [
        { name: 'VCC', pins: [] },
        { name: 'GND', pins: [] },
        { name: 'USART1_TX', pins: [] },
        { name: 'USART1_RX', pins: [] },
    ];
    mcp.initCircuit(filename, components, nets);
    return filename;
}
/**
 * LED Matrix Driver - Complex
 * Using shift registers to control LED matrix
 */
export function createLedMatrixCircuit(mcp) {
    const filename = 'led_matrix.kicad_sch';
    const components = [
        // Microcontroller
        {
            ref: 'U1',
            value: 'ATmega328P',
            footprint: 'DIP-28',
            x: 30000,
            y: 50000,
            pins: [
                { pin: '7', name: 'VCC', connected_to: [] },
                { pin: '8', name: 'GND', connected_to: [] },
                { pin: '14', name: 'PB0_CLK', connected_to: [] },
                { pin: '15', name: 'PB1_LATCH', connected_to: [] },
                { pin: '16', name: 'PB2_DATA', connected_to: [] },
            ],
            properties: new Map(),
        },
        // Shift register 1
        {
            ref: 'U2',
            value: '74HC595',
            footprint: 'DIP-16',
            x: 80000,
            y: 60000,
            pins: [
                { pin: '14', name: 'VCC', connected_to: [] },
                { pin: '7', name: 'GND', connected_to: [] },
                { pin: '11', name: 'CLK', connected_to: [] },
                { pin: '12', name: 'LATCH', connected_to: [] },
                { pin: '13', name: 'SER_IN', connected_to: [] },
                { pin: '9', name: 'SER_OUT', connected_to: [] },
            ],
            properties: new Map(),
        },
        // Shift register 2
        {
            ref: 'U3',
            value: '74HC595',
            footprint: 'DIP-16',
            x: 80000,
            y: 40000,
            pins: [
                { pin: '14', name: 'VCC', connected_to: [] },
                { pin: '7', name: 'GND', connected_to: [] },
                { pin: '11', name: 'CLK', connected_to: [] },
                { pin: '12', name: 'LATCH', connected_to: [] },
                { pin: '13', name: 'SER_IN', connected_to: [] },
                { pin: '9', name: 'SER_OUT', connected_to: [] },
            ],
            properties: new Map(),
        },
        // Decoupling capacitors
        {
            ref: 'C1',
            value: '100nF',
            footprint: 'C_0603',
            x: 50000,
            y: 50000,
            pins: [
                { pin: '+', name: '+', connected_to: [] },
                { pin: '-', name: '-', connected_to: [] },
            ],
            properties: new Map(),
        },
        {
            ref: 'C2',
            value: '100nF',
            footprint: 'C_0603',
            x: 100000,
            y: 60000,
            pins: [
                { pin: '+', name: '+', connected_to: [] },
                { pin: '-', name: '-', connected_to: [] },
            ],
            properties: new Map(),
        },
        {
            ref: 'C3',
            value: '100nF',
            footprint: 'C_0603',
            x: 100000,
            y: 40000,
            pins: [
                { pin: '+', name: '+', connected_to: [] },
                { pin: '-', name: '-', connected_to: [] },
            ],
            properties: new Map(),
        },
    ];
    const nets = [
        { name: 'VCC', pins: [] },
        { name: 'GND', pins: [] },
        { name: 'CLK', pins: [] },
        { name: 'LATCH', pins: [] },
        { name: 'DATA_IN', pins: [] },
    ];
    mcp.initCircuit(filename, components, nets);
    return filename;
}
//# sourceMappingURL=test-circuits.js.map