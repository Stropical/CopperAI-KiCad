/*
 * KiCad MCP Server - MCP JSON-RPC handler (initialize, tools/list, tools/call)
 *
 * Copyright (C) 2025 KiCad Developers, see AUTHORS.txt for contributors.
 *
 * This program is free software: you can redistribute it and/or modify it
 * under the terms of the GNU General Public License as published by the
 * Free Software Foundation, either version 3 of the License, or (at your
 * option) any later version.
 */

#ifndef MCP_MCP_HANDLER_H
#define MCP_MCP_HANDLER_H

#include "ipc_client.h"
#include <string>
#include <api/schematic/schematic_commands.pb.h>

// Forward-declare McpHandler for TransactionGuard
class McpHandler;

/**
 * Handles MCP JSON-RPC: initialize, notifications/initialized, tools/list, tools/call.
 * tools/call builds ApiRequest, sends via IpcClient, maps ApiResponse to MCP result.
 */
class McpHandler
{
public:
    explicit McpHandler( IpcClient& aIpc );

    /** Process JSON-RPC body (single object or array). Returns JSON-RPC response. */
    std::string Handle( const std::string& aBody );

    friend class TransactionGuard;

private:
    std::string HandleRequest( const std::string& method, const void* params, const std::string& id );
    std::string HandleInitialize( const void* params, const std::string& id );
    std::string HandleToolsList( const std::string& id );
    std::string HandleToolsCall( const void* params, const std::string& id );
    /** Discard an open commit so the next operation does not get "already has a commit in progress". */
    void endCommitDrop( const std::string& aCommitId );
    
    /** Get pin position for a component reference and pin number. Returns (x_mm, y_mm) of connection point (body).
        If aOutOrientationDegrees is non-null, fills with pin orientation (0=right, 90=up, 180=left, 270=down).
        If aOutTipX, aOutTipY, aOutHasTip are non-null and response has position_label (pin tip), fills tip coords and *aOutHasTip = true; else *aOutHasTip = false. */
    std::pair<double, double> getPinPosition( const std::string& reference, const std::string& pinNumber, std::string& err, double* aOutOrientationDegrees = nullptr, double* aOutTipX = nullptr, double* aOutTipY = nullptr, bool* aOutHasTip = nullptr );
    
    /** Get component bounding box center. Returns (x_mm, y_mm, width_mm, height_mm). */
    std::tuple<double, double, double, double> getComponentBounds( const std::string& reference, std::string& err );

    /**
     * Overload that accepts a pre-fetched summary response to avoid a redundant
     * GetSchematicSummary IPC call.  When @a cachedSummary is non-null the function
     * searches it directly; otherwise it falls back to the original behaviour.
     */
    std::tuple<double, double, double, double> getComponentBounds(
        const std::string& reference, std::string& err,
        const kiapi::schematic::types::GetSchematicSummaryResponse* cachedSummary );
    
    /** Find an empty spot near a reference position. Returns (x_mm, y_mm). */
    std::pair<double, double> findEmptySpot( double nearX, double nearY, double componentWidth, double componentHeight, std::string& err, double aMinSpacingMm = 15.0 );

    /** Find placement for a design block: adjacent to existing content (right or below), allows outside A4. Returns (center_x_mm, center_y_mm). */
    std::pair<double, double> findEmptySpotForBlock( double widthMm, double heightMm, std::string& err );
    
    /** Return true if placing a component with given center (x,y) and size would overlap an existing component or wire. Sets aOverlapDesc to a short description (e.g. "component U1"). */
    bool placementWouldOverlap( double x, double y, double componentWidth, double componentHeight, std::string& aOverlapDesc, double aMinSpacingMm = 12.0 );
    
    /** Calculate smart label offset based on pin position relative to component center. Returns (offset_x_mm, offset_y_mm). */
    std::pair<double, double> calculateLabelOffset( double pinX, double pinY, const std::string& reference, double defaultOffset );

    /** Get current schematic snapping grid step in mm (from GetSchematicSummary). Returns > 0 or 0.1 as fallback. */
    double getSchematicGridMm();

    /** Get current sheet path string for targeting CreateItems (e.g. "/" or "/child"). Empty if unavailable. */
    std::string getCurrentSheetPath();

    /** Get the file path of the currently open .kicad_sch (via GetOpenDocuments). Empty if unavailable. */
    std::string getCurrentSchematicPath();

    /** Project root directory (KIPRJMOD) from GetOpenDocuments. Empty if unavailable. */
    std::string getProjectDirectory();

    /** Fetch (or return cached) schematic summary. Cache lives within a single HandleToolsCall.
     *  Returns true on success and fills @a out; sets @a error on failure. */
    bool getCachedSummary( kiapi::schematic::types::GetSchematicSummaryResponse& out,
                           std::string& error );

    /** Invalidate the per-request summary cache. Called at the start of each HandleToolsCall. */
    void invalidateSummaryCache() { m_summaryValid = false; }

    IpcClient& m_ipc;

    // Per-request cache for GetSchematicSummary to avoid redundant IPC calls.
    // Invalidated at the start of each HandleToolsCall.
    bool m_summaryValid = false;
    kiapi::schematic::types::GetSchematicSummaryResponse m_cachedSummary;
};


/**
 * RAII guard for KiCad commit transactions.
 *
 * Acquires a commit in the constructor (with retry logic to clear stuck commits).
 * Automatically drops uncommitted transactions in the destructor.
 *
 * Usage:
 *   TransactionGuard txn( handler, ipc, err );
 *   if( !txn.ok() ) { ... return error ... }
 *   // ... do work with txn.commitId() ...
 *   txn.commit();   // or txn.drop();
 */
class TransactionGuard
{
public:
    /**
     * Begin a commit transaction. Retries up to 2 attempts, clearing stuck
     * commits between attempts. Check ok() after construction.
     */
    TransactionGuard( McpHandler& aHandler, IpcClient& aIpc, std::string& aError );

    /** Auto-drop if not committed or dropped. */
    ~TransactionGuard();

    /** Non-copyable, non-movable. */
    TransactionGuard( const TransactionGuard& ) = delete;
    TransactionGuard& operator=( const TransactionGuard& ) = delete;

    /** True if a commit was successfully started. */
    bool ok() const { return !m_commitId.empty(); }

    /** The commit ID from begin_commit. Empty if ok() is false. */
    const std::string& commitId() const { return m_commitId; }

    /** Commit the transaction (CMA_COMMIT). Sets released flag. */
    bool commit();

    /** Drop the transaction (CMA_DROP). Sets released flag. */
    void drop();

    /** True if commit() or drop() was already called. */
    bool released() const { return m_released; }

private:
    McpHandler&  m_handler;
    IpcClient&   m_ipc;
    std::string  m_commitId;
    bool         m_released = false;
};

#endif // MCP_MCP_HANDLER_H
