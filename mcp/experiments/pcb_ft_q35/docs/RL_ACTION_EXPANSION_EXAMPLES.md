# RL Action Expansion Examples

This note collects concrete hard-placement cases from the mined oracle corpus and maps them to the next action families the agent should learn.

The current hard policy mostly ranks local inverse-style repairs. That is useful, but it is too narrow for agent-style schematic placement. The next actions should encode intent, not just displacement.

Sources used here:

- `data/rl/oracle_hard_train.pt`
- `data/rl/oracle_hard_eval.pt`

## 1. Decoupler Placement

These examples are capacitors that should be handled as proximity-preserving parts near an IC or power source. A generic inverse move can recover them, but it does not teach the agent the semantic goal.

Examples:

- `data/scraped_schematics/files/tdaede__td-io/master/jvs-power.kicad_sch`
  - `selected_reference: C10`
  - `secondary_reference: C9`
  - `oracle_best_action_label: overshoot_inverse`
  - What the new action should do: keep the capacitor close to the power entry or regulator cluster, but allow a slightly larger clearance than the raw inverse move.

- `data/scraped_schematics/files/shanteacontrols__OpenDeck/master/bin/sch/opendeck/v3.0.1/LEDs.kicad_sch`
  - `selected_reference: C7`
  - `secondary_reference: C9`
  - `oracle_best_action_label: cluster_inverse`
  - What the new action should do: move the decoupler with its nearby capacitor pair as a local unit, preserving the power-loop shape.

- `data/scraped_schematics/files/elipsitz__gamebub/handheld/pcb/handheld_rev2/fpga_io_34.kicad_sch`
  - `selected_reference: C1102`
  - `secondary_reference: C1101`
  - `oracle_best_action_label: inverse_move`
  - What the new action should do: place the cap back into the IC-local support region instead of only restoring the exact geometric offset.

Suggested actions:

- `decoupler_toward_ic`
- `decoupler_toward_power`
- `decoupler_pair_compact`

## 2. Connector Alignment

Connectors should be placed with board-edge and enclosure intent. The hard corpus shows that a connector is often tied to another connector or nearby support part, but the agent should learn a dedicated alignment move rather than a pure translation.

Examples:

- `data/scraped_schematics/files/NVNTLabs__switch2-SDEX2M2/main/MicroSD Express Sniffer V0.3/MicroSD Express Sniffer.kicad_sch`
  - `selected_reference: J4`
  - `secondary_reference: J3`
  - `oracle_best_action_label: cluster_half_inverse`
  - What the new action should do: keep the connector pair aligned, compact, and edge-aware.

- `data/scraped_schematics/files/elipsitz__gamebub/handheld/pcb/handheld_rev1/fpga_io_14.kicad_sch`
  - `selected_reference: J802`
  - `secondary_reference: D102`
  - `oracle_best_action_label: cluster_half_inverse`
  - What the new action should do: maintain the connector anchor and preserve its nearby support component spacing.

- `data/scraped_schematics/files/jakubfi__ictester/master/hw/usb.kicad_sch`
  - `selected_reference: J6`
  - `secondary_reference: U9`
  - `oracle_best_action_label: half_inverse`
  - What the new action should do: restore the connector to a stable board-edge position while preserving the local relationship to the IC.

Suggested actions:

- `connector_edge_align`
- `connector_pair_compact`
- `connector_to_board_edge`

## 3. Passive Cluster Compaction

This is the most important missing family. The current action set can recover a resistor/capacitor cluster only indirectly. The agent should instead learn to compact a local passive group while preserving relative topology.

Examples:

- `data/scraped_schematics/files/devbisme__KiField/master/tests/integration/kicad6/leaf1.kicad_sch`
  - `selected_reference: R5`
  - `secondary_reference: C2`
  - `oracle_best_action_label: cluster_compact_cw`
  - What the new action should do: tighten the R/C pair without breaking the local routing intent.

- `data/scraped_schematics/files/elipsitz__gamebub/handheld/pcb/handheld_rev1/lcd.kicad_sch`
  - `selected_reference: R1201`
  - `secondary_reference: U1201`
  - `oracle_best_action_label: cluster_compact_cw`
  - What the new action should do: keep the passive adjacent to its support IC and reduce wasted spacing.

- `data/scraped_schematics/files/sanni__cartreader/master/hardware/vselect/VSelect.kicad_sch`
  - `selected_reference: R1`
  - `secondary_reference: U1`
  - `oracle_best_action_label: cluster_inverse`
  - What the new action should do: restore the passive cluster as a unit, not just one symbol at a time.

- `data/scraped_schematics/files/torvalds__1590A/main/Base/Buffered/Switch.kicad_sch`
  - `selected_reference: R18`
  - `secondary_reference: D3`
  - `oracle_best_action_label: cluster_compact_cw`
  - What the new action should do: compact a mixed passive/diode support cluster while keeping its local shape.

Suggested actions:

- `passive_cluster_compact`
- `passive_cluster_align_axis`
- `passive_cluster_follow_neighbor`

## What To Encode In The Policy

The new candidate features should tell the model:

- whether the selected item is a decoupler, connector, or passive
- whether the move is edge-aware
- whether the move is cluster-preserving
- whether the move is intended to compact or align relative to a neighbor

If the action set expands without these cues, the policy will just relearn inverse-move variants.
