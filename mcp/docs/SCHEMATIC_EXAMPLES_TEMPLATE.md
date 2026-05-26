---
title: Schematic Good-vs-Bad Example Template
purpose: Reusable format for training/evaluation examples
version: 1.0
last_updated: 2026-03-05
tags: [schematic, examples, training, validation]
---

# Reusable Template

Use this block as a starting point for new examples.

````md
---
id: <short_unique_id>
title: <example title>
domain: <power|digital|analog|mixed>
difficulty: <beginner|intermediate|advanced>
---

## Inputs
- Context: <what circuit/problem this example targets>
- Components: <required parts and values>
- Assumptions: <board constraints, supply, frequency, etc.>

## Constraints
- <placement/routing/net constraints>
- <ERC/DRC constraints>
- <style or architecture constraints>

## Bad Example
```text
<describe the bad implementation clearly and concretely>
```

## Why Bad
- <failure mode 1>
- <failure mode 2>
- <failure mode 3>

## Good Example
```text
<describe the corrected implementation with specific actions>
```

## Why Good
- <improvement 1>
- <improvement 2>
- <improvement 3>

## Validation
- Checks:
  - <observable check 1>
  - <observable check 2>
  - <observable check 3>
- Expected Result: <pass/fail criteria>

## Reusable Pattern
- Rule: <portable design rule>
- When to Apply: <conditions>
- Exceptions: <known exceptions/tradeoffs>
````

# Filled Mini-Example (Decoupling)

---
id: decoupling-placement-basic
title: Local Decoupling at MCU Power Pins
domain: digital
difficulty: beginner
---

## Inputs
- Context: 3.3V MCU with one `VDD` and one `GND` pin pair.
- Components: `U1` (MCU), `C1=100nF` (ceramic), `C2=1uF` (bulk local), `+3V3`, `GND`.
- Assumptions: Two-layer board, moderate edge rates, no special RF requirements.

## Constraints
- `C1` must be physically closest to the `VDD/GND` pin pair.
- Minimize loop area (`VDD -> C1 -> GND`).
- No long shared trace from `VDD` pin before decoupling branch.

## Bad Example
```text
C1 is placed far from U1 (20 mm away), connected through thin meandering traces.
C2 is near U1 but C1 is closer to regulator than to MCU.
VDD enters U1 first, then branches to C1.
```

## Why Bad
- High inductance path reduces high-frequency decoupling effectiveness.
- Larger current loop increases noise and transient voltage droop.
- Branch order allows switching current spikes to propagate before local bypassing.

## Good Example
```text
Place C1 immediately adjacent to U1 VDD pin (within a few mm), with direct short trace
to VDD and a short return to ground. Place C2 nearby (slightly farther than C1).
Route supply so decoupling connection is local at the MCU pin region.
```

## Why Good
- Short paths lower parasitic inductance for fast transient current delivery.
- Small loop area reduces EMI and ground bounce.
- Proper local bypassing stabilizes MCU supply during switching events.

## Validation
- Checks:
  - `C1` is the nearest capacitor to `U1` power pin pair.
  - Trace length from `U1.VDD` to `C1` pad is minimal vs alternatives.
  - Return path from `C1` to `GND` is short and direct.
- Expected Result: ERC clean, and placement review confirms local high-frequency decoupling priority.

## Reusable Pattern
- Rule: Place smallest-value high-frequency decoupler closest to IC power pins; place larger bulk cap nearby.
- When to Apply: Any digital/IC power pin decoupling scenario.
- Exceptions: Very high-speed/RF designs may require additional package-specific or plane-based strategies.
