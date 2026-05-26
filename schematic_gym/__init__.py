"""SchematicGym -- Gymnasium environment for KiCad schematic capture."""

from __future__ import annotations

import gymnasium

gymnasium.register(
    id="SchematicGym-v0",
    entry_point="schematic_gym.env:SchematicGymEnv",
)

gymnasium.register(
    id="SchematicGym-ERCFixer-v0",
    entry_point="schematic_gym.error_fixer.env:ERCFixerEnv",
)
