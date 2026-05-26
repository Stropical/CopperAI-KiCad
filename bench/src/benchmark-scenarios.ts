/**
 * Benchmark scenarios - circuit design tasks for agents
 */

export interface SuccessCriteria {
  name: string;
  check: (result: any) => boolean;
}

export interface BenchmarkScenario {
  id: string;
  title: string;
  difficulty: 'easy' | 'moderate' | 'hard';
  circuit: string;
  task: string;
  description: string;
  success_criteria: SuccessCriteria[];
  expected_tokens?: number;  // approximate baseline
  expected_duration_ms?: number;
}

export const benchmarkScenarios: BenchmarkScenario[] = [
  // ========== EASY SCENARIOS ==========
  {
    id: 'led_blink_power',
    title: 'Connect Power to LED Circuit',
    difficulty: 'easy',
    circuit: 'led_blink.kicad_sch',
    task:
      'Complete the LED blink circuit by connecting the power supply (J1) to VCC and GND nets. Both 5V and GND should reach the microcontroller (U1).',
    description:
      'Basic power distribution. Agent should connect J1 VCC → U1 pin 7 (VCC), and J1 GND → U1 pin 19 (GND).',
    success_criteria: [
      {
        name: 'J1 VCC connected to VCC net',
        check: (result: any) => result.nets?.VCC?.pins?.includes('J1:VCC'),
      },
      {
        name: 'J1 GND connected to GND net',
        check: (result: any) => result.nets?.GND?.pins?.includes('J1:GND'),
      },
      {
        name: 'U1 pin 7 on VCC net',
        check: (result: any) => result.nets?.VCC?.pins?.includes('U1:7'),
      },
      {
        name: 'U1 pin 19 on GND net',
        check: (result: any) => result.nets?.GND?.pins?.includes('U1:19'),
      },
    ],
    expected_tokens: 800,
    expected_duration_ms: 2000,
  },

  {
    id: 'led_blink_gpio',
    title: 'Connect LED to GPIO',
    difficulty: 'easy',
    circuit: 'led_blink.kicad_sch',
    task:
      'Connect the LED circuit to microcontroller GPIO. Connect U1 pin 1 (PD0) to one side of R1, then R1 to the LED anode, and LED cathode to GND.',
    description: 'GPIO pin routing. Agent should create GPIO_PD0 net and wire the LED chain.',
    success_criteria: [
      {
        name: 'GPIO_PD0 net exists',
        check: (result: any) => result.nets?.GPIO_PD0 !== undefined,
      },
      {
        name: 'U1 pin 1 on GPIO_PD0',
        check: (result: any) => result.nets?.GPIO_PD0?.pins?.includes('U1:1'),
      },
      {
        name: 'R1 connected to GPIO_PD0',
        check: (result: any) => result.nets?.GPIO_PD0?.pins?.includes('R1:1'),
      },
      {
        name: 'LED cathode on GND',
        check: (result: any) => result.nets?.GND?.pins?.includes('LED1:-'),
      },
    ],
    expected_tokens: 1200,
    expected_duration_ms: 3000,
  },

  {
    id: 'voltage_reg_input',
    title: 'Connect Regulator Input',
    difficulty: 'easy',
    circuit: 'voltage_regulator.kicad_sch',
    task:
      'Connect the DC jack (J1) input to the voltage regulators. J1 positive should connect through C1 to both U1 and U2 inputs. J1 GND connects to the GND net.',
    description: 'Input distribution. Agent should create VIN_12V net and distribute it.',
    success_criteria: [
      {
        name: 'VIN_12V net created',
        check: (result: any) => result.nets?.VIN_12V !== undefined,
      },
      {
        name: 'J1 VCC on VIN_12V',
        check: (result: any) => result.nets?.VIN_12V?.pins?.includes('J1:+'),
      },
      {
        name: 'U1 input on VIN_12V',
        check: (result: any) => result.nets?.VIN_12V?.pins?.includes('U1:1'),
      },
      {
        name: 'U2 input on VIN_12V',
        check: (result: any) => result.nets?.VIN_12V?.pins?.includes('U2:1'),
      },
    ],
    expected_tokens: 1000,
    expected_duration_ms: 2500,
  },

  // ========== MODERATE SCENARIOS ==========
  {
    id: 'voltage_reg_complete',
    title: 'Complete Voltage Regulator',
    difficulty: 'moderate',
    circuit: 'voltage_regulator.kicad_sch',
    task:
      'Wire a complete 12V→5V/3.3V power supply. Connect input (J1) through decoupling cap C1 to both regulators. Connect regulator outputs to their respective output caps and label VCC_5V and VCC_3V3 nets. All grounds should connect to GND net.',
    description:
      'Multi-net power distribution with decoupling. Agent must understand capacitor placement and parallel regulator topology.',
    success_criteria: [
      {
        name: 'VCC_5V net created',
        check: (result: any) => result.nets?.VCC_5V !== undefined,
      },
      {
        name: 'VCC_3V3 net created',
        check: (result: any) => result.nets?.VCC_3V3 !== undefined,
      },
      {
        name: 'U1 output on VCC_5V',
        check: (result: any) => result.nets?.VCC_5V?.pins?.includes('U1:3'),
      },
      {
        name: 'U2 output on VCC_3V3',
        check: (result: any) => result.nets?.VCC_3V3?.pins?.includes('U2:3'),
      },
      {
        name: 'C2 on VCC_5V output',
        check: (result: any) =>
          result.nets?.VCC_5V?.pins?.includes('C2:+') && result.nets?.GND?.pins?.includes('C2:-'),
      },
      {
        name: 'C3 on VCC_3V3 output',
        check: (result: any) =>
          result.nets?.VCC_3V3?.pins?.includes('C3:+') && result.nets?.GND?.pins?.includes('C3:-'),
      },
    ],
    expected_tokens: 2500,
    expected_duration_ms: 5000,
  },

  {
    id: 'uart_mcu_side',
    title: 'Wire UART Microcontroller Side',
    difficulty: 'moderate',
    circuit: 'uart_interface.kicad_sch',
    task:
      'Connect the microcontroller UART pins to the MAX232 transceiver. U1 USART1_TX (pin 10) should connect to U2 T1IN, and U1 USART1_RX (pin 11) should connect to U2 R1OUT. Both should have distinct nets labeled USART1_TX and USART1_RX.',
    description:
      'MCU-to-transceiver wiring. Requires understanding pin function names and creating appropriate signal nets.',
    success_criteria: [
      {
        name: 'USART1_TX net created',
        check: (result: any) => result.nets?.USART1_TX !== undefined,
      },
      {
        name: 'USART1_RX net created',
        check: (result: any) => result.nets?.USART1_RX !== undefined,
      },
      {
        name: 'U1 pin 10 on USART1_TX',
        check: (result: any) => result.nets?.USART1_TX?.pins?.includes('U1:10'),
      },
      {
        name: 'U2 T1IN on USART1_TX',
        check: (result: any) => result.nets?.USART1_TX?.pins?.includes('U2:11'),
      },
      {
        name: 'U1 pin 11 on USART1_RX',
        check: (result: any) => result.nets?.USART1_RX?.pins?.includes('U1:11'),
      },
      {
        name: 'U2 R1OUT on USART1_RX',
        check: (result: any) => result.nets?.USART1_RX?.pins?.includes('U2:14'),
      },
    ],
    expected_tokens: 2000,
    expected_duration_ms: 4500,
  },

  // ========== HARD SCENARIOS ==========
  {
    id: 'uart_complete',
    title: 'Complete UART Interface',
    difficulty: 'hard',
    circuit: 'uart_interface.kicad_sch',
    task:
      'Design a complete RS232 UART interface. Wire U1 (MCU) TX/RX to U2 (MAX232) inputs, connect charge pump caps (C1, C2) between U2 pins, power U2 from VCC, and connect the RS232 output (J1) to U2 outputs. Use USART1_TX, USART1_RX nets on MCU side and RS232_TX, RS232_RX on the connector side.',
    description:
      'Complex transceiver circuit with charge pump and proper signal isolation. Requires knowledge of transceiver pinout and capacitor placement.',
    success_criteria: [
      {
        name: 'MCU UART nets created',
        check: (result: any) =>
          result.nets?.USART1_TX !== undefined && result.nets?.USART1_RX !== undefined,
      },
      {
        name: 'RS232 nets created',
        check: (result: any) =>
          result.nets?.RS232_TX !== undefined && result.nets?.RS232_RX !== undefined,
      },
      {
        name: 'U2 powered from VCC',
        check: (result: any) =>
          result.nets?.VCC?.pins?.includes('U2:15') && result.nets?.VCC?.pins?.includes('U2:16'),
      },
      {
        name: 'U2 GND connected',
        check: (result: any) => result.nets?.GND?.pins?.includes('U2:6'),
      },
      {
        name: 'J1 connected to RS232 nets',
        check: (result: any) =>
          result.nets?.RS232_TX?.pins?.includes('J1:3') && result.nets?.RS232_RX?.pins?.includes('J1:2'),
      },
    ],
    expected_tokens: 4000,
    expected_duration_ms: 8000,
  },

  {
    id: 'matrix_daisy_chain',
    title: 'Wire LED Matrix Daisy Chain',
    difficulty: 'hard',
    circuit: 'led_matrix.kicad_sch',
    task:
      'Wire a daisy-chained shift register circuit. Connect U1 CLK output to both U2 and U3 CLK inputs. Connect U1 LATCH to both U2 and U3 LATCH inputs. Connect U1 DATA to U2 serial input, then daisy-chain U2 serial output to U3 serial input. Power both shift registers and add decoupling caps C2 and C3 close to their VCC pins.',
    description:
      'Complex digital logic circuit with daisy chaining. Requires understanding of serial data flow and clock distribution.',
    success_criteria: [
      {
        name: 'CLK net exists and connects U1 to U2 and U3',
        check: (result: any) => {
          const clk = result.nets?.CLK;
          return (
            clk &&
            clk.pins?.includes('U1:14') &&
            clk.pins?.includes('U2:11') &&
            clk.pins?.includes('U3:11')
          );
        },
      },
      {
        name: 'LATCH net connects U1 to U2 and U3',
        check: (result: any) => {
          const latch = result.nets?.LATCH;
          return (
            latch &&
            latch.pins?.includes('U1:15') &&
            latch.pins?.includes('U2:12') &&
            latch.pins?.includes('U3:12')
          );
        },
      },
      {
        name: 'DATA_IN net from U1 to U2',
        check: (result: any) =>
          result.nets?.DATA_IN?.pins?.includes('U1:16') &&
          result.nets?.DATA_IN?.pins?.includes('U2:13'),
      },
      {
        name: 'U2 daisy-chains to U3',
        check: (result: any) => {
          // U2 pin 9 (SER_OUT) should connect to U3 pin 13 (SER_IN)
          const daisy = result.nets?.DAISY_CHAIN;
          return daisy && daisy.pins?.includes('U2:9') && daisy.pins?.includes('U3:13');
        },
      },
      {
        name: 'Decoupling caps on regulators',
        check: (result: any) =>
          (result.nets?.VCC?.pins?.includes('C2:+') && result.nets?.GND?.pins?.includes('C2:-')) ||
          (result.nets?.VCC?.pins?.includes('C3:+') && result.nets?.GND?.pins?.includes('C3:-')),
      },
    ],
    expected_tokens: 5000,
    expected_duration_ms: 10000,
  },
];

/**
 * Get scenarios by difficulty
 */
export function getScenariosByDifficulty(difficulty: 'easy' | 'moderate' | 'hard') {
  return benchmarkScenarios.filter(s => s.difficulty === difficulty);
}

/**
 * Get scenarios for a specific circuit
 */
export function getScenariosByCircuit(circuit: string) {
  return benchmarkScenarios.filter(s => s.circuit === circuit);
}
