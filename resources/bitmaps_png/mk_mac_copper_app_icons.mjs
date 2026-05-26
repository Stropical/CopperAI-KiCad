#!/usr/bin/env node

import { spawnSync } from 'node:child_process';
import { promises as fs } from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

import sharp from '../../mcp/website/node_modules/sharp/lib/index.js';

const __dirname = path.dirname( fileURLToPath( import.meta.url ) );
const repoRoot = path.resolve( __dirname, '..', '..' );
const iconSource = path.join( __dirname, 'copper_ai_app_icon.png' );

const iconTargets = [
    'bitmap2component/bitmap2component.icns',
    'cvpcb/cvpcb.icns',
    'eeschema/eeschema.icns',
    'eeschema/eeschema_doc.icns',
    'eeschema/libedit.icns',
    'eeschema/libedit_doc.icns',
    'gerbview/gerbview.icns',
    'gerbview/gerbview_doc.icns',
    'kicad/kicad.icns',
    'kicad/kicad_doc.icns',
    'pagelayout_editor/pagelayout_editor.icns',
    'pagelayout_editor/pagelayout_editor_doc.icns',
    'pcb_calculator/pcb_calculator.icns',
    'pcbnew/fpedit.icns',
    'pcbnew/fpedit_doc.icns',
    'pcbnew/pcbnew.icns',
    'pcbnew/pcbnew_doc.icns',
    'cvpcb/cvpcb_doc.icns'
];

const iconsetEntries = [
    [ 'icon_16x16.png', 16 ],
    [ 'icon_16x16@2x.png', 32 ],
    [ 'icon_32x32.png', 32 ],
    [ 'icon_32x32@2x.png', 64 ],
    [ 'icon_128x128.png', 128 ],
    [ 'icon_128x128@2x.png', 256 ],
    [ 'icon_256x256.png', 256 ],
    [ 'icon_256x256@2x.png', 512 ]
];

function runOrThrow( command, args )
{
    const result = spawnSync( command, args, { stdio: 'inherit' } );

    if( result.status !== 0 )
        throw new Error( `${command} exited with status ${result.status ?? 'unknown'}` );
}

async function main()
{
    const tempRoot = await fs.mkdtemp( path.join( os.tmpdir(), 'copper-ai-icon-' ) );
    const iconsetDir = path.join( tempRoot, 'CopperAI.iconset' );
    const generatedIcns = path.join( tempRoot, 'CopperAI.icns' );

    await fs.mkdir( iconsetDir, { recursive: true } );

    for( const [ filename, size ] of iconsetEntries )
    {
        // macOS HIG: app icons need ~10% inset so they don't appear oversized
        // next to native icons.  Resize artwork to 80% of the canvas and centre
        // it on a transparent background.
        const artworkSize = Math.round( size * 0.80 );

        await sharp( iconSource )
            .resize( artworkSize, artworkSize )
            .extend( {
                top:        Math.floor( ( size - artworkSize ) / 2 ),
                bottom:     Math.ceil( ( size - artworkSize ) / 2 ),
                left:       Math.floor( ( size - artworkSize ) / 2 ),
                right:      Math.ceil( ( size - artworkSize ) / 2 ),
                background: { r: 0, g: 0, b: 0, alpha: 0 }
            } )
            .png()
            .toFile( path.join( iconsetDir, filename ) );
    }

    runOrThrow( 'iconutil', [ '-c', 'icns', iconsetDir, '-o', generatedIcns ] );

    for( const relativeTarget of iconTargets )
    {
        const target = path.join( repoRoot, relativeTarget );
        await fs.copyFile( generatedIcns, target );
        console.log( `updated ${relativeTarget}` );
    }
}

main().catch( ( error ) =>
{
    console.error( error );
    process.exit( 1 );
} );
