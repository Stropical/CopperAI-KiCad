/*
 * KiCad MCP Server - Minimal HTTP server (127.0.0.1, POST /mcp)
 *
 * Copyright (C) 2025 KiCad Developers, see AUTHORS.txt for contributors.
 *
 * This program is free software: you can redistribute it and/or modify it
 * under the terms of the GNU General Public License as published by the
 * Free Software Foundation, either version 3 of the License, or (at your
 * option) any later version.
 */

#ifndef MCP_HTTP_SERVER_H
#define MCP_HTTP_SERVER_H

#include <functional>
#include <string>
#include <cstdint>

/**
 * Minimal HTTP server that binds to 127.0.0.1 and serves POST /mcp.
 * Handler receives request body (JSON-RPC), returns response body (JSON).
 * Optional Origin check for security.
 */
class HttpServer
{
public:
    using Handler = std::function<std::string( const std::string& method,
                                               const std::string& path,
                                               const std::string& body,
                                               const std::string& origin )>;

    HttpServer( uint16_t aPort, Handler aHandler );
    ~HttpServer();

    /** Start listening. Returns false on bind failure. */
    bool Start();
    void Stop();
    /** Block until Stop() or error. */
    void Run();

private:
    void ServeClient( int clientFd );

    uint16_t  m_port;
    Handler   m_handler;
    int       m_listenFd = -1;
    bool      m_stop = false;
};

#endif // MCP_HTTP_SERVER_H
