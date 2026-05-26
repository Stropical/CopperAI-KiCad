/*
 * KiCad MCP Server - In-process runner (runs HTTP server in a background thread)
 *
 * Copyright (C) 2025 KiCad Developers, see AUTHORS.txt for contributors.
 *
 * This program is free software: you can redistribute it and/or modify it
 * under the terms of the GNU General Public License as published by the
 * Free Software Foundation, either version 3 of the License, or (at your
 * option) any later version.
 */

#ifndef KICAD_MCP_SERVER_RUNNER_H
#define KICAD_MCP_SERVER_RUNNER_H

#include <atomic>
#include <cstdint>
#include <memory>
#include <mutex>
#include <string>
#include <thread>

class HttpServer;

/**
 * Runs the MCP HTTP server (POST /mcp JSON-RPC) in a separate thread, bridging
 * to the KiCad IPC API socket. Start() after the API server is listening;
 * Stop() on exit.
 */
class KICAD_MCP_SERVER
{
public:
    KICAD_MCP_SERVER();
    ~KICAD_MCP_SERVER();

    /** Start the HTTP server thread. Uses aSocketPath for IPC; port from KICAD_MCP_PORT or 8080. */
    bool Start( uint16_t aPort, const std::string& aSocketPath );

    void Stop();

    bool Running() const { return m_thread.joinable(); }

private:
    void run( uint16_t aPort, std::string aSocketPath );

    std::thread              m_thread;
    std::atomic<HttpServer*> m_server{ nullptr };
    std::mutex               m_handlerMutex;  // serializes handler (IpcClient is not thread-safe)
};

#endif // KICAD_MCP_SERVER_RUNNER_H
