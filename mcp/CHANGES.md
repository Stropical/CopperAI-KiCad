# MCP Tool Updates

## Summary
Updated the KiCad MCP tools to improve usability with automatic positioning and fixes for wiring issues.

## Changes Made

### 1. Auto-Placement with "near" Field
**File**: `mcp_handler.cpp`, `mcp_handler.h`

The `place_component` tool now supports automatic positioning using a "near" field:

```json
{
  "library": "Device",
  "symbol": "C",
  "reference": "C1",
  "value": "10u",
  "near": {
    "reference": "U1",
    "pin": "3"  // Optional - if omitted, places near component center
  }
}
```

**How it works - Three placement modes:**

1. **Explicit coordinates**: Provide `x` and `y` → places at exact position
   ```json
   { "x": 100, "y": 50 }
   ```

2. **Relative placement**: Provide `near` → places relative to another component
   ```json
   { "near": { "reference": "U1", "pin": "VCC" } }
   ```
   - If `pin` is provided, places near that specific pin
   - If `pin` is omitted, places near component center
   - Uses 20mm minimum spacing and collision detection

3. **Auto-placement**: Provide neither `x`/`y` nor `near` → finds empty spot automatically
   ```json
   { "library": "Device", "symbol": "R", "reference": "R1" }
   ```
   - Starts search from schematic center (100mm, 100mm)
   - Uses collision detection to find empty spot
   - Prevents components from ending up at (0, 0)

**Implementation details:**
- Added `getPinPosition()` helper method to get pin coordinates
- Added `getComponentBounds()` helper method to get component position and dimensions
- Added `findEmptySpot()` helper method to calculate placement position with proper spacing
  - Queries all existing components from schematic summary
  - Checks for bounding box overlaps with MIN_SPACING_MM buffer
  - Tries positions in a spiral pattern (5 rings, 8 directions per ring)
  - Returns first non-overlapping position found

### 2. Automatic Inductor Rotation
**File**: `mcp_handler.cpp`

Inductors (symbol "L") are now automatically rotated 90 degrees regardless of the rotation parameter:

```json
{
  "library": "Device",
  "symbol": "L",
  "reference": "L1",
  "value": "10u",
  "rotation": 0  // Will be overridden to 90
}
```

**Implementation:**
- Check if symbol == "L" before placement
- Force rotation to 90.0 degrees
- Report in success message that inductor was auto-rotated

### 3. Fixed Pin-to-Pin Wiring Loop Issue
**File**: `mcp_handler.cpp`

The `connect_pin_to_pin` tool previously had a bug where it would fail silently if pin positions couldn't be retrieved, potentially causing wires to be created at (0,0) or entering an infinite loop.

**Fix:**
- Replaced local lambda `getPinPos` with call to the new `getPinPosition()` helper method
- Added explicit error checking after each pin position lookup
- Returns error immediately if either pin position cannot be retrieved
- Provides clear error messages indicating which pin failed

**Before:**
```cpp
auto [x1, y1] = getPinPos( ref1, pin1 );  // Could return (0,0) on error
auto [x2, y2] = getPinPos( ref2, pin2 );  // No error checking
// Continue with potentially invalid positions...
```

**After:**
```cpp
auto [x1, y1] = getPinPosition( ref1, pin1, err );
if( !err.empty() ) {
    // Return error immediately with clear message
}
auto [x2, y2] = getPinPosition( ref2, pin2, err );
if( !err.empty() ) {
    // Return error immediately with clear message
}
// Only continue if both positions are valid
```

### 4. Smart Label Positioning Based on Pin Direction
**Files**: `mcp_handler.cpp`, `mcp_handler.h`, `skills/prompt.md`

Global labels are now intelligently positioned based on pin direction relative to component center:

**Before:**
- Labels always placed to the right (+2mm X offset)
- Caused readability issues when pins point left
- Labels appeared inside component bodies

**After:**
- Labels placed in the direction the pin is facing:
  - Right-side pins → label to the right
  - Left-side pins → label to the left
  - Top pins → label above
  - Bottom pins → label below
- Determined by analyzing pin position relative to component center

**Implementation:**
- Added `calculateLabelOffset()` helper method
- Analyzes pin X/Y offset from component center
- Returns appropriate (offsetX, offsetY) based on predominant direction
- Used by both `connect_net_to_pin` and `connect_pin_to_pin`

**Example:**
```
Component U1 at (100, 100) with width 20mm:
- Pin VIN at (85, 100) → left side → label at (83, 100) [left of pin]
- Pin VOUT at (115, 100) → right side → label at (117, 100) [right of pin]
- Pin GND at (100, 110) → bottom → label at (100, 112) [below pin]
```

### 5. Updated Documentation
**File**: `skills/prompt.md`

Updated the system prompt to document:
- New "near" field for auto-placement
- Automatic 90-degree rotation for inductors
- Smart label positioning based on pin direction
- Updated tool reference section

## Collision Detection Algorithm

The `findEmptySpot()` function uses a multi-ring spiral search pattern:

1. **Get existing components**: Queries schematic summary and calculates bounding boxes for all placed components
2. **Spiral pattern**: For each ring (0-4), tries 8 positions:
   - Cardinal directions: Right, Down, Left, Up
   - Diagonal directions: Down-Right, Down-Left, Up-Right, Up-Left
3. **Distance calculation**: 
   - Ring 0: MIN_SPACING_MM (20mm) + component dimensions
   - Ring 1: 2× spacing + component dimensions
   - Ring 2: 3× spacing + component dimensions
   - etc.
4. **Overlap check**: For each candidate position, checks if its bounding box (with MIN_SPACING_MM buffer) overlaps any existing component
5. **First fit**: Returns the first non-overlapping position found
6. **Fallback**: If all 40 positions are occupied (unlikely), returns a default offset to the right

This ensures that multiple components placed near the same pin will spread out in a predictable pattern rather than stacking on top of each other.

## Testing Recommendations

1. **Test auto-placement:**
   ```json
   // Place capacitor near U1 pin 3
   { "library": "Device", "symbol": "C", "reference": "C1", "near": { "reference": "U1", "pin": "3" } }
   
   // Place resistor near U1 center
   { "library": "Device", "symbol": "R", "reference": "R1", "near": { "reference": "U1" } }
   ```

2. **Test inductor rotation:**
   ```json
   { "library": "Device", "symbol": "L", "reference": "L1", "value": "10u", "x": 100, "y": 100 }
   // Should be placed at 90 degrees
   ```

3. **Test pin-to-pin wiring:**
   ```json
   { "reference1": "U1", "pin1": "1", "reference2": "R1", "pin2": "1", "net_name": "VCC" }
   // Should wire correctly or return clear error if pins don't exist
   ```

## API Changes

### place_component
**New optional parameter:**
- `near` (object): Auto-placement reference
  - `reference` (string, required): Component reference to place near
  - `pin` (string, optional): Specific pin to place near

**Behavior changes:**
- If `symbol == "L"`, rotation is forced to 90 degrees
- If `near` is specified without `x`/`y`, position is calculated automatically

### connect_pin_to_pin
**Behavior changes:**
- Now returns explicit errors if pin positions cannot be retrieved
- No longer creates wires at invalid positions
- Clearer error messages

## Files Modified

1. `/Users/ethanmarreel/Downloads/kicad-9.0.7/mcp/mcp_handler.h`
   - Added helper method declarations

2. `/Users/ethanmarreel/Downloads/kicad-9.0.7/mcp/mcp_handler.cpp`
   - Implemented helper methods
   - Updated `place_component` handler
   - Fixed `connect_pin_to_pin` handler

3. `/Users/ethanmarreel/Downloads/kicad-9.0.7/mcp/skills/prompt.md`
   - Updated documentation for new features
