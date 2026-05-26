/*
 * KiCad MCP Server - Main entry: HTTP server on 127.0.0.1, POST /mcp -> MCP handler -> KiCad IPC
 *
 * Copyright (C) 2025 KiCad Developers, see AUTHORS.txt for contributors.
 *
 * This program is free software: you can redistribute it and/or modify it
 * under the terms of the GNU General Public License as published by the
 * Free Software Foundation, either version 3 of the License, or (at your
 * option) any later version.
 */

#include "http_server.h"
#include "mcp_handler.h"
#include "ipc_client.h"
#include <cstdlib>
#include <iostream>
#include <csignal>

static HttpServer* g_server = nullptr;

static void onSignal( int )
{
    if( g_server )
        g_server->Stop();
}


int main( int argc, char* argv[] )
{
    (void) argc;
    (void) argv;

    const char* portEnv = std::getenv( "KICAD_MCP_PORT" );
    uint16_t port = 8080;
    if( portEnv )
    {
        int p = std::atoi( portEnv );
        if( p > 0 && p < 65536 )
            port = (uint16_t) p;
    }

    IpcClient ipc;
    McpHandler handler( ipc );

    auto httpHandler = [&handler]( const std::string& method, const std::string& path,
                                   const std::string& body, const std::string& origin ) -> std::string
    {
        (void) path;
        (void) origin;
        if( method == "POST" && !body.empty() )
            return handler.Handle( body );
        return "";
    };

    HttpServer server( port, httpHandler );
    g_server = &server;

#ifdef _WIN32
    (void) onSignal;
#else
    std::signal( SIGINT, onSignal );
    std::signal( SIGTERM, onSignal );
#endif

    if( !server.Start() )
    {
        std::cerr << "kicad-mcp-server: failed to bind 127.0.0.1:" << port << std::endl;
        return 1;
    }

    std::cerr << "kicad-mcp-server: listening on http://127.0.0.1:" << port << "/mcp" << std::endl;
    server.Run();
    g_server = nullptr;
    return 0;
}
