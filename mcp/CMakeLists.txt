# MCP HTTP server: bridge from HTTP (webview) to KiCad IPC API.
# Standalone executable; no wx, no common. Requires KICAD_IPC_API (nng, kiapi).

set( MCP_SRCS
    main.cpp
    ipc_client.cpp
    http_server.cpp
    mcp_handler.cpp
    )

add_executable( kicad-mcp-server ${MCP_SRCS} )

target_link_libraries( kicad-mcp-server PRIVATE
    nlohmann_json
    kiapi
    NNG::nng
    )

target_include_directories( kicad-mcp-server PRIVATE
    ${PROJECT_SOURCE_DIR}/include
    ${PROJECT_BINARY_DIR}/api/cpp
    )

# kiapi protobuf headers use KIAPI_IMPORTEXPORT=APIIMPORT; APIIMPORT is defined in import_export.h
if( MSVC )
    target_compile_options( kicad-mcp-server PRIVATE /FI${PROJECT_SOURCE_DIR}/include/import_export.h )
else()
    target_compile_options( kicad-mcp-server PRIVATE -include ${PROJECT_SOURCE_DIR}/include/import_export.h )
endif()

# Optional: install so users can run the MCP server
install( TARGETS kicad-mcp-server
    RUNTIME DESTINATION ${KICAD_BIN}
    COMPONENT binary
    )
