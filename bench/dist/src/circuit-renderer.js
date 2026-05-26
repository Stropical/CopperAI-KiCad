/**
 * Circuit renderer - Converts circuit state to SVG visualization
 */
const DEFAULT_OPTIONS = {
    width: 1200,
    height: 800,
    showLabels: true,
    showPins: true,
    highlightNets: [],
};
/**
 * Render a circuit as SVG
 */
export function renderCircuitSVG(circuit, options = {}) {
    const opts = { ...DEFAULT_OPTIONS, ...options };
    // Calculate bounds
    let minX = Infinity, maxX = -Infinity, minY = Infinity, maxY = -Infinity;
    for (const comp of circuit.components.values()) {
        minX = Math.min(minX, comp.x - 5000);
        maxX = Math.max(maxX, comp.x + 5000);
        minY = Math.min(minY, comp.y - 3000);
        maxY = Math.max(maxY, comp.y + 3000);
    }
    if (!isFinite(minX)) {
        minX = 0;
        maxX = 100000;
        minY = 0;
        maxY = 100000;
    }
    const padding = 5000;
    const worldWidth = maxX - minX + padding * 2;
    const worldHeight = maxY - minY + padding * 2;
    const scaleX = (opts.width - 40) / worldWidth;
    const scaleY = (opts.height - 40) / worldHeight;
    const scale = Math.min(scaleX, scaleY);
    const worldToScreen = (x, y) => ({
        x: (x - minX + padding) * scale + 20,
        y: (y - minY + padding) * scale + 20,
    });
    // Color map for nets
    const netColors = generateNetColors(circuit.nets.size);
    let netIndex = 0;
    const netColorMap = new Map();
    for (const netName of circuit.nets.keys()) {
        netColorMap.set(netName, netColors[netIndex % netColors.length]);
        netIndex++;
    }
    let svg = `<?xml version="1.0" encoding="UTF-8"?>
<svg xmlns="http://www.w3.org/2000/svg" width="${opts.width}" height="${opts.height}" viewBox="0 0 ${opts.width} ${opts.height}">
  <defs>
    <style>
      .component-box { fill: #f0f0f0; stroke: #333; stroke-width: 2; }
      .component-label { font-size: 12px; font-weight: bold; fill: #000; }
      .component-value { font-size: 10px; fill: #666; }
      .pin { fill: #ff6b6b; }
      .pin-label { font-size: 8px; fill: #000; }
      .net-line { stroke-width: 2; fill: none; }
      .net-label { font-size: 10px; font-weight: bold; }
      .title { font-size: 16px; font-weight: bold; fill: #000; }
      .legend { font-size: 10px; fill: #666; }
    </style>
  </defs>

  <!-- Background -->
  <rect width="${opts.width}" height="${opts.height}" fill="white"/>

  <!-- Title -->
  <text x="20" y="25" class="title">${circuit.filename}</text>

  <!-- Draw nets (wires) first -->
`;
    // Draw wires
    for (const [wireId, wire] of circuit.wires.entries()) {
        const [x1, y1] = wire.from.split(',').map(Number);
        const [x2, y2] = wire.to.split(',').map(Number);
        const p1 = worldToScreen(x1, y1);
        const p2 = worldToScreen(x2, y2);
        svg += `  <line x1="${p1.x}" y1="${p1.y}" x2="${p2.x}" y2="${p2.y}" class="net-line" stroke="#999" stroke-dasharray="5,5"/>
`;
    }
    // Draw nets (as colored lines between connected pins)
    for (const [netName, net] of circuit.nets.entries()) {
        if (net.pins.length < 2)
            continue;
        const color = netColorMap.get(netName) || '#666';
        const pinPositions = net.pins
            .map(pinId => {
            const [ref, pinNum] = pinId.split(':');
            const comp = circuit.components.get(ref);
            if (!comp)
                return null;
            return worldToScreen(comp.x, comp.y);
        })
            .filter(p => p !== null);
        // Draw lines connecting pins in this net
        if (pinPositions.length > 1) {
            for (let i = 0; i < pinPositions.length - 1; i++) {
                svg += `  <line x1="${pinPositions[i].x}" y1="${pinPositions[i].y}" x2="${pinPositions[i + 1].x}" y2="${pinPositions[i + 1].y}" class="net-line" stroke="${color}" opacity="0.7"/>
`;
            }
        }
    }
    // Draw components
    for (const [ref, comp] of circuit.components.entries()) {
        const pos = worldToScreen(comp.x, comp.y);
        const width = 3000 * scale;
        const height = 2000 * scale;
        // Component box
        svg += `  <rect x="${pos.x - width / 2}" y="${pos.y - height / 2}" width="${width}" height="${height}" class="component-box" data-component="${ref}"/>
`;
        // Component reference label
        svg += `  <text x="${pos.x}" y="${pos.y - height / 2 + 15}" text-anchor="middle" class="component-label">${ref}</text>
`;
        // Component value
        svg += `  <text x="${pos.x}" y="${pos.y + 5}" text-anchor="middle" class="component-value">${comp.value}</text>
`;
        // Draw pins
        if (opts.showPins) {
            for (let i = 0; i < comp.pins.length; i++) {
                const pin = comp.pins[i];
                const pinX = pos.x - width / 2 + (i / comp.pins.length) * width;
                const pinY = pos.y + height / 2;
                // Find which net this pin is on
                const netName = pin.connected_to?.[0] || 'unconnected';
                const pinColor = netColorMap.get(netName) || '#ccc';
                svg += `  <circle cx="${pinX}" cy="${pinY}" r="3" class="pin" fill="${pinColor}"/>
`;
                if (opts.showPins) {
                    svg += `  <text x="${pinX}" y="${pinY + 12}" text-anchor="middle" class="pin-label">${pin.pin}</text>
`;
                }
            }
        }
    }
    // Draw net labels
    if (opts.showLabels) {
        let labelY = 50;
        svg += `  <g id="legend">
`;
        for (const [netName, net] of circuit.nets.entries()) {
            if (net.pins.length === 0)
                continue;
            const color = netColorMap.get(netName) || '#666';
            svg += `    <line x1="20" y1="${labelY}" x2="40" y2="${labelY}" class="net-line" stroke="${color}"/>
`;
            svg += `    <text x="50" y="${labelY + 4}" class="legend">${netName} (${net.pins.length} pins)</text>
`;
            labelY += 18;
        }
        svg += `  </g>
`;
    }
    svg += `</svg>`;
    return svg;
}
/**
 * Generate distinct colors for nets
 */
function generateNetColors(count) {
    const baseColors = [
        '#ff6b6b', // red
        '#4ecdc4', // teal
        '#45b7d1', // blue
        '#f7b731', // yellow
        '#5f27cd', // purple
        '#00d2d3', // cyan
        '#ff9ff3', // pink
        '#54a0ff', // light blue
        '#48dbfb', // sky blue
        '#ffa502', // orange
        '#26de81', // green
        '#ee5a6f', // coral
    ];
    const colors = [];
    for (let i = 0; i < count; i++) {
        colors.push(baseColors[i % baseColors.length]);
    }
    return colors;
}
/**
 * Render a simple text representation of the circuit
 */
export function renderCircuitText(circuit) {
    let text = `CIRCUIT: ${circuit.filename}\n`;
    text += `${'='.repeat(60)}\n\n`;
    text += `COMPONENTS (${circuit.components.size}):\n`;
    text += `${'-'.repeat(60)}\n`;
    for (const [ref, comp] of circuit.components.entries()) {
        text += `${ref.padEnd(10)} ${comp.value.padEnd(20)} ${comp.footprint}\n`;
        text += `  Pins: ${comp.pins.map(p => p.pin).join(', ')}\n`;
    }
    text += `\nNETS (${circuit.nets.size}):\n`;
    text += `${'-'.repeat(60)}\n`;
    for (const [netName, net] of circuit.nets.entries()) {
        text += `${netName.padEnd(20)} → ${net.pins.join(', ')}\n`;
    }
    text += `\nWIRES (${circuit.wires.size}):\n`;
    text += `${'-'.repeat(60)}\n`;
    for (const [wireId, wire] of circuit.wires.entries()) {
        text += `${wireId.padEnd(15)} ${wire.from} → ${wire.to}\n`;
    }
    return text;
}
//# sourceMappingURL=circuit-renderer.js.map