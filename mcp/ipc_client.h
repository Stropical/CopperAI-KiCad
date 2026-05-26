/*
 * KiCad MCP Server - IPC client to KiCad API
 *
 * Copyright (C) 2025 KiCad Developers, see AUTHORS.txt for contributors.
 *
 * This program is free software: you can redistribute it and/or modify it
 * under the terms of the GNU General Public License as published by the
 * Free Software Foundation, either version 3 of the License, or (at your
 * option) any later version.
 */

#ifndef MCP_IPC_CLIENT_H
#define MCP_IPC_CLIENT_H

#include <nng/nng.h>
#include <string>

namespace kiapi
{
namespace common
{
class ApiRequest;
class ApiResponse;
}
}

/**
 * Client that connects to KiCad's IPC API (NNG REP socket), sends
 * serialized ApiRequest, receives serialized ApiResponse.
 */
class IpcClient
{
public:
    IpcClient();
    ~IpcClient();

    /** Resolve socket path: KICAD_API_SOCKET env or default (e.g. /tmp/kicad/api.sock). */
    static std::string GetDefaultSocketPath();

    /**
     * Connect to a KiCad process that exposes the schematic API (Eeschema).
     * The project manager often owns api.sock and only handles a subset of commands;
     * Eeschema listens on api-<pid>.sock when api.sock is already taken.
     */
    bool EnsureSchematicApiConnection( std::string& aError );

    /** Connect to the given socket path (ipc://<path>). Returns true on success. */
    bool Connect( const std::string& aSocketPath );

    bool IsConnected() const { return m_connected; }
    void Disconnect();

    /** After Disconnect() or when the remote editor may have closed, clear cached verification. */
    void InvalidateSchematicSocketCache();

    /**
     * Send ApiRequest and receive ApiResponse. Returns true on success;
     * aResponse is filled. On failure aError is set.
     */
    bool SendRequest( const kiapi::common::ApiRequest& aRequest,
                      kiapi::common::ApiResponse&      aResponse,
                      std::string&                     aError );

private:
    static bool probeSchematicApi( IpcClient& aIpc, std::string& aError );

    nng_socket m_socket{};
    bool       m_connected = false;
    bool       m_schematicSocketVerified = false;
};

#endif // MCP_IPC_CLIENT_H
