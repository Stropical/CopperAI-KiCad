/*
 * KiCad MCP Server - In-process runner implementation
 *
 * Copyright (C) 2025 KiCad Developers, see AUTHORS.txt for contributors.
 *
 * This program is free software: you can redistribute it and/or modify it
 * under the terms of the GNU General Public License as published by the
 * Free Software Foundation, either version 3 of the License, or (at your
 * option) any later version.
 */

#include <api/mcp_server_runner.h>
#include <import_export.h>
#include <http_server.h>
#include <ipc_client.h>
#include <mcp_handler.h>
#include <chrono>
#include <cstdlib>
#include <cstring>
#include <mutex>
#include <thread>

KICAD_MCP_SERVER::KICAD_MCP_SERVER() = default;


KICAD_MCP_SERVER::~KICAD_MCP_SERVER()
{
    Stop();
}


bool KICAD_MCP_SERVER::Start( uint16_t aPort, const std::string& aSocketPath )
{
    if( m_thread.joinable() )
        return true;

    m_thread = std::thread( &KICAD_MCP_SERVER::run, this, aPort, aSocketPath );
    return true;
}


void KICAD_MCP_SERVER::Stop()
{
    if( !m_thread.joinable() )
        return;

    // Wait for the worker thread to set m_server (at most ~2s). If IPC connect or
    // bind failed, the thread may exit without ever setting it, so don't spin forever.
    const int timeoutMs = 2000;
    int waited = 0;
    while( waited < timeoutMs && m_server.load() == nullptr )
    {
        std::this_thread::sleep_for( std::chrono::milliseconds( 5 ) );
        waited += 5;
    }
    if( HttpServer* s = m_server.load() )
        s->Stop();

    m_thread.join();
}


void KICAD_MCP_SERVER::run( uint16_t aPort, std::string aSocketPath )
{
    IpcClient ipc;
    if( !aSocketPath.empty() )
        ipc.Connect( aSocketPath );

    McpHandler handler( ipc );

    auto httpHandler = [this, &handler]( const std::string& method, const std::string& path,
                                          const std::string& body, const std::string& origin ) -> std::string
    {
        (void) path;
        (void) origin;
        if( method != "POST" || body.empty() )
            return "";
        std::lock_guard<std::mutex> lock( m_handlerMutex );
        return handler.Handle( body );
    };

    HttpServer server( aPort, httpHandler );
    if( !server.Start() )
        return;

    m_server = &server;
    server.Run();
    m_server = nullptr;
}
