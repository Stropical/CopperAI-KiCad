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

#include <import_export.h>
#include "ipc_client.h"
#include <api/common/envelope.pb.h>
#include <api/schematic/schematic_commands.pb.h>
#include <nng/nng.h>
#include <nng/protocol/reqrep0/req.h>
#include <algorithm>
#include <cstdlib>
#include <cstring>
#include <vector>

#ifdef _WIN32
#include <windows.h>
#else
#include <dirent.h>
#include <unistd.h>
#endif

namespace
{
void appendUnique( std::vector<std::string>& aOut, const std::string& aPath )
{
    if( aPath.empty() )
        return;
    if( std::find( aOut.begin(), aOut.end(), aPath ) != aOut.end() )
        return;
    aOut.push_back( aPath );
}


std::string kicadIpcDir()
{
#ifdef __APPLE__
    return "/tmp/kicad";
#else
    const char* tmp = std::getenv( "TMPDIR" );
    if( tmp && tmp[0] != '\0' )
        return std::string( tmp ) + "/kicad";
    return "/tmp/kicad";
#endif
}


/**
 * Ordered socket paths to try for schematic tools: explicit env, default api.sock,
 * then any api-<pid>.sock under the KiCad IPC directory (non-Windows).
 */
std::vector<std::string> schematicSocketCandidates()
{
    std::vector<std::string> paths;
    const char* env = std::getenv( "KICAD_API_SOCKET" );
    if( env && env[0] != '\0' )
        appendUnique( paths, std::string( env ) );

    appendUnique( paths, IpcClient::GetDefaultSocketPath() );

#ifndef _WIN32
    const std::string dir = kicadIpcDir();
    DIR* d = opendir( dir.c_str() );
    if( d )
    {
        std::vector<std::string> extras;
        while( dirent* ent = readdir( d ) )
        {
            std::string name( ent->d_name );
            if( name == "api.sock" )
                continue;
            if( name.size() > 8 && name.compare( 0, 4, "api-" ) == 0
                && name.compare( name.size() - 5, 5, ".sock" ) == 0 )
            {
                extras.push_back( dir + "/" + name );
            }
        }
        closedir( d );
        std::sort( extras.begin(), extras.end() );
        for( const std::string& p : extras )
            appendUnique( paths, p );
    }
#endif

    return paths;
}
} // namespace


std::string IpcClient::GetDefaultSocketPath()
{
    const char* env = std::getenv( "KICAD_API_SOCKET" );
    if( env && env[0] != '\0' )
        return std::string( env );

#ifdef __APPLE__
    return "/tmp/kicad/api.sock";
#else
    const char* tmp = std::getenv( "TMPDIR" );
    if( tmp && tmp[0] != '\0' )
        return std::string( tmp ) + "/kicad/api.sock";
    return "/tmp/kicad/api.sock";
#endif
}


IpcClient::IpcClient() = default;


IpcClient::~IpcClient()
{
    Disconnect();
}


bool IpcClient::Connect( const std::string& aSocketPath )
{
    if( m_connected )
        return true;

    std::string url = "ipc://" + aSocketPath;

    int r = nng_req0_open( &m_socket );
    if( r != 0 )
        return false;

    r = nng_dial( m_socket, url.c_str(), nullptr, 0 );
    if( r != 0 )
    {
        nng_close( m_socket );
        return false;
    }

    m_connected = true;
    return true;
}


void IpcClient::Disconnect()
{
    if( !m_connected )
        return;
    nng_close( m_socket );
    m_connected = false;
    m_schematicSocketVerified = false;
}


void IpcClient::InvalidateSchematicSocketCache()
{
    m_schematicSocketVerified = false;
}


bool IpcClient::probeSchematicApi( IpcClient& aIpc, std::string& aError )
{
    kiapi::common::ApiRequest req;
    req.mutable_header()->set_client_name( "mcp-socket-probe" );
    kiapi::schematic::types::GetSchematicSummary cmd;
    req.mutable_message()->PackFrom( cmd );
    kiapi::common::ApiResponse resp;

    if( !aIpc.SendRequest( req, resp, aError ) )
        return false;

    if( resp.status().status() == kiapi::common::ApiStatusCode::AS_UNHANDLED )
        return false;

    return true;
}


bool IpcClient::EnsureSchematicApiConnection( std::string& aError )
{
    if( m_connected && m_schematicSocketVerified )
        return true;

    if( m_connected )
    {
        if( probeSchematicApi( *this, aError ) )
        {
            m_schematicSocketVerified = true;
            return true;
        }
        Disconnect();
    }

    for( const std::string& path : schematicSocketCandidates() )
    {
        if( !Connect( path ) )
            continue;

        if( probeSchematicApi( *this, aError ) )
        {
            m_schematicSocketVerified = true;
            return true;
        }

        Disconnect();
    }

    aError = "No KiCad schematic editor API socket found. Open the schematic in Eeschema, or set "
             "KICAD_API_SOCKET to the socket path shown in KiCad's API / plugin environment (often "
             "api-<pid>.sock under the KiCad temp directory when the project manager holds api.sock).";
    return false;
}


bool IpcClient::SendRequest( const kiapi::common::ApiRequest& aRequest,
                             kiapi::common::ApiResponse&      aResponse,
                             std::string&                     aError )
{
    if( !m_connected )
    {
        aError = "IPC client not connected";
        return false;
    }

    std::string reqBuf;
    if( !aRequest.SerializeToString( &reqBuf ) )
    {
        aError = "Failed to serialize ApiRequest";
        return false;
    }

    int r = nng_send( m_socket, (void*) reqBuf.data(), reqBuf.size(), 0 );
    if( r != 0 )
    {
        aError = "nng_send failed";
        return false;
    }

    void*  recvBuf = nullptr;
    size_t recvLen = 0;
    r = nng_recv( m_socket, &recvBuf, &recvLen, NNG_FLAG_ALLOC );
    if( r != 0 )
    {
        aError = "nng_recv failed";
        Disconnect();
        return false;
    }

    bool ok = aResponse.ParseFromArray( recvBuf, (int) recvLen );
    nng_free( recvBuf, recvLen );
    if( !ok )
    {
        aError = "Failed to parse ApiResponse";
        Disconnect();
        return false;
    }
    return true;
}
