/*
 * KiCad MCP Server - Minimal HTTP server
 *
 * Copyright (C) 2025 KiCad Developers, see AUTHORS.txt for contributors.
 *
 * This program is free software: you can redistribute it and/or modify it
 * under the terms of the GNU General Public License as published by the
 * Free Software Foundation, either version 3 of the License, or (at your
 * option) any later version.
 */

#include "http_server.h"
#include <algorithm>
#include <cerrno>
#include <cstring>
#include <iostream>
#include <sstream>
#include <thread>
#include <typeinfo>
#include <vector>

#ifdef _WIN32
#include <winsock2.h>
#include <ws2tcpip.h>
#pragma comment( lib, "ws2_32.lib" )
using ssize_t = int;
#else
#include <arpa/inet.h>
#include <netinet/in.h>
#include <sys/socket.h>
#include <unistd.h>
#endif

namespace
{
const size_t BUF_SIZE = 64 * 1024;

#ifdef _WIN32
void close_socket( int fd )
{
    closesocket( fd );
}
#else
void close_socket( int fd )
{
    close( fd );
}
#endif

std::string readLine( const std::string& buf, size_t& pos )
{
    size_t start = pos;
    while( pos < buf.size() && buf[pos] != '\r' && buf[pos] != '\n' )
        ++pos;
    std::string line = buf.substr( start, pos - start );
    if( pos < buf.size() && buf[pos] == '\r' )
        ++pos;
    if( pos < buf.size() && buf[pos] == '\n' )
        ++pos;
    return line;
}

// Returns true if raw request headers contain Accept: ... text/event-stream ...
bool wantsEventStream( const std::string& raw )
{
    size_t pos = 0;
    while( pos < raw.size() )
    {
        size_t start = pos;
        while( pos < raw.size() && raw[pos] != '\r' && raw[pos] != '\n' )
            ++pos;
        std::string line = raw.substr( start, pos - start );
        if( line.size() >= 7 )
        {
            std::string key = line.substr( 0, 7 );
            for( char& c : key )
                c = ( c >= 'A' && c <= 'Z' ) ? (char) ( c + 32 ) : c;
            if( key == "accept:" && line.find( "event-stream" ) != std::string::npos )
                return true;
        }
        while( pos < raw.size() && ( raw[pos] == '\r' || raw[pos] == '\n' ) )
            ++pos;
    }
    return false;
}

void parseHeaders( const std::string& raw, std::string& method, std::string& path,
                   std::string& body, std::string& origin, size_t* outBodyStart,
                   size_t* outContentLength, bool* outConnectionClose )
{
    method.clear();
    path.clear();
    body.clear();
    origin.clear();
    if( outBodyStart )
        *outBodyStart = 0;
    if( outContentLength )
        *outContentLength = 0;
    if( outConnectionClose )
        *outConnectionClose = false;

    size_t             pos = 0;
    std::string        requestLine = readLine( raw, pos );
    std::istringstream iss( requestLine );
    iss >> method >> path;
    if( path.empty() )
        return;

    std::string line;
    bool        contentLengthSet = false;
    size_t      contentLength = 0;
    while( pos < raw.size() )
    {
        line = readLine( raw, pos );
        if( line.empty() )
            break;
        // Content-Length (case-insensitive)
        if( line.size() >= 15 )
        {
            std::string key = line.substr( 0, 15 );
            for( char& c : key )
                c = ( c >= 'A' && c <= 'Z' ) ? (char) ( c + 32 ) : c;
            if( key == "content-length:" )
            {
                try
                {
                    size_t start = line.find_first_not_of( " \t", 15 );
                    if( start != std::string::npos )
                    {
                        contentLength = (size_t) std::stoul( line.substr( start ) );
                        contentLengthSet = true;
                    }
                }
                catch( const std::exception& )
                {
                    contentLengthSet = false;
                }
            }
        }
        else if( line.find( "Origin:" ) == 0 )
        {
            size_t o = line.find_first_not_of( " \t", 7 );
            if( o != std::string::npos )
                origin = line.substr( o );
        }
        else if( line.size() >= 10 )
        {
            std::string key = line.substr( 0, 10 );
            for( char& c : key )
                c = ( c >= 'A' && c <= 'Z' ) ? (char) ( c + 32 ) : c;
            if( key == "connection:" )
            {
                size_t v = line.find_first_not_of( " \t", 10 );
                if( v != std::string::npos )
                {
                    std::string val = line.substr( v );
                    for( char& c : val )
                        c = ( c >= 'A' && c <= 'Z' ) ? (char) ( c + 32 ) : c;
                    if( val.find( "close" ) != std::string::npos && outConnectionClose )
                        *outConnectionClose = true;
                }
            }
        }
    }

    if( outBodyStart )
        *outBodyStart = pos;
    if( outContentLength && contentLengthSet )
        *outContentLength = contentLength;

    if( contentLengthSet && pos + contentLength <= raw.size() )
        body = raw.substr( pos, contentLength );
}

std::string escapeForJson( const std::string& s, size_t maxLen = 500 )
{
    std::string out;
    out.reserve( s.size() + 16 );
    size_t n = 0;
    for( char c : s )
    {
        if( n >= maxLen )
        {
            out += "...";
            break;
        }
        if( c == '\\' )
            out += "\\\\";
        else if( c == '"' )
            out += "\\\"";
        else if( c == '\n' )
            out += "\\n";
        else if( c == '\r' )
            out += "\\r";
        else if( static_cast<unsigned char>( c ) < 0x20 )
            out += "?";
        else
            out += c;
        ++n;
    }
    return out;
}
} // namespace


HttpServer::HttpServer( uint16_t aPort, Handler aHandler ) :
        m_port( aPort ), m_handler( std::move( aHandler ) )
{
}


HttpServer::~HttpServer()
{
    Stop();
}


bool HttpServer::Start()
{
#ifdef _WIN32
    WSADATA wsa;
    if( WSAStartup( MAKEWORD( 2, 2 ), &wsa ) != 0 )
        return false;
#endif

    m_listenFd = socket( AF_INET, SOCK_STREAM, 0 );
    if( m_listenFd < 0 )
        return false;

    int opt = 1;
#ifdef _WIN32
    setsockopt( m_listenFd, SOL_SOCKET, SO_REUSEADDR, (const char*) &opt, sizeof( opt ) );
#else
    setsockopt( m_listenFd, SOL_SOCKET, SO_REUSEADDR, &opt, sizeof( opt ) );
#endif

    struct sockaddr_in addr;
    memset( &addr, 0, sizeof( addr ) );
    addr.sin_family = AF_INET;
    addr.sin_addr.s_addr = inet_addr( "127.0.0.1" );
    addr.sin_port = htons( m_port );

    if( bind( m_listenFd, (struct sockaddr*) &addr, sizeof( addr ) ) < 0 )
    {
        close_socket( m_listenFd );
        m_listenFd = -1;
        return false;
    }
    if( listen( m_listenFd, 5 ) < 0 )
    {
        close_socket( m_listenFd );
        m_listenFd = -1;
        return false;
    }
    m_stop = false;
    return true; // success
}


void HttpServer::Stop()
{
    m_stop = true;
    if( m_listenFd >= 0 )
    {
        close_socket( m_listenFd );
        m_listenFd = -1;
    }
#ifdef _WIN32
    WSACleanup();
#endif
}


void HttpServer::ServeClient( int clientFd )
{
    std::vector<char> buf( BUF_SIZE );
    const size_t      maxRequest = 1024 * 1024;

    while( true )
    {
        std::string responseBody;
        int         status = 200;
        std::string contentType = "application/json";

        std::string raw;
        raw.reserve( 4096 );

        // Read until we have "\r\n\r\n" (full headers)
        while( raw.size() < maxRequest )
        {
            ssize_t n = recv( clientFd, buf.data(), buf.size(), 0 );
            if( n <= 0 )
            {
                close_socket( clientFd );
                return;
            }
            raw.append( buf.data(), (size_t) n );
            if( raw.find( "\r\n\r\n" ) != std::string::npos )
                break;
        }

        if( raw.find( "\r\n\r\n" ) == std::string::npos )
        {
            close_socket( clientFd );
            return;
        }

        std::string method, path, body, origin;
        size_t      bodyStart = 0;
        size_t      contentLength = 0;
        bool        connectionClose = false;
        parseHeaders( raw, method, path, body, origin, &bodyStart, &contentLength,
                      &connectionClose );

        // If client sent Expect: 100-continue, send 100 Continue so it will send the body
        if( method == "POST" && contentLength > 0
            && raw.find( "100-continue" ) != std::string::npos )
        {
            const char* cont = "HTTP/1.1 100 Continue\r\n\r\n";
            send( clientFd, cont, (size_t) strlen( cont ), 0 );
        }

        // Read until we have the full body (if Content-Length was set)
        while( contentLength > 0 && raw.size() < bodyStart + contentLength
               && raw.size() < maxRequest )
        {
            size_t toRead = bodyStart + contentLength - raw.size();
            if( toRead > buf.size() )
                toRead = buf.size();
            ssize_t n = recv( clientFd, buf.data(), (ssize_t) toRead, 0 );
            if( n <= 0 )
            {
                close_socket( clientFd );
                return;
            }
            raw.append( buf.data(), (size_t) n );
        }
        if( contentLength > 0 && raw.size() >= bodyStart + contentLength )
            body = raw.substr( bodyStart, contentLength );

        // MCP Streamable HTTP: GET with Accept: text/event-stream opens an SSE receive channel.
        // Respond with event-stream and keep connection open so the client does not see "channel closed".
        const bool pathOk = ( path.find( "/mcp" ) == 0 || path == "/" || path.empty() );
        if( method == "GET" && pathOk && wantsEventStream( raw ) )
        {
            const char* sseHeaders = "HTTP/1.1 200 OK\r\n"
                                     "Content-Type: text/event-stream\r\n"
                                     "Cache-Control: no-cache\r\n"
                                     "Connection: keep-alive\r\n"
                                     "Access-Control-Allow-Origin: *\r\n"
                                     "\r\n"
                                     ": mcp\n\n";
            if( send( clientFd, sseHeaders, (size_t) strlen( sseHeaders ), 0 )
                != (ssize_t) strlen( sseHeaders ) )
            {
                close_socket( clientFd );
                return;
            }
            // Keep this connection open (client may use it as the receive channel). When client
            // closes, recv will return 0. No further requests are read on this connection.
            while( recv( clientFd, buf.data(), buf.size(), 0 ) > 0 )
                ;
            close_socket( clientFd );
            return;
        }

        if( pathOk && method == "OPTIONS" )
        {
            // Handle CORS preflight before the main dispatch.  MSVC rejects
            // continuing the connection loop from inside the try/catch below.
            const char* optionsHeaders = "HTTP/1.1 204 No Content\r\n"
                                         "Access-Control-Allow-Origin: *\r\n"
                                         "Access-Control-Allow-Methods: POST, GET, OPTIONS\r\n"
                                         "Access-Control-Allow-Headers: Content-Type, "
                                         "Mcp-Session-Id, x-mcp-session-id\r\n"
                                         "Access-Control-Max-Age: 86400\r\n"
                                         "Content-Length: 0\r\n"
                                         "Connection: keep-alive\r\n"
                                         "\r\n";
            if( send( clientFd, optionsHeaders, (size_t) strlen( optionsHeaders ), 0 )
                != (ssize_t) strlen( optionsHeaders ) )
            {
                close_socket( clientFd );
                return;
            }

            continue;
        }

        try
        {
            if( !pathOk )
            {
                status = 404;
                responseBody = "{\"error\":\"Not Found\"}";
            }
            else if( method != "POST" && method != "GET" )
            {
                status = 405;
                responseBody = "{\"error\":\"Method Not Allowed\"}";
            }
            else if( method == "GET" )
            {
                responseBody = "{\"message\":\"KiCad MCP server. Send JSON-RPC via POST.\"}";
            }
            else
            {
                responseBody = m_handler( method, path, body, origin );
                // MCP Streamable HTTP: for JSON-RPC notifications (e.g. notifications/initialized),
                // server MUST return 202 Accepted with no body (spec). Returning 200 with body
                // causes strict clients (e.g. rmcp) to close the channel and fail handshake.
                if( body.find( "notifications/initialized" ) != std::string::npos )
                {
                    status = 202;
                    responseBody.clear();
                }
                else if( responseBody.empty() )
                {
                    status = 500;
                    responseBody = "{\"error\":\"Internal error\",\"message\":\"Handler returned "
                                   "empty response\","
                                   "\"debug\":{\"method\":\""
                                   + escapeForJson( method, 32 ) + "\",\"path\":\""
                                   + escapeForJson( path, 256 )
                                   + "\",\"bodySize\":" + std::to_string( body.size() )
                                   + ",\"bodyPreview\":\"" + escapeForJson( body, 200 ) + "\"}}";
                }
            }
        }
        catch( const std::exception& e )
        {
            status = 500;
            const char* rawMsg = e.what();
            std::string msg = rawMsg ? rawMsg : "(no message)";
            std::string excType = typeid( e ).name();
            responseBody = "{\"error\":\"Internal error\",\"message\":\"" + escapeForJson( msg )
                           + "\",\"debug\":{\"exceptionType\":\"" + escapeForJson( excType )
                           + "\"}}";
        }
        catch( ... )
        {
            status = 500;
            responseBody = "{\"error\":\"Internal error\",\"message\":\"Unknown exception\","
                           "\"debug\":{\"exceptionType\":\"non-std exception\"}}";
        }

        // MCP Streamable HTTP: first successful initialize response must include Mcp-Session-Id
        bool addSessionId = ( method == "POST" && status == 200 && pathOk
                              && body.find( "\"method\"" ) != std::string::npos
                              && body.find( "initialize" ) != std::string::npos );

        const char* statusText = ( status == 200 ) ? " OK" : ( status == 202 ) ? " Accepted" : "";
        std::ostringstream out;
        out << "HTTP/1.1 " << status << statusText << "\r\n"
            << "Content-Type: " << contentType << "\r\n"
            << "Content-Length: " << responseBody.size() << "\r\n"
            << "Access-Control-Allow-Origin: *\r\n"
            << "Access-Control-Allow-Methods: POST, GET, OPTIONS\r\n"
            << "Access-Control-Allow-Headers: Content-Type, Mcp-Session-Id, x-mcp-session-id\r\n"
            << "Connection: keep-alive\r\n";
        if( addSessionId )
            out << "Mcp-Session-Id: kicad-mcp-1\r\n";
        out << "\r\n" << responseBody;

        std::string resp = out.str();
        if( send( clientFd, resp.c_str(), resp.size(), 0 ) != (ssize_t) resp.size() )
        {
            close_socket( clientFd );
            return;
        }

        if( connectionClose )
            break;
    }

    close_socket( clientFd );
}


void HttpServer::Run()
{
    while( !m_stop && m_listenFd >= 0 )
    {
        struct sockaddr_in clientAddr;
        socklen_t          len = sizeof( clientAddr );
        int                clientFd = accept( m_listenFd, (struct sockaddr*) &clientAddr, &len );
        if( clientFd < 0 )
        {
            if( m_stop )
                break;
            continue;
        }
        // Serve each connection in its own thread so GET (SSE) and POST can run concurrently.
        std::thread(
                [this, clientFd]()
                {
                    ServeClient( clientFd );
                } )
                .detach();
    }
}
