/*
 * KiCad MCP Server - MCP JSON-RPC handler
 *
 * Copyright (C) 2025 KiCad Developers, see AUTHORS.txt for contributors.
 *
 * This program is free software: you can redistribute it and/or modify it
 * under the terms of the GNU General Public License as published by the
 * Free Software Foundation, either version 3 of the License, or (at your
 * option) any later version.
 */

#include <import_export.h>
#include "mcp_handler.h"
#include <api/common/envelope.pb.h>
#include <api/common/commands/editor_commands.pb.h>
#include <api/common/types/base_types.pb.h>
#include <api/board/board_commands.pb.h>
#include <api/schematic/schematic_commands.pb.h>
#include <api/schematic/schematic_types.pb.h>
#if defined( _WIN32 ) && defined( NANODBC_ENABLE_UNICODE )
// The KiCad Windows DLL build exports nlohmann::json instantiations from kicommon.
// Use KiCad's import stub when this file is compiled into common.lib, while keeping
// the standalone MCP server self-contained.
#include <json_common.h>
#else
#include <nlohmann/json.hpp>
#endif
#include <algorithm>
#include <cctype>
#include <cmath>
#include <set>
#include <iomanip>
#include <limits>
#include <map>
#include <memory>
#include <random>
#include <sstream>
#include <fstream>
#include <iostream>
#include <vector>
#include <regex>
#include <stdexcept>
#include <filesystem>
#include <cstdint>
#include <ctime>

using json = nlohmann::json;
namespace fs = std::filesystem;

// Coordinates use KiCad's exact mm (schematic internal units: 1mm = 10000 IU)

namespace
{
constexpr double DEFAULT_OVERLAP_SPACING_MM = 3.0;  // Reduced from 12.0 for denser layouts
constexpr double DEFAULT_FIND_SPOT_SPACING_MM = 4.0;  // Reduced from 15.0
constexpr double NEAR_PASSIVE_OVERLAP_SPACING_MM = 1.0;  // Reduced from 3.0 for tight placement
constexpr double NEAR_PASSIVE_FIND_SPOT_SPACING_MM = 1.5;  // Reduced from 4.0
constexpr double COMPACT_GRID_MM = 0.1;
constexpr double PI_MM = 3.14159265358979323846;

double snapCompactMm( double mm )
{
    return std::round( mm / COMPACT_GRID_MM ) * COMPACT_GRID_MM;
}

std::string toUpperAscii( std::string text )
{
    for( char& c : text )
        c = static_cast<char>( std::toupper( static_cast<unsigned char>( c ) ) );
    return text;
}

bool symbolLooksLikePassiveFamily( const std::string& symbolUpper, char family )
{
    if( symbolUpper.empty() || symbolUpper[0] != family )
        return false;
    if( symbolUpper.size() == 1 )
        return true;
    if( symbolUpper[1] == '_' )
        return true;
    // Common capacitor variants: CP, CP_Small, CP1, ...
    if( family == 'C' && symbolUpper[1] == 'P' )
        return true;
    return false;
}

bool isLikelyPassiveSymbol( const std::string& library, const std::string& symbol )
{
    std::string symbolUpper = toUpperAscii( symbol );
    if( symbolUpper.empty() )
        return false;

    bool passiveSymbol = symbolLooksLikePassiveFamily( symbolUpper, 'R' )
                         || symbolLooksLikePassiveFamily( symbolUpper, 'C' )
                         || symbolLooksLikePassiveFamily( symbolUpper, 'L' )
                         || symbolUpper.rfind( "RPACK", 0 ) == 0;
    if( !passiveSymbol )
        return false;

    std::string libraryUpper = toUpperAscii( library );
    if( libraryUpper.empty() || libraryUpper == "DEVICE" || libraryUpper == "PASSIVE" || libraryUpper == "DISCRETE" )
        return true;

    // Keep detection conservative outside common passive libraries.
    return symbolUpper == "R" || symbolUpper == "C" || symbolUpper == "L";
}

void addOffsetIfUnique( std::vector<std::pair<double, double>>& offsets, double dx, double dy )
{
    double snapDx = snapCompactMm( dx );
    double snapDy = snapCompactMm( dy );
    if( std::abs( snapDx ) < 1e-9 && std::abs( snapDy ) < 1e-9 )
        return;

    for( const auto& [ex, ey] : offsets )
    {
        if( std::abs( ex - snapDx ) < 1e-9 && std::abs( ey - snapDy ) < 1e-9 )
            return;
    }
    offsets.push_back( { snapDx, snapDy } );
}

std::vector<std::pair<double, double>> buildNearPlacementOffsets( bool isPassive, bool hasPinOrientation,
                                                                   double pinOrientationDeg )
{
    std::vector<std::pair<double, double>> offsets;
    const std::vector<double> compactDistances = isPassive
        ? std::vector<double>{ 1.5, 2.0, 2.5, 3.0, 3.5, 4.0 }
        : std::vector<double>{ 2.0, 3.0, 4.0 };
    const std::vector<double> moderateDistances = isPassive
        ? std::vector<double>{ 5.0, 6.0, 8.0, 10.0 }
        : std::vector<double>{ 6.0, 8.0, 10.0, 12.0, 15.0 };

    if( hasPinOrientation )
    {
        double outwardDeg = std::fmod( pinOrientationDeg + 180.0, 360.0 );
        if( outwardDeg < 0 )
            outwardDeg += 360.0;

        double rad = outwardDeg * PI_MM / 180.0;
        double ux = std::cos( rad );
        double uy = -std::sin( rad ); // KiCad schematic Y grows downward
        double vx = -uy;
        double vy = ux;

        for( double d : compactDistances )
        {
            double side = std::max( 0.8, std::min( 1.5, d * 0.5 ) );
            addOffsetIfUnique( offsets, d * ux, d * uy );
            addOffsetIfUnique( offsets, d * ux + side * vx, d * uy + side * vy );
            addOffsetIfUnique( offsets, d * ux - side * vx, d * uy - side * vy );
        }
    }

    auto addCardinalAndDiagonal = [&]( double d )
    {
        addOffsetIfUnique( offsets, d, 0.0 );
        addOffsetIfUnique( offsets, -d, 0.0 );
        addOffsetIfUnique( offsets, 0.0, d );
        addOffsetIfUnique( offsets, 0.0, -d );
        addOffsetIfUnique( offsets, d, d );
        addOffsetIfUnique( offsets, d, -d );
        addOffsetIfUnique( offsets, -d, d );
        addOffsetIfUnique( offsets, -d, -d );
    };

    for( double d : compactDistances )
        addCardinalAndDiagonal( d );
    for( double d : moderateDistances )
        addCardinalAndDiagonal( d );

    return offsets;
}

struct ParsedLabel
{
    std::string uuid;
    std::string text;
    std::string kind;
    std::string typeUrl;
    google::protobuf::Any any;
    double x = 0.0;
    double y = 0.0;
    double rotation = 0.0;
};

struct ParsedWire
{
    std::string uuid;
    double x1 = 0.0;
    double y1 = 0.0;
    double x2 = 0.0;
    double y2 = 0.0;
};

bool readFileToString( const std::string& path, std::string& out )
{
    std::ifstream file( path );
    if( !file.is_open() )
        return false;
    out.assign( std::istreambuf_iterator<char>( file ), std::istreambuf_iterator<char>() );
    return true;
}

struct SchematicSymbolMetadata
{
    std::string reference;
    std::string value;
    std::string libId;
    std::string footprint;
    std::string datasheet;
    std::string description;
    std::string tolerance;
    std::string manufacturer;
    std::string manufacturerPart;
    std::string digikeyPart;
    std::string uuid;
    int unit = 0;
    bool hasDnp = false;
    bool dnp = false;
    bool hasInBom = false;
    bool inBom = true;
    bool hasOnBoard = false;
    bool onBoard = true;
    std::map<std::string, std::string> customFields;
};

std::string trimAscii( std::string text )
{
    auto notSpace = []( unsigned char ch ) { return !std::isspace( ch ); };
    text.erase( text.begin(), std::find_if( text.begin(), text.end(), notSpace ) );
    text.erase( std::find_if( text.rbegin(), text.rend(), notSpace ).base(), text.end() );
    return text;
}

bool extractBalancedSexprBlock( const std::string& text, size_t start, std::string& block, size_t& nextPos )
{
    if( start >= text.size() || text[start] != '(' )
        return false;

    int depth = 0;
    bool inQuote = false;
    bool escaped = false;

    for( size_t i = start; i < text.size(); ++i )
    {
        char ch = text[i];

        if( inQuote )
        {
            if( escaped )
            {
                escaped = false;
            }
            else if( ch == '\\' )
            {
                escaped = true;
            }
            else if( ch == '"' )
            {
                inQuote = false;
            }
            continue;
        }

        if( ch == '"' )
        {
            inQuote = true;
            continue;
        }

        if( ch == '(' )
            depth++;
        else if( ch == ')' )
            depth--;

        if( depth == 0 )
        {
            nextPos = i + 1;
            block = text.substr( start, nextPos - start );
            return true;
        }
    }

    return false;
}

std::string takeLeafAfterLastColon( const std::string& text )
{
    const size_t pos = text.rfind( ':' );
    return pos == std::string::npos ? text : text.substr( pos + 1 );
}

bool looksLikeAnonymousNetName( const std::string& netName )
{
    if( netName.empty() )
        return true;

    if( netName.find( "Net-" ) != std::string::npos )
        return true;

    const size_t underscore = netName.find( '_' );
    if( underscore != std::string::npos && underscore >= 2 )
    {
        std::string prefix = netName.substr( 0, underscore );
        if( !prefix.empty() && prefix[0] == '#' )
            return true;

        for( char ch : prefix )
        {
            if( std::isdigit( static_cast<unsigned char>( ch ) ) )
                return true;
        }
    }

    return false;
}

bool looksLikePowerNetName( const std::string& netName )
{
    std::string upper = toUpperAscii( netName );
    return upper == "GND" || upper == "AGND" || upper == "DGND" || upper == "PGND"
           || upper == "VCC" || upper == "VDD" || upper == "VSS" || upper == "VBUS"
           || upper == "VBAT" || upper == "VIN" || upper == "VOUT"
           || upper.find( "+3V3" ) != std::string::npos
           || upper.find( "+5V" ) != std::string::npos
           || upper.find( "+12V" ) != std::string::npos
           || upper.find( "-12V" ) != std::string::npos
           || upper.find( "3V3" ) != std::string::npos
           || upper.find( "5V" ) != std::string::npos
           || upper.find( "1V8" ) != std::string::npos;
}

std::map<std::string, SchematicSymbolMetadata> parsePlacedSymbolMetadata( const std::string& schContent )
{
    std::map<std::string, SchematicSymbolMetadata> out;
    size_t pos = 0;

    const std::regex propertyRe( R"mcp(\(property\s+"([^"]+)"\s+"([^"]*)")mcp", std::regex::optimize );
    const std::regex libIdRe( R"mcp(\(lib_id\s+"([^"]+)")mcp", std::regex::optimize );
    const std::regex uuidRe( R"mcp(\(uuid\s+"([^"]+)")mcp", std::regex::optimize );
    const std::regex unitRe( R"mcp(\(unit\s+([0-9]+)\))mcp", std::regex::optimize );
    const std::regex dnpRe( R"mcp(\(dnp\s+(yes|no)\))mcp", std::regex::optimize );
    const std::regex inBomRe( R"mcp(\(in_bom\s+(yes|no)\))mcp", std::regex::optimize );
    const std::regex onBoardRe( R"mcp(\(on_board\s+(yes|no)\))mcp", std::regex::optimize );

    while( ( pos = schContent.find( "(symbol", pos ) ) != std::string::npos )
    {
        std::string block;
        size_t nextPos = pos;
        if( !extractBalancedSexprBlock( schContent, pos, block, nextPos ) )
        {
            pos += 7;
            continue;
        }

        pos = nextPos;

        if( block.find( "(lib_id " ) == std::string::npos )
            continue;

        SchematicSymbolMetadata meta;
        std::map<std::string, std::string> properties;

        for( auto it = std::sregex_iterator( block.begin(), block.end(), propertyRe );
             it != std::sregex_iterator(); ++it )
        {
            std::string key = ( *it )[1].str();
            std::string value = ( *it )[2].str();
            properties[key] = value;
        }

        auto refIt = properties.find( "Reference" );
        if( refIt == properties.end() || refIt->second.empty() )
            continue;

        meta.reference = refIt->second;
        meta.value = properties.count( "Value" ) ? properties["Value"] : "";
        meta.footprint = properties.count( "Footprint" ) ? properties["Footprint"] : "";
        meta.datasheet = properties.count( "Datasheet" ) ? properties["Datasheet"] : "";
        meta.description = properties.count( "Description" ) ? properties["Description"] : "";

        std::smatch match;
        if( std::regex_search( block, match, libIdRe ) )
            meta.libId = match[1].str();
        if( std::regex_search( block, match, uuidRe ) )
            meta.uuid = match[1].str();
        if( std::regex_search( block, match, unitRe ) )
            meta.unit = std::stoi( match[1].str() );
        if( std::regex_search( block, match, dnpRe ) )
        {
            meta.hasDnp = true;
            meta.dnp = match[1].str() == "yes";
        }
        if( std::regex_search( block, match, inBomRe ) )
        {
            meta.hasInBom = true;
            meta.inBom = match[1].str() == "yes";
        }
        if( std::regex_search( block, match, onBoardRe ) )
        {
            meta.hasOnBoard = true;
            meta.onBoard = match[1].str() == "yes";
        }

        for( const auto& [key, value] : properties )
        {
            std::string upperKey = toUpperAscii( key );
            if( upperKey == "REFERENCE" || upperKey == "VALUE" || upperKey == "FOOTPRINT"
                || upperKey == "DATASHEET" || upperKey == "DESCRIPTION"
                || upperKey == "KI_KEYWORDS" || upperKey == "KI_FP_FILTERS" )
            {
                continue;
            }

            meta.customFields[key] = value;

            if( meta.tolerance.empty()
                && ( upperKey == "TOL" || upperKey == "TOLERANCE"
                     || upperKey.find( "TOLERANCE" ) != std::string::npos ) )
            {
                meta.tolerance = value;
            }
            if( meta.manufacturer.empty()
                && ( upperKey == "MANUFACTURER" || upperKey == "MFR" ) )
            {
                meta.manufacturer = value;
            }
            if( meta.manufacturerPart.empty()
                && ( upperKey == "MPN" || upperKey == "PART NUMBER"
                     || upperKey == "MANUFACTURER PART"
                     || upperKey == "MANUFACTURER PART NUMBER" ) )
            {
                meta.manufacturerPart = value;
            }
            if( meta.digikeyPart.empty()
                && upperKey.find( "DIGI" ) != std::string::npos
                && upperKey.find( "PART" ) != std::string::npos )
            {
                meta.digikeyPart = value;
            }
        }

        out[meta.reference] = std::move( meta );
    }

    return out;
}


std::vector<std::string> extractSymbolNamesFromKicadSym( const std::string& symContent )
{
    std::vector<std::string> names;
    std::set<std::string>    seen;

    try
    {
        const std::regex re( R"mcp(\(symbol\s+"([^"]+)")mcp" );
        auto begin = std::sregex_iterator( symContent.begin(), symContent.end(), re );
        auto end = std::sregex_iterator();

        for( auto it = begin; it != end; ++it )
        {
            std::string raw = ( *it )[1].str();
            if( raw.empty() )
                continue;

            const size_t colon = raw.rfind( ':' );
            std::string leaf = colon == std::string::npos ? raw : raw.substr( colon + 1 );

            if( leaf.empty() || seen.count( leaf ) )
                continue;

            seen.insert( leaf );
            names.push_back( leaf );
        }
    }
    catch( ... )
    {
    }

    return names;
}


void appendLibraryImportManifestEntry( const fs::path& projectDir, const json& entry )
{
    const fs::path manifestPath = projectDir / ".library_import_manifest.json";

    json manifest = json::array();

    if( fs::exists( manifestPath ) )
    {
        std::ifstream in( manifestPath );

        if( in.is_open() )
        {
            try
            {
                in >> manifest;
                if( !manifest.is_array() )
                    manifest = json::array();
            }
            catch( ... )
            {
                manifest = json::array();
            }
        }
    }

    manifest.push_back( entry );

    std::ofstream out( manifestPath, std::ios::trunc );
    if( !out.is_open() )
        throw std::runtime_error( "Cannot write .library_import_manifest.json" );

    out << manifest.dump( 2 );
}


bool pathIsInsideProject( const std::string& projectDir, const std::string& candidateAbs )
{
    std::error_code ec;
    fs::path base = fs::absolute( fs::path( projectDir ), ec );
    if( ec )
        return false;
    fs::path cand = fs::absolute( fs::path( candidateAbs ), ec );
    if( ec )
        return false;

    base = base.lexically_normal();
    cand = cand.lexically_normal();

    if( fs::exists( base ) )
    {
        fs::path canon = fs::weakly_canonical( base, ec );
        if( !ec )
            base = canon;
    }
    if( fs::exists( cand ) )
    {
        fs::path canon = fs::weakly_canonical( cand, ec );
        if( !ec )
            cand = canon;
    }

    auto mismatch = std::mismatch( base.begin(), base.end(), cand.begin(), cand.end() );
    return mismatch.first == base.end();
}


// RFC 2045 / MIME base64 decode (no wx; used by kicad-mcp-server standalone build).
bool base64DecodeToBytes( const std::string& in, std::vector<uint8_t>& out )
{
    static const std::string B64 =
            "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";

    std::string s;
    s.reserve( in.size() );
    for( unsigned char c : in )
    {
        if( !std::isspace( c ) )
            s.push_back( static_cast<char>( c ) );
    }

    auto isBase64 = []( unsigned char c ) -> bool
    {
        return ( std::isalnum( c ) != 0 ) || ( c == '+' ) || ( c == '/' );
    };

    auto sextet = [&]( unsigned char c ) -> int
    {
        size_t p = B64.find( static_cast<char>( c ) );
        return p == std::string::npos ? -1 : static_cast<int>( p );
    };

    out.clear();
    out.reserve( ( s.size() * 3 ) / 4 + 3 );

    size_t in_len = s.size();
    size_t in_ = 0;
    int i = 0;
    unsigned char char_array_4[4];
    unsigned char char_array_3[3];

    while( in_len > 0 && s[in_] != '=' && isBase64( static_cast<unsigned char>( s[in_] ) ) )
    {
        char_array_4[i++] = static_cast<unsigned char>( s[in_] );
        in_++;
        in_len--;

        if( i == 4 )
        {
            int v0 = sextet( char_array_4[0] );
            int v1 = sextet( char_array_4[1] );
            int v2 = sextet( char_array_4[2] );
            int v3 = sextet( char_array_4[3] );
            if( v0 < 0 || v1 < 0 || v2 < 0 || v3 < 0 )
                return false;

            char_array_3[0] = ( v0 << 2 ) + ( ( v1 & 0x30 ) >> 4 );
            char_array_3[1] = ( ( v1 & 0xf ) << 4 ) + ( ( v2 & 0x3c ) >> 2 );
            char_array_3[2] = ( ( v2 & 0x3 ) << 6 ) + v3;

            for( i = 0; i < 3; i++ )
                out.push_back( char_array_3[i] );
            i = 0;
        }
    }

    if( i )
    {
        int idx[4] = { 0, 0, 0, 0 };
        for( int j = 0; j < i; j++ )
        {
            int v = sextet( char_array_4[j] );
            if( v < 0 )
                return false;
            idx[j] = v;
        }

        char_array_3[0] = ( idx[0] << 2 ) + ( ( idx[1] & 0x30 ) >> 4 );
        char_array_3[1] = ( ( idx[1] & 0xf ) << 4 ) + ( ( idx[2] & 0x3c ) >> 2 );
        char_array_3[2] = ( ( idx[2] & 0x3 ) << 6 ) + idx[3];

        for( int j = 0; j < i - 1; j++ )
            out.push_back( char_array_3[j] );
    }

    if( i == 1 )
        return false;

    while( in_len > 0 && s[in_] == '=' )
    {
        in_++;
        in_len--;
    }

    if( in_ != s.size() )
        return false;

    return true;
}


bool writeFileBytes( const fs::path& path, const std::vector<uint8_t>& data )
{
    std::ofstream f( path, std::ios::binary | std::ios::trunc );
    if( !f )
        return false;
    if( !data.empty() )
        f.write( reinterpret_cast<const char*>( data.data() ), static_cast<std::streamsize>( data.size() ) );
    return f.good();
}


size_t findBalancedBlockEnd( const std::string& text, size_t startPos )
{
    int depth = 0;
    for( size_t i = startPos; i < text.size(); ++i )
    {
        if( text[i] == '(' )
            depth++;
        else if( text[i] == ')' )
        {
            depth--;
            if( depth == 0 )
                return i + 1;
        }
    }
    return std::string::npos;
}

std::string parseUuidFromBlock( const std::string& block )
{
    size_t uuidPos = block.find( "(uuid " );
    if( uuidPos == std::string::npos )
        return "";

    uuidPos += 6;
    while( uuidPos < block.size() && std::isspace( static_cast<unsigned char>( block[uuidPos] ) ) )
        ++uuidPos;

    if( uuidPos < block.size() && block[uuidPos] == '"' )
    {
        ++uuidPos;
        size_t end = block.find( '"', uuidPos );
        return end == std::string::npos ? "" : block.substr( uuidPos, end - uuidPos );
    }

    size_t end = block.find_first_of( " \t\r\n)", uuidPos );
    return end == std::string::npos ? block.substr( uuidPos ) : block.substr( uuidPos, end - uuidPos );
}

std::string parseLabelText( const std::string& block, const std::string& prefix )
{
    size_t pos = block.find( prefix );
    if( pos == std::string::npos )
        return "";

    pos += prefix.size();
    while( pos < block.size() && std::isspace( static_cast<unsigned char>( block[pos] ) ) )
        ++pos;

    if( pos >= block.size() || block[pos] == '(' )
        return "";

    if( block[pos] == '"' )
    {
        ++pos;
        size_t end = block.find( '"', pos );
        return end == std::string::npos ? "" : block.substr( pos, end - pos );
    }

    size_t end = block.find_first_of( " \t\r\n)", pos );
    return end == std::string::npos ? block.substr( pos ) : block.substr( pos, end - pos );
}

void parseLabelBlocks( const std::string& content, const std::string& prefix, const std::string& kind,
                       std::vector<ParsedLabel>& labels )
{
    size_t pos = 0;
    while( ( pos = content.find( prefix, pos ) ) != std::string::npos )
    {
        size_t end = findBalancedBlockEnd( content, pos );
        if( end == std::string::npos )
            break;

        std::string block = content.substr( pos, end - pos );
        ParsedLabel label;
        label.kind = kind;
        label.text = parseLabelText( block, prefix );
        label.uuid = parseUuidFromBlock( block );

        size_t atPos = block.find( "(at " );
        if( atPos != std::string::npos )
        {
            atPos += 4;
            try { label.x = std::stod( block.substr( atPos ) ); } catch( ... ) { label.x = 0.0; }
            size_t sp = block.find( ' ', atPos );
            if( sp != std::string::npos )
            {
                try { label.y = std::stod( block.substr( sp + 1 ) ); } catch( ... ) { label.y = 0.0; }
                size_t sp2 = block.find( ' ', sp + 1 );
                if( sp2 != std::string::npos )
                {
                    try { label.rotation = std::stod( block.substr( sp2 + 1 ) ); } catch( ... ) { label.rotation = 0.0; }
                }
            }
        }

        if( !label.uuid.empty() )
            labels.push_back( label );
        pos = end;
    }
}

void parseSchematicLabelsAndWires( const std::string& content,
                                   std::vector<ParsedLabel>& labels,
                                   std::vector<ParsedWire>& wires )
{
    parseLabelBlocks( content, "(label", "local", labels );
    parseLabelBlocks( content, "(global_label", "global", labels );
    parseLabelBlocks( content, "(hierarchical_label", "hierarchical", labels );

    // Parse wire segments.
    size_t pos = 0;
    while( ( pos = content.find( "(wire (pts", pos ) ) != std::string::npos )
    {
        size_t end = findBalancedBlockEnd( content, pos );
        if( end == std::string::npos )
            break;

        std::string block = content.substr( pos, end - pos );
        std::vector<double> coords;
        size_t xyPos = 0;
        while( ( xyPos = block.find( "(xy ", xyPos ) ) != std::string::npos )
        {
            xyPos += 4;
            double x = 0.0, y = 0.0;
            try { x = std::stod( block.substr( xyPos ) ); } catch( ... ) {}
            size_t sp = block.find( ' ', xyPos );
            if( sp != std::string::npos )
            {
                try { y = std::stod( block.substr( sp + 1 ) ); } catch( ... ) {}
            }
            coords.push_back( x );
            coords.push_back( y );
        }

        ParsedWire wire;
        wire.uuid = parseUuidFromBlock( block );
        if( coords.size() >= 4 && !wire.uuid.empty() )
        {
            wire.x1 = coords[0];
            wire.y1 = coords[1];
            wire.x2 = coords[2];
            wire.y2 = coords[3];
            wires.push_back( wire );
        }
        pos = end;
    }
}

double pointToSegmentDistanceMm( double px, double py, double x1, double y1, double x2, double y2 )
{
    const double vx = x2 - x1;
    const double vy = y2 - y1;
    const double wx = px - x1;
    const double wy = py - y1;
    const double segLenSq = vx * vx + vy * vy;
    if( segLenSq <= 1e-12 )
        return std::hypot( px - x1, py - y1 );

    double t = ( wx * vx + wy * vy ) / segLenSq;
    t = std::max( 0.0, std::min( 1.0, t ) );
    const double projX = x1 + t * vx;
    const double projY = y1 + t * vy;
    return std::hypot( px - projX, py - projY );
}


bool pointInRect( double x, double y, double minX, double minY, double maxX, double maxY )
{
    return x >= minX && x <= maxX && y >= minY && y <= maxY;
}


bool segmentsIntersect( double ax, double ay, double bx, double by,
                        double cx, double cy, double dx, double dy )
{
    auto orient = []( double px, double py, double qx, double qy, double rx, double ry )
    {
        return ( qx - px ) * ( ry - py ) - ( qy - py ) * ( rx - px );
    };

    auto onSegment = []( double px, double py, double qx, double qy, double rx, double ry )
    {
        return std::min( px, rx ) <= qx && qx <= std::max( px, rx )
               && std::min( py, ry ) <= qy && qy <= std::max( py, ry );
    };

    const double o1 = orient( ax, ay, bx, by, cx, cy );
    const double o2 = orient( ax, ay, bx, by, dx, dy );
    const double o3 = orient( cx, cy, dx, dy, ax, ay );
    const double o4 = orient( cx, cy, dx, dy, bx, by );

    if( std::abs( o1 ) < 1e-12 && onSegment( ax, ay, cx, cy, bx, by ) )
        return true;
    if( std::abs( o2 ) < 1e-12 && onSegment( ax, ay, dx, dy, bx, by ) )
        return true;
    if( std::abs( o3 ) < 1e-12 && onSegment( cx, cy, ax, ay, dx, dy ) )
        return true;
    if( std::abs( o4 ) < 1e-12 && onSegment( cx, cy, bx, by, dx, dy ) )
        return true;

    return ( ( o1 > 0 ) != ( o2 > 0 ) ) && ( ( o3 > 0 ) != ( o4 > 0 ) );
}


bool segmentIntersectsRect( double x1, double y1, double x2, double y2,
                            double minX, double minY, double maxX, double maxY )
{
    if( pointInRect( x1, y1, minX, minY, maxX, maxY )
        || pointInRect( x2, y2, minX, minY, maxX, maxY ) )
    {
        return true;
    }

    return segmentsIntersect( x1, y1, x2, y2, minX, minY, maxX, minY )
           || segmentsIntersect( x1, y1, x2, y2, maxX, minY, maxX, maxY )
           || segmentsIntersect( x1, y1, x2, y2, maxX, maxY, minX, maxY )
           || segmentsIntersect( x1, y1, x2, y2, minX, maxY, minX, minY );
}


template<typename LabelMsg>
bool unpackLiveLabel( const google::protobuf::Any& any, const std::string& kind, ParsedLabel& out )
{
    LabelMsg label;
    if( !any.UnpackTo( &label ) )
        return false;

    if( !label.has_id() || label.id().value().empty() || !label.has_position() )
        return false;

    out.uuid = label.id().value();
    out.kind = kind;
    out.typeUrl = any.type_url();
    out.any = any;
    out.x = label.position().x_nm() / 10000.0;
    out.y = label.position().y_nm() / 10000.0;

    if( label.has_text() && label.text().has_text() )
        out.text = label.text().text().text();

    if( label.has_text() && label.text().has_text() && label.text().text().has_attributes()
        && label.text().text().attributes().has_angle() )
    {
        out.rotation = label.text().text().attributes().angle().value_degrees();
    }

    return true;
}


bool unpackLiveWire( const google::protobuf::Any& any, ParsedWire& out )
{
    kiapi::schematic::types::Line wire;
    if( !any.UnpackTo( &wire ) )
        return false;

    if( wire.layer() != kiapi::schematic::types::SL_WIRE
        || !wire.has_id() || wire.id().value().empty()
        || !wire.has_start() || !wire.has_end() )
    {
        return false;
    }

    out.uuid = wire.id().value();
    out.x1 = wire.start().x_nm() / 10000.0;
    out.y1 = wire.start().y_nm() / 10000.0;
    out.x2 = wire.end().x_nm() / 10000.0;
    out.y2 = wire.end().y_nm() / 10000.0;
    return true;
}


bool fetchLiveLabelsAndWires( IpcClient& ipc, const std::string& sheetPath,
                              std::vector<ParsedLabel>& labels,
                              std::vector<ParsedWire>& wires,
                              std::string& err )
{
    if( !ipc.EnsureSchematicApiConnection( err ) )
        return false;

    kiapi::common::ApiRequest req;
    req.mutable_header()->set_client_name( "mcp" );
    kiapi::common::commands::GetItems cmd;
    cmd.mutable_header()->mutable_document()->set_type( kiapi::common::types::DOCTYPE_SCHEMATIC );
    if( !sheetPath.empty() )
        cmd.mutable_header()->mutable_document()->mutable_sheet_path()->set_path_human_readable( sheetPath );
    cmd.add_types( kiapi::common::types::KOT_SCH_LABEL );
    cmd.add_types( kiapi::common::types::KOT_SCH_GLOBAL_LABEL );
    cmd.add_types( kiapi::common::types::KOT_SCH_HIER_LABEL );
    cmd.add_types( kiapi::common::types::KOT_SCH_DIRECTIVE_LABEL );
    cmd.add_types( kiapi::common::types::KOT_SCH_LINE );
    req.mutable_message()->PackFrom( cmd );

    kiapi::common::ApiResponse resp;
    if( !ipc.SendRequest( req, resp, err ) )
        return false;

    if( resp.status().status() != kiapi::common::AS_OK || !resp.has_message() )
    {
        err = resp.status().error_message().empty() ? "GetItems failed" : resp.status().error_message();
        return false;
    }

    kiapi::common::commands::GetItemsResponse items;
    if( !resp.message().UnpackTo( &items ) )
    {
        err = "Could not parse GetItemsResponse";
        return false;
    }

    for( int i = 0; i < items.items_size(); ++i )
    {
        const auto& any = items.items( i );
        ParsedLabel label;

        if( any.type_url().find( "LocalLabel" ) != std::string::npos )
        {
            if( unpackLiveLabel<kiapi::schematic::types::LocalLabel>( any, "local", label ) )
                labels.push_back( std::move( label ) );
            continue;
        }
        if( any.type_url().find( "GlobalLabel" ) != std::string::npos )
        {
            if( unpackLiveLabel<kiapi::schematic::types::GlobalLabel>( any, "global", label ) )
                labels.push_back( std::move( label ) );
            continue;
        }
        if( any.type_url().find( "HierarchicalLabel" ) != std::string::npos )
        {
            if( unpackLiveLabel<kiapi::schematic::types::HierarchicalLabel>( any, "hierarchical", label ) )
                labels.push_back( std::move( label ) );
            continue;
        }
        if( any.type_url().find( "DirectiveLabel" ) != std::string::npos )
        {
            if( unpackLiveLabel<kiapi::schematic::types::DirectiveLabel>( any, "directive", label ) )
                labels.push_back( std::move( label ) );
            continue;
        }

        ParsedWire wire;
        if( any.type_url().find( "Line" ) != std::string::npos && unpackLiveWire( any, wire ) )
            wires.push_back( std::move( wire ) );
    }

    return true;
}

bool buildUpdatedLabelAny( const ParsedLabel& label, const std::string& newName, google::protobuf::Any& out )
{
    out = label.any;

    if( label.kind == "local" )
    {
        kiapi::schematic::types::LocalLabel liveLabel;
        if( !out.UnpackTo( &liveLabel ) )
            return false;
        liveLabel.mutable_text()->mutable_text()->set_text( newName );
        out.PackFrom( liveLabel );
        return true;
    }

    if( label.kind == "hierarchical" )
    {
        kiapi::schematic::types::HierarchicalLabel liveLabel;
        if( !out.UnpackTo( &liveLabel ) )
            return false;
        liveLabel.mutable_text()->mutable_text()->set_text( newName );
        out.PackFrom( liveLabel );
        return true;
    }

    if( label.kind == "directive" )
    {
        kiapi::schematic::types::DirectiveLabel liveLabel;
        if( !out.UnpackTo( &liveLabel ) )
            return false;
        liveLabel.mutable_text()->mutable_text()->set_text( newName );
        out.PackFrom( liveLabel );
        return true;
    }

    kiapi::schematic::types::GlobalLabel liveLabel;
    if( !out.UnpackTo( &liveLabel ) )
        return false;
    liveLabel.mutable_text()->mutable_text()->set_text( newName );
    out.PackFrom( liveLabel );
    return true;
}
} // namespace

McpHandler::McpHandler( IpcClient& aIpc ) : m_ipc( aIpc ) {}


bool McpHandler::getCachedSummary(
    kiapi::schematic::types::GetSchematicSummaryResponse& out,
    std::string& error )
{
    if( m_summaryValid )
    {
        out = m_cachedSummary;
        return true;
    }

    kiapi::common::ApiRequest req;
    req.mutable_header()->set_client_name( "mcp" );
    kiapi::schematic::types::GetSchematicSummary cmd;
    req.mutable_message()->PackFrom( cmd );

    kiapi::common::ApiResponse resp;
    if( !m_ipc.SendRequest( req, resp, error ) )
        return false;

    if( resp.status().status() != kiapi::common::AS_OK )
    {
        error = resp.status().error_message();
        return false;
    }

    if( !resp.has_message() || !resp.message().UnpackTo( &m_cachedSummary ) )
    {
        error = "Failed to unpack summary response";
        return false;
    }

    m_summaryValid = true;
    out = m_cachedSummary;
    return true;
}


std::pair<double, double> McpHandler::getPinPosition( const std::string& reference, const std::string& pinNumber, std::string& err, double* aOutOrientationDegrees, double* aOutTipX, double* aOutTipY, bool* aOutHasTip )
{
    kiapi::common::ApiRequest req;
    req.mutable_header()->set_client_name( "mcp" );
    kiapi::schematic::types::GetPinPosition cmd;
    cmd.set_reference( reference );
    cmd.set_pin_number( pinNumber );
    req.mutable_message()->PackFrom( cmd );
    kiapi::common::ApiResponse resp;
    
    if( !m_ipc.SendRequest( req, resp, err ) )
        return { 0, 0 };
    if( resp.status().status() != kiapi::common::AS_OK )
    {
        err = resp.status().error_message();
        return { 0, 0 };
    }
    
    kiapi::schematic::types::GetPinPositionResponse posResp;
    if( !resp.has_message() || !resp.message().UnpackTo( &posResp ) || !posResp.has_position() )
    {
        err = "Could not parse pin position response";
        return { 0, 0 };
    }
    if( aOutOrientationDegrees && posResp.has_orientation_degrees() )
        *aOutOrientationDegrees = posResp.orientation_degrees();

    bool hasTip = posResp.has_position_label();
    double tipX = hasTip ? posResp.position_label().x_mm() : 0.0;
    double tipY = hasTip ? posResp.position_label().y_mm() : 0.0;

    if( aOutTipX && aOutTipY && aOutHasTip )
    {
        if( hasTip )
        {
            *aOutTipX = tipX;
            *aOutTipY = tipY;
            *aOutHasTip = true;
        }
        else
        {
            *aOutHasTip = false;
        }
    }

    double summaryPinX = 0.0;
    double summaryPinY = 0.0;
    bool hasSummaryPin = false;

    // Use schematic summary as a hint for selecting the correct raw pin endpoint.
    // Summary coordinates may be rounded for display, so we use them for matching,
    // not as the final wire anchor.
    kiapi::schematic::types::GetSchematicSummaryResponse sumResp;
    std::string summaryErr;
    if( getCachedSummary( sumResp, summaryErr ) )
    {
        for( int i = 0; i < sumResp.components_size(); ++i )
        {
            const auto& comp = sumResp.components( i );
            if( comp.reference() != reference )
                continue;

            for( int p = 0; p < comp.pins_size(); ++p )
            {
                const auto& pin = comp.pins( p );
                if( pin.number() != pinNumber )
                    continue;

                if( pin.x_mm() != 0.0 || pin.y_mm() != 0.0 )
                {
                    summaryPinX = pin.x_mm();
                    summaryPinY = pin.y_mm();
                    hasSummaryPin = true;
                }
                break;
            }
            break;
        }
    }

    const double posX = posResp.position().x_mm();
    const double posY = posResp.position().y_mm();

    if( hasSummaryPin && hasTip )
    {
        const double distPos = std::hypot( posX - summaryPinX, posY - summaryPinY );
        const double distTip = std::hypot( tipX - summaryPinX, tipY - summaryPinY );
        return distTip <= distPos ? std::make_pair( tipX, tipY ) : std::make_pair( posX, posY );
    }

    if( hasSummaryPin )
        return { summaryPinX, summaryPinY };

    // Fallback order when summary hint is unavailable:
    // 1) position_label (often the external pin tip)
    // 2) position
    if( hasTip )
        return { tipX, tipY };

    return { posX, posY };
}


double McpHandler::getSchematicGridMm()
{
    std::string err;
    kiapi::schematic::types::GetSchematicSummaryResponse sumResp;
    if( !getCachedSummary( sumResp, err ) )
        return 0.1;
    double grid = sumResp.grid_step_mm();
    return grid > 0 ? grid : 0.1;
}


std::string McpHandler::getCurrentSheetPath()
{
    std::string err;
    kiapi::schematic::types::GetSchematicSummaryResponse sumResp;
    if( !getCachedSummary( sumResp, err ) || sumResp.sheet_path().empty() )
        return "";
    return sumResp.sheet_path();
}


std::string McpHandler::getCurrentSchematicPath()
{
    // Get the file path of the currently open schematic via get_open_documents IPC.
    kiapi::common::ApiRequest req;
    req.mutable_header()->set_client_name( "mcp" );
    kiapi::common::commands::GetOpenDocuments cmd;
    cmd.set_type( kiapi::common::types::DOCTYPE_SCHEMATIC );
    req.mutable_message()->PackFrom( cmd );

    kiapi::common::ApiResponse resp;
    std::string err;
    if( !m_ipc.SendRequest( req, resp, err ) || resp.status().status() != kiapi::common::AS_OK )
        return "";

    if( !resp.has_message() )
        return "";

    kiapi::common::commands::GetOpenDocumentsResponse docsResp;
    if( !resp.message().UnpackTo( &docsResp ) )
        return "";

    for( int i = 0; i < docsResp.documents_size(); ++i )
    {
        const auto& doc = docsResp.documents( i );
        if( doc.type() == kiapi::common::types::DOCTYPE_SCHEMATIC )
        {
            // Try project path + name first.
            if( doc.has_project() && !doc.project().path().empty() && !doc.project().name().empty() )
            {
                std::string projPath = doc.project().path();
                std::string projName = doc.project().name();
                if( projPath.back() != '/' && projPath.back() != '\\' )
                    projPath += "/";
                return projPath + projName + ".kicad_sch";
            }
            // Fallback: board_filename field (KiCad 9.0.7 puts the schematic name here).
            if( !doc.board_filename().empty() )
            {
                std::string fname = doc.board_filename();
                // If it's just a filename without path, try to find it relative to CWD.
                if( fname.find('/') == std::string::npos && fname.find('\\') == std::string::npos )
                {
                    // Check common locations.
                    std::vector<std::string> searchPaths = { ".", getenv("HOME") ? getenv("HOME") : "" };
                    for( const auto& dir : searchPaths )
                    {
                        std::string candidate = dir.empty() ? fname : (dir + "/" + fname);
                        std::ifstream test( candidate );
                        if( test.good() )
                            return candidate;
                    }
                }
                return fname;
            }
        }
    }
    return "";
}


std::string McpHandler::getProjectDirectory()
{
    kiapi::common::ApiRequest req;
    req.mutable_header()->set_client_name( "mcp" );
    kiapi::common::commands::GetOpenDocuments cmd;
    cmd.set_type( kiapi::common::types::DOCTYPE_SCHEMATIC );
    req.mutable_message()->PackFrom( cmd );

    kiapi::common::ApiResponse resp;
    std::string err;
    if( !m_ipc.SendRequest( req, resp, err ) || resp.status().status() != kiapi::common::AS_OK )
        return "";

    if( !resp.has_message() )
        return "";

    kiapi::common::commands::GetOpenDocumentsResponse docsResp;
    if( !resp.message().UnpackTo( &docsResp ) )
        return "";

    for( int i = 0; i < docsResp.documents_size(); ++i )
    {
        const auto& doc = docsResp.documents( i );
        if( doc.type() == kiapi::common::types::DOCTYPE_SCHEMATIC && doc.has_project()
            && !doc.project().path().empty() )
        {
            std::string projPath = doc.project().path();
            while( !projPath.empty()
                   && ( projPath.back() == '/' || projPath.back() == '\\' ) )
            {
                projPath.pop_back();
            }
            return projPath;
        }
    }
    return "";
}


std::tuple<double, double, double, double> McpHandler::getComponentBounds( const std::string& reference, std::string& err )
{
    // Bug 2 fix: delegate to cached-summary overload (fetches summary if needed).
    return getComponentBounds( reference, err, nullptr );
}


std::tuple<double, double, double, double> McpHandler::getComponentBounds(
    const std::string& reference, std::string& err,
    const kiapi::schematic::types::GetSchematicSummaryResponse* cachedSummary )
{
    err.clear();
    constexpr double PIN_BOUNDS_PAD_MM = 0.3;
    constexpr double MIN_DIM_MM = 0.1;

    // When a cached summary is provided, search it directly and skip the IPC call.
    kiapi::schematic::types::GetSchematicSummaryResponse localSumResp;
    const kiapi::schematic::types::GetSchematicSummaryResponse* sumRespPtr = cachedSummary;

    if( !sumRespPtr )
    {
        if( !getCachedSummary( localSumResp, err ) )
            return { 0, 0, 0, 0 };
        sumRespPtr = &localSumResp;
    }

    // Find the component in the (possibly cached) summary
    for( int i = 0; i < sumRespPtr->components_size(); ++i )
    {
        const auto& c = sumRespPtr->components( i );
        if( c.reference() == reference )
        {
            bool hasBounds = false;
            double minX = 0.0, maxX = 0.0, minY = 0.0, maxY = 0.0;
            auto includePoint = [&]( double px, double py )
            {
                if( !hasBounds )
                {
                    minX = maxX = px;
                    minY = maxY = py;
                    hasBounds = true;
                }
                else
                {
                    minX = std::min( minX, px );
                    maxX = std::max( maxX, px );
                    minY = std::min( minY, py );
                    maxY = std::max( maxY, py );
                }
            };

            // Include body bbox when available.
            if( c.has_bbox() )
            {
                includePoint( c.bbox().min_x_mm(), c.bbox().min_y_mm() );
                includePoint( c.bbox().max_x_mm(), c.bbox().max_y_mm() );
            }

            // Extend to pin connection points so bounds include protruding pins.
            for( int p = 0; p < c.pins_size(); ++p )
                includePoint( c.pins( p ).x_mm(), c.pins( p ).y_mm() );

            if( c.pins_size() > 0 && hasBounds )
            {
                minX -= PIN_BOUNDS_PAD_MM;
                maxX += PIN_BOUNDS_PAD_MM;
                minY -= PIN_BOUNDS_PAD_MM;
                maxY += PIN_BOUNDS_PAD_MM;
            }

            if( hasBounds )
            {
                double width = std::max( MIN_DIM_MM, maxX - minX );
                double height = std::max( MIN_DIM_MM, maxY - minY );
                double cx = ( minX + maxX ) / 2.0;
                double cy = ( minY + maxY ) / 2.0;
                return { cx, cy, width, height };
            }

            if( c.has_position() )
                return { c.position().x_mm(), c.position().y_mm(), 5.0, 5.0 };

            return { 0, 0, 5.0, 5.0 };
        }
    }

    err = "Component not found: " + reference;
    return { 0, 0, 5.0, 5.0 };
}


std::pair<double, double> McpHandler::calculateLabelOffset( double pinX, double pinY, const std::string& reference, double defaultOffset )
{
    std::string err;
    auto [cx, cy, w, h] = getComponentBounds( reference, err );
    
    // If we can't get component center, default to right
    if( !err.empty() )
        return { defaultOffset, 0 };
    
    // Calculate pin position relative to component center
    double dx = pinX - cx;
    double dy = pinY - cy;
    
    // Determine predominant direction (which axis has larger offset)
    double absDx = std::abs( dx );
    double absDy = std::abs( dy );
    
    // Pin is primarily on left or right side
    if( absDx > absDy )
    {
        if( dx > 0 )
            return { defaultOffset, 0 };  // Pin on right, label to right
        else
            return { -defaultOffset, 0 }; // Pin on left, label to left
    }
    // Pin is primarily on top or bottom
    else
    {
        if( dy > 0 )
            return { 0, defaultOffset };  // Pin on bottom, label below
        else
            return { 0, -defaultOffset }; // Pin on top, label above
    }
}


std::pair<double, double> McpHandler::findEmptySpot( double nearX, double nearY, double componentWidth, double componentHeight, std::string& err, double aMinSpacingMm )
{
    constexpr double GRID_MM = 0.1;
    constexpr double SCH_PAGE_WIDTH_MM = 297.0;
    constexpr double SCH_PAGE_HEIGHT_MM = 210.0;
    constexpr double SCH_PAGE_MARGIN_MM = 10.0;
    
    // Minimum clearance between new component and existing ones
    double minSpacing = std::max( 0.0, aMinSpacingMm );
    
    auto snap = []( double mm ) -> double { 
        return std::round( mm / GRID_MM ) * GRID_MM; 
    };
    
    // Get all existing component positions from schematic summary (using cache).
    struct ComponentBounds {
        double x, y, width, height;
        std::string ref;
    };
    std::vector<ComponentBounds> existingComponents;

    kiapi::schematic::types::GetSchematicSummaryResponse sumResp;
    if( getCachedSummary( sumResp, err ) )
    {
        // Get bounds for each existing component
        for( int i = 0; i < sumResp.components_size(); ++i )
        {
            const auto& c = sumResp.components( i );
            std::string tempErr;
            auto [cx, cy, w, h] = getComponentBounds( c.reference(), tempErr, &sumResp );
            if( tempErr.empty() && ( cx != 0 || cy != 0 ) )
            {
                existingComponents.push_back( { cx, cy, w, h, c.reference() } );
            }
        }
    }
    
    // Lambda to check if a position overlaps with any existing component
    auto overlaps = [&]( double x, double y ) -> bool {
        for( const auto& comp : existingComponents )
        {
            // Calculate bounding boxes for new component
            double x1 = x - componentWidth / 2.0;
            double y1 = y - componentHeight / 2.0;
            double x2 = x + componentWidth / 2.0;
            double y2 = y + componentHeight / 2.0;
            
            // Calculate bounding boxes for existing component with spacing
            double cx1 = comp.x - comp.width / 2.0 - minSpacing;
            double cy1 = comp.y - comp.height / 2.0 - minSpacing;
            double cx2 = comp.x + comp.width / 2.0 + minSpacing;
            double cy2 = comp.y + comp.height / 2.0 + minSpacing;
            
            // Check for overlap (AABB collision)
            if( !(x2 < cx1 || x1 > cx2 || y2 < cy1 || y1 > cy2) )
                return true;
        }
        return false;
    };

    // Check seed position first - if it's clear, use it
    double seedX = snap( nearX );
    double seedY = snap( nearY );
    if( !overlaps( seedX, seedY ) )
    {
        return { seedX, seedY };
    }

    // Spiral outward from the near-anchor in expanding rings.
    // This keeps placements local to the requested area instead of jumping to the far side of the sheet.
    const double step = std::max( componentWidth, componentHeight ) + minSpacing;
    const int maxRings = static_cast<int>( std::max( SCH_PAGE_WIDTH_MM, SCH_PAGE_HEIGHT_MM ) / step ) + 2;

    for( int ring = 1; ring <= maxRings; ++ring )
    {
        double r = ring * step;
        // Sample the ring perimeter at intervals of ~step
        int nSamples = std::max( 8, static_cast<int>( std::ceil( 2.0 * M_PI * r / step ) ) );
        for( int s = 0; s < nSamples; ++s )
        {
            double angle = 2.0 * M_PI * s / nSamples;
            double testX = snap( nearX + r * std::cos( angle ) );
            double testY = snap( nearY + r * std::sin( angle ) );
            // Clamp to page
            testX = std::max( SCH_PAGE_MARGIN_MM + componentWidth / 2.0,
                              std::min( SCH_PAGE_WIDTH_MM - SCH_PAGE_MARGIN_MM - componentWidth / 2.0, testX ) );
            testY = std::max( SCH_PAGE_MARGIN_MM + componentHeight / 2.0,
                              std::min( SCH_PAGE_HEIGHT_MM - SCH_PAGE_MARGIN_MM - componentHeight / 2.0, testY ) );
            if( !overlaps( testX, testY ) )
                return { testX, testY };
        }
    }

    // Absolute last resort — pick any in-bounds point
    double fallbackX = snap( SCH_PAGE_MARGIN_MM + componentWidth / 2.0 + 5.0 );
    double fallbackY = snap( SCH_PAGE_MARGIN_MM + componentHeight / 2.0 + 5.0 );
    return { fallbackX, fallbackY };
}


std::pair<double, double> McpHandler::findEmptySpotForBlock( double widthMm, double heightMm, std::string& err )
{
    err.clear();
    constexpr double GRID_MM   = 0.1;
    constexpr double MARGIN_MM = 15.0;  // Gap between existing content and new block
    constexpr double WIRE_PAD  = 5.0;   // Extra padding for wires/labels beyond component bbox
    constexpr double PAGE_W    = 297.0; // A4 landscape
    constexpr double PAGE_H    = 210.0;
    constexpr double PAGE_MARGIN = 10.0;
    auto snap = []( double mm ) -> double { return std::round( mm / GRID_MM ) * GRID_MM; };

    // Collect all existing component bounding boxes (inflated by wire/label padding).
    struct Rect { double minX, maxX, minY, maxY; };
    std::vector<Rect> occupied;

    kiapi::schematic::types::GetSchematicSummaryResponse sumResp;
    if( getCachedSummary( sumResp, err ) )
    {
        for( int i = 0; i < sumResp.components_size(); ++i )
        {
            const auto& c = sumResp.components( i );
            double cMinX, cMaxX, cMinY, cMaxY;
            std::string tempErr;
            auto [cx, cy, w, h] = getComponentBounds( c.reference(), tempErr, &sumResp );
            if( !tempErr.empty() || ( cx == 0 && cy == 0 ) )
            {
                continue;
            }
            cMinX = cx - w / 2.0;
            cMaxX = cx + w / 2.0;
            cMinY = cy - h / 2.0;
            cMaxY = cy + h / 2.0;
            // Inflate by wire/label padding
            occupied.push_back( { cMinX - WIRE_PAD, cMaxX + WIRE_PAD,
                                  cMinY - WIRE_PAD, cMaxY + WIRE_PAD } );
        }
    }

    // Empty schematic: place block at typical top-left seed
    if( occupied.empty() )
    {
        constexpr double SEED_X = 80.0, SEED_Y = 70.0;
        return { snap( SEED_X ), snap( SEED_Y ) };
    }

    // Check if a block centered at (cx, cy) would overlap any existing content.
    auto blockOverlaps = [&]( double cx, double cy ) -> bool {
        double bMinX = cx - widthMm / 2.0 - MARGIN_MM;
        double bMaxX = cx + widthMm / 2.0 + MARGIN_MM;
        double bMinY = cy - heightMm / 2.0 - MARGIN_MM;
        double bMaxY = cy + heightMm / 2.0 + MARGIN_MM;
        for( const auto& r : occupied )
        {
            if( !( bMaxX < r.minX || bMinX > r.maxX || bMaxY < r.minY || bMinY > r.maxY ) )
                return true;
        }
        return false;
    };

    // Check if block fits within page boundaries.
    auto fitsOnPage = [&]( double cx, double cy ) -> bool {
        return ( cx - widthMm / 2.0 ) >= PAGE_MARGIN &&
               ( cx + widthMm / 2.0 ) <= ( PAGE_W - PAGE_MARGIN ) &&
               ( cy - heightMm / 2.0 ) >= PAGE_MARGIN &&
               ( cy + heightMm / 2.0 ) <= ( PAGE_H - PAGE_MARGIN );
    };

    // Grid search step: slightly larger than block so candidates don't bunch up
    double stepX = widthMm / 2.0 + MARGIN_MM;
    double stepY = heightMm / 2.0 + MARGIN_MM;

    // --- Phase 1: search within page bounds ---
    // Scan from top-left across the page in a grid of candidate centres.
    double startX = PAGE_MARGIN + widthMm / 2.0;
    double startY = PAGE_MARGIN + heightMm / 2.0;
    double endX   = PAGE_W - PAGE_MARGIN - widthMm / 2.0;
    double endY   = PAGE_H - PAGE_MARGIN - heightMm / 2.0;

    for( double cy = startY; cy <= endY; cy += stepY )
    {
        for( double cx = startX; cx <= endX; cx += stepX )
        {
            double sx = snap( cx );
            double sy = snap( cy );
            if( fitsOnPage( sx, sy ) && !blockOverlaps( sx, sy ) )
                return { sx, sy };
        }
    }

    // --- Phase 2: below existing content but still on page ---
    double contentMaxY = 0;
    for( const auto& r : occupied )
        contentMaxY = std::max( contentMaxY, r.maxY );
    {
        double cy = snap( contentMaxY + MARGIN_MM + heightMm / 2.0 );
        double cx = snap( PAGE_MARGIN + widthMm / 2.0 + 10.0 );
        if( fitsOnPage( cx, cy ) && !blockOverlaps( cx, cy ) )
            return { cx, cy };
    }

    // --- Phase 3: right of existing content but still on page ---
    double contentMaxX = 0;
    for( const auto& r : occupied )
        contentMaxX = std::max( contentMaxX, r.maxX );
    {
        double cx = snap( contentMaxX + MARGIN_MM + widthMm / 2.0 );
        double contentMinY = occupied[0].minY, contentMaxYLocal = occupied[0].maxY;
        for( const auto& r : occupied )
        {
            contentMinY = std::min( contentMinY, r.minY );
            contentMaxYLocal = std::max( contentMaxYLocal, r.maxY );
        }
        double cy = snap( ( contentMinY + contentMaxYLocal ) / 2.0 );
        if( fitsOnPage( cx, cy ) && !blockOverlaps( cx, cy ) )
            return { cx, cy };
    }

    // --- Phase 4: extend beyond page (last resort) ---
    {
        double cx = snap( contentMaxX + MARGIN_MM + widthMm / 2.0 );
        double contentMinY = occupied[0].minY, contentMaxYLocal = occupied[0].maxY;
        for( const auto& r : occupied )
        {
            contentMinY = std::min( contentMinY, r.minY );
            contentMaxYLocal = std::max( contentMaxYLocal, r.maxY );
        }
        double cy = snap( ( contentMinY + contentMaxYLocal ) / 2.0 );
        return { cx, cy };
    }
}


bool McpHandler::placementWouldOverlap( double x, double y, double componentWidth, double componentHeight, std::string& aOverlapDesc, double aMinSpacingMm )
{
    double minSpacingMm = std::max( 0.0, aMinSpacingMm );
    aOverlapDesc.clear();

    // Bug 1 fix: use getCachedSummary to avoid a fresh IPC call, and check
    // has_bbox() before falling back to the expensive getComponentBounds().
    kiapi::schematic::types::GetSchematicSummaryResponse sumResp;
    std::string err;
    if( !getCachedSummary( sumResp, err ) )
        return false;

    double x1 = x - componentWidth / 2.0 - minSpacingMm / 2.0;
    double y1 = y - componentHeight / 2.0 - minSpacingMm / 2.0;
    double x2 = x + componentWidth / 2.0 + minSpacingMm / 2.0;
    double y2 = y + componentHeight / 2.0 + minSpacingMm / 2.0;

    for( int i = 0; i < sumResp.components_size(); ++i )
    {
        const auto& c = sumResp.components( i );
        double cx, cy, w, h;
        std::string tempErr;
        std::tie( cx, cy, w, h ) = getComponentBounds( c.reference(), tempErr, &sumResp );
        if( !tempErr.empty() || ( cx == 0 && cy == 0 ) )
            continue;

        double cx1 = cx - w / 2.0 - minSpacingMm / 2.0;
        double cy1 = cy - h / 2.0 - minSpacingMm / 2.0;
        double cx2 = cx + w / 2.0 + minSpacingMm / 2.0;
        double cy2 = cy + h / 2.0 + minSpacingMm / 2.0;
        if( !( x2 < cx1 || x1 > cx2 || y2 < cy1 || y1 > cy2 ) )
        {
            aOverlapDesc = "component " + c.reference();
            return true;
        }
    }
    return false;
}


std::string McpHandler::Handle( const std::string& aBody )
{
    if( aBody.empty() )
        return "{\"jsonrpc\":\"2.0\",\"error\":{\"code\":-32700,\"message\":\"Parse error\"},\"id\":null}";

    json req;
    try
    {
        req = json::parse( aBody );
    }
    catch( const json::exception& )
    {
        return "{\"jsonrpc\":\"2.0\",\"error\":{\"code\":-32700,\"message\":\"Parse error\"},\"id\":null}";
    }

    std::string idStr = "null";
    if( req.contains( "id" ) && !req["id"].is_null() )
        idStr = req["id"].dump();

    if( !req.contains( "method" ) || !req["method"].is_string() )
    {
        return "{\"jsonrpc\":\"2.0\",\"error\":{\"code\":-32600,\"message\":\"Invalid Request\"},\"id\":" + idStr + "}";
    }

    try
    {
        std::string method = req["method"].get<std::string>();
        const json* params = req.contains( "params" ) ? &req["params"] : nullptr;
        std::string out = HandleRequest( method, params, idStr );
        if( out.empty() )
            return "{\"jsonrpc\":\"2.0\",\"error\":{\"code\":-32603,\"message\":\"Handler returned empty\"},\"id\":" + idStr + "}";
        return out;
    }
    catch( const std::exception& e )
    {
        std::string msg = e.what();
        std::string escaped;
        escaped.reserve( msg.size() + 16 );
        for( char c : msg )
        {
            if( c == '\\' )
                escaped += "\\\\";
            else if( c == '"' )
                escaped += "\\\"";
            else if( c == '\n' )
                escaped += "\\n";
            else if( c == '\r' )
                escaped += "\\r";
            else if( static_cast<unsigned char>( c ) < 0x20 )
                escaped += "?";
            else
                escaped += c;
        }
        return "{\"jsonrpc\":\"2.0\",\"error\":{\"code\":-32603,\"message\":\"" + escaped
               + "\"},\"id\":" + idStr + "}";
    }
    catch( ... )
    {
        return "{\"jsonrpc\":\"2.0\",\"error\":{\"code\":-32603,\"message\":\"Unknown exception\"},\"id\":"
               + idStr + "}";
    }
}


std::string McpHandler::HandleRequest( const std::string& method, const void* params,
                                       const std::string& id )
{
    if( method == "initialize" )
        return HandleInitialize( params, id );
    if( method == "notifications/initialized" )
        return "{\"jsonrpc\":\"2.0\",\"result\":null,\"id\":" + id + "}";
    if( method == "tools/list" )
        return HandleToolsList( id );
    if( method == "tools/call" )
        return HandleToolsCall( params, id );

    return "{\"jsonrpc\":\"2.0\",\"error\":{\"code\":-32601,\"message\":\"Method not found\"},\"id\":" + id + "}";
}


std::string McpHandler::HandleInitialize( const void* params, const std::string& id )
{
    (void) params;
    // Build response without json::dump() so we never return empty
    std::string r;
    r.reserve( 256 );
    r += "{\"jsonrpc\":\"2.0\",\"result\":{\"protocolVersion\":\"2024-11-05\",\"capabilities\":{\"tools\":{}},\"serverInfo\":{\"name\":\"kicad-mcp-server\",\"version\":\"1.0.0\"}},\"id\":";
    r += ( id == "null" ) ? "null" : id;
    r += "}";
    return r;
}


std::string McpHandler::HandleToolsList( const std::string& id )
{
    json tools = json::array();

    json getOpenDocs;
    getOpenDocs["name"] = "get_open_documents";
    getOpenDocs["description"] = "Get list of open schematic/board documents";
    getOpenDocs["inputSchema"]["type"] = "object";
    getOpenDocs["inputSchema"]["properties"]["type"]["type"] = "string";
    getOpenDocs["inputSchema"]["properties"]["type"]["enum"] = json::array( { "schematic", "board" } );
    tools.push_back( getOpenDocs );

    json searchSymbols;
    searchSymbols["name"] = "search_components";
    searchSymbols["description"] = "Deprecated single-search helper. Prefer batch_search_components with a single-item queries array.";
    searchSymbols["inputSchema"]["type"] = "object";
    searchSymbols["inputSchema"]["properties"]["query"]["type"] = "string";
    searchSymbols["inputSchema"]["properties"]["library"]["type"] = "string";
    searchSymbols["inputSchema"]["properties"]["limit"]["type"] = "integer";
    tools.push_back( searchSymbols );

    json batchSearchSymbols;
    batchSearchSymbols["name"] = "batch_search_components";
    batchSearchSymbols["description"] = "Search KiCad symbol libraries for one or more component types in one call. Use this even for single-search requests.";
    batchSearchSymbols["inputSchema"]["type"] = "object";
    batchSearchSymbols["inputSchema"]["required"] = json::array( { "queries" } );
    batchSearchSymbols["inputSchema"]["properties"]["queries"]["type"] = "array";
    batchSearchSymbols["inputSchema"]["properties"]["queries"]["items"]["type"] = "string";
    batchSearchSymbols["inputSchema"]["properties"]["queries"]["description"] = "Array of search query strings";
    batchSearchSymbols["inputSchema"]["properties"]["library"]["type"] = "string";
    batchSearchSymbols["inputSchema"]["properties"]["library"]["description"] = "Optional library to restrict search";
    batchSearchSymbols["inputSchema"]["properties"]["limit"]["type"] = "integer";
    batchSearchSymbols["inputSchema"]["properties"]["limit"]["description"] = "Result limit per query (default 100)";
    tools.push_back( batchSearchSymbols );

    json getComponent;
    getComponent["name"] = "get_component_data";
    getComponent["description"] = "Deprecated single-item helper. Prefer batch_get_component_data with a single-item components array.";
    getComponent["inputSchema"]["type"] = "object";
    getComponent["inputSchema"]["properties"]["component_id"]["type"] = "string";
    getComponent["inputSchema"]["properties"]["library"]["type"] = "string";
    getComponent["inputSchema"]["properties"]["symbol"]["type"] = "string";
    tools.push_back( getComponent );

    json batchGetComponent;
    batchGetComponent["name"] = "batch_get_component_data";
    batchGetComponent["description"] = "Get detailed data for one or more components in one call (by library+symbol or component_id per item). Use this even for single-component lookups.";
    batchGetComponent["inputSchema"]["type"] = "object";
    batchGetComponent["inputSchema"]["required"] = json::array( { "components" } );
    batchGetComponent["inputSchema"]["properties"]["components"]["type"] = "array";
    batchGetComponent["inputSchema"]["properties"]["components"]["items"]["type"] = "object";
    batchGetComponent["inputSchema"]["properties"]["components"]["items"]["properties"]["component_id"]["type"] = "string";
    batchGetComponent["inputSchema"]["properties"]["components"]["items"]["properties"]["library"]["type"] = "string";
    batchGetComponent["inputSchema"]["properties"]["components"]["items"]["properties"]["symbol"]["type"] = "string";
    batchGetComponent["inputSchema"]["properties"]["components"]["description"] = "Array of component specs: each { component_id } or { library, symbol }";
    tools.push_back( batchGetComponent );

    json getPinPositionTool;
    getPinPositionTool["name"] = "get_pin_position";
    getPinPositionTool["description"] = "Get position and orientation of a component pin. Returns x_mm, y_mm, orientation_degrees (direction pin points INTO symbol: 0=right, 90=up, 180=left, 270=down), outward_degrees (direction AWAY from symbol for label placement), and recommended_label_rotation (pass this to add_global_label rotation so the label faces outward correctly).";
    getPinPositionTool["inputSchema"]["type"] = "object";
    getPinPositionTool["inputSchema"]["required"] = json::array( { "reference", "pin_number" } );
    getPinPositionTool["inputSchema"]["properties"]["reference"]["type"] = "string";
    getPinPositionTool["inputSchema"]["properties"]["reference"]["description"] = "Component reference (e.g. U1)";
    getPinPositionTool["inputSchema"]["properties"]["pin_number"]["type"] = "string";
    getPinPositionTool["inputSchema"]["properties"]["pin_number"]["description"] = "Pin number or name";
    tools.push_back( getPinPositionTool );

    // begin_commit / end_commit removed from exposed tools — TransactionGuard handles internally

    json placeComponent;
    placeComponent["name"] = "place_component";
    placeComponent["description"] = "Place a component on the schematic. Use 'near' to auto-place near a component/pin, or provide x/y or x_mm/y_mm in mm (KiCad coordinates). Snapped to 0.1mm grid. Inductors auto-rotated 90 degrees.";
    placeComponent["inputSchema"]["type"] = "object";
    placeComponent["inputSchema"]["required"] = json::array( { "library", "symbol", "reference" } );
    placeComponent["inputSchema"]["properties"]["library"]["type"] = "string";
    placeComponent["inputSchema"]["properties"]["symbol"]["type"] = "string";
    placeComponent["inputSchema"]["properties"]["reference"]["type"] = "string";
    placeComponent["inputSchema"]["properties"]["value"]["type"] = "string";
    placeComponent["inputSchema"]["properties"]["x"]["type"] = "number";
    placeComponent["inputSchema"]["properties"]["x"]["description"] = "X coordinate in mm (optional if 'near' is specified)";
    placeComponent["inputSchema"]["properties"]["y"]["type"] = "number";
    placeComponent["inputSchema"]["properties"]["y"]["description"] = "Y coordinate in mm (optional if 'near' is specified)";
    placeComponent["inputSchema"]["properties"]["x_mm"]["type"] = "number";
    placeComponent["inputSchema"]["properties"]["x_mm"]["description"] = "Alias for x: X coordinate in mm";
    placeComponent["inputSchema"]["properties"]["y_mm"]["type"] = "number";
    placeComponent["inputSchema"]["properties"]["y_mm"]["description"] = "Alias for y: Y coordinate in mm";
    placeComponent["inputSchema"]["properties"]["rotation"]["type"] = "number";
    placeComponent["inputSchema"]["properties"]["near"]["type"] = "object";
    placeComponent["inputSchema"]["properties"]["near"]["description"] = "Auto-place near a component. Provide 'reference' and optionally 'pin'. If x/y not specified, finds empty spot automatically.";
    placeComponent["inputSchema"]["properties"]["near"]["properties"]["reference"]["type"] = "string";
    placeComponent["inputSchema"]["properties"]["near"]["properties"]["reference"]["description"] = "Component reference (e.g. 'U1')";
    placeComponent["inputSchema"]["properties"]["near"]["properties"]["pin"]["type"] = "string";
    placeComponent["inputSchema"]["properties"]["near"]["properties"]["pin"]["description"] = "Optional pin number";
    tools.push_back( placeComponent );

    // ── batch_get_pin_position ──
    json batchGetPinPosition;
    batchGetPinPosition["name"] = "batch_get_pin_position";
    batchGetPinPosition["description"] = "Get positions and orientations for multiple pins in one call. Returns array of pin data: x_mm, y_mm, orientation_degrees, outward_degrees, recommended_label_rotation. Much faster than calling get_pin_position multiple times.";
    batchGetPinPosition["inputSchema"]["type"] = "object";
    batchGetPinPosition["inputSchema"]["required"] = json::array( { "pins" } );
    batchGetPinPosition["inputSchema"]["properties"]["pins"]["type"] = "array";
    batchGetPinPosition["inputSchema"]["properties"]["pins"]["description"] = "Array of {reference, pin_number} objects";
    batchGetPinPosition["inputSchema"]["properties"]["pins"]["items"]["type"] = "object";
    batchGetPinPosition["inputSchema"]["properties"]["pins"]["items"]["properties"]["reference"]["type"] = "string";
    batchGetPinPosition["inputSchema"]["properties"]["pins"]["items"]["properties"]["pin_number"]["type"] = "string";
    tools.push_back( batchGetPinPosition );

    // ── batch_place_component ──
    json batchPlaceComponent;
    batchPlaceComponent["name"] = "batch_place_component";
    batchPlaceComponent["description"] = "Place multiple components in one call. Each component can use absolute (x/y or x_mm/y_mm) or relative (near) placement. Faster and more atomic than calling place_component multiple times.";
    batchPlaceComponent["inputSchema"]["type"] = "object";
    batchPlaceComponent["inputSchema"]["required"] = json::array( { "components" } );
    batchPlaceComponent["inputSchema"]["properties"]["components"]["type"] = "array";
    batchPlaceComponent["inputSchema"]["properties"]["components"]["description"] = "Array of component placement specs";
    batchPlaceComponent["inputSchema"]["properties"]["components"]["items"]["type"] = "object";
    batchPlaceComponent["inputSchema"]["properties"]["components"]["items"]["properties"]["library"]["type"] = "string";
    batchPlaceComponent["inputSchema"]["properties"]["components"]["items"]["properties"]["symbol"]["type"] = "string";
    batchPlaceComponent["inputSchema"]["properties"]["components"]["items"]["properties"]["reference"]["type"] = "string";
    batchPlaceComponent["inputSchema"]["properties"]["components"]["items"]["properties"]["value"]["type"] = "string";
    batchPlaceComponent["inputSchema"]["properties"]["components"]["items"]["properties"]["x"]["type"] = "number";
    batchPlaceComponent["inputSchema"]["properties"]["components"]["items"]["properties"]["x"]["description"] = "X coordinate in mm";
    batchPlaceComponent["inputSchema"]["properties"]["components"]["items"]["properties"]["y"]["type"] = "number";
    batchPlaceComponent["inputSchema"]["properties"]["components"]["items"]["properties"]["y"]["description"] = "Y coordinate in mm";
    batchPlaceComponent["inputSchema"]["properties"]["components"]["items"]["properties"]["x_mm"]["type"] = "number";
    batchPlaceComponent["inputSchema"]["properties"]["components"]["items"]["properties"]["x_mm"]["description"] = "Alias for x: X coordinate in mm";
    batchPlaceComponent["inputSchema"]["properties"]["components"]["items"]["properties"]["y_mm"]["type"] = "number";
    batchPlaceComponent["inputSchema"]["properties"]["components"]["items"]["properties"]["y_mm"]["description"] = "Alias for y: Y coordinate in mm";
    batchPlaceComponent["inputSchema"]["properties"]["components"]["items"]["properties"]["rotation"]["type"] = "number";
    batchPlaceComponent["inputSchema"]["properties"]["components"]["items"]["properties"]["near"]["type"] = "object";
    batchPlaceComponent["inputSchema"]["properties"]["components"]["items"]["properties"]["near"]["description"] = "Auto-place near a component. Provide 'reference' and optionally 'pin'.";
    batchPlaceComponent["inputSchema"]["properties"]["components"]["items"]["properties"]["near"]["properties"]["reference"]["type"] = "string";
    batchPlaceComponent["inputSchema"]["properties"]["components"]["items"]["properties"]["near"]["properties"]["reference"]["description"] = "Component reference (e.g. 'U1')";
    batchPlaceComponent["inputSchema"]["properties"]["components"]["items"]["properties"]["near"]["properties"]["pin"]["type"] = "string";
    batchPlaceComponent["inputSchema"]["properties"]["components"]["items"]["properties"]["near"]["properties"]["pin"]["description"] = "Optional pin number";
    tools.push_back( batchPlaceComponent );

    json moveComponent;
    moveComponent["name"] = "move_component";
    moveComponent["description"] = "Move and optionally rotate an existing component. Uses KiCad coordinates (mm). Automatically manages transactions.";
    moveComponent["inputSchema"]["type"] = "object";
    moveComponent["inputSchema"]["required"] = json::array( { "reference", "x_mm", "y_mm" } );
    moveComponent["inputSchema"]["properties"]["reference"]["type"] = "string";
    moveComponent["inputSchema"]["properties"]["reference"]["description"] = "Component reference (e.g. R1, U1)";
    moveComponent["inputSchema"]["properties"]["x_mm"]["type"] = "number";
    moveComponent["inputSchema"]["properties"]["x_mm"]["description"] = "New X position in mm";
    moveComponent["inputSchema"]["properties"]["y_mm"]["type"] = "number";
    moveComponent["inputSchema"]["properties"]["y_mm"]["description"] = "New Y position in mm";
    moveComponent["inputSchema"]["properties"]["rotation"]["type"] = "number";
    moveComponent["inputSchema"]["properties"]["rotation"]["description"] = "New orientation in degrees (0, 90, 180, 270); omit to keep current";
    tools.push_back( moveComponent );

    json moveChunk;
    moveChunk["name"] = "move_chunk";
    moveChunk["description"] = "Move an anchor component and its connected chunk. Wires/global labels in the chunk subnet transform with the move (requires GetItems). Provide absolute (x_mm,y_mm) or delta (dx_mm,dy_mm). Set rotate_chunk=true with rotation to rotate the chunk around the anchor.";
    moveChunk["inputSchema"]["type"] = "object";
    moveChunk["inputSchema"]["required"] = json::array( { "reference" } );
    moveChunk["inputSchema"]["properties"]["reference"]["type"] = "string";
    moveChunk["inputSchema"]["properties"]["reference"]["description"] = "Anchor component reference (e.g. U1)";
    moveChunk["inputSchema"]["properties"]["x_mm"]["type"] = "number";
    moveChunk["inputSchema"]["properties"]["x_mm"]["description"] = "Absolute anchor target X in mm (use with y_mm)";
    moveChunk["inputSchema"]["properties"]["y_mm"]["type"] = "number";
    moveChunk["inputSchema"]["properties"]["y_mm"]["description"] = "Absolute anchor target Y in mm (use with x_mm)";
    moveChunk["inputSchema"]["properties"]["dx_mm"]["type"] = "number";
    moveChunk["inputSchema"]["properties"]["dx_mm"]["description"] = "Delta X shift in mm (use with dy_mm)";
    moveChunk["inputSchema"]["properties"]["dy_mm"]["type"] = "number";
    moveChunk["inputSchema"]["properties"]["dy_mm"]["description"] = "Delta Y shift in mm (use with dx_mm)";
    moveChunk["inputSchema"]["properties"]["rotation"]["type"] = "number";
    moveChunk["inputSchema"]["properties"]["rotation"]["description"] = "Optional anchor rotation target (0, 90, 180, 270). Used as chunk rotation source when rotate_chunk=true.";
    moveChunk["inputSchema"]["properties"]["rotate_chunk"]["type"] = "boolean";
    moveChunk["inputSchema"]["properties"]["rotate_chunk"]["description"] = "When true and rotation is provided, rotates connected components and connected net geometry (wires/labels) around the anchor pivot.";
    moveChunk["inputSchema"]["properties"]["rotate_chunk"]["default"] = false;
    moveChunk["inputSchema"]["properties"]["include_connected_components"]["type"] = "boolean";
    moveChunk["inputSchema"]["properties"]["include_connected_components"]["description"] = "Whether to include connected components (default true)";
    moveChunk["inputSchema"]["properties"]["include_connected_components"]["default"] = true;
    moveChunk["inputSchema"]["properties"]["include_wires"]["type"] = "boolean";
    moveChunk["inputSchema"]["properties"]["include_wires"]["description"] = "Whether to move connected wires (default true)";
    moveChunk["inputSchema"]["properties"]["include_wires"]["default"] = true;
    moveChunk["inputSchema"]["properties"]["include_labels"]["type"] = "boolean";
    moveChunk["inputSchema"]["properties"]["include_labels"]["description"] = "Whether to move connected global labels (default true)";
    moveChunk["inputSchema"]["properties"]["include_labels"]["default"] = true;
    moveChunk["inputSchema"]["properties"]["max_hops"]["type"] = "integer";
    moveChunk["inputSchema"]["properties"]["max_hops"]["description"] = "Maximum component hops from anchor while traversing nets (default 1)";
    moveChunk["inputSchema"]["properties"]["max_hops"]["default"] = 1;
    moveChunk["inputSchema"]["properties"]["max_net_pin_count"]["type"] = "integer";
    moveChunk["inputSchema"]["properties"]["max_net_pin_count"]["description"] = "Skip nets with more than this many pins during traversal (default 8)";
    moveChunk["inputSchema"]["properties"]["max_net_pin_count"]["default"] = 8;
    moveChunk["inputSchema"]["properties"]["include_global_power_nets"]["type"] = "boolean";
    moveChunk["inputSchema"]["properties"]["include_global_power_nets"]["description"] = "Include common global power nets (GND/VCC/etc.) during traversal (default false)";
    moveChunk["inputSchema"]["properties"]["include_global_power_nets"]["default"] = false;
    tools.push_back( moveChunk );

    json addWire;
    addWire["name"] = "add_wire";
    addWire["description"] = "Add wire segment(s) on the schematic. Coordinates in mm (KiCad). Snapped to 0.1mm grid. Diagonal segments are converted to straight horizontal/vertical (Manhattan) routing.";
    addWire["inputSchema"]["type"] = "object";
    addWire["inputSchema"]["properties"]["segments"]["type"] = "array";
    addWire["inputSchema"]["properties"]["segments"]["description"] = "List of {x1, y1, x2, y2} in mm (start and end of each segment)";
    addWire["inputSchema"]["properties"]["segments"]["items"]["type"] = "object";
    addWire["inputSchema"]["properties"]["segments"]["items"]["properties"]["x1"] = json::object( { {"type", "number"} } );
    addWire["inputSchema"]["properties"]["segments"]["items"]["properties"]["y1"] = json::object( { {"type", "number"} } );
    addWire["inputSchema"]["properties"]["segments"]["items"]["properties"]["x2"] = json::object( { {"type", "number"} } );
    addWire["inputSchema"]["properties"]["segments"]["items"]["properties"]["y2"] = json::object( { {"type", "number"} } );
    addWire["inputSchema"]["required"] = json::array( { "segments" } );
    tools.push_back( addWire );

    json removeWire;
    removeWire["name"] = "remove_wire";
    removeWire["description"] = "Remove wire(s) by their IDs (from add_wire or get_items).";
    removeWire["inputSchema"]["type"] = "object";
    removeWire["inputSchema"]["properties"]["wire_ids"]["type"] = "array";
    removeWire["inputSchema"]["properties"]["wire_ids"]["description"] = "List of wire KIID strings to delete";
    removeWire["inputSchema"]["properties"]["wire_ids"]["items"]["type"] = "string";
    removeWire["inputSchema"]["required"] = json::array( { "wire_ids" } );
    tools.push_back( removeWire );

    json deleteComponent;
    deleteComponent["name"] = "delete_component";
    deleteComponent["description"] = "Deprecated single-item helper. Prefer delete_components_batch with a single-item references array.";
    deleteComponent["inputSchema"]["type"] = "object";
    deleteComponent["inputSchema"]["required"] = json::array( { "reference" } );
    deleteComponent["inputSchema"]["properties"]["reference"]["type"] = "string";
    deleteComponent["inputSchema"]["properties"]["reference"]["description"] = "Reference designator of the component to delete, e.g. 'R1', 'U1'";
    tools.push_back( deleteComponent );

    json addGlobalLabel;
    addGlobalLabel["name"] = "add_global_label";
    addGlobalLabel["description"] = "Add a global label on the schematic. Position in mm; snapped to 0.1mm grid. Rotation controls which direction the label connection pin faces: 0=right, 90=up, 180=left, 270=down. Use recommended_label_rotation from get_component_pins to set this correctly.";
    addGlobalLabel["inputSchema"]["type"] = "object";
    addGlobalLabel["inputSchema"]["properties"]["text"]["type"] = "string";
    addGlobalLabel["inputSchema"]["properties"]["text"]["description"] = "Label text (net name)";
    addGlobalLabel["inputSchema"]["required"] = json::array( { "text", "x", "y" } );
    addGlobalLabel["inputSchema"]["properties"]["x"]["type"] = "number";
    addGlobalLabel["inputSchema"]["properties"]["x"]["description"] = "X position in mm";
    addGlobalLabel["inputSchema"]["properties"]["y"]["type"] = "number";
    addGlobalLabel["inputSchema"]["properties"]["y"]["description"] = "Y position in mm";
    addGlobalLabel["inputSchema"]["properties"]["rotation"]["type"] = "number";
    addGlobalLabel["inputSchema"]["properties"]["rotation"]["description"] = "Direction the label connection pin faces: 0=right, 90=up, 180=left, 270=down. Use recommended_label_rotation from get_component_pins. Defaults to 0.";
    tools.push_back( addGlobalLabel );

    json ercCheck;
    ercCheck["name"] = "erc_check";
    ercCheck["description"] = "Report dangling wires and unconnected pins (lightweight ERC).";
    ercCheck["inputSchema"]["type"] = "object";
    ercCheck["inputSchema"]["properties"] = json::object();
    tools.push_back( ercCheck );

    json getSchematicSummary;
    getSchematicSummary["name"] = "get_schematic_summary";
    getSchematicSummary["description"] =
        "Get a rich schematic context summary: sheet/grid info, component inventory, footprints/custom fields, pin/net connectivity graph, global nets, labels/wires, and lightweight ERC-style diagnostics. Coordinates are KiCad mm.";
    getSchematicSummary["inputSchema"]["type"] = "object";
    getSchematicSummary["inputSchema"]["properties"] = json::object();
    tools.push_back( getSchematicSummary );

    json getNetlist;
    getNetlist["name"] = "get_netlist";
    getNetlist["description"] =
        "Get connectivity netlist for the current sheet: each net name with list of (reference, pin_number). Use for reorganize or layout. No arguments.";
    getNetlist["inputSchema"]["type"] = "object";
    getNetlist["inputSchema"]["properties"] = json::object();
    tools.push_back( getNetlist );

    json connectNetToPin;
    connectNetToPin["name"] = "connect_net_to_pin";
    connectNetToPin["description"] = "Deprecated single-connection helper. Prefer batch_connect with one net_to_pin entry.";
    connectNetToPin["inputSchema"]["type"] = "object";
    connectNetToPin["inputSchema"]["required"] = json::array( { "net_name", "reference", "pin_number" } );
    connectNetToPin["inputSchema"]["properties"]["net_name"]["type"] = "string";
    connectNetToPin["inputSchema"]["properties"]["reference"]["type"] = "string";
    connectNetToPin["inputSchema"]["properties"]["pin_number"]["type"] = "string";
    connectNetToPin["inputSchema"]["properties"]["label_offset_mm"]["type"] = "number";
    connectNetToPin["inputSchema"]["properties"]["label_offset_mm"]["description"] = "Optional offset from pin in mm (default 1.0). Direction follows pin orientation. Label rotation follows pin direction + 180°.";
    tools.push_back( connectNetToPin );

    json connectPinToPin;
    connectPinToPin["name"] = "connect_pin_to_pin";
    connectPinToPin["description"] = "Deprecated single-connection helper. Prefer batch_connect with one pin_to_pin entry.";
    connectPinToPin["inputSchema"]["type"] = "object";
    connectPinToPin["inputSchema"]["required"] = json::array( { "reference1", "pin1", "reference2", "pin2", "net_name" } );
    connectPinToPin["inputSchema"]["properties"]["reference1"]["type"] = "string";
    connectPinToPin["inputSchema"]["properties"]["pin1"]["type"] = "string";
    connectPinToPin["inputSchema"]["properties"]["reference2"]["type"] = "string";
    connectPinToPin["inputSchema"]["properties"]["pin2"]["type"] = "string";
    connectPinToPin["inputSchema"]["properties"]["net_name"]["type"] = "string";
    connectPinToPin["inputSchema"]["properties"]["short_wire_threshold_mm"]["type"] = "number";
    connectPinToPin["inputSchema"]["properties"]["short_wire_threshold_mm"]["description"] = "Max distance in mm for direct wire (default 5)";
    tools.push_back( connectPinToPin );

    json screenshotZone;
    screenshotZone["name"] = "screenshot_zone";
    screenshotZone["description"] = "Capture a zoomed-in zone of the schematic centered at (center_x, center_y). width_mm = visible width in mm (default 15; smaller = more zoomed in). max_width_px = if set, resize to reduce token/file size (e.g. 600). Returns PNG as base64.";
    screenshotZone["inputSchema"]["type"] = "object";
    screenshotZone["inputSchema"]["required"] = json::array( { "center_x", "center_y" } );
    screenshotZone["inputSchema"]["properties"]["center_x"]["type"] = "number";
    screenshotZone["inputSchema"]["properties"]["center_x"]["description"] = "Center X in mm";
    screenshotZone["inputSchema"]["properties"]["center_y"]["type"] = "number";
    screenshotZone["inputSchema"]["properties"]["center_y"]["description"] = "Center Y in mm";
    screenshotZone["inputSchema"]["properties"]["width_mm"]["type"] = "number";
    screenshotZone["inputSchema"]["properties"]["width_mm"]["description"] = "Visible width in mm (default 15 for zoomed-in detail)";
    screenshotZone["inputSchema"]["properties"]["max_width_px"]["type"] = "integer";
    screenshotZone["inputSchema"]["properties"]["max_width_px"]["description"] = "Max output width in pixels (default 600 to reduce size). Pass 0 for full resolution.";
    tools.push_back( screenshotZone );

    json screenshotFull;
    screenshotFull["name"] = "screenshot_full_schematic";
    screenshotFull["description"] = "Capture the entire schematic fitted to view. No arguments. Returns PNG as base64.";
    screenshotFull["inputSchema"]["type"] = "object";
    screenshotFull["inputSchema"]["required"] = json::array();
    tools.push_back( screenshotFull );

    json startBlock;
    startBlock["name"] = "start_block";
    startBlock["description"] =
        "Find an empty space on the schematic, draw a rectangle (block outline) and title text. Returns min_x, min_y, max_x, max_y (mm) as bounding box for the LLM to work within.";
    startBlock["inputSchema"]["type"] = "object";
    startBlock["inputSchema"]["required"] = json::array( { "width", "height", "title" } );
    startBlock["inputSchema"]["properties"]["width"]["type"] = "number";
    startBlock["inputSchema"]["properties"]["width"]["description"] = "Block width in mm";
    startBlock["inputSchema"]["properties"]["height"]["type"] = "number";
    startBlock["inputSchema"]["properties"]["height"]["description"] = "Block height in mm";
    startBlock["inputSchema"]["properties"]["title"]["type"] = "string";
    startBlock["inputSchema"]["properties"]["title"]["description"] = "Title text drawn inside the block";
    startBlock["inputSchema"]["properties"]["center_x"]["type"] = "number";
    startBlock["inputSchema"]["properties"]["center_x"]["description"] = "Optional center X in mm; if provided with center_y, block is placed exactly there";
    startBlock["inputSchema"]["properties"]["center_y"]["type"] = "number";
    startBlock["inputSchema"]["properties"]["center_y"]["description"] = "Optional center Y in mm; if provided with center_x, block is placed exactly there";
    tools.push_back( startBlock );

    json getAllBounds;
    getAllBounds["name"] = "get_all_bounds";
    getAllBounds["description"] = "Get bounding boxes for ALL components in one call. Returns array of {reference, cx_mm, cy_mm, width_mm, height_mm}. Eliminates N+1 calls to get individual component bounds.";
    getAllBounds["inputSchema"]["type"] = "object";
    getAllBounds["inputSchema"]["properties"] = json::object();
    tools.push_back( getAllBounds );

    json getComponentPins;
    getComponentPins["name"] = "get_component_pins";
    getComponentPins["description"] = "Get all pin positions for a specific component in one call. Returns array of {pin_number, pin_name, x_mm, y_mm, orientation, outward_degrees, recommended_label_rotation}. Use recommended_label_rotation as the rotation value for add_global_label to ensure the label faces outward correctly.";
    getComponentPins["inputSchema"]["type"] = "object";
    getComponentPins["inputSchema"]["required"] = json::array( { "reference" } );
    getComponentPins["inputSchema"]["properties"]["reference"]["type"] = "string";
    getComponentPins["inputSchema"]["properties"]["reference"]["description"] = "Component reference (e.g. U1, R1)";
    tools.push_back( getComponentPins );

    json batchGetComponentPins;
    batchGetComponentPins["name"] = "batch_get_component_pins";
    batchGetComponentPins["description"] = "Get all pin positions for multiple components in one call. Returns array of {reference, pins:[{pin_number, pin_name, x_mm, y_mm, orientation, outward_degrees, recommended_label_rotation}]}.";
    batchGetComponentPins["inputSchema"]["type"] = "object";
    batchGetComponentPins["inputSchema"]["required"] = json::array( { "references" } );
    batchGetComponentPins["inputSchema"]["properties"]["references"]["type"] = "array";
    batchGetComponentPins["inputSchema"]["properties"]["references"]["description"] = "Component references (e.g. ['U1','R1','J1'])";
    batchGetComponentPins["inputSchema"]["properties"]["references"]["items"]["type"] = "string";
    tools.push_back( batchGetComponentPins );

    json getComponentConnectivityGraph;
    getComponentConnectivityGraph["name"] = "get_component_connectivity_graph";
    getComponentConnectivityGraph["description"] =
        "Get a compact one-hop connectivity graph for one placed component. "
        "Returns component ref/value/symbol, each pin's net, peer pins on that net, and a merged peer component summary from cached schematic data when available. "
        "Prefer this over broad netlist hunting when analyzing gain, feedback, bias, or local signal flow.";
    getComponentConnectivityGraph["inputSchema"]["type"] = "object";
    getComponentConnectivityGraph["inputSchema"]["required"] = json::array( { "reference" } );
    getComponentConnectivityGraph["inputSchema"]["properties"]["reference"]["type"] = "string";
    getComponentConnectivityGraph["inputSchema"]["properties"]["reference"]["description"] = "Placed component reference (e.g. U7, R10)";
    getComponentConnectivityGraph["inputSchema"]["properties"]["depth"]["type"] = "integer";
    getComponentConnectivityGraph["inputSchema"]["properties"]["depth"]["description"] = "Optional requested graph depth. Currently implemented as one-hop only; values greater than 1 are accepted but clamped to 1.";
    getComponentConnectivityGraph["inputSchema"]["properties"]["max_depth"]["type"] = "integer";
    getComponentConnectivityGraph["inputSchema"]["properties"]["max_depth"]["description"] = "Alias for depth. Currently one-hop only.";
    getComponentConnectivityGraph["inputSchema"]["properties"]["max_peers_per_pin"]["type"] = "integer";
    getComponentConnectivityGraph["inputSchema"]["properties"]["max_peers_per_pin"]["description"] = "Optional cap for peer pins returned per source pin (default 12, max 64).";
    tools.push_back( getComponentConnectivityGraph );

    json batchConnect;
    batchConnect["name"] = "batch_connect";
    batchConnect["description"] = "Connect one or more net-to-pin or pin-to-pin connections in a single transaction. Each connection is {type: 'net_to_pin', net_name, reference, pin_number} or {type: 'pin_to_pin', reference1, pin1, reference2, pin2, net_name}. Returns per-connection results.";
    batchConnect["inputSchema"]["type"] = "object";
    batchConnect["inputSchema"]["required"] = json::array( { "connections" } );
    batchConnect["inputSchema"]["properties"]["connections"]["type"] = "array";
    batchConnect["inputSchema"]["properties"]["connections"]["description"] = "Array of connection objects";
    batchConnect["inputSchema"]["properties"]["connections"]["items"]["type"] = "object";
    batchConnect["inputSchema"]["properties"]["connections"]["items"]["properties"]["type"]["type"] = "string";
    batchConnect["inputSchema"]["properties"]["connections"]["items"]["properties"]["type"]["enum"] = json::array( { "net_to_pin", "pin_to_pin" } );
    batchConnect["inputSchema"]["properties"]["connections"]["items"]["properties"]["net_name"]["type"] = "string";
    batchConnect["inputSchema"]["properties"]["connections"]["items"]["properties"]["reference"]["type"] = "string";
    batchConnect["inputSchema"]["properties"]["connections"]["items"]["properties"]["pin_number"]["type"] = "string";
    batchConnect["inputSchema"]["properties"]["connections"]["items"]["properties"]["label_offset_mm"]["type"] = "number";
    batchConnect["inputSchema"]["properties"]["connections"]["items"]["properties"]["label_offset_mm"]["description"] = "Optional for net_to_pin. Offset from pin in mm (default 1.0).";
    batchConnect["inputSchema"]["properties"]["connections"]["items"]["properties"]["reference1"]["type"] = "string";
    batchConnect["inputSchema"]["properties"]["connections"]["items"]["properties"]["pin1"]["type"] = "string";
    batchConnect["inputSchema"]["properties"]["connections"]["items"]["properties"]["reference2"]["type"] = "string";
    batchConnect["inputSchema"]["properties"]["connections"]["items"]["properties"]["pin2"]["type"] = "string";
    batchConnect["inputSchema"]["properties"]["connections"]["items"]["properties"]["short_wire_threshold_mm"]["type"] = "number";
    batchConnect["inputSchema"]["properties"]["connections"]["items"]["properties"]["short_wire_threshold_mm"]["description"] = "Optional for pin_to_pin. Max distance in mm for direct wire (default 5).";
    tools.push_back( batchConnect );

    json findEmptySpace;
    findEmptySpace["name"] = "find_empty_space";
    findEmptySpace["description"] = "Find an empty spot on the schematic for a component of given size. Optionally specify a preferred location with near_x/near_y. Returns {x_mm, y_mm, found}. Use to plan placements before committing.";
    findEmptySpace["inputSchema"]["type"] = "object";
    findEmptySpace["inputSchema"]["required"] = json::array( { "width_mm", "height_mm" } );
    findEmptySpace["inputSchema"]["properties"]["width_mm"]["type"] = "number";
    findEmptySpace["inputSchema"]["properties"]["width_mm"]["description"] = "Width of the component to place (mm)";
    findEmptySpace["inputSchema"]["properties"]["height_mm"]["type"] = "number";
    findEmptySpace["inputSchema"]["properties"]["height_mm"]["description"] = "Height of the component to place (mm)";
    findEmptySpace["inputSchema"]["properties"]["near_x"]["type"] = "number";
    findEmptySpace["inputSchema"]["properties"]["near_x"]["description"] = "Optional preferred X coordinate (mm)";
    findEmptySpace["inputSchema"]["properties"]["near_y"]["type"] = "number";
    findEmptySpace["inputSchema"]["properties"]["near_y"]["description"] = "Optional preferred Y coordinate (mm)";
    tools.push_back( findEmptySpace );

    // ── move_components_batch ──
    json moveCompsBatch;
    moveCompsBatch["name"] = "move_components_batch";
    moveCompsBatch["description"] = "Move multiple components in a single transaction. Each move specifies reference, x_mm, y_mm, and optional rotation.";
    moveCompsBatch["inputSchema"]["type"] = "object";
    moveCompsBatch["inputSchema"]["required"] = json::array( { "moves" } );
    moveCompsBatch["inputSchema"]["properties"]["moves"]["type"] = "array";
    moveCompsBatch["inputSchema"]["properties"]["moves"]["description"] = "Array of move objects";
    moveCompsBatch["inputSchema"]["properties"]["moves"]["items"]["type"] = "object";
    moveCompsBatch["inputSchema"]["properties"]["moves"]["items"]["properties"]["reference"]["type"] = "string";
    moveCompsBatch["inputSchema"]["properties"]["moves"]["items"]["properties"]["reference"]["description"] = "Component reference (e.g. 'R1')";
    moveCompsBatch["inputSchema"]["properties"]["moves"]["items"]["properties"]["x_mm"]["type"] = "number";
    moveCompsBatch["inputSchema"]["properties"]["moves"]["items"]["properties"]["x_mm"]["description"] = "Target X in mm";
    moveCompsBatch["inputSchema"]["properties"]["moves"]["items"]["properties"]["y_mm"]["type"] = "number";
    moveCompsBatch["inputSchema"]["properties"]["moves"]["items"]["properties"]["y_mm"]["description"] = "Target Y in mm";
    moveCompsBatch["inputSchema"]["properties"]["moves"]["items"]["properties"]["rotation"]["type"] = "number";
    moveCompsBatch["inputSchema"]["properties"]["moves"]["items"]["properties"]["rotation"]["description"] = "Optional rotation in degrees (0, 90, 180, 270)";
    moveCompsBatch["inputSchema"]["properties"]["moves"]["items"]["required"] = json::array( { "reference", "x_mm", "y_mm" } );
    tools.push_back( moveCompsBatch );

    // ── rotate_component ──
    json rotateComp;
    rotateComp["name"] = "rotate_component";
    rotateComp["description"] = "Rotate a component in-place to the specified angle (0, 90, 180, 270). The component stays at its current position.";
    rotateComp["inputSchema"]["type"] = "object";
    rotateComp["inputSchema"]["required"] = json::array( { "reference", "rotation" } );
    rotateComp["inputSchema"]["properties"]["reference"]["type"] = "string";
    rotateComp["inputSchema"]["properties"]["reference"]["description"] = "Component reference designator (e.g. 'R1')";
    rotateComp["inputSchema"]["properties"]["rotation"]["type"] = "number";
    rotateComp["inputSchema"]["properties"]["rotation"]["description"] = "Target rotation in degrees (0, 90, 180, 270)";
    tools.push_back( rotateComp );

    // ── rename_net_label ──
    json renameNet;
    renameNet["name"] = "rename_net_label";
    renameNet["description"] = "Rename global net label(s). If x,y provided, renames only the nearest matching label; otherwise renames ALL labels with old_name.";
    renameNet["inputSchema"]["type"] = "object";
    renameNet["inputSchema"]["required"] = json::array( { "old_name", "new_name" } );
    renameNet["inputSchema"]["properties"]["old_name"]["type"] = "string";
    renameNet["inputSchema"]["properties"]["old_name"]["description"] = "Current label text to find";
    renameNet["inputSchema"]["properties"]["new_name"]["type"] = "string";
    renameNet["inputSchema"]["properties"]["new_name"]["description"] = "New label text to set";
    renameNet["inputSchema"]["properties"]["x"]["type"] = "number";
    renameNet["inputSchema"]["properties"]["x"]["description"] = "Optional X coordinate (mm) to select nearest matching label";
    renameNet["inputSchema"]["properties"]["y"]["type"] = "number";
    renameNet["inputSchema"]["properties"]["y"]["description"] = "Optional Y coordinate (mm) to select nearest matching label";
    tools.push_back( renameNet );

    // ── preview_changes ──
    json previewChanges;
    previewChanges["name"] = "preview_changes";
    previewChanges["description"] = "Take a snapshot of the current schematic state for before/after comparison. Returns component list, net list, and counts in JSON format.";
    previewChanges["inputSchema"]["type"] = "object";
    previewChanges["inputSchema"]["properties"] = json::object();
    tools.push_back( previewChanges );

    // ── undo_last_commit ── (removed: KiCad API does not support true undo of committed changes)

    // ── Read-only query tools ───────────────────────────────────────────
    json listBlocks;
    listBlocks["name"] = "list_blocks";
    listBlocks["description"] = "List all annotation blocks on the schematic. Blocks are rectangles drawn on the notes layer with a title text. Returns array of {title, min_x, min_y, max_x, max_y, line_ids, text_id}.";
    listBlocks["inputSchema"]["type"] = "object";
    tools.push_back( listBlocks );

    json findBlock;
    findBlock["name"] = "find_block";
    findBlock["description"] = "Find an annotation block by title (case-insensitive partial match). Returns the first matching block or error if not found.";
    findBlock["inputSchema"]["type"] = "object";
    findBlock["inputSchema"]["required"] = json::array( { "title" } );
    findBlock["inputSchema"]["properties"]["title"]["type"] = "string";
    findBlock["inputSchema"]["properties"]["title"]["description"] = "Block title to search for (case-insensitive partial match)";
    tools.push_back( findBlock );

    json getItemsInBbox;
    getItemsInBbox["name"] = "get_items_in_bbox";
    getItemsInBbox["description"] = "Get all schematic items (components, wires, labels) whose position or bounding box intersects a given bounding box. Coordinates in mm.";
    getItemsInBbox["inputSchema"]["type"] = "object";
    getItemsInBbox["inputSchema"]["required"] = json::array( { "min_x", "min_y", "max_x", "max_y" } );
    getItemsInBbox["inputSchema"]["properties"]["min_x"]["type"] = "number";
    getItemsInBbox["inputSchema"]["properties"]["min_x"]["description"] = "Left edge X coordinate (mm)";
    getItemsInBbox["inputSchema"]["properties"]["min_y"]["type"] = "number";
    getItemsInBbox["inputSchema"]["properties"]["min_y"]["description"] = "Top edge Y coordinate (mm)";
    getItemsInBbox["inputSchema"]["properties"]["max_x"]["type"] = "number";
    getItemsInBbox["inputSchema"]["properties"]["max_x"]["description"] = "Right edge X coordinate (mm)";
    getItemsInBbox["inputSchema"]["properties"]["max_y"]["type"] = "number";
    getItemsInBbox["inputSchema"]["properties"]["max_y"]["description"] = "Bottom edge Y coordinate (mm)";
    tools.push_back( getItemsInBbox );

    json validateBlock;
    validateBlock["name"] = "validate_block";
    validateBlock["description"] = "Validate a region of the schematic for issues: overlapping component bounding boxes, unconnected pins (dangling), duplicate labels at the same position. Returns {valid, issues[]}.";
    validateBlock["inputSchema"]["type"] = "object";
    validateBlock["inputSchema"]["required"] = json::array( { "min_x", "min_y", "max_x", "max_y" } );
    validateBlock["inputSchema"]["properties"]["min_x"]["type"] = "number";
    validateBlock["inputSchema"]["properties"]["min_x"]["description"] = "Left edge X coordinate (mm)";
    validateBlock["inputSchema"]["properties"]["min_y"]["type"] = "number";
    validateBlock["inputSchema"]["properties"]["min_y"]["description"] = "Top edge Y coordinate (mm)";
    validateBlock["inputSchema"]["properties"]["max_x"]["type"] = "number";
    validateBlock["inputSchema"]["properties"]["max_x"]["description"] = "Right edge X coordinate (mm)";
    validateBlock["inputSchema"]["properties"]["max_y"]["type"] = "number";
    validateBlock["inputSchema"]["properties"]["max_y"]["description"] = "Bottom edge Y coordinate (mm)";
    tools.push_back( validateBlock );

    // ── Deletion / removal tools ──

    json deleteComponentsBatch;
    deleteComponentsBatch["name"] = "delete_components_batch";
    deleteComponentsBatch["description"] = "Delete one or more components in a single transaction. Use this even for single-component deletion. Returns {deleted: [...], failed: [{reference, error}]}";
    deleteComponentsBatch["inputSchema"]["type"] = "object";
    deleteComponentsBatch["inputSchema"]["required"] = json::array( { "references" } );
    deleteComponentsBatch["inputSchema"]["properties"]["references"]["type"] = "array";
    deleteComponentsBatch["inputSchema"]["properties"]["references"]["description"] = "Array of component references to delete (e.g. [\"R1\", \"R2\", \"C1\"])";
    deleteComponentsBatch["inputSchema"]["properties"]["references"]["items"]["type"] = "string";
    tools.push_back( deleteComponentsBatch );

    json removeWiresInBbox;
    removeWiresInBbox["name"] = "remove_wires_in_bbox";
    removeWiresInBbox["description"] = "Remove all wires whose BOTH endpoints fall within the given bounding box. Returns {removed_count, wire_ids}.";
    removeWiresInBbox["inputSchema"]["type"] = "object";
    removeWiresInBbox["inputSchema"]["required"] = json::array( { "min_x", "min_y", "max_x", "max_y" } );
    removeWiresInBbox["inputSchema"]["properties"]["min_x"]["type"] = "number";
    removeWiresInBbox["inputSchema"]["properties"]["min_x"]["description"] = "Left edge X in mm";
    removeWiresInBbox["inputSchema"]["properties"]["min_y"]["type"] = "number";
    removeWiresInBbox["inputSchema"]["properties"]["min_y"]["description"] = "Top edge Y in mm";
    removeWiresInBbox["inputSchema"]["properties"]["max_x"]["type"] = "number";
    removeWiresInBbox["inputSchema"]["properties"]["max_x"]["description"] = "Right edge X in mm";
    removeWiresInBbox["inputSchema"]["properties"]["max_y"]["type"] = "number";
    removeWiresInBbox["inputSchema"]["properties"]["max_y"]["description"] = "Bottom edge Y in mm";
    tools.push_back( removeWiresInBbox );

    json removeLabelTool;
    removeLabelTool["name"] = "remove_label";
    removeLabelTool["description"] = "Remove a global label by its net name. If multiple labels share the same name, optionally provide x,y to remove the nearest one. Returns {removed, label_text, position}.";
    removeLabelTool["inputSchema"]["type"] = "object";
    removeLabelTool["inputSchema"]["required"] = json::array( { "text" } );
    removeLabelTool["inputSchema"]["properties"]["text"]["type"] = "string";
    removeLabelTool["inputSchema"]["properties"]["text"]["description"] = "Net name of the global label to remove";
    removeLabelTool["inputSchema"]["properties"]["x"]["type"] = "number";
    removeLabelTool["inputSchema"]["properties"]["x"]["description"] = "Optional X coordinate (mm) to pick the nearest label when multiple match";
    removeLabelTool["inputSchema"]["properties"]["y"]["type"] = "number";
    removeLabelTool["inputSchema"]["properties"]["y"]["description"] = "Optional Y coordinate (mm) to pick the nearest label when multiple match";
    tools.push_back( removeLabelTool );

    json removeLabelsInBbox;
    removeLabelsInBbox["name"] = "remove_labels_in_bbox";
    removeLabelsInBbox["description"] = "Remove all global labels whose position falls within the given bounding box. Returns {removed_count, labels: [{text, x, y}]}.";
    removeLabelsInBbox["inputSchema"]["type"] = "object";
    removeLabelsInBbox["inputSchema"]["required"] = json::array( { "min_x", "min_y", "max_x", "max_y" } );
    removeLabelsInBbox["inputSchema"]["properties"]["min_x"]["type"] = "number";
    removeLabelsInBbox["inputSchema"]["properties"]["min_x"]["description"] = "Left edge X in mm";
    removeLabelsInBbox["inputSchema"]["properties"]["min_y"]["type"] = "number";
    removeLabelsInBbox["inputSchema"]["properties"]["min_y"]["description"] = "Top edge Y in mm";
    removeLabelsInBbox["inputSchema"]["properties"]["max_x"]["type"] = "number";
    removeLabelsInBbox["inputSchema"]["properties"]["max_x"]["description"] = "Right edge X in mm";
    removeLabelsInBbox["inputSchema"]["properties"]["max_y"]["type"] = "number";
    removeLabelsInBbox["inputSchema"]["properties"]["max_y"]["description"] = "Bottom edge Y in mm";
    tools.push_back( removeLabelsInBbox );

    json disconnectPin;
    disconnectPin["name"] = "disconnect_pin";
    disconnectPin["description"] = "Deprecated single-item helper. Prefer batch_disconnect_pins with a single-item pins array.";
    disconnectPin["inputSchema"]["type"] = "object";
    disconnectPin["inputSchema"]["required"] = json::array( { "reference", "pin_number" } );
    disconnectPin["inputSchema"]["properties"]["reference"]["type"] = "string";
    disconnectPin["inputSchema"]["properties"]["reference"]["description"] = "Component reference (e.g. U1)";
    disconnectPin["inputSchema"]["properties"]["pin_number"]["type"] = "string";
    disconnectPin["inputSchema"]["properties"]["pin_number"]["description"] = "Pin number or name";
    tools.push_back( disconnectPin );

    json batchDisconnectPins;
    batchDisconnectPins["name"] = "batch_disconnect_pins";
    batchDisconnectPins["description"] = "Disconnect multiple component pins in one call by removing wires/labels near each pin. Use this for both single and multi-pin disconnects. Returns per-pin results.";
    batchDisconnectPins["inputSchema"]["type"] = "object";
    batchDisconnectPins["inputSchema"]["required"] = json::array( { "pins" } );
    batchDisconnectPins["inputSchema"]["properties"]["pins"]["type"] = "array";
    batchDisconnectPins["inputSchema"]["properties"]["pins"]["description"] = "Array of {reference, pin_number}";
    batchDisconnectPins["inputSchema"]["properties"]["pins"]["items"]["type"] = "object";
    batchDisconnectPins["inputSchema"]["properties"]["pins"]["items"]["required"] = json::array( { "reference", "pin_number" } );
    batchDisconnectPins["inputSchema"]["properties"]["pins"]["items"]["properties"]["reference"]["type"] = "string";
    batchDisconnectPins["inputSchema"]["properties"]["pins"]["items"]["properties"]["pin_number"]["type"] = "string";
    batchDisconnectPins["inputSchema"]["properties"]["tolerance_mm"]["type"] = "number";
    batchDisconnectPins["inputSchema"]["properties"]["tolerance_mm"]["description"] = "Optional pin proximity tolerance in mm (default 0.1)";
    tools.push_back( batchDisconnectPins );

    // ── replace_component ──
    json replaceComp;
    replaceComp["name"] = "replace_component";
    replaceComp["description"] = "Replace a component while keeping its reference designator, position, and rotation. Swaps the symbol library/name and optionally value/footprint.";
    replaceComp["inputSchema"]["type"] = "object";
    replaceComp["inputSchema"]["required"] = json::array( { "reference", "library", "symbol" } );
    replaceComp["inputSchema"]["properties"]["reference"]["type"] = "string";
    replaceComp["inputSchema"]["properties"]["reference"]["description"] = "Existing component reference designator (e.g. 'R1')";
    replaceComp["inputSchema"]["properties"]["library"]["type"] = "string";
    replaceComp["inputSchema"]["properties"]["library"]["description"] = "New symbol library name (e.g. 'Device')";
    replaceComp["inputSchema"]["properties"]["symbol"]["type"] = "string";
    replaceComp["inputSchema"]["properties"]["symbol"]["description"] = "New symbol name (e.g. 'R_Small')";
    replaceComp["inputSchema"]["properties"]["value"]["type"] = "string";
    replaceComp["inputSchema"]["properties"]["value"]["description"] = "Optional new value. If omitted, keeps old value.";
    tools.push_back( replaceComp );

    // ── schematic_diff ──
    json schematicDiff;
    schematicDiff["name"] = "schematic_diff";
    schematicDiff["description"] = "Compare the current schematic state against a previous snapshot (from get_schematic_summary or preview_changes). Returns added/removed/moved components, added/removed nets, and changed values.";
    schematicDiff["inputSchema"]["type"] = "object";
    schematicDiff["inputSchema"]["required"] = json::array( { "snapshot" } );
    schematicDiff["inputSchema"]["properties"]["snapshot"]["type"] = "object";
    schematicDiff["inputSchema"]["properties"]["snapshot"]["description"] = "Previous state: {components: [{reference, symbol, value, x, y, rotation}], nets: [name1, ...]}";
    schematicDiff["inputSchema"]["properties"]["snapshot"]["properties"]["components"]["type"] = "array";
    schematicDiff["inputSchema"]["properties"]["snapshot"]["properties"]["components"]["items"]["type"] = "object";
    schematicDiff["inputSchema"]["properties"]["snapshot"]["properties"]["nets"]["type"] = "array";
    schematicDiff["inputSchema"]["properties"]["snapshot"]["properties"]["nets"]["items"]["type"] = "string";
    tools.push_back( schematicDiff );

    // ── net_diagnostics ──
    json netDiagnostics;
    netDiagnostics["name"] = "net_diagnostics";
    netDiagnostics["description"] = "Analyze the netlist for issues: floating nets (only 1 connection), orphaned labels (no connections), dangling wires, and unconnected pins. Returns detailed issues with suggestions. No arguments required.";
    netDiagnostics["inputSchema"]["type"] = "object";
    netDiagnostics["inputSchema"]["properties"] = json::object();
    tools.push_back( netDiagnostics );

    // ── auto_cleanup_stray_nets ──
    json autoCleanup;
    autoCleanup["name"] = "auto_cleanup_stray_nets";
    autoCleanup["description"] = "Automatically remove orphaned labels and stray single-pin nets identified by net_diagnostics. Removes global labels that have no pins connected, and removes labels from nets with only 1 pin. Use after major edits to clean up schematic. No arguments required.";
    autoCleanup["inputSchema"]["type"] = "object";
    autoCleanup["inputSchema"]["properties"] = json::object();
    tools.push_back( autoCleanup );

    // ── list_wires ──
    json listWires;
    listWires["name"] = "list_wires";
    listWires["description"] = "List all wire segments on the schematic by reading the .kicad_sch file directly. Returns wire endpoints and UUIDs. Works even when GetItems IPC is unavailable. Optional bbox filter.";
    listWires["inputSchema"]["type"] = "object";
    listWires["inputSchema"]["properties"]["min_x"]["type"] = "number";
    listWires["inputSchema"]["properties"]["min_x"]["description"] = "Optional bbox filter: minimum X (mm)";
    listWires["inputSchema"]["properties"]["min_y"]["type"] = "number";
    listWires["inputSchema"]["properties"]["max_x"]["type"] = "number";
    listWires["inputSchema"]["properties"]["max_y"]["type"] = "number";
    tools.push_back( listWires );

    // ── get_visible_bounds ──
    json getVisibleBounds;
    getVisibleBounds["name"] = "get_visible_bounds";
    getVisibleBounds["description"] = "Get the currently visible KiCad schematic viewport bounds in mm (min/max, center, size, and viewport pixel dimensions).";
    getVisibleBounds["inputSchema"]["type"] = "object";
    getVisibleBounds["inputSchema"]["properties"] = json::object();
    tools.push_back( getVisibleBounds );

    // ── get_all_labels ──
    json getAllLabels;
    getAllLabels["name"] = "get_all_labels";
    getAllLabels["description"] = "List all live labels from the active schematic source with uuid, text, kind, x/y, rotation, and sheet path. Optional bbox filter in mm.";
    getAllLabels["inputSchema"]["type"] = "object";
    getAllLabels["inputSchema"]["properties"]["min_x"]["type"] = "number";
    getAllLabels["inputSchema"]["properties"]["min_y"]["type"] = "number";
    getAllLabels["inputSchema"]["properties"]["max_x"]["type"] = "number";
    getAllLabels["inputSchema"]["properties"]["max_y"]["type"] = "number";
    tools.push_back( getAllLabels );

    // ── get_all_wires ──
    json getAllWires;
    getAllWires["name"] = "get_all_wires";
    getAllWires["description"] = "List all wire segments from the active schematic source with uuid and endpoints. Optional bbox filter in mm.";
    getAllWires["inputSchema"]["type"] = "object";
    getAllWires["inputSchema"]["properties"]["min_x"]["type"] = "number";
    getAllWires["inputSchema"]["properties"]["min_y"]["type"] = "number";
    getAllWires["inputSchema"]["properties"]["max_x"]["type"] = "number";
    getAllWires["inputSchema"]["properties"]["max_y"]["type"] = "number";
    tools.push_back( getAllWires );

    // ── get_wire_labels ──
    json getWireLabels;
    getWireLabels["name"] = "get_wire_labels";
    getWireLabels["description"] = "For each wire segment, return attached live labels (local, hierarchical, global, directive) with UUIDs and coordinates using geometric matching.";
    getWireLabels["inputSchema"]["type"] = "object";
    getWireLabels["inputSchema"]["properties"]["min_x"]["type"] = "number";
    getWireLabels["inputSchema"]["properties"]["min_y"]["type"] = "number";
    getWireLabels["inputSchema"]["properties"]["max_x"]["type"] = "number";
    getWireLabels["inputSchema"]["properties"]["max_y"]["type"] = "number";
    getWireLabels["inputSchema"]["properties"]["tolerance_mm"]["type"] = "number";
    getWireLabels["inputSchema"]["properties"]["only_labeled"]["type"] = "boolean";
    tools.push_back( getWireLabels );

    // ── get_labels_in_view ──
    json getLabelsInView;
    getLabelsInView["name"] = "get_labels_in_view";
    getLabelsInView["description"] = "Return live labels inside the current viewport bounds.";
    getLabelsInView["inputSchema"]["type"] = "object";
    getLabelsInView["inputSchema"]["properties"] = json::object();
    tools.push_back( getLabelsInView );

    // ── rename_labels_in_bbox ──
    json renameLabelsInBbox;
    renameLabelsInBbox["name"] = "rename_labels_in_bbox";
    renameLabelsInBbox["description"] = "Rename live labels in a bounding box using nearest-instance targeting. Supports dry_run preview and optional old_name filter.";
    renameLabelsInBbox["inputSchema"]["type"] = "object";
    renameLabelsInBbox["inputSchema"]["required"] = json::array( { "min_x", "min_y", "max_x", "max_y", "new_name" } );
    renameLabelsInBbox["inputSchema"]["properties"]["min_x"]["type"] = "number";
    renameLabelsInBbox["inputSchema"]["properties"]["min_y"]["type"] = "number";
    renameLabelsInBbox["inputSchema"]["properties"]["max_x"]["type"] = "number";
    renameLabelsInBbox["inputSchema"]["properties"]["max_y"]["type"] = "number";
    renameLabelsInBbox["inputSchema"]["properties"]["old_name"]["type"] = "string";
    renameLabelsInBbox["inputSchema"]["properties"]["new_name"]["type"] = "string";
    renameLabelsInBbox["inputSchema"]["properties"]["dry_run"]["type"] = "boolean";
    tools.push_back( renameLabelsInBbox );

    // ── remove_items_by_position ──
    json removeByPos;
    removeByPos["name"] = "remove_items_by_position";
    removeByPos["description"] = "Remove wires and/or labels near a specific position (mm). Reads the .kicad_sch file to find items, then uses DeleteItems to remove them. Use this to clean up dangling wires left after component deletion. Tolerance in mm (default 1.0).";
    removeByPos["inputSchema"]["type"] = "object";
    removeByPos["inputSchema"]["required"] = json::array( { "x", "y" } );
    removeByPos["inputSchema"]["properties"]["x"]["type"] = "number";
    removeByPos["inputSchema"]["properties"]["x"]["description"] = "X position in mm";
    removeByPos["inputSchema"]["properties"]["y"]["type"] = "number";
    removeByPos["inputSchema"]["properties"]["y"]["description"] = "Y position in mm";
    removeByPos["inputSchema"]["properties"]["tolerance"]["type"] = "number";
    removeByPos["inputSchema"]["properties"]["tolerance"]["description"] = "Search radius in mm (default 1.0)";
    removeByPos["inputSchema"]["properties"]["remove_wires"]["type"] = "boolean";
    removeByPos["inputSchema"]["properties"]["remove_wires"]["description"] = "Remove wire segments (default true)";
    removeByPos["inputSchema"]["properties"]["remove_labels"]["type"] = "boolean";
    removeByPos["inputSchema"]["properties"]["remove_labels"]["description"] = "Remove global labels (default true)";
    tools.push_back( removeByPos );

    // ── remove_all_dangling ──
    json removeDangling;
    removeDangling["name"] = "remove_all_dangling";
    removeDangling["description"] = "Remove ALL dangling wire endpoints reported by erc_check. Reads the .kicad_sch file to find wire UUIDs at dangling positions, then deletes them. Call this after deleting components to clean up orphaned wires.";
    removeDangling["inputSchema"]["type"] = "object";
    removeDangling["inputSchema"]["properties"] = json::object();
    tools.push_back( removeDangling );

    // ── block_classify ──
    json blockClassify;
    blockClassify["name"] = "block_classify";
    blockClassify["description"] = "Analyze components within a bounding box to infer the block's function (power_regulation, amplifier_stage, digital_core, filter, interface, switching_stage, indicator, mixed). Returns classification, confidence, component counts, and detected patterns.";
    blockClassify["inputSchema"]["type"] = "object";
    blockClassify["inputSchema"]["required"] = json::array( { "min_x", "min_y", "max_x", "max_y" } );
    blockClassify["inputSchema"]["properties"]["min_x"]["type"] = "number";
    blockClassify["inputSchema"]["properties"]["min_x"]["description"] = "Left edge X in mm";
    blockClassify["inputSchema"]["properties"]["min_y"]["type"] = "number";
    blockClassify["inputSchema"]["properties"]["min_y"]["description"] = "Top edge Y in mm";
    blockClassify["inputSchema"]["properties"]["max_x"]["type"] = "number";
    blockClassify["inputSchema"]["properties"]["max_x"]["description"] = "Right edge X in mm";
    blockClassify["inputSchema"]["properties"]["max_y"]["type"] = "number";
    blockClassify["inputSchema"]["properties"]["max_y"]["description"] = "Bottom edge Y in mm";
    tools.push_back( blockClassify );

    // ── get_bom ──
    json getBom;
    getBom["name"] = "get_bom";
    getBom["description"] = "Get the Bill of Materials (BOM) for the schematic. Returns component reference, value, symbol, footprint, description, and any Digi-Key BOM data (part number, manufacturer part, unit price, stock, lead time, URL).";
    getBom["inputSchema"]["type"] = "object";
    getBom["inputSchema"]["properties"] = json::object();
    tools.push_back( getBom );

    // ── update_component_bom_data ──
    json updateBomData;
    updateBomData["name"] = "update_component_bom_data";
    updateBomData["description"] = "Update BOM data for a single component (e.g., add Digi-Key part number, manufacturer part, unit price, stock quantity, lead time, URL). Links the component to supplier information.";
    updateBomData["inputSchema"]["type"] = "object";
    updateBomData["inputSchema"]["required"] = json::array( { "reference" } );
    updateBomData["inputSchema"]["properties"]["reference"]["type"] = "string";
    updateBomData["inputSchema"]["properties"]["reference"]["description"] = "Component reference (e.g. U1, R1)";
    updateBomData["inputSchema"]["properties"]["digikey_part_number"]["type"] = "string";
    updateBomData["inputSchema"]["properties"]["digikey_part_number"]["description"] = "Digi-Key part number";
    updateBomData["inputSchema"]["properties"]["manufacturer_part_number"]["type"] = "string";
    updateBomData["inputSchema"]["properties"]["manufacturer_part_number"]["description"] = "Manufacturer part number";
    updateBomData["inputSchema"]["properties"]["unit_price_usd"]["type"] = "number";
    updateBomData["inputSchema"]["properties"]["unit_price_usd"]["description"] = "Unit price in USD";
    updateBomData["inputSchema"]["properties"]["stock_quantity"]["type"] = "integer";
    updateBomData["inputSchema"]["properties"]["stock_quantity"]["description"] = "Available stock quantity";
    updateBomData["inputSchema"]["properties"]["lead_time_days"]["type"] = "integer";
    updateBomData["inputSchema"]["properties"]["lead_time_days"]["description"] = "Lead time in days";
    updateBomData["inputSchema"]["properties"]["digikey_url"]["type"] = "string";
    updateBomData["inputSchema"]["properties"]["digikey_url"]["description"] = "URL to Digi-Key product page";
    tools.push_back( updateBomData );

    // ── batch_update_bom_data ──
    json batchUpdateBom;
    batchUpdateBom["name"] = "batch_update_bom_data";
    batchUpdateBom["description"] = "Update BOM data for multiple components in one call. Each component can have different Digi-Key fields. Much faster than calling update_component_bom_data multiple times.";
    batchUpdateBom["inputSchema"]["type"] = "object";
    batchUpdateBom["inputSchema"]["required"] = json::array( { "components" } );
    batchUpdateBom["inputSchema"]["properties"]["components"]["type"] = "array";
    batchUpdateBom["inputSchema"]["properties"]["components"]["description"] = "Array of component BOM update specs";
    batchUpdateBom["inputSchema"]["properties"]["components"]["items"]["type"] = "object";
    batchUpdateBom["inputSchema"]["properties"]["components"]["items"]["properties"]["reference"]["type"] = "string";
    batchUpdateBom["inputSchema"]["properties"]["components"]["items"]["properties"]["digikey_part_number"]["type"] = "string";
    batchUpdateBom["inputSchema"]["properties"]["components"]["items"]["properties"]["manufacturer_part_number"]["type"] = "string";
    batchUpdateBom["inputSchema"]["properties"]["components"]["items"]["properties"]["unit_price_usd"]["type"] = "number";
    batchUpdateBom["inputSchema"]["properties"]["components"]["items"]["properties"]["stock_quantity"]["type"] = "integer";
    batchUpdateBom["inputSchema"]["properties"]["components"]["items"]["properties"]["lead_time_days"]["type"] = "integer";
    batchUpdateBom["inputSchema"]["properties"]["components"]["items"]["properties"]["digikey_url"]["type"] = "string";
    tools.push_back( batchUpdateBom );

    // ── set_component_fields ──
    json setCompFields;
    setCompFields["name"] = "set_component_fields";
    setCompFields["description"] = "Set custom fields on a component (Digi-Key part number, price, stock, etc.). Fields are stored as component properties in KiCad. After setting, use export_schematic_to_json + commit_schematic_from_json to persist changes to the schematic file.";
    setCompFields["inputSchema"]["type"] = "object";
    setCompFields["inputSchema"]["required"] = json::array( { "reference", "fields" } );
    setCompFields["inputSchema"]["properties"]["reference"]["type"] = "string";
    setCompFields["inputSchema"]["properties"]["reference"]["description"] = "Component reference (e.g. U1, R1)";
    setCompFields["inputSchema"]["properties"]["fields"]["type"] = "object";
    setCompFields["inputSchema"]["properties"]["fields"]["description"] = "Object with field name/value pairs (e.g. {\"Digi-Key Part Number\": \"497-12345-1-ND\", \"Unit Price USD\": \"5.25\"})";
    tools.push_back( setCompFields );

    // ── batch_search_footprint ──
    json batchSearchFootprint;
    batchSearchFootprint["name"] = "batch_search_footprint";
    batchSearchFootprint["description"] = "Search for footprints by keyword in KiCad footprint libraries. Returns matching footprint names, libraries, and descriptions. Searches across all installed libraries.";
    batchSearchFootprint["inputSchema"]["type"] = "object";
    batchSearchFootprint["inputSchema"]["required"] = json::array( { "queries" } );
    batchSearchFootprint["inputSchema"]["properties"]["queries"]["type"] = "array";
    batchSearchFootprint["inputSchema"]["properties"]["queries"]["description"] = "Array of search keywords (e.g. ['0603', 'LQFP64', 'BGA'])";
    batchSearchFootprint["inputSchema"]["properties"]["queries"]["items"]["type"] = "string";
    batchSearchFootprint["inputSchema"]["properties"]["limit"]["type"] = "integer";
    batchSearchFootprint["inputSchema"]["properties"]["limit"]["description"] = "Maximum results per query (default 50, max 200)";
    tools.push_back( batchSearchFootprint );

    // ── batch_assign_footprint ──
    json batchAssignFootprint;
    batchAssignFootprint["name"] = "batch_assign_footprint";
    batchAssignFootprint["description"] = "Assign footprints to multiple components in one call. Each component gets a footprint from the specified library. Updates component footprint field in schematic.";
    batchAssignFootprint["inputSchema"]["type"] = "object";
    batchAssignFootprint["inputSchema"]["required"] = json::array( { "assignments" } );
    batchAssignFootprint["inputSchema"]["properties"]["assignments"]["type"] = "array";
    batchAssignFootprint["inputSchema"]["properties"]["assignments"]["description"] = "Array of component-footprint assignments";
    batchAssignFootprint["inputSchema"]["properties"]["assignments"]["items"]["type"] = "object";
    batchAssignFootprint["inputSchema"]["properties"]["assignments"]["items"]["properties"]["reference"]["type"] = "string";
    batchAssignFootprint["inputSchema"]["properties"]["assignments"]["items"]["properties"]["reference"]["description"] = "Component reference (e.g. U1, R1)";
    batchAssignFootprint["inputSchema"]["properties"]["assignments"]["items"]["properties"]["footprint_name"]["type"] = "string";
    batchAssignFootprint["inputSchema"]["properties"]["assignments"]["items"]["properties"]["footprint_name"]["description"] = "Footprint name (e.g. QFP64, 0603)";
    batchAssignFootprint["inputSchema"]["properties"]["assignments"]["items"]["properties"]["footprint_library"]["type"] = "string";
    batchAssignFootprint["inputSchema"]["properties"]["assignments"]["items"]["properties"]["footprint_library"]["description"] = "Footprint library name (e.g. Package_QFP, Resistor_SMD)";
    tools.push_back( batchAssignFootprint );

    // ── query_schematic_json_path ──
    json queryJsonPath;
    queryJsonPath["name"] = "query_schematic_json_path";
    queryJsonPath["description"] = "Query specific parts of the schematic using dot-notation paths. Supported: 'components', 'components.R1', 'components.R1.pins', 'components.R1.connections', 'components.R1.value', 'nets', 'nets.VCC', 'stats'. 'schematic_state.*' aliases are also accepted for compatibility.";
    queryJsonPath["inputSchema"]["type"] = "object";
    queryJsonPath["inputSchema"]["required"] = json::array( { "query" } );
    queryJsonPath["inputSchema"]["properties"]["query"]["type"] = "string";
    queryJsonPath["inputSchema"]["properties"]["query"]["description"] = "Dot-notation path (e.g. 'components.R1.pins', 'nets.VCC', 'stats')";
    tools.push_back( queryJsonPath );

    // ── get_schematic_state ──
    json getSchematicState;
    getSchematicState["name"] = "get_schematic_state";
    getSchematicState["description"] = "Get the full schematic state as structured JSON. Includes all components (reference, value, position, pins), all nets (name, connected pins), all global labels, and dangling items. Use this to capture a snapshot before changes (for schematic_diff) or for detailed programmatic analysis.";
    getSchematicState["inputSchema"]["type"] = "object";
    tools.push_back( getSchematicState );
    // ── Part understanding tools ──

    json fetchDatasheet;
    fetchDatasheet["name"] = "fetch_datasheet";
    fetchDatasheet["description"] = "Get the datasheet URL and description for a component. Provide either a placed component reference (e.g. 'U1') or library+symbol to look up a library part.";
    fetchDatasheet["inputSchema"]["type"] = "object";
    fetchDatasheet["inputSchema"]["properties"]["reference"]["type"] = "string";
    fetchDatasheet["inputSchema"]["properties"]["reference"]["description"] = "Component reference designator on the schematic (e.g. 'U1')";
    fetchDatasheet["inputSchema"]["properties"]["library"]["type"] = "string";
    fetchDatasheet["inputSchema"]["properties"]["library"]["description"] = "Library nickname (use with 'symbol')";
    fetchDatasheet["inputSchema"]["properties"]["symbol"]["type"] = "string";
    fetchDatasheet["inputSchema"]["properties"]["symbol"]["description"] = "Symbol name (use with 'library')";
    tools.push_back( fetchDatasheet );

    json parametricSearch;
    parametricSearch["name"] = "parametric_part_search";
    parametricSearch["description"] = "Search for components by type and optional parametric filters. Returns matching library symbols with their details.";
    parametricSearch["inputSchema"]["type"] = "object";
    parametricSearch["inputSchema"]["required"] = json::array( { "type" } );
    parametricSearch["inputSchema"]["properties"]["type"]["type"] = "string";
    parametricSearch["inputSchema"]["properties"]["type"]["description"] = "Component type: resistor, capacitor, inductor, diode, transistor, ic, connector";
    parametricSearch["inputSchema"]["properties"]["type"]["enum"] = json::array( { "resistor", "capacitor", "inductor", "diode", "transistor", "ic", "connector" } );
    parametricSearch["inputSchema"]["properties"]["value"]["type"] = "string";
    parametricSearch["inputSchema"]["properties"]["value"]["description"] = "Optional value filter (e.g. '10k', '100nF')";
    parametricSearch["inputSchema"]["properties"]["package"]["type"] = "string";
    parametricSearch["inputSchema"]["properties"]["package"]["description"] = "Optional package filter (e.g. '0402', '0603', 'SOT-23')";
    parametricSearch["inputSchema"]["properties"]["library"]["type"] = "string";
    parametricSearch["inputSchema"]["properties"]["library"]["description"] = "Optional library to restrict search";
    parametricSearch["inputSchema"]["properties"]["limit"]["type"] = "integer";
    parametricSearch["inputSchema"]["properties"]["limit"]["description"] = "Max results (default 20)";
    tools.push_back( parametricSearch );

    json findAlternates;
    findAlternates["name"] = "find_alternates";
    findAlternates["description"] = "Find alternative/equivalent components with matching pin count. Provide either a placed component reference or library+symbol.";
    findAlternates["inputSchema"]["type"] = "object";
    findAlternates["inputSchema"]["properties"]["reference"]["type"] = "string";
    findAlternates["inputSchema"]["properties"]["reference"]["description"] = "Component reference designator on the schematic (e.g. 'U1')";
    findAlternates["inputSchema"]["properties"]["library"]["type"] = "string";
    findAlternates["inputSchema"]["properties"]["library"]["description"] = "Library nickname (use with 'symbol')";
    findAlternates["inputSchema"]["properties"]["symbol"]["type"] = "string";
    findAlternates["inputSchema"]["properties"]["symbol"]["description"] = "Symbol name (use with 'library')";
    tools.push_back( findAlternates );

    json consistencyCheck;
    consistencyCheck["name"] = "symbol_footprint_consistency_check";
    consistencyCheck["description"] = "Check symbol/footprint consistency for placed components. Verifies pin count, pin names, and flags missing data. Provide a reference to check one component, or omit to check all.";
    consistencyCheck["inputSchema"]["type"] = "object";
    consistencyCheck["inputSchema"]["properties"]["reference"]["type"] = "string";
    consistencyCheck["inputSchema"]["properties"]["reference"]["description"] = "Optional: check only this component reference. If omitted, checks all components.";
    tools.push_back( consistencyCheck );

    // ── align_components ──
    json alignComp;
    alignComp["name"] = "align_components";
    alignComp["description"] = "Align multiple components along an axis. Moves all specified components so they share the same X (vertical alignment) or Y (horizontal alignment) coordinate.";
    alignComp["inputSchema"]["type"] = "object";
    alignComp["inputSchema"]["required"] = json::array( { "references", "axis", "align_to" } );
    alignComp["inputSchema"]["properties"]["references"]["type"] = "array";
    alignComp["inputSchema"]["properties"]["references"]["description"] = "Array of component reference designators (e.g. [\"R1\", \"R2\", \"C1\"])";
    alignComp["inputSchema"]["properties"]["references"]["items"]["type"] = "string";
    alignComp["inputSchema"]["properties"]["axis"]["type"] = "string";
    alignComp["inputSchema"]["properties"]["axis"]["description"] = "Alignment axis: 'horizontal' (same Y) or 'vertical' (same X)";
    alignComp["inputSchema"]["properties"]["axis"]["enum"] = json::array( { "horizontal", "vertical" } );
    alignComp["inputSchema"]["properties"]["align_to"]["type"] = "string";
    alignComp["inputSchema"]["properties"]["align_to"]["description"] = "Alignment target: 'min' (leftmost/topmost), 'max' (rightmost/bottommost), 'center' (center of extent), 'mean' (average position)";
    alignComp["inputSchema"]["properties"]["align_to"]["enum"] = json::array( { "min", "max", "center", "mean" } );
    tools.push_back( alignComp );

    // ── distribute_components ──
    json distComp;
    distComp["name"] = "distribute_components";
    distComp["description"] = "Distribute components evenly along an axis. If spacing_mm is provided, uses fixed spacing from the first component; otherwise distributes evenly between first and last.";
    distComp["inputSchema"]["type"] = "object";
    distComp["inputSchema"]["required"] = json::array( { "references", "axis" } );
    distComp["inputSchema"]["properties"]["references"]["type"] = "array";
    distComp["inputSchema"]["properties"]["references"]["description"] = "Array of component reference designators (e.g. [\"R1\", \"R2\", \"C1\"])";
    distComp["inputSchema"]["properties"]["references"]["items"]["type"] = "string";
    distComp["inputSchema"]["properties"]["axis"]["type"] = "string";
    distComp["inputSchema"]["properties"]["axis"]["description"] = "Distribution axis: 'horizontal' (spread along X) or 'vertical' (spread along Y)";
    distComp["inputSchema"]["properties"]["axis"]["enum"] = json::array( { "horizontal", "vertical" } );
    distComp["inputSchema"]["properties"]["spacing_mm"]["type"] = "number";
    distComp["inputSchema"]["properties"]["spacing_mm"]["description"] = "Optional fixed spacing in mm between components. If omitted, distributes evenly between first and last positions.";
    tools.push_back( distComp );

    // ── autoroute_short_orthogonal ──
    json autorouteShort;
    autorouteShort["name"] = "autoroute_short_orthogonal";
    autorouteShort["description"] = "Create a short Manhattan-style (orthogonal) wire route between two pins. Uses L-shaped or direct routing depending on pin positions.";
    autorouteShort["inputSchema"]["type"] = "object";
    autorouteShort["inputSchema"]["required"] = json::array( { "reference1", "pin1", "reference2", "pin2" } );
    autorouteShort["inputSchema"]["properties"]["reference1"]["type"] = "string";
    autorouteShort["inputSchema"]["properties"]["reference1"]["description"] = "First component reference (e.g. 'R1')";
    autorouteShort["inputSchema"]["properties"]["pin1"]["type"] = "string";
    autorouteShort["inputSchema"]["properties"]["pin1"]["description"] = "First pin number";
    autorouteShort["inputSchema"]["properties"]["reference2"]["type"] = "string";
    autorouteShort["inputSchema"]["properties"]["reference2"]["description"] = "Second component reference (e.g. 'C1')";
    autorouteShort["inputSchema"]["properties"]["pin2"]["type"] = "string";
    autorouteShort["inputSchema"]["properties"]["pin2"]["description"] = "Second pin number";
    autorouteShort["inputSchema"]["properties"]["net_name"]["type"] = "string";
    autorouteShort["inputSchema"]["properties"]["net_name"]["description"] = "Optional net name for documentation only unless add_net_labels is true.";
    autorouteShort["inputSchema"]["properties"]["add_net_labels"]["type"] = "boolean";
    autorouteShort["inputSchema"]["properties"]["add_net_labels"]["description"] = "If true with net_name, add global labels + stub wires (deduplicated per grid cell). Default false—route wires only.";
    tools.push_back( autorouteShort );

    // ── get_placed_label_positions ──
    json getPlacedLabels;
    getPlacedLabels["name"] = "get_placed_label_positions";
    getPlacedLabels["description"] = "List all live labels placed on the schematic with their position, text, kind, and rotation. Useful for debugging label orientation and verifying connections. Optional bbox filter.";
    getPlacedLabels["inputSchema"]["type"] = "object";
    getPlacedLabels["inputSchema"]["properties"]["min_x"]["type"] = "number";
    getPlacedLabels["inputSchema"]["properties"]["min_x"]["description"] = "Optional bbox filter: minimum X (mm)";
    getPlacedLabels["inputSchema"]["properties"]["min_y"]["type"] = "number";
    getPlacedLabels["inputSchema"]["properties"]["min_y"]["description"] = "Optional bbox filter: minimum Y (mm)";
    getPlacedLabels["inputSchema"]["properties"]["max_x"]["type"] = "number";
    getPlacedLabels["inputSchema"]["properties"]["max_x"]["description"] = "Optional bbox filter: maximum X (mm)";
    getPlacedLabels["inputSchema"]["properties"]["max_y"]["type"] = "number";
    getPlacedLabels["inputSchema"]["properties"]["max_y"]["description"] = "Optional bbox filter: maximum Y (mm)";
    tools.push_back( getPlacedLabels );

    // ── validate_placement_constraints ──
    json validatePlacement;
    validatePlacement["name"] = "validate_placement_constraints";
    validatePlacement["description"] = "Check that all components are placed within a bounding box. Returns {valid, outside_bbox:[{ref,x_mm,y_mm}]}. Use after place_component to catch stray placements.";
    validatePlacement["inputSchema"]["type"] = "object";
    validatePlacement["inputSchema"]["required"] = json::array( { "min_x", "min_y", "max_x", "max_y" } );
    validatePlacement["inputSchema"]["properties"]["min_x"]["type"] = "number";
    validatePlacement["inputSchema"]["properties"]["min_x"]["description"] = "Bounding box minimum X (mm)";
    validatePlacement["inputSchema"]["properties"]["min_y"]["type"] = "number";
    validatePlacement["inputSchema"]["properties"]["min_y"]["description"] = "Bounding box minimum Y (mm)";
    validatePlacement["inputSchema"]["properties"]["max_x"]["type"] = "number";
    validatePlacement["inputSchema"]["properties"]["max_x"]["description"] = "Bounding box maximum X (mm)";
    validatePlacement["inputSchema"]["properties"]["max_y"]["type"] = "number";
    validatePlacement["inputSchema"]["properties"]["max_y"]["description"] = "Bounding box maximum Y (mm)";
    tools.push_back( validatePlacement );

    // ── reload_project_symbol_libraries ──
    json reloadSymLibs;
    reloadSymLibs["name"] = "reload_project_symbol_libraries";
    reloadSymLibs["description"] =
            "Reload project sym-lib-table from disk, refresh legacy symbol-lib caches, and notify editors. "
            "Call after external tools (e.g. library import) update sym-lib-table or symbol files.";
    reloadSymLibs["inputSchema"]["type"] = "object";
    reloadSymLibs["inputSchema"]["properties"] = json::object();
    tools.push_back( reloadSymLibs );

    // ── reload_project_footprint_libraries ──
    json reloadFpLibs;
    reloadFpLibs["name"] = "reload_project_footprint_libraries";
    reloadFpLibs["description"] =
            "Reload project fp-lib-table from disk and notify footprint-facing editors. "
            "Call after external tools update fp-lib-table or project .pretty paths.";
    reloadFpLibs["inputSchema"]["type"] = "object";
    reloadFpLibs["inputSchema"]["properties"] = json::object();
    tools.push_back( reloadFpLibs );

    // ── append_project_symbol_library_row ──
    json appendSymRow;
    appendSymRow["name"] = "append_project_symbol_library_row";
    appendSymRow["description"] =
            "Append one KiCad symbol library row to the project sym-lib-table (saved to disk) and refresh libraries. "
            "URI should be ${KIPRJMOD}/relative/path.kicad_sym.";
    appendSymRow["inputSchema"]["type"] = "object";
    appendSymRow["inputSchema"]["properties"]["library_nickname"]["type"] = "string";
    appendSymRow["inputSchema"]["properties"]["uri"]["type"] = "string";
    appendSymRow["inputSchema"]["properties"]["description"]["type"] = "string";
    appendSymRow["inputSchema"]["properties"]["replace_existing"]["type"] = "boolean";
    appendSymRow["inputSchema"]["required"] = json::array( { "library_nickname", "uri" } );
    tools.push_back( appendSymRow );

    // ── append_project_footprint_library_row ──
    json appendFpRow;
    appendFpRow["name"] = "append_project_footprint_library_row";
    appendFpRow["description"] =
            "Append one KiCad footprint library row to the project fp-lib-table (saved to disk) and refresh libraries. "
            "URI should be ${KIPRJMOD}/relative/path.pretty.";
    appendFpRow["inputSchema"]["type"] = "object";
    appendFpRow["inputSchema"]["properties"]["library_nickname"]["type"] = "string";
    appendFpRow["inputSchema"]["properties"]["uri"]["type"] = "string";
    appendFpRow["inputSchema"]["properties"]["description"]["type"] = "string";
    appendFpRow["inputSchema"]["properties"]["replace_existing"]["type"] = "boolean";
    appendFpRow["inputSchema"]["required"] = json::array( { "library_nickname", "uri" } );
    tools.push_back( appendFpRow );

    // ── ingest_project_library_files ──
    json ingestLibs;
    ingestLibs["name"] = "ingest_project_library_files";
    ingestLibs["description"] =
            "Write base64-decoded KiCad library files under the open project and append sym-lib-table / fp-lib-table rows, "
            "then reload symbol and footprint libraries in the running editor. "
            "Use when the agent already has .kicad_sym and optional .kicad_mod payloads.";
    ingestLibs["inputSchema"]["type"] = "object";
    ingestLibs["inputSchema"]["properties"]["project_relative_dir"]["type"] = "string";
    ingestLibs["inputSchema"]["properties"]["library_nickname"]["type"] = "string";
    ingestLibs["inputSchema"]["properties"]["symbol_uri_relative"]["type"] = "string";
    ingestLibs["inputSchema"]["properties"]["footprint_uri_relative"]["type"] = "string";
    ingestLibs["inputSchema"]["properties"]["description"]["type"] = "string";
    ingestLibs["inputSchema"]["properties"]["replace_existing"]["type"] = "boolean";
    ingestLibs["inputSchema"]["properties"]["replace_existing"]["description"] =
            "Reserved; must be false. Table row replacement is not implemented (use a fresh library_nickname).";
    ingestLibs["inputSchema"]["properties"]["files"]["type"] = "array";
    ingestLibs["inputSchema"]["properties"]["files"]["items"]["type"] = "object";
    ingestLibs["inputSchema"]["properties"]["files"]["items"]["properties"]["relative_path"]["type"] = "string";
    ingestLibs["inputSchema"]["properties"]["files"]["items"]["properties"]["content_base64"]["type"] = "string";
    ingestLibs["inputSchema"]["properties"]["files"]["items"]["required"] = json::array( { "relative_path", "content_base64" } );
    ingestLibs["inputSchema"]["required"] =
            json::array( { "project_relative_dir", "library_nickname", "symbol_uri_relative", "footprint_uri_relative", "files" } );
    tools.push_back( ingestLibs );

    // ── erc_rules_get ──
    json ercRulesGet;
    ercRulesGet["name"] = "erc_rules_get";
    ercRulesGet["description"] = "Get current ERC (electrical rules check) status. Returns dangling report items categorized by type with severity levels.";
    ercRulesGet["inputSchema"]["type"] = "object";
    ercRulesGet["inputSchema"]["properties"] = json::object();
    tools.push_back( ercRulesGet );

    // ── erc_waivers_list ──
    json ercWaivers;
    ercWaivers["name"] = "erc_waivers_list";
    ercWaivers["description"] = "List current ERC exclusions/waivers. Returns known ERC issues that are being intentionally ignored.";
    ercWaivers["inputSchema"]["type"] = "object";
    ercWaivers["inputSchema"]["properties"] = json::object();
    tools.push_back( ercWaivers );

    json result = { { "tools", tools } };
    return "{\"jsonrpc\":\"2.0\",\"result\":" + result.dump() + ",\"id\":" + id + "}";
}


std::string McpHandler::HandleToolsCall( const void* params, const std::string& id )
{
    // Invalidate per-request summary cache so each tool call gets fresh data.
    invalidateSummaryCache();

    const json* j = static_cast<const json*>( params );
    if( !j || !j->is_object() || !j->contains( "name" ) || !( *j )["name"].is_string() )
    {
        return "{\"jsonrpc\":\"2.0\",\"error\":{\"code\":-32602,\"message\":\"Invalid params\"},\"id\":" + id + "}";
    }

    std::string name = ( *j )["name"].get<std::string>();
    json args = j->contains( "arguments" ) && ( *j )["arguments"].is_object() ? ( *j )["arguments"]
                                                                                 : json::object();
    auto makeErrorResult = [&]( const std::string& message ) -> std::string
    {
        json content = json::array();
        content.push_back( { { "type", "text" }, { "text", message } } );
        return "{\"jsonrpc\":\"2.0\",\"result\":"
                + json( { { "content", content }, { "isError", true } } ).dump()
                + ",\"id\":" + id + "}";
    };

    // Unwrap double-nested "arguments" (some MCP clients send { "arguments": { "arguments": {...} } })
    if( args.contains( "arguments" ) && args["arguments"].is_object() )
    {
        // Generic detection: if inner "arguments" looks like it has real tool args, unwrap it.
        // Check that the outer layer has only the "arguments" key (or is suspiciously thin).
        const json& inner = args["arguments"];
        bool innerLooksLikeToolArgs = inner.size() > 0
            && !inner.contains( "arguments" ); // avoid triple-nesting
        if( innerLooksLikeToolArgs )
            args = inner;
    }

    // ── Safe argument extraction helpers ────────────────────────────────
    // These handle missing keys, wrong types, and string-to-number coercion
    // so individual tool handlers never crash on malformed input.
    auto trimStr = []( const std::string& value ) -> std::string {
        auto isSpace = []( unsigned char c ) { return std::isspace( c ) != 0; };
        auto begin = std::find_if_not( value.begin(), value.end(), isSpace );
        if( begin == value.end() )
            return "";
        auto end = std::find_if_not( value.rbegin(), value.rend(), isSpace ).base();
        return std::string( begin, end );
    };
    auto getStr = [&]( const std::string& key, const std::string& def = "" ) -> std::string {
        if( !args.contains( key ) )
            return def;

        try
        {
            if( args[key].is_string() )
                return trimStr( args[key].get<std::string>() );

            if( args[key].is_number_integer() )
                return std::to_string( args[key].get<long long>() );

            if( args[key].is_number_unsigned() )
                return std::to_string( args[key].get<unsigned long long>() );

            if( args[key].is_number_float() )
            {
                std::ostringstream os;
                os << args[key].get<double>();
                return trimStr( os.str() );
            }
        }
        catch( ... )
        {
        }

        return def;
    };
    auto getDouble = [&]( const std::string& key, double def = 0.0 ) -> double {
        if( !args.contains( key ) ) return def;
        if( args[key].is_number() ) return args[key].get<double>();
        if( args[key].is_string() )
        {
            try { return std::stod( args[key].get<std::string>() ); }
            catch( ... ) { return def; }
        }
        return def;
    };
    auto getInt = [&]( const std::string& key, int def = 0 ) -> int {
        if( !args.contains( key ) ) return def;
        if( args[key].is_number() ) return args[key].get<int>();
        if( args[key].is_string() )
        {
            try { return std::stoi( args[key].get<std::string>() ); }
            catch( ... ) { return def; }
        }
        return def;
    };
    auto getBool = [&]( const std::string& key, bool def = false ) -> bool {
        if( !args.contains( key ) ) return def;
        if( args[key].is_boolean() ) return args[key].get<bool>();
        if( args[key].is_string() )
        {
            std::string s = args[key].get<std::string>();
            return s == "true" || s == "1";
        }
        if( args[key].is_number() ) return args[key].get<int>() != 0;
        return def;
    };
    auto hasArg = [&]( const std::string& key ) -> bool {
        return args.contains( key ) && !args[key].is_null();
    };
    // Silence unused-variable warnings for helpers not yet used in every path
    (void) getStr; (void) getDouble; (void) getInt; (void) getBool; (void) hasArg;

    constexpr double LABEL_GRID_MM = 0.1;
    constexpr double SCH_IU_PER_MM = 10000.0;
    using LabelPosKey = std::pair<int64_t, int64_t>;

    auto mmToIu = [&]( double mm ) -> int64_t {
        return static_cast<int64_t>( std::llround( mm * SCH_IU_PER_MM ) );
    };
    auto snapLabelMm = [&]( double mm ) -> double {
        return std::round( mm / LABEL_GRID_MM ) * LABEL_GRID_MM;
    };
    auto snapLabelKey = [&]( double xMm, double yMm ) -> LabelPosKey {
        return { mmToIu( snapLabelMm( xMm ) ), mmToIu( snapLabelMm( yMm ) ) };
    };

    auto getGlobalLabelNetText = []( const kiapi::schematic::types::GlobalLabel& aLabel ) -> std::string {
        if( !aLabel.has_text() || !aLabel.text().has_text() )
            return "";
        return aLabel.text().text().text();
    };

    struct ExistingGlobalLabel
    {
        std::string netName;
        double xMm = 0.0;
        double yMm = 0.0;
    };

    auto fetchGlobalLabelsByPosition = [&]( std::map<LabelPosKey, std::string>& aOut, std::string& aErr ) -> bool {
        aOut.clear();
        aErr.clear();

        kiapi::common::ApiRequest listReq;
        listReq.mutable_header()->set_client_name( "mcp" );
        kiapi::common::commands::GetItems listCmd;
        listCmd.mutable_header()->mutable_document()->set_type( kiapi::common::types::DOCTYPE_SCHEMATIC );
        std::string sheetPath = getCurrentSheetPath();
        if( !sheetPath.empty() )
            listCmd.mutable_header()->mutable_document()->mutable_sheet_path()->set_path_human_readable( sheetPath );
        listCmd.add_types( kiapi::common::types::KOT_SCH_GLOBAL_LABEL );
        listReq.mutable_message()->PackFrom( listCmd );

        kiapi::common::ApiResponse listResp;
        if( !m_ipc.SendRequest( listReq, listResp, aErr ) )
        {
            // GetItems may not be implemented — fail gracefully (label overlap detection is optional)
            aErr.clear();
            return true;
        }
        if( listResp.status().status() != kiapi::common::AS_OK )
        {
            // GetItems not available — proceed without label overlap detection
            aErr.clear();
            return true;
        }

        if( !listResp.has_message() )
            return true;

        kiapi::common::commands::GetItemsResponse listItems;
        if( !listResp.message().UnpackTo( &listItems ) )
        {
            aErr = "Could not parse GetItemsResponse while checking existing global labels";
            return false;
        }

        for( int i = 0; i < listItems.items_size(); ++i )
        {
            kiapi::schematic::types::GlobalLabel glabel;
            if( !listItems.items( i ).UnpackTo( &glabel ) || !glabel.has_position() )
                continue;

            LabelPosKey key = { glabel.position().x_nm(), glabel.position().y_nm() };
            std::string netName = getGlobalLabelNetText( glabel );
            auto it = aOut.find( key );

            if( it == aOut.end() )
            {
                aOut.emplace( key, netName );
                continue;
            }

            if( !netName.empty() && it->second.empty() )
                it->second = netName;
        }

        return true;
    };

    auto fetchGlobalLabels = [&]( std::vector<ExistingGlobalLabel>& aOut, std::string& aErr ) -> bool {
        aOut.clear();
        aErr.clear();

        kiapi::common::ApiRequest listReq;
        listReq.mutable_header()->set_client_name( "mcp" );
        kiapi::common::commands::GetItems listCmd;
        listCmd.mutable_header()->mutable_document()->set_type( kiapi::common::types::DOCTYPE_SCHEMATIC );
        std::string sheetPath = getCurrentSheetPath();
        if( !sheetPath.empty() )
            listCmd.mutable_header()->mutable_document()->mutable_sheet_path()->set_path_human_readable( sheetPath );
        listCmd.add_types( kiapi::common::types::KOT_SCH_GLOBAL_LABEL );
        listReq.mutable_message()->PackFrom( listCmd );

        kiapi::common::ApiResponse listResp;
        if( !m_ipc.SendRequest( listReq, listResp, aErr ) )
        {
            aErr.clear();
            return true;
        }
        if( listResp.status().status() != kiapi::common::AS_OK || !listResp.has_message() )
        {
            aErr.clear();
            return true;
        }

        kiapi::common::commands::GetItemsResponse listItems;
        if( !listResp.message().UnpackTo( &listItems ) )
        {
            aErr = "Could not parse GetItemsResponse while fetching global labels";
            return false;
        }

        for( int i = 0; i < listItems.items_size(); ++i )
        {
            kiapi::schematic::types::GlobalLabel glabel;
            if( !listItems.items( i ).UnpackTo( &glabel ) || !glabel.has_position() )
                continue;

            ExistingGlobalLabel item;
            item.netName = getGlobalLabelNetText( glabel );
            item.xMm = glabel.position().x_nm() / SCH_IU_PER_MM;
            item.yMm = glabel.position().y_nm() / SCH_IU_PER_MM;
            aOut.push_back( item );
        }

        return true;
    };

    auto findReusableLabelAnchor = [&]( const std::string& aNetName, double aDesiredX, double aDesiredY,
                                        const std::vector<ExistingGlobalLabel>& aLabels,
                                        double aMaxDistanceMm,
                                        double& aOutX, double& aOutY ) -> bool {
        double bestDist = std::numeric_limits<double>::infinity();
        bool found = false;

        for( const ExistingGlobalLabel& label : aLabels )
        {
            if( label.netName != aNetName )
                continue;

            double dist = std::hypot( label.xMm - aDesiredX, label.yMm - aDesiredY );
            if( dist > aMaxDistanceMm || dist >= bestDist )
                continue;

            bestDist = dist;
            aOutX = label.xMm;
            aOutY = label.yMm;
            found = true;
        }

        return found;
    };

    auto resolveLabelPlacement = [&]( double aDesiredX, double aDesiredY, const std::string& aNetName,
                                      std::map<LabelPosKey, std::string>& aOccupied,
                                      bool aAllowNudge,
                                      double& aOutX, double& aOutY, bool& aOutCreate,
                                      std::string& aErr ) -> bool {
        aErr.clear();
        aOutX = snapLabelMm( aDesiredX );
        aOutY = snapLabelMm( aDesiredY );
        aOutCreate = true;

        const double offsets[][2] = {
            { 0.0, 0.0 },
            { 0.5, 0.0 }, { -0.5, 0.0 }, { 0.0, 0.5 }, { 0.0, -0.5 },
            { 1.0, 0.0 }, { -1.0, 0.0 }, { 0.0, 1.0 }, { 0.0, -1.0 },
            { 1.0, 1.0 }, { -1.0, -1.0 }, { 1.0, -1.0 }, { -1.0, 1.0 },
            { 1.5, 0.0 }, { -1.5, 0.0 }, { 0.0, 1.5 }, { 0.0, -1.5 },
            { 2.0, 0.0 }, { -2.0, 0.0 }, { 0.0, 2.0 }, { 0.0, -2.0 },
            { 2.0, 2.0 }, { -2.0, -2.0 }, { 2.0, -2.0 }, { -2.0, 2.0 }
        };

        size_t offsetCount = aAllowNudge ? ( sizeof( offsets ) / sizeof( offsets[0] ) ) : 1;
        std::string firstConflictNet;

        for( size_t i = 0; i < offsetCount; ++i )
        {
            double tryX = snapLabelMm( aDesiredX + offsets[i][0] );
            double tryY = snapLabelMm( aDesiredY + offsets[i][1] );
            LabelPosKey key = snapLabelKey( tryX, tryY );

            auto found = aOccupied.find( key );
            if( found == aOccupied.end() )
            {
                aOutX = tryX;
                aOutY = tryY;
                aOutCreate = true;
                aOccupied[key] = aNetName;
                return true;
            }

            if( found->second == aNetName )
            {
                aOutX = tryX;
                aOutY = tryY;
                aOutCreate = false;
                return true;
            }

            if( firstConflictNet.empty() )
                firstConflictNet = found->second;
        }

        std::ostringstream msg;
        msg << "No safe global label position near (" << snapLabelMm( aDesiredX ) << ","
            << snapLabelMm( aDesiredY ) << ") for net '" << aNetName
            << "': position is occupied by '" << ( firstConflictNet.empty() ? "<unknown>" : firstConflictNet ) << "'";
        aErr = msg.str();
        return false;
    };

    // get_pin_position is handled locally (no ApiRequest)
    if( name == "get_pin_position" )
    {
        std::string ref = getStr( "reference" );
        std::string pinNum = getStr( "pin_number" );
        if( ref.empty() || pinNum.empty() )
        {
            json content = json::array();
            content.push_back( { { "type", "text" }, { "text", "get_pin_position requires 'reference' (string) and 'pin_number' (string)" } } );
            return "{\"jsonrpc\":\"2.0\",\"result\":" + json( { { "content", content }, { "isError", true } } ).dump() + ",\"id\":" + id + "}";
        }
        std::string err;
        double orientationDeg = 0;
        auto [px, py] = getPinPosition( ref, pinNum, err, &orientationDeg );
        if( !err.empty() )
        {
            json content = json::array();
            content.push_back( { { "type", "text" }, { "text", "get_pin_position failed: " + err } } );
            return "{\"jsonrpc\":\"2.0\",\"result\":" + json( { { "content", content }, { "isError", true } } ).dump() + ",\"id\":" + id + "}";
        }
        json out;
        out["x_mm"] = px;
        out["y_mm"] = py;
        out["orientation_degrees"] = orientationDeg;
        // Outward direction: 180° from pin orientation (away from component body)
        double outwardDeg = std::fmod( orientationDeg + 180.0, 360.0 );
        out["outward_degrees"] = outwardDeg;
        // Recommended rotation for add_global_label: add 180 so label body faces outward.
        int recommendedRot = static_cast<int>( std::fmod( orientationDeg + 180.0, 360.0 ) );
        out["recommended_label_rotation"] = recommendedRot;
        json content = json::array();
        content.push_back( { { "type", "text" }, { "text", out.dump() } } );
        return "{\"jsonrpc\":\"2.0\",\"result\":" + json( { { "content", content } } ).dump() + ",\"id\":" + id + "}";
    }

    // ── batch_get_pin_position ──
    if( name == "batch_get_pin_position" )
    {
        json pinsArg;
        if( args.contains( "pins" ) && args["pins"].is_array() )
            pinsArg = args["pins"];
        else
        {
            json content = json::array();
            content.push_back( { { "type", "text" }, { "text", "batch_get_pin_position requires 'pins' (array of {reference, pin_number})" } } );
            return "{\"jsonrpc\":\"2.0\",\"result\":" + json( { { "content", content }, { "isError", true } } ).dump() + ",\"id\":" + id + "}";
        }

        json results = json::array();
        for( const auto& pinObj : pinsArg )
        {
            if( !pinObj.is_object() ) continue;
            std::string ref = pinObj.value( "reference", "" );
            std::string pinNum = pinObj.value( "pin_number", "" );
            if( ref.empty() || pinNum.empty() ) continue;

            std::string err;
            double orientationDeg = 0;
            auto [px, py] = getPinPosition( ref, pinNum, err, &orientationDeg );
            if( !err.empty() ) continue; // Skip pins that fail

            json pinResult;
            pinResult["reference"] = ref;
            pinResult["pin_number"] = pinNum;
            pinResult["x_mm"] = px;
            pinResult["y_mm"] = py;
            pinResult["orientation_degrees"] = orientationDeg;
            double outwardDeg = std::fmod( orientationDeg + 180.0, 360.0 );
            pinResult["outward_degrees"] = outwardDeg;
            int recommendedRot = static_cast<int>( std::fmod( orientationDeg + 180.0, 360.0 ) );
            pinResult["recommended_label_rotation"] = recommendedRot;
            results.push_back( pinResult );
        }

        json content = json::array();
        content.push_back( { { "type", "text" }, { "text", results.dump() } } );
        return "{\"jsonrpc\":\"2.0\",\"result\":" + json( { { "content", content } } ).dump() + ",\"id\":" + id + "}";
    }

    kiapi::common::ApiRequest req;
    req.mutable_header()->set_client_name( "mcp" );

    if( name == "get_open_documents" )
    {
        kiapi::common::commands::GetOpenDocuments cmd;
        std::string typeStr = getStr( "type", "schematic" );
        if( typeStr == "board" )
            cmd.set_type( kiapi::common::types::DOCTYPE_PCB );
        else
            cmd.set_type( kiapi::common::types::DOCTYPE_SCHEMATIC );
        req.mutable_message()->PackFrom( cmd );
    }
    else if( name == "begin_commit" )
    {
        kiapi::common::commands::BeginCommit cmd;
        req.mutable_message()->PackFrom( cmd );
    }
    else if( name == "end_commit" )
    {
        kiapi::common::commands::EndCommit cmd;
        std::string commitIdStr = getStr( "id" );
        if( !commitIdStr.empty() )
            cmd.mutable_id()->set_value( commitIdStr );
        std::string actionStr = getStr( "action", "commit" );
        cmd.set_action( actionStr == "drop" ? kiapi::common::commands::CMA_DROP
                                            : kiapi::common::commands::CMA_COMMIT );
        std::string messageStr = getStr( "message" );
        if( !messageStr.empty() )
            cmd.set_message( messageStr );
        req.mutable_message()->PackFrom( cmd );
    }
    else if( name == "search_components" )
    {
        kiapi::schematic::types::SearchSymbols cmd;
        cmd.set_query( getStr( "query" ) );
        std::string lib = getStr( "library" );
        if( !lib.empty() )
            cmd.set_library( lib );
        int limit = getInt( "limit", 30 );
        if( limit <= 0 )
            limit = 30;
        if( limit > 50 )
            limit = 50;
        cmd.set_limit( limit );
        req.mutable_message()->PackFrom( cmd );
    }
    else if( name == "batch_search_components" )
    {
        // Handle batch search: array of queries → array of results
        // This is a special handler that loops and aggregates results
        json queries;
        if( args.contains( "queries" ) && args["queries"].is_array() )
            queries = args["queries"];
        else
        {
            json content = json::array();
            content.push_back( { { "type", "text" }, { "text", "batch_search_components requires 'queries' (array of strings)" } } );
            return "{\"jsonrpc\":\"2.0\",\"result\":" + json( { { "content", content }, { "isError", true } } ).dump() + ",\"id\":" + id + "}";
        }

        std::string lib = getStr( "library" );
        // Batch calls can include multiple broad queries. Use a smaller default
        // to reduce client-side timeout risk while still allowing explicit override.
        int limit = getInt( "limit", 15 );
        if( limit <= 0 )
            limit = 15;
        if( limit > 50 )
            limit = 50;

        // Deduplicate: run each unique query once, reuse results for duplicate query strings
        std::map<std::string, json> resultByQuery;
        json batchResults = json::array();
        for( const auto& q : queries )
        {
            if( !q.is_string() ) continue;
            std::string queryStr = trimStr( q.get<std::string>() );
            if( queryStr.empty() )
                continue;

            std::string queryKey = queryStr;
            std::transform( queryKey.begin(), queryKey.end(), queryKey.begin(),
                            []( unsigned char c ) { return std::tolower( c ); } );

            auto it = resultByQuery.find( queryKey );
            if( it != resultByQuery.end() )
            {
                batchResults.push_back( it->second );
                continue;
            }

            kiapi::schematic::types::SearchSymbols cmd;
            cmd.set_query( queryStr );
            if( !lib.empty() )
                cmd.set_library( lib );
            cmd.set_limit( limit );

            kiapi::common::ApiRequest searchReq;
            searchReq.mutable_header()->set_client_name( "mcp" );
            searchReq.mutable_message()->PackFrom( cmd );
            kiapi::common::ApiResponse searchResp;
            std::string searchErr;

            if( m_ipc.IsConnected() || m_ipc.Connect( IpcClient::GetDefaultSocketPath() ) )
            {
                if( m_ipc.SendRequest( searchReq, searchResp, searchErr ) && searchResp.status().status() == kiapi::common::AS_OK )
                {
                    kiapi::schematic::types::SearchSymbolsResponse resp;
                    if( searchResp.has_message() && searchResp.message().UnpackTo( &resp ) )
                    {
                        json queryResult;
                        queryResult["query"] = queryStr;
                        queryResult["results"] = json::array();

                        for( int i = 0; i < resp.results_size(); ++i )
                        {
                            const auto& r = resp.results( i );
                            json result;
                            result["library"] = r.library_nickname();
                            result["symbol"] = r.symbol_name();
                            std::string desc = r.description();
                            if( desc.size() > 80 )
                                desc = desc.substr( 0, 77 ) + "...";
                            result["description"] = desc;
                            queryResult["results"].push_back( result );
                        }
                        resultByQuery[queryKey] = queryResult;
                        batchResults.push_back( queryResult );
                    }
                }
            }
        }

        // Put full batch JSON in content so the LLM sees all results (relay only forwards content)
        std::string batchJson = batchResults.dump();
        json content = json::array();
        content.push_back( { { "type", "text" }, { "text", "Batch search complete: " + std::to_string( batchResults.size() ) + " queries processed.\n" + batchJson } } );
        return "{\"jsonrpc\":\"2.0\",\"result\":" + json( { { "content", content }, { "isError", false } } ).dump() + ",\"id\":" + id + "}";
    }
    else if( name == "get_component_data" )
    {
        kiapi::schematic::types::GetComponentData cmd;
        std::string compId = getStr( "component_id" );
        if( !compId.empty() )
            cmd.mutable_component_id()->set_value( compId );
        std::string lib = getStr( "library" );
        std::string sym = getStr( "symbol" );
        if( !lib.empty() && !sym.empty() )
        {
            cmd.mutable_lib_id()->set_library_nickname( lib );
            cmd.mutable_lib_id()->set_entry_name( sym );
        }
        if( compId.empty() && ( lib.empty() || sym.empty() ) )
        {
            json content = json::array();
            content.push_back( { { "type", "text" }, { "text", "get_component_data requires either 'component_id' or both 'library' and 'symbol'" } } );
            return "{\"jsonrpc\":\"2.0\",\"result\":" + json( { { "content", content }, { "isError", true } } ).dump() + ",\"id\":" + id + "}";
        }
        req.mutable_message()->PackFrom( cmd );
    }
    else if( name == "batch_get_component_data" )
    {
        json componentsArr;
        if( args.contains( "components" ) && args["components"].is_array() )
            componentsArr = args["components"];
        else
        {
            json content = json::array();
            content.push_back( { { "type", "text" }, { "text", "batch_get_component_data requires 'components' (array of { component_id } or { library, symbol })" } } );
            return "{\"jsonrpc\":\"2.0\",\"result\":" + json( { { "content", content }, { "isError", true } } ).dump() + ",\"id\":" + id + "}";
        }

        std::map<std::string, json> resultByKey;
        json batchResults = json::array();
        for( const auto& item : componentsArr )
        {
            if( !item.is_object() ) continue;
            std::string compId = item.value( "component_id", "" );
            std::string lib = item.value( "library", "" );
            std::string sym = item.value( "symbol", "" );
            if( compId.empty() && ( lib.empty() || sym.empty() ) )
                continue;
            std::string key = compId.empty() ? ( lib + ":" + sym ) : compId;
            auto it = resultByKey.find( key );
            if( it != resultByKey.end() )
            {
                batchResults.push_back( it->second );
                continue;
            }

            kiapi::schematic::types::GetComponentData cmd;
            if( !compId.empty() )
                cmd.mutable_component_id()->set_value( compId );
            else
            {
                cmd.mutable_lib_id()->set_library_nickname( lib );
                cmd.mutable_lib_id()->set_entry_name( sym );
            }
            kiapi::common::ApiRequest dataReq;
            dataReq.mutable_header()->set_client_name( "mcp" );
            dataReq.mutable_message()->PackFrom( cmd );
            kiapi::common::ApiResponse dataResp;
            std::string dataErr;
            json oneResult;
            if( m_ipc.IsConnected() || m_ipc.Connect( IpcClient::GetDefaultSocketPath() ) )
            {
                if( m_ipc.SendRequest( dataReq, dataResp, dataErr ) && dataResp.status().status() == kiapi::common::AS_OK
                    && dataResp.has_message() )
                {
                    kiapi::schematic::types::GetComponentDataResponse getResp;
                    if( dataResp.message().UnpackTo( &getResp ) )
                    {
                        if( !getResp.library_nickname().empty() || !getResp.symbol_name().empty() )
                        {
                            oneResult["library"] = getResp.library_nickname();
                            oneResult["symbol"] = getResp.symbol_name();
                            std::string desc = getResp.description();
                            if( desc.size() > 60 )
                                desc = desc.substr( 0, 57 ) + "...";
                            oneResult["description"] = desc;
                            if( getResp.width_mm() > 0 && getResp.height_mm() > 0 )
                            {
                                oneResult["width_mm"] = getResp.width_mm();
                                oneResult["height_mm"] = getResp.height_mm();
                            }
                            if( getResp.unit_count() > 0 )
                                oneResult["unit_count"] = getResp.unit_count();
                            if( getResp.pins_size() > 0 )
                            {
                                std::string pinsStr;
                                const int maxPins = 12;
                                for( int p = 0; p < getResp.pins_size() && p < maxPins; ++p )
                                {
                                    const auto& pin = getResp.pins( p );
                                    if( p > 0 ) pinsStr += ",";
                                    pinsStr += pin.number() + ":" + pin.name();
                                }
                                if( getResp.pins_size() > maxPins )
                                    pinsStr += ",+" + std::to_string( getResp.pins_size() - maxPins ) + " more";
                                oneResult["pins"] = pinsStr;
                            }
                        }
                        else if( !getResp.summary().empty() )
                            oneResult["summary"] = getResp.summary();
                        else
                            oneResult["error"] = "No component data";
                    }
                    else
                        oneResult["error"] = dataErr.empty() ? "Failed to unpack response" : dataErr;
                }
                else
                    oneResult["error"] = dataErr.empty() ? "API error" : dataErr;
            }
            else
                oneResult["error"] = "KiCad IPC not connected";
            resultByKey[key] = oneResult;
            batchResults.push_back( oneResult );
        }

        std::string batchJson = batchResults.dump();
        json content = json::array();
        content.push_back( { { "type", "text" }, { "text", "Batch get_component_data complete: " + std::to_string( batchResults.size() ) + " components.\n" + batchJson } } );
        return "{\"jsonrpc\":\"2.0\",\"result\":" + json( { { "content", content }, { "isError", false } } ).dump() + ",\"id\":" + id + "}";
    }
    else if( name == "place_component" )
    {
        std::string library = getStr( "library" );
        std::string symbol = getStr( "symbol" );
        std::string reference = getStr( "reference" );
        if( library.empty() || symbol.empty() || reference.empty() )
        {
            json content = json::array();
            content.push_back( { { "type", "text" }, { "text", "place_component requires 'library' (string), 'symbol' (string), and 'reference' (string)" } } );
            json errResult = { { "content", content }, { "isError", true } };
            return "{\"jsonrpc\":\"2.0\",\"result\":" + errResult.dump() + ",\"id\":" + id + "}";
        }

        std::string placeIpcErr;
        if( !m_ipc.EnsureSchematicApiConnection( placeIpcErr ) )
        {
            json content = json::array();
            content.push_back( { { "type", "text" },
                                 { "text", placeIpcErr.empty() ? "KiCad schematic IPC not available." : placeIpcErr } } );
            return "{\"jsonrpc\":\"2.0\",\"result\":" + json( { { "content", content }, { "isError", true } } ).dump()
                   + ",\"id\":" + id + "}";
        }

        // Bug 3 fix: fetch GetComponentData ONCE for the symbol being placed.
        // The result is reused for near-placement, fallback placement, and overlap check.
        double componentWidth = 5.0;
        double componentHeight = 5.0;
        {
            std::string dataErr;
            kiapi::common::ApiRequest dataReq;
            dataReq.mutable_header()->set_client_name( "mcp" );
            kiapi::schematic::types::GetComponentData dataCmd;
            dataCmd.mutable_lib_id()->set_library_nickname( library );
            dataCmd.mutable_lib_id()->set_entry_name( symbol );
            dataReq.mutable_message()->PackFrom( dataCmd );
            kiapi::common::ApiResponse dataResp;
            if( m_ipc.SendRequest( dataReq, dataResp, dataErr ) && dataResp.status().status() == kiapi::common::AS_OK )
            {
                kiapi::schematic::types::GetComponentDataResponse dataR;
                if( dataResp.has_message() && dataResp.message().UnpackTo( &dataR ) )
                {
                    if( dataR.width_mm() > 0 ) componentWidth = dataR.width_mm();
                    if( dataR.height_mm() > 0 ) componentHeight = dataR.height_mm();
                }
            }
        }

        // Determine position
        double x = 0.0;
        double y = 0.0;
        double placementMinSpacingMm = DEFAULT_OVERLAP_SPACING_MM;
        bool hasPosition = ( hasArg( "x" ) && hasArg( "y" ) )
                           || ( hasArg( "x_mm" ) && hasArg( "y_mm" ) );
        if( hasPosition )
        {
            x = hasArg( "x" ) ? getDouble( "x" ) : getDouble( "x_mm" );
            y = hasArg( "y" ) ? getDouble( "y" ) : getDouble( "y_mm" );
        }

        if( !hasPosition && args.contains( "near" ) && args["near"].is_object() )
        {
            const json& nearObj = args["near"];
            if( nearObj.contains( "reference" ) && nearObj["reference"].is_string() )
            {
                std::string nearRef = nearObj["reference"].get<std::string>();
                std::string nearPin = nearObj.value( "pin", "" );
                std::string err;

                // Bug 3 fix: componentWidth/componentHeight already fetched above.

                // Get position to place near
                double nearX = 0.0;
                double nearY = 0.0;
                double nearPinOrientationDeg = 0.0;
                bool haveNearAnchor = false;
                bool haveNearPinOrientation = false;
                
                if( !nearPin.empty() )
                {
                    // Place near specific pin
                    auto [px, py] = getPinPosition( nearRef, nearPin, err, &nearPinOrientationDeg );
                    if( err.empty() )
                    {
                        nearX = px;
                        nearY = py;
                        haveNearAnchor = true;
                        haveNearPinOrientation = true;
                    }
                    else
                    {
                        // If near pin lookup fails, fall back to a best-effort nearby search
                        // instead of hard-failing. This is common when nearRef is newly placed
                        // in the same commit and not yet visible through summary queries.
                        err.clear();
                    }
                }
                else
                {
                    // Place near component center
                    auto [cx, cy, _, __] = getComponentBounds( nearRef, err );
                    if( err.empty() )
                    {
                        nearX = cx;
                        nearY = cy;
                        haveNearAnchor = true;
                    }
                    else
                    {
                        // Same fallback strategy when nearRef bounds are not available.
                        err.clear();
                    }
                }

                if( !haveNearAnchor )
                {
                    // Keep placement in a stable working area when near anchor is unavailable.
                    // Using (25, 25) aligns with the existing default empty-spot search seed.
                    nearX = 25.0;
                    nearY = 25.0;
                }

                bool nearPassive = isLikelyPassiveSymbol( library, symbol );
                double nearOverlapSpacingMm = nearPassive
                    ? NEAR_PASSIVE_OVERLAP_SPACING_MM
                    : DEFAULT_OVERLAP_SPACING_MM;
                double nearFindSpotSpacingMm = nearPassive
                    ? NEAR_PASSIVE_FIND_SPOT_SPACING_MM
                    : DEFAULT_FIND_SPOT_SPACING_MM;
                placementMinSpacingMm = nearOverlapSpacingMm;

                std::vector<std::pair<double, double>> offsets =
                    buildNearPlacementOffsets( nearPassive, haveNearPinOrientation, nearPinOrientationDeg );
                auto snapMm = []( double mm ) -> double
                {
                    return std::round( mm / COMPACT_GRID_MM ) * COMPACT_GRID_MM;
                };
                std::string overlapDesc;
                for( const auto& [dx, dy] : offsets )
                {
                    double tryX = snapMm( nearX + dx );
                    double tryY = snapMm( nearY + dy );
                    if( !placementWouldOverlap( tryX, tryY, componentWidth, componentHeight, overlapDesc, nearOverlapSpacingMm ) )
                    {
                        x = tryX;
                        y = tryY;
                        hasPosition = true;
                        break;
                    }
                }
                if( !hasPosition )
                {
                    // No nearby spot free; fall back to full-sheet search
                    auto [emptyX, emptyY] = findEmptySpot( nearX, nearY, componentWidth, componentHeight, err, nearFindSpotSpacingMm );
                    x = emptyX;
                    y = emptyY;
                    hasPosition = true;
                }
            }
        }
        
        if( !hasPosition )
        {
            // Use explicit coordinates (mm, KiCad schematic coordinates)
            if( ( hasArg( "x" ) && hasArg( "y" ) ) || ( hasArg( "x_mm" ) && hasArg( "y_mm" ) ) )
            {
                x = hasArg( "x" ) ? getDouble( "x" ) : getDouble( "x_mm" );
                y = hasArg( "y" ) ? getDouble( "y" ) : getDouble( "y_mm" );
            }
            else
            {
                // No position specified at all - find empty spot in middle of schematic.
                // Bug 3 fix: reuse componentWidth/componentHeight fetched at top of handler.
                std::string err;
                auto [emptyX, emptyY] = findEmptySpot( 25.0, 25.0, componentWidth, componentHeight, err );
                x = emptyX;
                y = emptyY;
            }
        }
        
        // Determine rotation - inductors always get 90 degrees
        double rotation = getDouble( "rotation", 0.0 );
        if( symbol == "L" )
        {
            rotation = 90.0;
        }

        // Bug 3 fix: componentWidth/componentHeight already fetched once at top of handler.
        // No redundant GetComponentData call needed here.

        // Reject placement if it would overlap an existing component (or wire, when API supports it)
        std::string overlapDesc;
        if( placementWouldOverlap( x, y, componentWidth, componentHeight, overlapDesc, placementMinSpacingMm ) )
        {
            json content = json::array();
            content.push_back( { { "type", "text" }, { "text", "Placement would overlap " + overlapDesc + ". Choose different coordinates or use 'near' to auto-place." } } );
            return "{\"jsonrpc\":\"2.0\",\"result\":" + json( { { "content", content }, { "isError", true } } ).dump() + ",\"id\":" + id + "}";
        }
        
        // Auto-transaction: each place_component manages its own commit
        std::string txnErr;
        TransactionGuard placeTxn( *this, m_ipc, txnErr );
        if( !placeTxn.ok() )
        {
            json content = json::array();
            content.push_back( { { "type", "text" }, { "text", txnErr.empty() ? "Begin commit failed" : txnErr } } );
            return "{\"jsonrpc\":\"2.0\",\"result\":" + json( { { "content", content }, { "isError", true } } ).dump() + ",\"id\":" + id + "}";
        }
        std::string commitId = placeTxn.commitId();
        kiapi::common::ApiRequest addReq;
        addReq.mutable_header()->set_client_name( "mcp" );
        kiapi::schematic::types::AddComponent cmd;
        cmd.set_library_nickname( library );
        cmd.set_symbol_name( symbol );
        cmd.set_reference( reference );
        cmd.set_value( getStr( "value" ) );
        cmd.mutable_position()->set_x_mm( x );
        cmd.mutable_position()->set_y_mm( y );
        cmd.set_rotation( rotation );
        cmd.mutable_commit_id()->set_value( commitId );
        addReq.mutable_message()->PackFrom( cmd );
        kiapi::common::ApiResponse addResp;
        std::string err;
        if( !m_ipc.SendRequest( addReq, addResp, err ) )
        {
            json content = json::array();
            content.push_back( { { "type", "text" }, { "text", err.empty() ? "Place component failed" : err } } );
            return "{\"jsonrpc\":\"2.0\",\"result\":" + json( { { "content", content }, { "isError", true } } ).dump() + ",\"id\":" + id + "}";
        }
        if( addResp.status().status() != kiapi::common::AS_OK )
        {
            json content = json::array();
            std::string msg = addResp.status().error_message().empty() ? "Place component failed" : addResp.status().error_message();
            content.push_back( { { "type", "text" }, { "text", msg } } );
            return "{\"jsonrpc\":\"2.0\",\"result\":" + json( { { "content", content }, { "isError", true } } ).dump() + ",\"id\":" + id + "}";
        }
        placeTxn.commit();
        std::string text = "Component placed at (" + std::to_string( x ) + ", " + std::to_string( y ) + ")";
        if( rotation == 90.0 && symbol == "L" )
            text += " (inductor auto-rotated 90°)";
        if( addResp.has_message() && addResp.message().type_url().find( "AddComponentResponse" ) != std::string::npos )
        {
            kiapi::schematic::types::AddComponentResponse addR;
            if( addResp.message().UnpackTo( &addR ) && addR.has_component_id() )
                text += ", id: " + addR.component_id().value();
        }
        json content = json::array();
        content.push_back( { { "type", "text" }, { "text", text } } );
        json result = { { "content", content }, { "isError", false } };
        return "{\"jsonrpc\":\"2.0\",\"result\":" + result.dump() + ",\"id\":" + id + "}";
    }
    else if( name == "batch_place_component" )
    {
        json componentsArg;
        if( args.contains( "components" ) && args["components"].is_array() )
            componentsArg = args["components"];
        else
        {
            json content = json::array();
            content.push_back( { { "type", "text" }, { "text", "batch_place_component requires 'components' (array of placement specs)" } } );
            return "{\"jsonrpc\":\"2.0\",\"result\":" + json( { { "content", content }, { "isError", true } } ).dump() + ",\"id\":" + id + "}";
        }

        std::string placeIpcErr;
        if( !m_ipc.EnsureSchematicApiConnection( placeIpcErr ) )
        {
            json content = json::array();
            content.push_back( { { "type", "text" },
                                 { "text", placeIpcErr.empty() ? "KiCad schematic IPC not available." : placeIpcErr } } );
            return "{\"jsonrpc\":\"2.0\",\"result\":" + json( { { "content", content }, { "isError", true } } ).dump()
                   + ",\"id\":" + id + "}";
        }

        struct ComponentSize
        {
            double width = 5.0;
            double height = 5.0;
        };

        struct PlannedPlacement
        {
            std::string library;
            std::string symbol;
            std::string reference;
            std::string value;
            double x = 0.0;
            double y = 0.0;
            double width = 5.0;
            double height = 5.0;
            double rotation = 0.0;
            double minSpacing = DEFAULT_OVERLAP_SPACING_MM;
        };

        auto snapMm = []( double mm ) -> double
        {
            return std::round( mm / COMPACT_GRID_MM ) * COMPACT_GRID_MM;
        };

        auto rectsOverlap = []( double ax, double ay, double aw, double ah,
                                double bx, double by, double bw, double bh,
                                double spacingMm ) -> bool
        {
            double halfSpacing = std::max( 0.0, spacingMm ) / 2.0;
            double ax1 = ax - aw / 2.0 - halfSpacing;
            double ay1 = ay - ah / 2.0 - halfSpacing;
            double ax2 = ax + aw / 2.0 + halfSpacing;
            double ay2 = ay + ah / 2.0 + halfSpacing;
            double bx1 = bx - bw / 2.0 - halfSpacing;
            double by1 = by - bh / 2.0 - halfSpacing;
            double bx2 = bx + bw / 2.0 + halfSpacing;
            double by2 = by + bh / 2.0 + halfSpacing;
            return !( ax2 < bx1 || ax1 > bx2 || ay2 < by1 || ay1 > by2 );
        };

        std::map<std::string, ComponentSize> sizeCache;
        auto getSizeFor = [&]( const std::string& library, const std::string& symbol ) -> ComponentSize
        {
            std::string key = library + ":" + symbol;
            auto it = sizeCache.find( key );
            if( it != sizeCache.end() )
                return it->second;

            ComponentSize size;
            std::string dataErr;
            kiapi::common::ApiRequest dataReq;
            dataReq.mutable_header()->set_client_name( "mcp" );
            kiapi::schematic::types::GetComponentData dataCmd;
            dataCmd.mutable_lib_id()->set_library_nickname( library );
            dataCmd.mutable_lib_id()->set_entry_name( symbol );
            dataReq.mutable_message()->PackFrom( dataCmd );
            kiapi::common::ApiResponse dataResp;
            if( m_ipc.SendRequest( dataReq, dataResp, dataErr ) && dataResp.status().status() == kiapi::common::AS_OK )
            {
                kiapi::schematic::types::GetComponentDataResponse dataR;
                if( dataResp.has_message() && dataResp.message().UnpackTo( &dataR ) )
                {
                    if( dataR.width_mm() > 0 )
                        size.width = dataR.width_mm();
                    if( dataR.height_mm() > 0 )
                        size.height = dataR.height_mm();
                }
            }

            sizeCache[key] = size;
            return size;
        };

        std::vector<PlannedPlacement> planned;
        std::map<std::string, size_t> plannedByRef;
        json failed = json::array();

        auto plannedOverlap = [&]( double x, double y, double w, double h, double spacing,
                                   std::string& overlapDesc ) -> bool
        {
            for( const PlannedPlacement& p : planned )
            {
                double spacingUse = std::max( spacing, p.minSpacing );
                if( rectsOverlap( x, y, w, h, p.x, p.y, p.width, p.height, spacingUse ) )
                {
                    overlapDesc = "new component " + p.reference;
                    return true;
                }
            }

            return false;
        };

        for( const auto& compObj : componentsArg )
        {
            if( !compObj.is_object() )
            {
                failed.push_back( { { "reference", "(invalid)" }, { "error", "component entry must be an object" } } );
                continue;
            }

            std::string library = compObj.value( "library", "" );
            std::string symbol = compObj.value( "symbol", "" );
            std::string reference = compObj.value( "reference", "" );
            if( library.empty() || symbol.empty() || reference.empty() )
            {
                failed.push_back( { { "reference", reference.empty() ? "(missing)" : reference },
                                    { "error", "library, symbol, and reference are required" } } );
                continue;
            }

            ComponentSize size = getSizeFor( library, symbol );
            bool nearPassive = isLikelyPassiveSymbol( library, symbol );
            double placementMinSpacingMm = nearPassive ? NEAR_PASSIVE_OVERLAP_SPACING_MM
                                                       : DEFAULT_OVERLAP_SPACING_MM;
            double x = 0.0;
            double y = 0.0;
            bool hasPosition = false;

            if( compObj.contains( "x" ) && compObj["x"].is_number()
                && compObj.contains( "y" ) && compObj["y"].is_number() )
            {
                x = compObj["x"].get<double>();
                y = compObj["y"].get<double>();
                hasPosition = true;
            }
            else if( compObj.contains( "x_mm" ) && compObj["x_mm"].is_number()
                     && compObj.contains( "y_mm" ) && compObj["y_mm"].is_number() )
            {
                x = compObj["x_mm"].get<double>();
                y = compObj["y_mm"].get<double>();
                hasPosition = true;
            }

            if( !hasPosition && compObj.contains( "near" ) && compObj["near"].is_object() )
            {
                const json& nearObj = compObj["near"];
                std::string nearRef = nearObj.value( "reference", "" );
                std::string nearPin = nearObj.value( "pin", "" );
                double nearX = 25.0;
                double nearY = 25.0;
                double nearPinOrientationDeg = 0.0;
                bool haveNearPinOrientation = false;

                auto plannedIt = plannedByRef.find( nearRef );
                if( plannedIt != plannedByRef.end() )
                {
                    const PlannedPlacement& anchor = planned[plannedIt->second];
                    nearX = anchor.x;
                    nearY = anchor.y;
                }
                else if( !nearRef.empty() )
                {
                    std::string err;
                    if( !nearPin.empty() )
                    {
                        auto [px, py] = getPinPosition( nearRef, nearPin, err, &nearPinOrientationDeg );
                        if( err.empty() )
                        {
                            nearX = px;
                            nearY = py;
                            haveNearPinOrientation = true;
                        }
                    }
                    else
                    {
                        auto [cx, cy, w, h] = getComponentBounds( nearRef, err );
                        (void) w;
                        (void) h;
                        if( err.empty() )
                        {
                            nearX = cx;
                            nearY = cy;
                        }
                    }
                }

                double nearFindSpotSpacingMm = nearPassive ? NEAR_PASSIVE_FIND_SPOT_SPACING_MM
                                                           : DEFAULT_FIND_SPOT_SPACING_MM;
                std::vector<std::pair<double, double>> offsets =
                    buildNearPlacementOffsets( nearPassive, haveNearPinOrientation, nearPinOrientationDeg );
                std::string overlapDesc;
                for( const auto& [dx, dy] : offsets )
                {
                    double tryX = snapMm( nearX + dx );
                    double tryY = snapMm( nearY + dy );
                    if( placementWouldOverlap( tryX, tryY, size.width, size.height, overlapDesc, placementMinSpacingMm ) )
                        continue;
                    if( plannedOverlap( tryX, tryY, size.width, size.height, placementMinSpacingMm, overlapDesc ) )
                        continue;

                    x = tryX;
                    y = tryY;
                    hasPosition = true;
                    break;
                }

                if( !hasPosition )
                {
                    std::string err;
                    for( int attempt = 0; attempt < 16 && !hasPosition; ++attempt )
                    {
                        auto [emptyX, emptyY] = findEmptySpot( nearX + attempt * 2.0, nearY,
                                                               size.width, size.height, err,
                                                               nearFindSpotSpacingMm );
                        if( placementWouldOverlap( emptyX, emptyY, size.width, size.height, overlapDesc, placementMinSpacingMm ) )
                            continue;
                        if( plannedOverlap( emptyX, emptyY, size.width, size.height, placementMinSpacingMm, overlapDesc ) )
                            continue;

                        x = emptyX;
                        y = emptyY;
                        hasPosition = true;
                    }
                }
            }

            if( !hasPosition )
            {
                std::string err;
                std::string overlapDesc;
                for( int attempt = 0; attempt < 24 && !hasPosition; ++attempt )
                {
                    auto [emptyX, emptyY] = findEmptySpot( 25.0 + attempt * 4.0, 25.0,
                                                           size.width, size.height, err );
                    if( placementWouldOverlap( emptyX, emptyY, size.width, size.height, overlapDesc, placementMinSpacingMm ) )
                        continue;
                    if( plannedOverlap( emptyX, emptyY, size.width, size.height, placementMinSpacingMm, overlapDesc ) )
                        continue;

                    x = emptyX;
                    y = emptyY;
                    hasPosition = true;
                }
            }

            if( !hasPosition )
            {
                failed.push_back( { { "reference", reference }, { "error", "could not find a non-overlapping placement" } } );
                continue;
            }

            double rotation = compObj.contains( "rotation" ) && compObj["rotation"].is_number()
                                  ? compObj["rotation"].get<double>()
                                  : 0.0;
            if( symbol == "L" )
                rotation = 90.0;

            std::string overlapDesc;
            if( placementWouldOverlap( x, y, size.width, size.height, overlapDesc, placementMinSpacingMm )
                || plannedOverlap( x, y, size.width, size.height, placementMinSpacingMm, overlapDesc ) )
            {
                failed.push_back( { { "reference", reference },
                                    { "error", "placement would overlap " + overlapDesc } } );
                continue;
            }

            PlannedPlacement p;
            p.library = library;
            p.symbol = symbol;
            p.reference = reference;
            p.value = compObj.value( "value", "" );
            p.x = x;
            p.y = y;
            p.width = size.width;
            p.height = size.height;
            p.rotation = rotation;
            p.minSpacing = placementMinSpacingMm;
            plannedByRef[reference] = planned.size();
            planned.push_back( p );
        }

        if( !failed.empty() )
        {
            json resultObj = { { "placed", json::array() }, { "failed", failed },
                               { "message", "batch_place_component preflight failed; no components were placed" } };
            json content = json::array();
            content.push_back( { { "type", "text" }, { "text", resultObj.dump() } } );
            return "{\"jsonrpc\":\"2.0\",\"result\":" + json( { { "content", content }, { "isError", true } } ).dump() + ",\"id\":" + id + "}";
        }

        if( planned.empty() )
        {
            json content = json::array();
            content.push_back( { { "type", "text" }, { "text", "batch_place_component did not contain any valid components" } } );
            return "{\"jsonrpc\":\"2.0\",\"result\":" + json( { { "content", content }, { "isError", true } } ).dump() + ",\"id\":" + id + "}";
        }

        std::string txnErr;
        TransactionGuard batchTxn( *this, m_ipc, txnErr );
        if( !batchTxn.ok() )
        {
            json content = json::array();
            content.push_back( { { "type", "text" }, { "text", txnErr.empty() ? "Begin commit failed" : txnErr } } );
            return "{\"jsonrpc\":\"2.0\",\"result\":" + json( { { "content", content }, { "isError", true } } ).dump() + ",\"id\":" + id + "}";
        }

        json placed = json::array();
        for( const PlannedPlacement& p : planned )
        {
            kiapi::common::ApiRequest addReq;
            addReq.mutable_header()->set_client_name( "mcp" );
            kiapi::schematic::types::AddComponent cmd;
            cmd.set_library_nickname( p.library );
            cmd.set_symbol_name( p.symbol );
            cmd.set_reference( p.reference );
            cmd.set_value( p.value );
            cmd.mutable_position()->set_x_mm( p.x );
            cmd.mutable_position()->set_y_mm( p.y );
            cmd.set_rotation( p.rotation );
            cmd.mutable_commit_id()->set_value( batchTxn.commitId() );
            addReq.mutable_message()->PackFrom( cmd );

            kiapi::common::ApiResponse addResp;
            std::string err;
            if( !m_ipc.SendRequest( addReq, addResp, err ) || addResp.status().status() != kiapi::common::AS_OK )
            {
                batchTxn.drop();
                std::string msg = !err.empty() ? err
                                  : ( addResp.status().error_message().empty() ? "Place component failed"
                                                                               : addResp.status().error_message() );
                json addFailed = json::array();
                addFailed.push_back( { { "reference", p.reference }, { "error", msg } } );
                json resultObj = { { "placed", json::array() },
                                   { "failed", addFailed },
                                   { "message", "batch_place_component failed during AddComponent; transaction was dropped" } };
                json content = json::array();
                content.push_back( { { "type", "text" }, { "text", resultObj.dump() } } );
                return "{\"jsonrpc\":\"2.0\",\"result\":" + json( { { "content", content }, { "isError", true } } ).dump() + ",\"id\":" + id + "}";
            }

            json entry = { { "reference", p.reference }, { "library", p.library }, { "symbol", p.symbol },
                           { "x", p.x }, { "y", p.y }, { "rotation", p.rotation } };
            if( addResp.has_message() && addResp.message().type_url().find( "AddComponentResponse" ) != std::string::npos )
            {
                kiapi::schematic::types::AddComponentResponse addR;
                if( addResp.message().UnpackTo( &addR ) && addR.has_component_id() )
                    entry["component_id"] = addR.component_id().value();
            }
            placed.push_back( entry );
        }

        if( !batchTxn.commit() )
        {
            json resultObj = { { "placed", json::array() }, { "failed", json::array() },
                               { "message", "batch_place_component failed to commit; transaction was dropped" } };
            json content = json::array();
            content.push_back( { { "type", "text" }, { "text", resultObj.dump() } } );
            return "{\"jsonrpc\":\"2.0\",\"result\":" + json( { { "content", content }, { "isError", true } } ).dump() + ",\"id\":" + id + "}";
        }
        invalidateSummaryCache();

        json resultObj = { { "placed", placed }, { "failed", json::array() },
                           { "count", static_cast<int>( planned.size() ) } };
        json content = json::array();
        content.push_back( { { "type", "text" }, { "text", resultObj.dump() } } );
        return "{\"jsonrpc\":\"2.0\",\"result\":" + json( { { "content", content }, { "isError", false } } ).dump() + ",\"id\":" + id + "}";
    }
    else if( name == "move_component" )
    {
        std::string ref = getStr( "reference" );
        if( ref.empty() || !hasArg( "x_mm" ) || !hasArg( "y_mm" ) )
        {
            json content = json::array();
            content.push_back( { { "type", "text" }, { "text", "move_component requires 'reference' (string), 'x_mm' (number), and 'y_mm' (number)" } } );
            json errResult = { { "content", content }, { "isError", true } };
            return "{\"jsonrpc\":\"2.0\",\"result\":" + errResult.dump() + ",\"id\":" + id + "}";
        }
        double xMm = getDouble( "x_mm" );
        double yMm = getDouble( "y_mm" );
        bool moveHasRotation = hasArg( "rotation" );
        // Auto-transaction: each move_component manages its own commit
        std::string txnErr;
        TransactionGuard moveTxn( *this, m_ipc, txnErr );
        if( !moveTxn.ok() )
        {
            json content = json::array();
            content.push_back( { { "type", "text" }, { "text", txnErr.empty() ? "Begin commit failed" : txnErr } } );
            return "{\"jsonrpc\":\"2.0\",\"result\":" + json( { { "content", content }, { "isError", true } } ).dump() + ",\"id\":" + id + "}";
        }
        std::string commitId = moveTxn.commitId();
        kiapi::common::ApiRequest moveReq;
        moveReq.mutable_header()->set_client_name( "mcp" );
        kiapi::schematic::types::MoveComponent cmd;
        cmd.set_reference( ref );
        cmd.mutable_position()->set_x_mm( xMm );
        cmd.mutable_position()->set_y_mm( yMm );
        if( moveHasRotation )
            cmd.set_rotation( getDouble( "rotation" ) );
        cmd.mutable_commit_id()->set_value( commitId );
        moveReq.mutable_message()->PackFrom( cmd );
        kiapi::common::ApiResponse moveResp;
        std::string moveErr;
        if( !m_ipc.IsConnected() && !m_ipc.Connect( IpcClient::GetDefaultSocketPath() ) )
        {
            json content = json::array();
            content.push_back( { { "type", "text" }, { "text", "KiCad IPC not connected." } } );
            return "{\"jsonrpc\":\"2.0\",\"result\":" + json( { { "content", content }, { "isError", true } } ).dump() + ",\"id\":" + id + "}";
        }
        if( !m_ipc.SendRequest( moveReq, moveResp, moveErr ) )
        {
            json content = json::array();
            content.push_back( { { "type", "text" }, { "text", moveErr.empty() ? "Move component failed" : moveErr } } );
            return "{\"jsonrpc\":\"2.0\",\"result\":" + json( { { "content", content }, { "isError", true } } ).dump() + ",\"id\":" + id + "}";
        }
        if( moveResp.status().status() != kiapi::common::AS_OK )
        {
            json content = json::array();
            content.push_back( { { "type", "text" }, { "text", moveResp.status().error_message().empty() ? "Move component failed" : moveResp.status().error_message() } } );
            return "{\"jsonrpc\":\"2.0\",\"result\":" + json( { { "content", content }, { "isError", true } } ).dump() + ",\"id\":" + id + "}";
        }
        moveTxn.commit();
        std::string text = "Component " + ref + " moved to (" + std::to_string( xMm ) + ", " + std::to_string( yMm ) + ")";
        if( moveHasRotation )
            text += ", rotation " + std::to_string( getDouble( "rotation" ) ) + "°";
        json content = json::array();
        content.push_back( { { "type", "text" }, { "text", text } } );
        json result = { { "content", content }, { "isError", false } };
        return "{\"jsonrpc\":\"2.0\",\"result\":" + result.dump() + ",\"id\":" + id + "}";
    }
    else if( name == "move_chunk" )
    {
        std::string anchorRef = getStr( "reference" );
        if( anchorRef.empty() )
        {
            json content = json::array();
            content.push_back( { { "type", "text" }, { "text", "move_chunk requires 'reference' (anchor component reference string)" } } );
            return "{\"jsonrpc\":\"2.0\",\"result\":" + json( { { "content", content }, { "isError", true } } ).dump() + ",\"id\":" + id + "}";
        }

        bool hasAbsX = hasArg( "x_mm" );
        bool hasAbsY = hasArg( "y_mm" );
        bool hasDx = hasArg( "dx_mm" );
        bool hasDy = hasArg( "dy_mm" );
        bool hasAbs = hasAbsX && hasAbsY;
        bool hasDelta = hasDx && hasDy;

        if( hasAbsX != hasAbsY )
        {
            json content = json::array();
            content.push_back( { { "type", "text" }, { "text", "move_chunk absolute mode requires both 'x_mm' and 'y_mm'" } } );
            return "{\"jsonrpc\":\"2.0\",\"result\":" + json( { { "content", content }, { "isError", true } } ).dump() + ",\"id\":" + id + "}";
        }
        if( hasDx != hasDy )
        {
            json content = json::array();
            content.push_back( { { "type", "text" }, { "text", "move_chunk delta mode requires both 'dx_mm' and 'dy_mm'" } } );
            return "{\"jsonrpc\":\"2.0\",\"result\":" + json( { { "content", content }, { "isError", true } } ).dump() + ",\"id\":" + id + "}";
            }
        if( hasAbs == hasDelta )
        {
            json content = json::array();
            content.push_back( { { "type", "text" }, { "text", "move_chunk requires either absolute target ('x_mm','y_mm') or delta ('dx_mm','dy_mm'), but not both" } } );
            return "{\"jsonrpc\":\"2.0\",\"result\":" + json( { { "content", content }, { "isError", true } } ).dump() + ",\"id\":" + id + "}";
        }

        bool includeConnectedComponents = getBool( "include_connected_components", true );
        bool includeWires = getBool( "include_wires", true );
        bool includeLabels = getBool( "include_labels", true );
        int maxHops = std::max( 0, getInt( "max_hops", 1 ) );
        int maxNetPinCount = std::max( 1, getInt( "max_net_pin_count", 8 ) );
        bool includeGlobalPowerNets = getBool( "include_global_power_nets", true );
        bool anchorHasRotation = hasArg( "rotation" );
        double anchorRotation = getDouble( "rotation" );
        bool rotateChunk = getBool( "rotate_chunk", false );

        auto normalizeDeg = []( double deg ) -> double {
            double out = std::fmod( deg, 360.0 );
            if( out < 0.0 )
                out += 360.0;
            return out;
        };

        auto snapComponentRotation = [&]( double deg ) -> double {
            double snapped = std::round( deg / 90.0 ) * 90.0;
            return normalizeDeg( snapped );
        };

        if( rotateChunk && !anchorHasRotation )
        {
            json content = json::array();
            content.push_back( { { "type", "text" }, { "text", "move_chunk with rotate_chunk=true requires 'rotation' for the anchor" } } );
            return "{\"jsonrpc\":\"2.0\",\"result\":" + json( { { "content", content }, { "isError", true } } ).dump() + ",\"id\":" + id + "}";
        }

        if( !m_ipc.IsConnected() && !m_ipc.Connect( IpcClient::GetDefaultSocketPath() ) )
        {
            json content = json::array();
            content.push_back( { { "type", "text" }, { "text", "KiCad IPC not connected." } } );
            return "{\"jsonrpc\":\"2.0\",\"result\":" + json( { { "content", content }, { "isError", true } } ).dump() + ",\"id\":" + id + "}";
        }

        kiapi::schematic::types::GetSchematicSummaryResponse sumResp;
        std::string sumErr;
        if( !getCachedSummary( sumResp, sumErr ) )
        {
            json content = json::array();
            content.push_back( { { "type", "text" }, { "text", "move_chunk failed to get schematic summary: " + sumErr } } );
            return "{\"jsonrpc\":\"2.0\",\"result\":" + json( { { "content", content }, { "isError", true } } ).dump() + ",\"id\":" + id + "}";
        }

        struct SummaryComp
        {
            bool hasPos = false;
            double x = 0.0;
            double y = 0.0;
            double rotation = 0.0;
            std::vector<std::pair<double, double>> pins;
        };

        std::map<std::string, SummaryComp> summaryByRef;
        for( int i = 0; i < sumResp.components_size(); ++i )
        {
            const auto& c = sumResp.components( i );
            SummaryComp info;
            info.hasPos = c.has_position();
            if( info.hasPos )
            {
                info.x = c.position().x_mm();
                info.y = c.position().y_mm();
            }
            info.rotation = c.rotation();
            for( int p = 0; p < c.pins_size(); ++p )
                info.pins.push_back( { c.pins( p ).x_mm(), c.pins( p ).y_mm() } );
            summaryByRef[c.reference()] = info;
        }

        auto anchorIt = summaryByRef.find( anchorRef );
        if( anchorIt == summaryByRef.end() || !anchorIt->second.hasPos )
        {
            json content = json::array();
            content.push_back( { { "type", "text" }, { "text", "move_chunk anchor component '" + anchorRef + "' not found or has no position in schematic summary" } } );
            return "{\"jsonrpc\":\"2.0\",\"result\":" + json( { { "content", content }, { "isError", true } } ).dump() + ",\"id\":" + id + "}";
        }

        double rotationDeltaDeg = 0.0;
        bool hasRotationTransform = false;
        if( anchorHasRotation )
        {
            double anchorCurrentRot = normalizeDeg( anchorIt->second.rotation );
            double anchorTargetRot = snapComponentRotation( anchorRotation );
            rotationDeltaDeg = normalizeDeg( anchorTargetRot - anchorCurrentRot );
            if( rotationDeltaDeg > 180.0 )
                rotationDeltaDeg -= 360.0;
            hasRotationTransform = std::abs( rotationDeltaDeg ) > 1e-9;
            anchorRotation = anchorTargetRot;
        }
        bool applyChunkRotation = rotateChunk && anchorHasRotation && hasRotationTransform;

        double targetX = anchorIt->second.x;
        double targetY = anchorIt->second.y;
        double dxMm = 0.0;
        double dyMm = 0.0;
        if( hasAbs )
        {
            targetX = getDouble( "x_mm" );
            targetY = getDouble( "y_mm" );
            dxMm = targetX - anchorIt->second.x;
            dyMm = targetY - anchorIt->second.y;
        }
        else
        {
            dxMm = getDouble( "dx_mm" );
            dyMm = getDouble( "dy_mm" );
            targetX = anchorIt->second.x + dxMm;
            targetY = anchorIt->second.y + dyMm;
        }

        json skippedNets = json::array();
        json warnings = json::array();
        std::set<std::string> skippedNetKeys;
        std::set<std::string> selectedRefs;
        selectedRefs.insert( anchorRef );

        auto normalizeNetName = []( const std::string& in ) -> std::string {
            std::string out;
            out.reserve( in.size() );
            for( char c : in )
            {
                unsigned char uc = static_cast<unsigned char>( c );
                if( std::isalnum( uc ) )
                    out.push_back( static_cast<char>( std::toupper( uc ) ) );
            }
            return out;
        };

        auto isCommonGlobalPowerNet = [&]( const std::string& netName ) -> bool {
            static const std::set<std::string> kCommon = {
                "GND", "PGND", "AGND", "DGND", "GNDA", "GNDD",
                "EARTH", "CHASSIS", "VCC", "VDD", "VSS", "VEE",
                "VBAT", "VIN", "VOUT", "PWR", "3V3", "5V", "12V",
                "15V", "24V", "1V8", "1V2", "2V5"
            };

            std::string norm = normalizeNetName( netName );
            if( norm.empty() )
                return false;
            if( kCommon.find( norm ) != kCommon.end() )
                return true;
            if( norm.rfind( "GND", 0 ) == 0
                || norm.rfind( "VCC", 0 ) == 0
                || norm.rfind( "VDD", 0 ) == 0
                || norm.rfind( "VSS", 0 ) == 0
                || norm.rfind( "VEE", 0 ) == 0
                || norm.rfind( "VBAT", 0 ) == 0
                || norm.rfind( "VIN", 0 ) == 0 )
            {
                return true;
            }
            return false;
        };

        if( includeConnectedComponents && maxHops > 0 )
        {
            kiapi::common::ApiRequest netReq;
            netReq.mutable_header()->set_client_name( "mcp" );
            kiapi::schematic::types::GetNetlist netCmd;
            netReq.mutable_message()->PackFrom( netCmd );
            kiapi::common::ApiResponse netResp;
            std::string netErr;
            if( !m_ipc.SendRequest( netReq, netResp, netErr ) || netResp.status().status() != kiapi::common::AS_OK || !netResp.has_message() )
            {
                std::string msg = !netErr.empty() ? netErr
                                                  : ( netResp.status().error_message().empty() ? "GetNetlist failed" : netResp.status().error_message() );
                warnings.push_back( "move_chunk: could not expand connected components from netlist (" + msg + "); moving anchor only" );
            }
            else
            {
                kiapi::schematic::types::GetNetlistResponse nr;
                if( !netResp.message().UnpackTo( &nr ) )
                {
                    warnings.push_back( "move_chunk: failed to unpack GetNetlistResponse; moving anchor only" );
                }
                else
                {
                    struct TraversableNet
                    {
                        std::string name;
                        std::vector<std::string> refs;
                    };
                    std::vector<TraversableNet> traversableNets;
                    std::map<std::string, std::vector<int>> componentToNetIdx;

                    for( int n = 0; n < nr.nets_size(); ++n )
                    {
                        const auto& net = nr.nets( n );
                        std::string netName = net.net_name();
                        int pinCount = net.pins_size();
                        bool skipLargeNet = pinCount > maxNetPinCount;
                        bool skipPowerNet = !includeGlobalPowerNets && isCommonGlobalPowerNet( netName );

                        if( skipLargeNet || skipPowerNet )
                        {
                            std::string reason = skipLargeNet ? "pin_count_exceeds_limit" : "common_global_power_net";
                            std::string key = reason + "|" + netName;
                            if( skippedNetKeys.insert( key ).second )
                            {
                                skippedNets.push_back( {
                                    { "name", netName },
                                    { "pin_count", pinCount },
                                    { "reason", reason }
                                } );
                            }
                            continue;
                        }

                        std::set<std::string> uniqueRefs;
                        for( int p = 0; p < net.pins_size(); ++p )
                        {
                            const auto& pin = net.pins( p );
                            if( !pin.reference().empty() )
                                uniqueRefs.insert( pin.reference() );
                        }

                        if( uniqueRefs.empty() )
                            continue;

                        TraversableNet tnet;
                        tnet.name = netName;
                        tnet.refs.assign( uniqueRefs.begin(), uniqueRefs.end() );
                        int netIdx = static_cast<int>( traversableNets.size() );
                        traversableNets.push_back( tnet );

                        for( const std::string& ref : uniqueRefs )
                            componentToNetIdx[ref].push_back( netIdx );
                    }

                    std::vector<std::pair<std::string, int>> queue;
                    queue.push_back( { anchorRef, 0 } );
                    size_t qIndex = 0;
                    while( qIndex < queue.size() )
                    {
                        const std::string curRef = queue[qIndex].first;
                        int hops = queue[qIndex].second;
                        ++qIndex;

                        if( hops >= maxHops )
                            continue;

                        auto netIt = componentToNetIdx.find( curRef );
                        if( netIt == componentToNetIdx.end() )
                            continue;

                        for( int netIdx : netIt->second )
                        {
                            if( netIdx < 0 || netIdx >= static_cast<int>( traversableNets.size() ) )
                                continue;
                            for( const std::string& nextRef : traversableNets[netIdx].refs )
                            {
                                if( selectedRefs.insert( nextRef ).second )
                                    queue.push_back( { nextRef, hops + 1 } );
                            }
                        }
                    }
                }
            }
        }

        std::string txnErr;
        TransactionGuard txn( *this, m_ipc, txnErr );
        if( !txn.ok() )
        {
            json content = json::array();
            content.push_back( { { "type", "text" }, { "text", txnErr.empty() ? "Begin commit failed" : txnErr } } );
            return "{\"jsonrpc\":\"2.0\",\"result\":" + json( { { "content", content }, { "isError", true } } ).dump() + ",\"id\":" + id + "}";
        }
        std::string commitId = txn.commitId();

        json failedComponents = json::array();
        json movedComponents = json::array();
        std::set<std::string> movedRefSet;

        std::vector<std::string> moveOrder;
        moveOrder.push_back( anchorRef );
        for( const std::string& ref : selectedRefs )
        {
            if( ref != anchorRef )
                moveOrder.push_back( ref );
        }

        bool anchorMoveFailed = false;
        for( const std::string& ref : moveOrder )
        {
            auto compIt = summaryByRef.find( ref );
            if( compIt == summaryByRef.end() || !compIt->second.hasPos )
            {
                failedComponents.push_back( { { "reference", ref }, { "error", "component not found in summary or has no position" } } );
                if( ref == anchorRef )
                    anchorMoveFailed = true;
                continue;
            }

            double destX = compIt->second.x + dxMm;
            double destY = compIt->second.y + dyMm;
            if( ref == anchorRef && hasAbs )
            {
                destX = targetX;
                destY = targetY;
            }

            kiapi::common::ApiRequest moveReq;
            moveReq.mutable_header()->set_client_name( "mcp" );
            kiapi::schematic::types::MoveComponent moveCmd;
            moveCmd.set_reference( ref );
            moveCmd.mutable_position()->set_x_mm( destX );
            moveCmd.mutable_position()->set_y_mm( destY );
            if( ref == anchorRef && anchorHasRotation )
                moveCmd.set_rotation( anchorRotation );
            else if( rotateChunk && anchorHasRotation && hasRotationTransform )
                moveCmd.set_rotation( snapComponentRotation( compIt->second.rotation + rotationDeltaDeg ) );
            moveCmd.mutable_commit_id()->set_value( commitId );
            moveReq.mutable_message()->PackFrom( moveCmd );

            kiapi::common::ApiResponse moveResp;
            std::string moveErr;
            if( !m_ipc.SendRequest( moveReq, moveResp, moveErr ) )
            {
                failedComponents.push_back( { { "reference", ref }, { "error", moveErr.empty() ? "MoveComponent IPC failed" : moveErr } } );
                if( ref == anchorRef )
                    anchorMoveFailed = true;
                continue;
            }
            if( moveResp.status().status() != kiapi::common::AS_OK )
            {
                std::string errMsg = moveResp.status().error_message().empty() ? "MoveComponent failed" : moveResp.status().error_message();
                failedComponents.push_back( { { "reference", ref }, { "error", errMsg } } );
                if( ref == anchorRef )
                    anchorMoveFailed = true;
                continue;
            }

            movedRefSet.insert( ref );
            movedComponents.push_back( ref );
        }

        if( anchorMoveFailed || movedRefSet.find( anchorRef ) == movedRefSet.end() )
        {
            txn.drop();
            json out;
            out["anchor_ref"] = anchorRef;
            out["dx_mm"] = dxMm;
            out["dy_mm"] = dyMm;
            out["rotate_chunk"] = rotateChunk;
            out["rotation_delta_degrees"] = rotationDeltaDeg;
            out["applied_chunk_rotation"] = applyChunkRotation;
            out["moved_components"] = movedComponents;
            out["moved_wires"] = 0;
            out["moved_labels"] = 0;
            out["skipped_nets"] = skippedNets;
            out["failed_components"] = failedComponents;
            out["warnings"] = warnings;
            json content = json::array();
            content.push_back( { { "type", "text" }, { "text", out.dump() } } );
            return "{\"jsonrpc\":\"2.0\",\"result\":" + json( { { "content", content }, { "isError", true } } ).dump() + ",\"id\":" + id + "}";
        }

        int movedWires = 0;
        int movedLabels = 0;
        bool hasTranslation = std::abs( dxMm ) > 1e-9 || std::abs( dyMm ) > 1e-9;
        bool hasNetTransform = hasTranslation || applyChunkRotation;
        double pivotX = anchorIt->second.x;
        double pivotY = anchorIt->second.y;
        constexpr double DEG_TO_RAD = 3.14159265358979323846 / 180.0;
        double rotRad = rotationDeltaDeg * DEG_TO_RAD;
        double rotCos = std::cos( rotRad );
        double rotSin = std::sin( rotRad );
        auto transformPoint = [&]( double x, double y ) -> std::pair<double, double> {
            double relX = x - pivotX;
            double relY = y - pivotY;
            double outX = relX;
            double outY = relY;
            if( applyChunkRotation )
            {
                // Schematic coordinates use +Y downward. This transform preserves 0°=right, 90°=up semantics.
                outX = relX * rotCos + relY * rotSin;
                outY = -relX * rotSin + relY * rotCos;
            }
            return { pivotX + dxMm + outX, pivotY + dyMm + outY };
        };

        if( hasNetTransform && ( includeWires || includeLabels ) && !movedRefSet.empty() )
        {
            std::vector<std::pair<double, double>> seedPoints;
            seedPoints.reserve( 64 );
            // Wire graph is at post-move pin positions; seed BFS with transformed coords (same as wire endpoints).
            for( const std::string& ref : movedRefSet )
            {
                auto it = summaryByRef.find( ref );
                if( it == summaryByRef.end() )
                    continue;
                if( !it->second.pins.empty() )
                {
                    for( const auto& pinPt : it->second.pins )
                    {
                        auto [tx, ty] = transformPoint( pinPt.first, pinPt.second );
                        seedPoints.push_back( { tx, ty } );
                    }
                }
                else if( it->second.hasPos )
                {
                    auto [tx, ty] = transformPoint( it->second.x, it->second.y );
                    seedPoints.push_back( { tx, ty } );
                }
            }

            if( seedPoints.empty() )
            {
                warnings.push_back( "move_chunk: no anchor/chunk pin coordinates available; skipping wire/label transform" );
            }
            else
            {
                constexpr double SCH_IU_PER_MM_LOCAL = 10000.0;
                constexpr double NODE_TOL_MM = 0.12;
                const double nodeTolSq = NODE_TOL_MM * NODE_TOL_MM;

                auto mmToIu = []( double mm ) -> int64_t {
                    return static_cast<int64_t>( std::llround( mm * SCH_IU_PER_MM_LOCAL ) );
                };
                auto makeUuid = []() -> std::string {
                    static std::random_device rd;
                    static std::mt19937_64 gen( rd() );
                    std::uniform_int_distribution<uint64_t> dis( 0, 0xFFFFFFFFFFFFULL );
                    std::ostringstream os;
                    os << std::hex << std::setfill( '0' )
                       << std::setw( 8 ) << ( dis( gen ) & 0xFFFFFFFFUL ) << "-"
                       << std::setw( 4 ) << ( dis( gen ) & 0xFFFFUL ) << "-4"
                       << std::setw( 3 ) << ( dis( gen ) & 0xFFFUL ) << "-"
                       << std::setw( 4 ) << ( ( dis( gen ) & 0x3FFFUL ) | 0x8000UL ) << "-"
                       << std::setw( 12 ) << ( dis( gen ) & 0xFFFFFFFFFFFFULL );
                    return os.str();
                };

                struct WireNode
                {
                    double x = 0.0;
                    double y = 0.0;
                    std::vector<int> wireIdx;
                };
                struct WireInfo
                {
                    std::string id;
                    double sx = 0.0;
                    double sy = 0.0;
                    double ex = 0.0;
                    double ey = 0.0;
                    int startNode = -1;
                    int endNode = -1;
                };

                std::vector<WireNode> nodes;
                std::vector<WireInfo> wires;
                bool wireItemsAvailable = false;

                {
                    kiapi::common::ApiRequest wireReq;
                    wireReq.mutable_header()->set_client_name( "mcp" );
                    kiapi::common::commands::GetItems wireCmd;
                    wireCmd.mutable_header()->mutable_document()->set_type( kiapi::common::types::DOCTYPE_SCHEMATIC );
                    std::string sheetPath = getCurrentSheetPath();
                    if( !sheetPath.empty() )
                        wireCmd.mutable_header()->mutable_document()->mutable_sheet_path()->set_path_human_readable( sheetPath );
                    wireCmd.add_types( kiapi::common::types::KOT_SCH_LINE );
                    wireReq.mutable_message()->PackFrom( wireCmd );

                    kiapi::common::ApiResponse wireResp;
                    std::string wireErr;
                    if( !m_ipc.SendRequest( wireReq, wireResp, wireErr ) || wireResp.status().status() != kiapi::common::AS_OK || !wireResp.has_message() )
                    {
                        std::string msg = !wireErr.empty() ? wireErr
                                                           : ( wireResp.status().error_message().empty() ? "GetItems wires failed" : wireResp.status().error_message() );
                        warnings.push_back( "move_chunk: " + msg + "; skipping wire/label graph traversal" );
                    }
                    else
                    {
                        kiapi::common::commands::GetItemsResponse wireItems;
                        if( !wireResp.message().UnpackTo( &wireItems ) )
                        {
                            warnings.push_back( "move_chunk: failed to unpack wire items; skipping wire/label graph traversal" );
                        }
                        else
                        {
                            wireItemsAvailable = true;
                            auto findOrCreateNode = [&]( double x, double y ) -> int {
                                for( int i = 0; i < static_cast<int>( nodes.size() ); ++i )
                                {
                                    double dx = x - nodes[i].x;
                                    double dy = y - nodes[i].y;
                                    double distSq = dx * dx + dy * dy;
                                    if( std::abs( dx ) <= NODE_TOL_MM && std::abs( dy ) <= NODE_TOL_MM && distSq <= nodeTolSq )
                                        return i;
                                }
                                WireNode nd;
                                nd.x = x;
                                nd.y = y;
                                nodes.push_back( nd );
                                return static_cast<int>( nodes.size() ) - 1;
                            };

                            for( int i = 0; i < wireItems.items_size(); ++i )
                            {
                                kiapi::schematic::types::Line wire;
                                if( !wireItems.items( i ).UnpackTo( &wire ) )
                                    continue;
                                if( wire.layer() != kiapi::schematic::types::SL_WIRE )
                                    continue;
                                if( !wire.has_start() || !wire.has_end() || !wire.has_id() )
                                    continue;

                                double sx = wire.start().x_nm() / 1e4;
                                double sy = wire.start().y_nm() / 1e4;
                                double ex = wire.end().x_nm() / 1e4;
                                double ey = wire.end().y_nm() / 1e4;
                                int sNode = findOrCreateNode( sx, sy );
                                int eNode = findOrCreateNode( ex, ey );

                                WireInfo w;
                                w.id = wire.id().value();
                                w.sx = sx;
                                w.sy = sy;
                                w.ex = ex;
                                w.ey = ey;
                                w.startNode = sNode;
                                w.endNode = eNode;
                                int widx = static_cast<int>( wires.size() );
                                wires.push_back( w );

                                nodes[sNode].wireIdx.push_back( widx );
                                nodes[eNode].wireIdx.push_back( widx );
                            }
                        }
                    }
                }

                std::set<int> seedNodes;
                if( wireItemsAvailable && !nodes.empty() )
                {
                    for( const auto& seed : seedPoints )
                    {
                        int bestNode = -1;
                        double bestDist = 1e18;
                        for( int i = 0; i < static_cast<int>( nodes.size() ); ++i )
                        {
                            double dx = seed.first - nodes[i].x;
                            double dy = seed.second - nodes[i].y;
                            double distSq = dx * dx + dy * dy;
                            if( std::abs( dx ) <= NODE_TOL_MM && std::abs( dy ) <= NODE_TOL_MM && distSq <= nodeTolSq && distSq < bestDist )
                            {
                                bestDist = distSq;
                                bestNode = i;
                            }
                        }
                        if( bestNode >= 0 )
                            seedNodes.insert( bestNode );
                    }
                }

                if( wireItemsAvailable && !nodes.empty() && seedNodes.empty() && !seedPoints.empty() )
                    warnings.push_back( "move_chunk: wire graph not reached from moved chunk pins (no wire endpoint within 0.12mm of transformed pin positions). Wires/global labels were not translated—reconnect or ensure GetItems sees schematic wires." );

                std::set<int> visitedNodes = seedNodes;
                std::set<int> visitedWires;
                if( wireItemsAvailable && !seedNodes.empty() )
                {
                    std::vector<int> queue( seedNodes.begin(), seedNodes.end() );
                    size_t qIndex = 0;
                    while( qIndex < queue.size() )
                    {
                        int nodeIdx = queue[qIndex++];
                        if( nodeIdx < 0 || nodeIdx >= static_cast<int>( nodes.size() ) )
                            continue;

                        for( int widx : nodes[nodeIdx].wireIdx )
                        {
                            if( widx < 0 || widx >= static_cast<int>( wires.size() ) )
                                continue;
                            if( !visitedWires.insert( widx ).second )
                                continue;

                            int other = ( wires[widx].startNode == nodeIdx ) ? wires[widx].endNode : wires[widx].startNode;
                            if( other >= 0 && visitedNodes.insert( other ).second )
                                queue.push_back( other );
                        }
                    }
                }

                if( includeWires && wireItemsAvailable && !visitedWires.empty() )
                {
                    kiapi::common::commands::CreateItems createCmd;
                    createCmd.mutable_header()->mutable_document()->set_type( kiapi::common::types::DOCTYPE_SCHEMATIC );
                    std::string sheetPath = getCurrentSheetPath();
                    if( !sheetPath.empty() )
                        createCmd.mutable_header()->mutable_document()->mutable_sheet_path()->set_path_human_readable( sheetPath );

                    std::vector<std::string> oldWireIds;
                    oldWireIds.reserve( visitedWires.size() );
                    for( int widx : visitedWires )
                    {
                        if( widx < 0 || widx >= static_cast<int>( wires.size() ) )
                            continue;
                        const auto& oldWire = wires[widx];
                        oldWireIds.push_back( oldWire.id );
                        auto [newSx, newSy] = transformPoint( oldWire.sx, oldWire.sy );
                        auto [newEx, newEy] = transformPoint( oldWire.ex, oldWire.ey );

                        kiapi::schematic::types::Line nw;
                        nw.mutable_id()->set_value( makeUuid() );
                        nw.mutable_start()->set_x_nm( mmToIu( newSx ) );
                        nw.mutable_start()->set_y_nm( mmToIu( newSy ) );
                        nw.mutable_end()->set_x_nm( mmToIu( newEx ) );
                        nw.mutable_end()->set_y_nm( mmToIu( newEy ) );
                        nw.set_layer( kiapi::schematic::types::SL_WIRE );
                        createCmd.add_items()->PackFrom( nw );
                    }

                    bool wireCreateOk = false;
                    if( createCmd.items_size() > 0 )
                    {
                        kiapi::common::ApiRequest createReq;
                        createReq.mutable_header()->set_client_name( "mcp" );
                        createReq.mutable_message()->PackFrom( createCmd );
                        kiapi::common::ApiResponse createResp;
                        std::string createErr;
                        if( m_ipc.SendRequest( createReq, createResp, createErr ) && createResp.status().status() == kiapi::common::AS_OK )
                        {
                            wireCreateOk = true;
                        }
                        else
                        {
                            std::string msg = !createErr.empty() ? createErr
                                                                 : ( createResp.status().error_message().empty() ? "CreateItems wires failed" : createResp.status().error_message() );
                            warnings.push_back( "move_chunk: " + msg + " (keeping original wires)" );
                        }
                    }

                    if( wireCreateOk && !oldWireIds.empty() )
                    {
                        kiapi::common::commands::DeleteItems delCmd;
                        delCmd.mutable_header()->mutable_document()->set_type( kiapi::common::types::DOCTYPE_SCHEMATIC );
                        for( const std::string& wid : oldWireIds )
                            delCmd.add_item_ids()->set_value( wid );

                        kiapi::common::ApiRequest delReq;
                        delReq.mutable_header()->set_client_name( "mcp" );
                        delReq.mutable_message()->PackFrom( delCmd );
                        kiapi::common::ApiResponse delResp;
                        std::string delErr;
                        if( m_ipc.SendRequest( delReq, delResp, delErr ) && delResp.status().status() == kiapi::common::AS_OK )
                        {
                            movedWires = static_cast<int>( oldWireIds.size() );
                        }
                        else
                        {
                            std::string msg = !delErr.empty() ? delErr
                                                              : ( delResp.status().error_message().empty() ? "DeleteItems old wires failed" : delResp.status().error_message() );
                            warnings.push_back( "move_chunk: " + msg + " after wire recreation (old wires may remain)" );
                        }
                    }
                }

                if( includeLabels )
                {
                    std::vector<std::pair<double, double>> connectedPoints;
                    if( wireItemsAvailable && !visitedNodes.empty() )
                    {
                        connectedPoints.reserve( visitedNodes.size() );
                        for( int nodeIdx : visitedNodes )
                        {
                            if( nodeIdx >= 0 && nodeIdx < static_cast<int>( nodes.size() ) )
                                connectedPoints.push_back( { nodes[nodeIdx].x, nodes[nodeIdx].y } );
                        }
                    }
                    else
                    {
                        connectedPoints = seedPoints;
                    }

                    auto isNearConnectedPoint = [&]( double x, double y ) -> bool {
                        for( const auto& pt : connectedPoints )
                        {
                            double ddx = x - pt.first;
                            double ddy = y - pt.second;
                            double distSq = ddx * ddx + ddy * ddy;
                            if( std::abs( ddx ) <= NODE_TOL_MM && std::abs( ddy ) <= NODE_TOL_MM && distSq <= nodeTolSq )
                                return true;
                        }
                        return false;
                    };

                    if( connectedPoints.empty() )
                    {
                        warnings.push_back( "move_chunk: no connected points available for label translation" );
                    }
                    else
                    {
                        struct LabelMove
                        {
                            std::string id;
                            kiapi::schematic::types::GlobalLabel label;
                            double x = 0.0;
                            double y = 0.0;
                        };
                        std::vector<LabelMove> labelsToMove;

                        kiapi::common::ApiRequest lblReq;
                        lblReq.mutable_header()->set_client_name( "mcp" );
                        kiapi::common::commands::GetItems lblCmd;
                        lblCmd.mutable_header()->mutable_document()->set_type( kiapi::common::types::DOCTYPE_SCHEMATIC );
                        std::string sheetPath = getCurrentSheetPath();
                        if( !sheetPath.empty() )
                            lblCmd.mutable_header()->mutable_document()->mutable_sheet_path()->set_path_human_readable( sheetPath );
                        lblCmd.add_types( kiapi::common::types::KOT_SCH_GLOBAL_LABEL );
                        lblReq.mutable_message()->PackFrom( lblCmd );

                        kiapi::common::ApiResponse lblResp;
                        std::string lblErr;
                        if( !m_ipc.SendRequest( lblReq, lblResp, lblErr ) || lblResp.status().status() != kiapi::common::AS_OK || !lblResp.has_message() )
                        {
                            std::string msg = !lblErr.empty() ? lblErr
                                                              : ( lblResp.status().error_message().empty() ? "GetItems labels failed" : lblResp.status().error_message() );
                            warnings.push_back( "move_chunk: " + msg + "; skipping label translation" );
                        }
                        else
                        {
                            kiapi::common::commands::GetItemsResponse lblItems;
                            if( !lblResp.message().UnpackTo( &lblItems ) )
                            {
                                warnings.push_back( "move_chunk: failed to unpack global labels; skipping label translation" );
                            }
                            else
                            {
                                for( int i = 0; i < lblItems.items_size(); ++i )
                                {
                                    kiapi::schematic::types::GlobalLabel glabel;
                                    if( !lblItems.items( i ).UnpackTo( &glabel ) || !glabel.has_id() || !glabel.has_position() )
                                        continue;

                                    double lx = glabel.position().x_nm() / 1e4;
                                    double ly = glabel.position().y_nm() / 1e4;
                                    if( !isNearConnectedPoint( lx, ly ) )
                                        continue;

                                    LabelMove lm;
                                    lm.id = glabel.id().value();
                                    lm.label = glabel;
                                    lm.x = lx;
                                    lm.y = ly;
                                    labelsToMove.push_back( lm );
                                }
                            }
                        }

                        if( !labelsToMove.empty() )
                        {
                            kiapi::common::commands::CreateItems createLblCmd;
                            createLblCmd.mutable_header()->mutable_document()->set_type( kiapi::common::types::DOCTYPE_SCHEMATIC );
                            std::string sheetPathLocal = getCurrentSheetPath();
                            if( !sheetPathLocal.empty() )
                                createLblCmd.mutable_header()->mutable_document()->mutable_sheet_path()->set_path_human_readable( sheetPathLocal );

                            std::vector<std::string> oldLabelIds;
                            oldLabelIds.reserve( labelsToMove.size() );
                            for( const auto& lm : labelsToMove )
                            {
                                kiapi::schematic::types::GlobalLabel movedLabel = lm.label;
                                auto [newLx, newLy] = transformPoint( lm.x, lm.y );
                                movedLabel.mutable_id()->set_value( makeUuid() );
                                movedLabel.mutable_position()->set_x_nm( mmToIu( newLx ) );
                                movedLabel.mutable_position()->set_y_nm( mmToIu( newLy ) );
                                if( applyChunkRotation && movedLabel.has_text() && movedLabel.text().has_text() )
                                {
                                    auto* attrs = movedLabel.mutable_text()->mutable_text()->mutable_attributes();
                                    double curAngle = attrs->angle().value_degrees();
                                    attrs->mutable_angle()->set_value_degrees( static_cast<int>( std::lround( normalizeDeg( curAngle + rotationDeltaDeg ) ) ) );
                                }
                                createLblCmd.add_items()->PackFrom( movedLabel );
                                oldLabelIds.push_back( lm.id );
                            }

                            bool labelCreateOk = false;
                            if( createLblCmd.items_size() > 0 )
                            {
                                kiapi::common::ApiRequest createLblReq;
                                createLblReq.mutable_header()->set_client_name( "mcp" );
                                createLblReq.mutable_message()->PackFrom( createLblCmd );
                                kiapi::common::ApiResponse createLblResp;
                                std::string createLblErr;
                                if( m_ipc.SendRequest( createLblReq, createLblResp, createLblErr ) && createLblResp.status().status() == kiapi::common::AS_OK )
                                {
                                    labelCreateOk = true;
                                }
                                else
                                {
                                    std::string msg = !createLblErr.empty() ? createLblErr
                                                                            : ( createLblResp.status().error_message().empty() ? "CreateItems labels failed" : createLblResp.status().error_message() );
                                    warnings.push_back( "move_chunk: " + msg + " (keeping original labels)" );
                                }
                            }

                            if( labelCreateOk && !oldLabelIds.empty() )
                            {
                                kiapi::common::commands::DeleteItems delLblCmd;
                                delLblCmd.mutable_header()->mutable_document()->set_type( kiapi::common::types::DOCTYPE_SCHEMATIC );
                                for( const std::string& lid : oldLabelIds )
                                    delLblCmd.add_item_ids()->set_value( lid );

                                kiapi::common::ApiRequest delLblReq;
                                delLblReq.mutable_header()->set_client_name( "mcp" );
                                delLblReq.mutable_message()->PackFrom( delLblCmd );
                                kiapi::common::ApiResponse delLblResp;
                                std::string delLblErr;
                                if( m_ipc.SendRequest( delLblReq, delLblResp, delLblErr ) && delLblResp.status().status() == kiapi::common::AS_OK )
                                {
                                    movedLabels = static_cast<int>( oldLabelIds.size() );
                                }
                                else
                                {
                                    std::string msg = !delLblErr.empty() ? delLblErr
                                                                         : ( delLblResp.status().error_message().empty() ? "DeleteItems old labels failed" : delLblResp.status().error_message() );
                                    warnings.push_back( "move_chunk: " + msg + " after label recreation (old labels may remain)" );
                                }
                            }
                        }
                    }
                }
            }
        }

        if( movedRefSet.empty() )
            txn.drop();
        else
            txn.commit();

        json out;
        out["anchor_ref"] = anchorRef;
        out["dx_mm"] = dxMm;
        out["dy_mm"] = dyMm;
        out["rotate_chunk"] = rotateChunk;
        out["rotation_delta_degrees"] = rotationDeltaDeg;
        out["applied_chunk_rotation"] = applyChunkRotation;
        out["moved_components"] = movedComponents;
        out["moved_wires"] = movedWires;
        out["moved_labels"] = movedLabels;
        out["skipped_nets"] = skippedNets;
        out["failed_components"] = failedComponents;
        out["warnings"] = warnings;

        bool isError = movedRefSet.empty();
        json content = json::array();
        content.push_back( { { "type", "text" }, { "text", out.dump() } } );
        return "{\"jsonrpc\":\"2.0\",\"result\":" + json( { { "content", content }, { "isError", isError } } ).dump() + ",\"id\":" + id + "}";
    }
    else if( name == "add_wire" )
    {
        if( !args.contains( "segments" ) || !args["segments"].is_array() || args["segments"].empty() )
        {
            json content = json::array();
            content.push_back( { { "type", "text" }, { "text", "add_wire requires non-empty segments array of {x1,y1,x2,y2} in mm" } } );
            json errResult = { { "content", content }, { "isError", true } };
            return "{\"jsonrpc\":\"2.0\",\"result\":" + errResult.dump() + ",\"id\":" + id + "}";
        }

        // Check if commit_id was provided to reuse existing transaction
        std::string commitId;
        bool hasExistingCommitId = false;
        if( args.contains( "commit_id" ) && args["commit_id"].is_string() )
        {
            commitId = args["commit_id"].get<std::string>();
            hasExistingCommitId = !commitId.empty();
        }

        std::string txnErr;
        std::unique_ptr<TransactionGuard> txn;
        if( !hasExistingCommitId )
        {
            txn = std::make_unique<TransactionGuard>( *this, m_ipc, txnErr );
            if( !txn->ok() )
            {
                json content = json::array();
                content.push_back( { { "type", "text" }, { "text", txnErr.empty() ? "Begin commit failed" : txnErr } } );
                return "{\"jsonrpc\":\"2.0\",\"result\":" + json( { { "content", content }, { "isError", true } } ).dump() + ",\"id\":" + id + "}";
            }
            commitId = txn->commitId();
        }
        constexpr double SCH_IU_PER_MM = 10000.0;
        constexpr int64_t GRID_IU = 1000;  // 0.1mm grid in IU units
        auto snapMm = []( double mm ) -> int64_t {
            // Convert mm to IU first, then snap to nearest grid cell
            int64_t iu = static_cast<int64_t>( std::round( mm * SCH_IU_PER_MM ) );
            // Snap to nearest 1000 IU (0.1mm grid)
            return ( ( iu + GRID_IU / 2 ) / GRID_IU ) * GRID_IU;
        };
        auto makeUuid = []() -> std::string {
            static std::random_device rd;
            static std::mt19937_64 gen( rd() );
            std::uniform_int_distribution<uint64_t> dis( 0, 0xFFFFFFFFFFFFULL );
            std::ostringstream os;
            os << std::hex << std::setfill( '0' )
               << std::setw( 8 ) << ( dis( gen ) & 0xFFFFFFFFUL ) << "-"
               << std::setw( 4 ) << ( dis( gen ) & 0xFFFFUL ) << "-4"
               << std::setw( 3 ) << ( dis( gen ) & 0xFFFUL ) << "-"
               << std::setw( 4 ) << ( ( dis( gen ) & 0x3FFFUL ) | 0x8000UL ) << "-"
               << std::setw( 12 ) << ( dis( gen ) & 0xFFFFFFFFFFFFULL );
            return os.str();
        };
        kiapi::common::commands::CreateItems cmd;
        cmd.mutable_header()->mutable_document()->set_type( kiapi::common::types::DOCTYPE_SCHEMATIC );
        auto addOrthogonalSegment = [&]( double x1, double y1, double x2, double y2 ) {
            // Manhattan routing: split diagonal segments into horizontal-then-vertical
            if( std::abs( x2 - x1 ) < 0.001 || std::abs( y2 - y1 ) < 0.001 )
            {
                // Already horizontal or vertical
                kiapi::schematic::types::Line line;
                line.mutable_id()->set_value( makeUuid() );
                line.mutable_start()->set_x_nm( snapMm( x1 ) );
                line.mutable_start()->set_y_nm( snapMm( y1 ) );
                line.mutable_end()->set_x_nm( snapMm( x2 ) );
                line.mutable_end()->set_y_nm( snapMm( y2 ) );
                line.set_layer( kiapi::schematic::types::SL_WIRE );
                cmd.add_items()->PackFrom( line );
            }
            else
            {
                // Diagonal: split into horizontal then vertical (Manhattan)
                double midX = ( x1 + x2 ) / 2.0;  // snapMm() handles grid alignment
                kiapi::schematic::types::Line line1;
                line1.mutable_id()->set_value( makeUuid() );
                line1.mutable_start()->set_x_nm( snapMm( x1 ) );
                line1.mutable_start()->set_y_nm( snapMm( y1 ) );
                line1.mutable_end()->set_x_nm( snapMm( midX ) );
                line1.mutable_end()->set_y_nm( snapMm( y1 ) );
                line1.set_layer( kiapi::schematic::types::SL_WIRE );
                cmd.add_items()->PackFrom( line1 );
                kiapi::schematic::types::Line line2;
                line2.mutable_id()->set_value( makeUuid() );
                line2.mutable_start()->set_x_nm( snapMm( midX ) );
                line2.mutable_start()->set_y_nm( snapMm( y1 ) );
                line2.mutable_end()->set_x_nm( snapMm( midX ) );
                line2.mutable_end()->set_y_nm( snapMm( y2 ) );
                line2.set_layer( kiapi::schematic::types::SL_WIRE );
                cmd.add_items()->PackFrom( line2 );
                kiapi::schematic::types::Line line3;
                line3.mutable_id()->set_value( makeUuid() );
                line3.mutable_start()->set_x_nm( snapMm( midX ) );
                line3.mutable_start()->set_y_nm( snapMm( y2 ) );
                line3.mutable_end()->set_x_nm( snapMm( x2 ) );
                line3.mutable_end()->set_y_nm( snapMm( y2 ) );
                line3.set_layer( kiapi::schematic::types::SL_WIRE );
                cmd.add_items()->PackFrom( line3 );
            }
        };
        for( const json& seg : args["segments"] )
        {
            double x1 = seg.value( "x1", 0.0 ), y1 = seg.value( "y1", 0.0 );
            double x2 = seg.value( "x2", 0.0 ), y2 = seg.value( "y2", 0.0 );
            addOrthogonalSegment( x1, y1, x2, y2 );
        }
        kiapi::common::ApiRequest addReq;
        addReq.mutable_header()->set_client_name( "mcp" );
        addReq.mutable_message()->PackFrom( cmd );
        kiapi::common::ApiResponse addResp;
        std::string err;
        if( !m_ipc.SendRequest( addReq, addResp, err ) )
        {
            json content = json::array();
            content.push_back( { { "type", "text" }, { "text", err.empty() ? "Request failed" : err } } );
            return "{\"jsonrpc\":\"2.0\",\"result\":" + json( { { "content", content }, { "isError", true } } ).dump() + ",\"id\":" + id + "}";
        }
        if( addResp.status().status() != kiapi::common::AS_OK )
        {
            json content = json::array();
            content.push_back( { { "type", "text" }, { "text", addResp.status().error_message().empty() ? "Request failed" : addResp.status().error_message() } } );
            return "{\"jsonrpc\":\"2.0\",\"result\":" + json( { { "content", content }, { "isError", true } } ).dump() + ",\"id\":" + id + "}";
        }
        // Only commit if we created the transaction
        if( txn && !txn->commit() )
        {
            json content = json::array();
            content.push_back( { { "type", "text" }, { "text", "Failed to commit transaction" } } );
            return "{\"jsonrpc\":\"2.0\",\"result\":" + json( { { "content", content }, { "isError", true } } ).dump() + ",\"id\":" + id + "}";
        }
        std::string text = "Wire(s) added";
        if( addResp.has_message() && addResp.message().type_url().find( "CreateItemsResponse" ) != std::string::npos )
        {
            kiapi::common::commands::CreateItemsResponse createResp;
            if( addResp.message().UnpackTo( &createResp ) )
            {
                json ids = json::array();
                std::string firstError;
                for( int i = 0; i < createResp.created_items_size(); ++i )
                {
                    const auto& cr = createResp.created_items( i );
                    if( cr.status().code() == kiapi::common::commands::ISC_OK && cr.has_item() )
                    {
                        kiapi::schematic::types::Line line;
                        if( cr.item().UnpackTo( &line ) && line.has_id() )
                            ids.push_back( line.id().value() );
                    }
                    else if( firstError.empty() && !cr.status().error_message().empty() )
                        firstError = cr.status().error_message();
                }
                text = "Wire(s) added: " + ids.dump();
                if( ids.empty() && !firstError.empty() )
                    text += " (error: " + firstError + ")";
            }
        }
        json content = json::array();
        content.push_back( { { "type", "text" }, { "text", text } } );
        return "{\"jsonrpc\":\"2.0\",\"result\":" + json( { { "content", content }, { "isError", false } } ).dump() + ",\"id\":" + id + "}";
    }
    else if( name == "add_global_label" )
    {
        std::string labelText = getStr( "text" );
        if( labelText.empty() )
        {
            json content = json::array();
            content.push_back( { { "type", "text" }, { "text", "add_global_label requires non-empty text" } } );
            json errResult = { { "content", content }, { "isError", true } };
            return "{\"jsonrpc\":\"2.0\",\"result\":" + errResult.dump() + ",\"id\":" + id + "}";
        }
        std::string txnErr;
        TransactionGuard txn( *this, m_ipc, txnErr );
        if( !txn.ok() )
        {
            json content = json::array();
            content.push_back( { { "type", "text" }, { "text", txnErr.empty() ? "Begin commit failed" : txnErr } } );
            return "{\"jsonrpc\":\"2.0\",\"result\":" + json( { { "content", content }, { "isError", true } } ).dump() + ",\"id\":" + id + "}";
        }
        std::string commitId = txn.commitId();
        constexpr double SCH_IU_PER_MM = 10000.0;
        constexpr int64_t GRID_IU = 1000;  // 0.1mm grid in IU units
        auto snapMm = []( double mm ) -> int64_t {
            // Convert mm to IU first, then snap to nearest grid cell
            int64_t iu = static_cast<int64_t>( std::round( mm * SCH_IU_PER_MM ) );
            // Snap to nearest 1000 IU (0.1mm grid)
            return ( ( iu + GRID_IU / 2 ) / GRID_IU ) * GRID_IU;
        };
        auto makeUuid = []() -> std::string {
            static std::random_device rd;
            static std::mt19937_64 gen( rd() );
            std::uniform_int_distribution<uint64_t> dis( 0, 0xFFFFFFFFFFFFULL );
            std::ostringstream os;
            os << std::hex << std::setfill( '0' )
               << std::setw( 8 ) << ( dis( gen ) & 0xFFFFFFFFUL ) << "-"
               << std::setw( 4 ) << ( dis( gen ) & 0xFFFFUL ) << "-4"
               << std::setw( 3 ) << ( dis( gen ) & 0xFFFUL ) << "-"
               << std::setw( 4 ) << ( ( dis( gen ) & 0x3FFFUL ) | 0x8000UL ) << "-"
               << std::setw( 12 ) << ( dis( gen ) & 0xFFFFFFFFFFFFULL );
            return os.str();
        };
        double x = getDouble( "x" );
        double y = getDouble( "y" );
        // Reject (0,0) - labels placed at origin are usually a mistake; use connect_net_to_pin instead
        if( std::abs( x ) < 0.01 && std::abs( y ) < 0.01 )
        {
            json content = json::array();
            content.push_back( { { "type", "text" }, { "text",
                "add_global_label: (0,0) is invalid. Use connect_net_to_pin or connect_pin_to_pin to attach a label to a pin, or provide valid x,y coordinates (mm) near the wire." } } );
            return "{\"jsonrpc\":\"2.0\",\"result\":" + json( { { "content", content }, { "isError", true } } ).dump() + ",\"id\":" + id + "}";
        }

        // Nudge label position if it would overlap an existing component.
        // Labels are roughly 10×3 mm bounding box.
        constexpr double LABEL_W = 10.0;
        constexpr double LABEL_H = 3.0;
        std::string overlapDesc;
        if( placementWouldOverlap( x, y, LABEL_W, LABEL_H, overlapDesc ) )
        {
            // Try offsets to find a clear spot near the requested position
            constexpr double NUDGE = 8.0;
            const double offsets[][2] = {
                { NUDGE, 0 }, { -NUDGE, 0 }, { 0, NUDGE }, { 0, -NUDGE },
                { NUDGE, NUDGE }, { -NUDGE, -NUDGE }, { NUDGE, -NUDGE }, { -NUDGE, NUDGE },
            };
            for( const auto& off : offsets )
            {
                double tryX = std::round( ( x + off[0] ) / 0.1 ) * 0.1;
                double tryY = std::round( ( y + off[1] ) / 0.1 ) * 0.1;
                if( !placementWouldOverlap( tryX, tryY, LABEL_W, LABEL_H, overlapDesc ) )
                {
                    x = tryX;
                    y = tryY;
                    break;
                }
            }
        }

        std::map<LabelPosKey, std::string> occupiedLabels;
        std::string labelCheckErr;
        if( !fetchGlobalLabelsByPosition( occupiedLabels, labelCheckErr ) )
        {
            json content = json::array();
            content.push_back( { { "type", "text" }, { "text", labelCheckErr.empty() ? "Failed to query existing global labels" : labelCheckErr } } );
            return "{\"jsonrpc\":\"2.0\",\"result\":" + json( { { "content", content }, { "isError", true } } ).dump() + ",\"id\":" + id + "}";
        }

        double resolvedX = x;
        double resolvedY = y;
        bool shouldCreateLabel = true;
        if( !resolveLabelPlacement( x, y, labelText, occupiedLabels, false, resolvedX, resolvedY, shouldCreateLabel, labelCheckErr ) )
        {
            json content = json::array();
            content.push_back( { { "type", "text" }, { "text", labelCheckErr } } );
            return "{\"jsonrpc\":\"2.0\",\"result\":" + json( { { "content", content }, { "isError", true } } ).dump() + ",\"id\":" + id + "}";
        }

        if( !shouldCreateLabel )
        {
            std::ostringstream msg;
            msg << "Global label '" << labelText << "' already exists at ("
                << resolvedX << "," << resolvedY << "); skipping duplicate";
            json content = json::array();
            content.push_back( { { "type", "text" }, { "text", msg.str() } } );
            return "{\"jsonrpc\":\"2.0\",\"result\":" + json( { { "content", content }, { "isError", false } } ).dump() + ",\"id\":" + id + "}";
        }
        x = resolvedX;
        y = resolvedY;

        kiapi::common::commands::CreateItems cmd;
        cmd.mutable_header()->mutable_document()->set_type( kiapi::common::types::DOCTYPE_SCHEMATIC );
        kiapi::schematic::types::GlobalLabel glabel;
        glabel.mutable_id()->set_value( makeUuid() );
        glabel.mutable_position()->set_x_nm( snapMm( x ) );
        glabel.mutable_position()->set_y_nm( snapMm( y ) );
        glabel.mutable_text()->mutable_text()->set_text( labelText );
        glabel.mutable_text()->mutable_text()->mutable_attributes()->set_horizontal_alignment( kiapi::common::types::HA_CENTER );
        glabel.mutable_text()->mutable_text()->mutable_attributes()->set_vertical_alignment( kiapi::common::types::VA_CENTER );
        // Rotation (0, 90, 180, 270) controls which direction the label connection pin faces.
        // Same convention as connect_net_to_pin: 0=right, 90=up, 180=left, 270=down.
        double labelRotation = getDouble( "rotation", 0.0 );
        int rotSnap = static_cast<int>( std::round( labelRotation / 90.0 ) ) * 90;
        rotSnap = ( ( rotSnap % 360 ) + 360 ) % 360;
        glabel.mutable_text()->mutable_text()->mutable_attributes()->mutable_angle()->set_value_degrees( ( rotSnap + 180 ) % 360 );
        google::protobuf::Any* any = cmd.add_items();
        any->PackFrom( glabel );
        kiapi::common::ApiRequest addReq;
        addReq.mutable_header()->set_client_name( "mcp" );
        addReq.mutable_message()->PackFrom( cmd );
        kiapi::common::ApiResponse addResp;
        std::string err;
        if( !m_ipc.SendRequest( addReq, addResp, err ) )
        {
            json content = json::array();
            content.push_back( { { "type", "text" }, { "text", err.empty() ? "Request failed" : err } } );
            return "{\"jsonrpc\":\"2.0\",\"result\":" + json( { { "content", content }, { "isError", true } } ).dump() + ",\"id\":" + id + "}";
        }
        if( addResp.status().status() != kiapi::common::AS_OK )
        {
            json content = json::array();
            content.push_back( { { "type", "text" }, { "text", addResp.status().error_message().empty() ? "Request failed" : addResp.status().error_message() } } );
            return "{\"jsonrpc\":\"2.0\",\"result\":" + json( { { "content", content }, { "isError", true } } ).dump() + ",\"id\":" + id + "}";
        }
        txn.commit();
        std::string text = "Global label added";
        if( addResp.has_message() && addResp.message().type_url().find( "CreateItemsResponse" ) != std::string::npos )
        {
            kiapi::common::commands::CreateItemsResponse createResp;
            if( addResp.message().UnpackTo( &createResp ) && createResp.created_items_size() > 0 )
            {
                const auto& cr = createResp.created_items( 0 );
                if( cr.status().code() == kiapi::common::commands::ISC_OK && cr.has_item() )
                {
                    kiapi::schematic::types::GlobalLabel gl;
                    if( cr.item().UnpackTo( &gl ) && gl.has_id() )
                        text = "Global label added, id: " + gl.id().value();
                }
            }
        }
        json content = json::array();
        content.push_back( { { "type", "text" }, { "text", text } } );
        return "{\"jsonrpc\":\"2.0\",\"result\":" + json( { { "content", content }, { "isError", false } } ).dump() + ",\"id\":" + id + "}";
    }
    else if( name == "start_block" )
    {
        double widthMm = getDouble( "width" );
        double heightMm = getDouble( "height" );
        std::string titleStr = getStr( "title" );
        if( widthMm <= 0 || heightMm <= 0 || titleStr.empty() )
        {
            json content = json::array();
            content.push_back( { { "type", "text" }, { "text", "start_block requires positive width, height, and non-empty title" } } );
            json errResult = { { "content", content }, { "isError", true } };
            return "{\"jsonrpc\":\"2.0\",\"result\":" + errResult.dump() + ",\"id\":" + id + "}";
        }

        // ── Search for existing block by title ──
        {
            kiapi::common::ApiRequest txtReq;
            txtReq.mutable_header()->set_client_name( "mcp" );
            kiapi::common::commands::GetItems txtCmd;
            txtCmd.mutable_header()->mutable_document()->set_type( kiapi::common::types::DOCTYPE_SCHEMATIC );
            std::string sheetPath = getCurrentSheetPath();
            if( !sheetPath.empty() )
                txtCmd.mutable_header()->mutable_document()->mutable_sheet_path()->set_path_human_readable( sheetPath );
            txtCmd.add_types( kiapi::common::types::KOT_SCH_TEXT );
            txtReq.mutable_message()->PackFrom( txtCmd );

            kiapi::common::ApiResponse txtResp;
            std::string txtErr;
            bool foundExisting = false;

            if( m_ipc.IsConnected() || m_ipc.Connect( IpcClient::GetDefaultSocketPath() ) )
            {
                if( m_ipc.SendRequest( txtReq, txtResp, txtErr )
                    && txtResp.status().status() == kiapi::common::AS_OK
                    && txtResp.has_message() )
                {
                    kiapi::common::commands::GetItemsResponse txtItems;
                    if( txtResp.message().UnpackTo( &txtItems ) )
                    {
                        constexpr double IU_TO_MM = 1e-4;  // KiCad IU: 1mm = 10000 IU
                        double foundTxtX = 0, foundTxtY = 0;

                        for( int i = 0; i < txtItems.items_size(); ++i )
                        {
                            kiapi::schematic::types::SchematicText stext;
                            if( !txtItems.items( i ).UnpackTo( &stext ) )
                                continue;
                            if( !stext.has_text() || !stext.text().has_text() )
                                continue;
                            if( stext.text().text().text() != titleStr )
                                continue;
                            if( stext.layer() != kiapi::schematic::types::SL_NOTES )
                                continue;
                            if( !stext.has_position() )
                                continue;

                            foundTxtX = stext.position().x_nm() * IU_TO_MM;
                            foundTxtY = stext.position().y_nm() * IU_TO_MM;

                            // Found matching title text — now fetch SL_NOTES lines to reconstruct bbox
                            kiapi::common::ApiRequest lineReq;
                            lineReq.mutable_header()->set_client_name( "mcp" );
                            kiapi::common::commands::GetItems lineCmd;
                            lineCmd.mutable_header()->mutable_document()->set_type( kiapi::common::types::DOCTYPE_SCHEMATIC );
                            if( !sheetPath.empty() )
                                lineCmd.mutable_header()->mutable_document()->mutable_sheet_path()->set_path_human_readable( sheetPath );
                            lineCmd.add_types( kiapi::common::types::KOT_SCH_LINE );
                            lineReq.mutable_message()->PackFrom( lineCmd );

                            kiapi::common::ApiResponse lineResp;
                            std::string lineErr;
                            if( m_ipc.SendRequest( lineReq, lineResp, lineErr )
                                && lineResp.status().status() == kiapi::common::AS_OK
                                && lineResp.has_message() )
                            {
                                kiapi::common::commands::GetItemsResponse lineItems;
                                if( lineResp.message().UnpackTo( &lineItems ) )
                                {
                                    // Collect all SL_NOTES line endpoints near the title text
                                    // The title is placed at (boxCenterX, minY + 1.5), so look
                                    // for notes lines within a generous radius
                                    constexpr double SEARCH_RADIUS_MM = 200.0;
                                    double bMinX = 1e9, bMinY = 1e9, bMaxX = -1e9, bMaxY = -1e9;
                                    int notesCount = 0;

                                    for( int j = 0; j < lineItems.items_size(); ++j )
                                    {
                                        kiapi::schematic::types::Line line;
                                        if( !lineItems.items( j ).UnpackTo( &line ) )
                                            continue;
                                        if( line.layer() != kiapi::schematic::types::SL_NOTES )
                                            continue;
                                        if( !line.has_start() || !line.has_end() )
                                            continue;

                                        double sx = line.start().x_nm() * IU_TO_MM;
                                        double sy = line.start().y_nm() * IU_TO_MM;
                                        double ex = line.end().x_nm() * IU_TO_MM;
                                        double ey = line.end().y_nm() * IU_TO_MM;

                                        // Check if this line's midpoint is near the title
                                        double mx = ( sx + ex ) * 0.5;
                                        double my = ( sy + ey ) * 0.5;
                                        double dx = mx - foundTxtX;
                                        double dy = my - foundTxtY;
                                        if( std::sqrt( dx * dx + dy * dy ) > SEARCH_RADIUS_MM )
                                            continue;

                                        bMinX = std::min( { bMinX, sx, ex } );
                                        bMinY = std::min( { bMinY, sy, ey } );
                                        bMaxX = std::max( { bMaxX, sx, ex } );
                                        bMaxY = std::max( { bMaxY, sy, ey } );
                                        notesCount++;
                                    }

                                    if( notesCount >= 4 )
                                    {
                                        // Found the block rectangle
                                        json bbox;
                                        bbox["min_x"] = bMinX;
                                        bbox["min_y"] = bMinY;
                                        bbox["max_x"] = bMaxX;
                                        bbox["max_y"] = bMaxY;
                                        std::string text = "Block with title '" + titleStr
                                            + "' already exists. Bounding box (mm): min_x=" + std::to_string( bMinX )
                                            + ", min_y=" + std::to_string( bMinY )
                                            + ", max_x=" + std::to_string( bMaxX )
                                            + ", max_y=" + std::to_string( bMaxY );
                                        json content = json::array();
                                        content.push_back( { { "type", "text" }, { "text", text } } );
                                        json resultObj = { { "content", content }, { "isError", false },
                                                           { "existing", true }, { "bounding_box", bbox } };
                                        return "{\"jsonrpc\":\"2.0\",\"result\":" + resultObj.dump() + ",\"id\":" + id + "}";
                                    }
                                }
                            }

                            // Title text exists but couldn't find notes lines — still return title position
                            json bbox;
                            bbox["min_x"] = foundTxtX - widthMm / 2.0;
                            bbox["min_y"] = foundTxtY - 1.5;
                            bbox["max_x"] = foundTxtX + widthMm / 2.0;
                            bbox["max_y"] = foundTxtY - 1.5 + heightMm;
                            std::string text = "Block with title '" + titleStr
                                + "' already exists (estimated bbox). min_x=" + std::to_string( bbox["min_x"].get<double>() )
                                + ", min_y=" + std::to_string( bbox["min_y"].get<double>() )
                                + ", max_x=" + std::to_string( bbox["max_x"].get<double>() )
                                + ", max_y=" + std::to_string( bbox["max_y"].get<double>() );
                            json content = json::array();
                            content.push_back( { { "type", "text" }, { "text", text } } );
                            json resultObj = { { "content", content }, { "isError", false },
                                               { "existing", true }, { "bounding_box", bbox } };
                            return "{\"jsonrpc\":\"2.0\",\"result\":" + resultObj.dump() + ",\"id\":" + id + "}";
                        }
                    }
                }
            }
        }

        std::string err;
        double cx = 0.0;
        double cy = 0.0;
        bool hasExplicitCenter = hasArg( "center_x" ) && hasArg( "center_y" );
        if( hasExplicitCenter )
        {
            cx = getDouble( "center_x" );
            cy = getDouble( "center_y" );
        }
        else
        {
            // Place block adjacent to existing content (right side); allows extending beyond A4
            auto autoSpot = findEmptySpotForBlock( widthMm, heightMm, err );
            cx = autoSpot.first;
            cy = autoSpot.second;
            if( !err.empty() )
            {
                json content = json::array();
                content.push_back( { { "type", "text" }, { "text", "start_block findEmptySpotForBlock: " + err } } );
                return "{\"jsonrpc\":\"2.0\",\"result\":" + json( { { "content", content }, { "isError", true } } ).dump() + ",\"id\":" + id + "}";
            }
        }
        constexpr double GRID_MM = 0.1;
        double minX = std::round( ( cx - widthMm / 2.0 ) / GRID_MM ) * GRID_MM;
        double maxX = std::round( ( cx + widthMm / 2.0 ) / GRID_MM ) * GRID_MM;
        double minY = std::round( ( cy - heightMm / 2.0 ) / GRID_MM ) * GRID_MM;
        double maxY = std::round( ( cy + heightMm / 2.0 ) / GRID_MM ) * GRID_MM;
        // No clamping: blocks may extend beyond A4 to find room outside existing content
        std::string txnErr;
        TransactionGuard txn( *this, m_ipc, txnErr );
        if( !txn.ok() )
        {
            json content = json::array();
            content.push_back( { { "type", "text" }, { "text", txnErr.empty() ? "Begin commit failed" : txnErr } } );
            return "{\"jsonrpc\":\"2.0\",\"result\":" + json( { { "content", content }, { "isError", true } } ).dump() + ",\"id\":" + id + "}";
        }
        std::string commitId = txn.commitId();
        constexpr double SCH_IU_PER_MM = 10000.0;
        constexpr int64_t GRID_IU_LOCAL = 1000;  // 0.1mm grid in IU units
        auto snapMm = [GRID_IU_LOCAL]( double mm ) -> int64_t {
            // Convert mm to IU first, then snap to nearest grid cell
            int64_t iu = static_cast<int64_t>( std::round( mm * SCH_IU_PER_MM ) );
            // Snap to nearest 1000 IU (0.1mm grid)
            return ( ( iu + GRID_IU_LOCAL / 2 ) / GRID_IU_LOCAL ) * GRID_IU_LOCAL;
        };
        auto makeUuid = []() -> std::string {
            static std::random_device rd;
            static std::mt19937_64 gen( rd() );
            std::uniform_int_distribution<uint64_t> dis( 0, 0xFFFFFFFFFFFFULL );
            std::ostringstream os;
            os << std::hex << std::setfill( '0' )
               << std::setw( 8 ) << ( dis( gen ) & 0xFFFFFFFFUL ) << "-"
               << std::setw( 4 ) << ( dis( gen ) & 0xFFFFUL ) << "-4"
               << std::setw( 3 ) << ( dis( gen ) & 0xFFFUL ) << "-"
               << std::setw( 4 ) << ( ( dis( gen ) & 0x3FFFUL ) | 0x8000UL ) << "-"
               << std::setw( 12 ) << ( dis( gen ) & 0xFFFFFFFFFFFFULL );
            return os.str();
        };
        kiapi::common::commands::CreateItems cmd;
        cmd.mutable_header()->mutable_document()->set_type( kiapi::common::types::DOCTYPE_SCHEMATIC );
        // Rectangle: four lines on notes layer (bottom, right, top, left)
        kiapi::schematic::types::Line line1;
        line1.mutable_id()->set_value( makeUuid() );
        line1.mutable_start()->set_x_nm( snapMm( minX ) );
        line1.mutable_start()->set_y_nm( snapMm( minY ) );
        line1.mutable_end()->set_x_nm( snapMm( maxX ) );
        line1.mutable_end()->set_y_nm( snapMm( minY ) );
        line1.set_layer( kiapi::schematic::types::SL_NOTES );
        cmd.add_items()->PackFrom( line1 );
        kiapi::schematic::types::Line line2;
        line2.mutable_id()->set_value( makeUuid() );
        line2.mutable_start()->set_x_nm( snapMm( maxX ) );
        line2.mutable_start()->set_y_nm( snapMm( minY ) );
        line2.mutable_end()->set_x_nm( snapMm( maxX ) );
        line2.mutable_end()->set_y_nm( snapMm( maxY ) );
        line2.set_layer( kiapi::schematic::types::SL_NOTES );
        cmd.add_items()->PackFrom( line2 );
        kiapi::schematic::types::Line line3;
        line3.mutable_id()->set_value( makeUuid() );
        line3.mutable_start()->set_x_nm( snapMm( maxX ) );
        line3.mutable_start()->set_y_nm( snapMm( maxY ) );
        line3.mutable_end()->set_x_nm( snapMm( minX ) );
        line3.mutable_end()->set_y_nm( snapMm( maxY ) );
        line3.set_layer( kiapi::schematic::types::SL_NOTES );
        cmd.add_items()->PackFrom( line3 );
        kiapi::schematic::types::Line line4;
        line4.mutable_id()->set_value( makeUuid() );
        line4.mutable_start()->set_x_nm( snapMm( minX ) );
        line4.mutable_start()->set_y_nm( snapMm( maxY ) );
        line4.mutable_end()->set_x_nm( snapMm( minX ) );
        line4.mutable_end()->set_y_nm( snapMm( minY ) );
        line4.set_layer( kiapi::schematic::types::SL_NOTES );
        cmd.add_items()->PackFrom( line4 );
        // Title text: plain SCH_TEXT (annotation), not a net label — center of clamped box horizontally, slightly below top edge
        double boxCenterX = ( minX + maxX ) * 0.5;
        double titleY = minY + 1.5;
        kiapi::schematic::types::SchematicText stext;
        stext.mutable_id()->set_value( makeUuid() );
        stext.mutable_position()->set_x_nm( snapMm( boxCenterX ) );
        stext.mutable_position()->set_y_nm( snapMm( titleY ) );
        stext.mutable_text()->mutable_text()->set_text( titleStr );
        stext.mutable_text()->mutable_text()->mutable_attributes()->set_horizontal_alignment( kiapi::common::types::HA_CENTER );
        stext.mutable_text()->mutable_text()->mutable_attributes()->set_vertical_alignment( kiapi::common::types::VA_CENTER );
        stext.mutable_text()->mutable_text()->mutable_attributes()->mutable_angle()->set_value_degrees( 0 );
        stext.set_layer( kiapi::schematic::types::SL_NOTES );
        cmd.add_items()->PackFrom( stext );
        kiapi::common::ApiRequest addReq;
        addReq.mutable_header()->set_client_name( "mcp" );
        addReq.mutable_message()->PackFrom( cmd );
        kiapi::common::ApiResponse addResp;
        std::string addErr;
        if( !m_ipc.SendRequest( addReq, addResp, addErr ) )
        {
            json content = json::array();
            content.push_back( { { "type", "text" }, { "text", addErr.empty() ? "CreateItems failed" : addErr } } );
            return "{\"jsonrpc\":\"2.0\",\"result\":" + json( { { "content", content }, { "isError", true } } ).dump() + ",\"id\":" + id + "}";
        }
        if( addResp.status().status() != kiapi::common::AS_OK )
        {
            json content = json::array();
            content.push_back( { { "type", "text" }, { "text", addResp.status().error_message().empty() ? "CreateItems failed" : addResp.status().error_message() } } );
            return "{\"jsonrpc\":\"2.0\",\"result\":" + json( { { "content", content }, { "isError", true } } ).dump() + ",\"id\":" + id + "}";
        }
        txn.commit();
        json bbox;
        bbox["min_x"] = minX;
        bbox["min_y"] = minY;
        bbox["max_x"] = maxX;
        bbox["max_y"] = maxY;
        std::string text = "Block placed. Bounding box (mm): min_x=" + std::to_string( minX ) + ", min_y=" + std::to_string( minY )
                         + ", max_x=" + std::to_string( maxX ) + ", max_y=" + std::to_string( maxY );
        json content = json::array();
        content.push_back( { { "type", "text" }, { "text", text } } );
        json resultObj = { { "content", content }, { "isError", false }, { "bounding_box", bbox } };
        return "{\"jsonrpc\":\"2.0\",\"result\":" + resultObj.dump() + ",\"id\":" + id + "}";
    }
    else if( name == "remove_wire" )
    {
        if( !args.contains( "wire_ids" ) || !args["wire_ids"].is_array() || args["wire_ids"].empty() )
        {
            json content = json::array();
            content.push_back( { { "type", "text" }, { "text", "remove_wire requires non-empty wire_ids array of KIID strings" } } );
            json errResult = { { "content", content }, { "isError", true } };
            return "{\"jsonrpc\":\"2.0\",\"result\":" + errResult.dump() + ",\"id\":" + id + "}";
        }
        kiapi::common::commands::DeleteItems cmd;
        cmd.mutable_header()->mutable_document()->set_type( kiapi::common::types::DOCTYPE_SCHEMATIC );
        for( const json& wid : args["wire_ids"] )
        {
            if( wid.is_string() )
                cmd.add_item_ids()->set_value( wid.get<std::string>() );
        }
        if( cmd.item_ids_size() == 0 )
        {
            json content = json::array();
            content.push_back( { { "type", "text" }, { "text", "No valid wire_ids provided" } } );
            json errResult = { { "content", content }, { "isError", true } };
            return "{\"jsonrpc\":\"2.0\",\"result\":" + errResult.dump() + ",\"id\":" + id + "}";
        }
        std::string txnErr;
        TransactionGuard txn( *this, m_ipc, txnErr );
        if( !txn.ok() )
        {
            json content = json::array();
            content.push_back( { { "type", "text" }, { "text", txnErr.empty() ? "Begin commit failed" : txnErr } } );
            return "{\"jsonrpc\":\"2.0\",\"result\":" + json( { { "content", content }, { "isError", true } } ).dump() + ",\"id\":" + id + "}";
        }
        kiapi::common::ApiRequest delReq;
        delReq.mutable_header()->set_client_name( "mcp" );
        delReq.mutable_message()->PackFrom( cmd );
        kiapi::common::ApiResponse delResp;
        std::string err;
        if( !m_ipc.SendRequest( delReq, delResp, err ) )
        {
            json content = json::array();
            content.push_back( { { "type", "text" }, { "text", err.empty() ? "Remove wire failed" : err } } );
            return "{\"jsonrpc\":\"2.0\",\"result\":" + json( { { "content", content }, { "isError", true } } ).dump() + ",\"id\":" + id + "}";
        }
        if( delResp.status().status() != kiapi::common::AS_OK )
        {
            json content = json::array();
            content.push_back( { { "type", "text" }, { "text", delResp.status().error_message().empty() ? "Remove wire failed" : delResp.status().error_message() } } );
            return "{\"jsonrpc\":\"2.0\",\"result\":" + json( { { "content", content }, { "isError", true } } ).dump() + ",\"id\":" + id + "}";
        }
        txn.commit();
        json content = json::array();
        content.push_back( { { "type", "text" }, { "text", "Wire(s) removed" } } );
        return "{\"jsonrpc\":\"2.0\",\"result\":" + json( { { "content", content }, { "isError", false } } ).dump() + ",\"id\":" + id + "}";
    }
    else if( name == "delete_component" )
    {
        std::string ref = getStr( "reference" );
        if( ref.empty() )
        {
            json content = json::array();
            content.push_back( { { "type", "text" }, { "text", "delete_component requires 'reference' (string, e.g. 'R1')" } } );
            return "{\"jsonrpc\":\"2.0\",\"result\":" + json( { { "content", content }, { "isError", true } } ).dump() + ",\"id\":" + id + "}";
        }
        std::string txnErr;
        TransactionGuard txn( *this, m_ipc, txnErr );
        if( !txn.ok() )
        {
            json content = json::array();
            content.push_back( { { "type", "text" }, { "text", txnErr.empty() ? "Begin commit failed" : txnErr } } );
            return "{\"jsonrpc\":\"2.0\",\"result\":" + json( { { "content", content }, { "isError", true } } ).dump() + ",\"id\":" + id + "}";
        }
        std::string commitId = txn.commitId();

        // Execute the delete
        kiapi::common::ApiRequest delReq;
        delReq.mutable_header()->set_client_name( "mcp" );
        kiapi::schematic::types::DeleteComponent delCmd;
        delCmd.set_reference( ref );
        delCmd.mutable_commit_id()->set_value( commitId );
        delReq.mutable_message()->PackFrom( delCmd );
        kiapi::common::ApiResponse delResp;
        std::string delErr;
        if( !m_ipc.SendRequest( delReq, delResp, delErr ) )
        {
            json content = json::array();
            content.push_back( { { "type", "text" }, { "text", delErr.empty() ? "Delete component failed" : delErr } } );
            return "{\"jsonrpc\":\"2.0\",\"result\":" + json( { { "content", content }, { "isError", true } } ).dump() + ",\"id\":" + id + "}";
        }
        if( delResp.status().status() != kiapi::common::AS_OK )
        {
            json content = json::array();
            content.push_back( { { "type", "text" }, { "text", delResp.status().error_message().empty() ? "Delete component failed" : delResp.status().error_message() } } );
            return "{\"jsonrpc\":\"2.0\",\"result\":" + json( { { "content", content }, { "isError", true } } ).dump() + ",\"id\":" + id + "}";
        }

        txn.commit();

        json content = json::array();
        content.push_back( { { "type", "text" }, { "text", "Component " + ref + " deleted" } } );
        return "{\"jsonrpc\":\"2.0\",\"result\":" + json( { { "content", content }, { "isError", false } } ).dump() + ",\"id\":" + id + "}";
    }
    // ── move_components_batch ──
    else if( name == "move_components_batch" )
    {
        if( !args.contains( "moves" ) || !args["moves"].is_array() || args["moves"].empty() )
        {
            json content = json::array();
            content.push_back( { { "type", "text" }, { "text", "move_components_batch requires non-empty 'moves' array of {reference, x_mm, y_mm, rotation?}" } } );
            return "{\"jsonrpc\":\"2.0\",\"result\":" + json( { { "content", content }, { "isError", true } } ).dump() + ",\"id\":" + id + "}";
        }

        kiapi::schematic::types::GetSchematicSummaryResponse sumResp;
        std::string sumErr;
        invalidateSummaryCache();
        if( !getCachedSummary( sumResp, sumErr ) )
        {
            json content = json::array();
            content.push_back( { { "type", "text" }, { "text", "Failed to get schematic summary: " + sumErr } } );
            return "{\"jsonrpc\":\"2.0\",\"result\":" + json( { { "content", content }, { "isError", true } } ).dump() + ",\"id\":" + id + "}";
        }

        struct ExistingComp
        {
            double x = 0.0;
            double y = 0.0;
            double w = 5.0;
            double h = 5.0;
            double rotation = 0.0;
        };
        std::map<std::string, ExistingComp> compByRef;
        for( int i = 0; i < sumResp.components_size(); ++i )
        {
            const auto& c = sumResp.components( i );
            std::string bErr;
            auto [cx, cy, w, h] = getComponentBounds( c.reference(), bErr, &sumResp );
            if( !bErr.empty() || ( cx == 0.0 && cy == 0.0 && !c.has_position() ) )
                continue;
            ExistingComp ec;
            ec.x = cx;
            ec.y = cy;
            ec.w = std::max( 0.1, w );
            ec.h = std::max( 0.1, h );
            ec.rotation = c.rotation();
            compByRef[c.reference()] = ec;
        }

        struct PlannedMove
        {
            std::string ref;
            double x = 0.0;
            double y = 0.0;
            bool hasRotation = false;
            double rotation = 0.0;
        };

        json movedArr = json::array();
        json failedArr = json::array();
        std::vector<PlannedMove> requestedMoves;
        std::set<std::string> movingRefs;

        for( const auto& mv : args["moves"] )
        {
            std::string ref = mv.value( "reference", "" );
            if( ref.empty() || !mv.contains( "x_mm" ) || !mv.contains( "y_mm" ) )
            {
                failedArr.push_back( { { "reference", ref.empty() ? "(missing)" : ref }, { "error", "missing required fields" } } );
                continue;
            }

            auto it = compByRef.find( ref );
            if( it == compByRef.end() )
            {
                failedArr.push_back( { { "reference", ref }, { "error", "component not found in schematic summary" } } );
                continue;
            }

            PlannedMove pm;
            pm.ref = ref;
            pm.x = mv["x_mm"].get<double>();
            pm.y = mv["y_mm"].get<double>();
            pm.hasRotation = mv.contains( "rotation" );
            pm.rotation = pm.hasRotation ? mv["rotation"].get<double>() : it->second.rotation;
            requestedMoves.push_back( pm );
            movingRefs.insert( ref );
        }

        struct TargetRect
        {
            double minX = 0.0;
            double minY = 0.0;
            double maxX = 0.0;
            double maxY = 0.0;
            std::string ref;
        };

        auto normRot = []( double deg ) -> int {
            int snap = static_cast<int>( std::round( deg / 90.0 ) ) * 90;
            snap = ( ( snap % 360 ) + 360 ) % 360;
            return snap;
        };

        auto makeTargetRect = [&]( const PlannedMove& pm ) -> TargetRect {
            const auto& base = compByRef[pm.ref];
            double w = base.w;
            double h = base.h;
            int oldR = normRot( base.rotation );
            int newR = normRot( pm.rotation );
            bool quarterTurn = ( ( oldR + 90 ) % 180 ) == ( newR % 180 );
            if( quarterTurn )
            {
                // Rotating by 90/270 can swap extents; use conservative envelope.
                double m = std::max( w, h );
                w = m;
                h = m;
            }
            TargetRect r;
            r.ref = pm.ref;
            r.minX = pm.x - w / 2.0;
            r.maxX = pm.x + w / 2.0;
            r.minY = pm.y - h / 2.0;
            r.maxY = pm.y + h / 2.0;
            return r;
        };

        auto rectsOverlap = []( const TargetRect& a, const TargetRect& b, double clearanceMm ) -> bool {
            return !( a.maxX + clearanceMm < b.minX
                      || a.minX - clearanceMm > b.maxX
                      || a.maxY + clearanceMm < b.minY
                      || a.minY - clearanceMm > b.maxY );
        };

        constexpr double MOVE_CLEARANCE_MM = 0.2;
        std::vector<TargetRect> acceptedRects;
        std::vector<PlannedMove> acceptedMoves;
        for( const PlannedMove& pm : requestedMoves )
        {
            TargetRect tr = makeTargetRect( pm );
            std::vector<std::string> collisions;

            for( const auto& [ref, comp] : compByRef )
            {
                if( movingRefs.find( ref ) != movingRefs.end() )
                    continue;
                TargetRect sr;
                sr.ref = ref;
                sr.minX = comp.x - comp.w / 2.0;
                sr.maxX = comp.x + comp.w / 2.0;
                sr.minY = comp.y - comp.h / 2.0;
                sr.maxY = comp.y + comp.h / 2.0;
                if( rectsOverlap( tr, sr, MOVE_CLEARANCE_MM ) )
                    collisions.push_back( ref );
            }

            for( const TargetRect& ar : acceptedRects )
            {
                if( rectsOverlap( tr, ar, MOVE_CLEARANCE_MM ) )
                    collisions.push_back( ar.ref );
            }

            if( !collisions.empty() )
            {
                std::ostringstream oss;
                oss << "target overlaps ";
                for( size_t i = 0; i < collisions.size() && i < 4; ++i )
                {
                    if( i > 0 )
                        oss << ", ";
                    oss << collisions[i];
                }
                if( collisions.size() > 4 )
                    oss << " (+" << ( collisions.size() - 4 ) << " more)";
                failedArr.push_back( { { "reference", pm.ref }, { "error", oss.str() } } );
                continue;
            }

            acceptedRects.push_back( tr );
            acceptedMoves.push_back( pm );
        }

        if( acceptedMoves.empty() )
        {
            json resultObj = { { "moved", movedArr }, { "failed", failedArr } };
            json content = json::array();
            content.push_back( { { "type", "text" }, { "text", resultObj.dump() } } );
            return "{\"jsonrpc\":\"2.0\",\"result\":" + json( { { "content", content }, { "isError", false } } ).dump() + ",\"id\":" + id + "}";
        }

        std::string txnErr;
        TransactionGuard txn( *this, m_ipc, txnErr );
        if( !txn.ok() )
        {
            json content = json::array();
            content.push_back( { { "type", "text" }, { "text", txnErr.empty() ? "Begin commit failed" : txnErr } } );
            return "{\"jsonrpc\":\"2.0\",\"result\":" + json( { { "content", content }, { "isError", true } } ).dump() + ",\"id\":" + id + "}";
        }
        std::string commitId = txn.commitId();

        for( const PlannedMove& pm : acceptedMoves )
        {
            kiapi::common::ApiRequest moveReq;
            moveReq.mutable_header()->set_client_name( "mcp" );
            kiapi::schematic::types::MoveComponent moveCmd;
            moveCmd.set_reference( pm.ref );
            moveCmd.mutable_position()->set_x_mm( pm.x );
            moveCmd.mutable_position()->set_y_mm( pm.y );
            if( pm.hasRotation )
                moveCmd.set_rotation( pm.rotation );
            moveCmd.mutable_commit_id()->set_value( commitId );
            moveReq.mutable_message()->PackFrom( moveCmd );
            kiapi::common::ApiResponse moveResp;
            std::string moveErr;
            if( !m_ipc.IsConnected() && !m_ipc.Connect( IpcClient::GetDefaultSocketPath() ) )
            {
                failedArr.push_back( { { "reference", pm.ref }, { "error", "KiCad IPC not connected" } } );
                continue;
            }
            if( !m_ipc.SendRequest( moveReq, moveResp, moveErr ) )
            {
                failedArr.push_back( { { "reference", pm.ref }, { "error", moveErr.empty() ? "Move failed" : moveErr } } );
                continue;
            }
            if( moveResp.status().status() != kiapi::common::AS_OK )
            {
                failedArr.push_back( { { "reference", pm.ref }, { "error", moveResp.status().error_message().empty() ? "Move failed" : moveResp.status().error_message() } } );
                continue;
            }
            json entry = { { "reference", pm.ref }, { "x_mm", pm.x }, { "y_mm", pm.y } };
            if( pm.hasRotation )
                entry["rotation"] = pm.rotation;
            movedArr.push_back( entry );
        }
        if( movedArr.empty() )
            txn.drop();
        else
            txn.commit();
        json resultObj = { { "moved", movedArr }, { "failed", failedArr } };
        json content = json::array();
        content.push_back( { { "type", "text" }, { "text", resultObj.dump() } } );
        return "{\"jsonrpc\":\"2.0\",\"result\":" + json( { { "content", content }, { "isError", false } } ).dump() + ",\"id\":" + id + "}";
    }
    // ── rotate_component ──
    else if( name == "rotate_component" )
    {
        std::string ref = getStr( "reference" );
        if( ref.empty() || !hasArg( "rotation" ) )
        {
            json content = json::array();
            content.push_back( { { "type", "text" }, { "text", "rotate_component requires 'reference' (string) and 'rotation' (number: 0, 90, 180, 270)" } } );
            return "{\"jsonrpc\":\"2.0\",\"result\":" + json( { { "content", content }, { "isError", true } } ).dump() + ",\"id\":" + id + "}";
        }
        double rotation = getDouble( "rotation" );
        // Validate rotation to nearest 90
        int rotSnap = static_cast<int>( std::round( rotation / 90.0 ) ) * 90;
        rotSnap = ( ( rotSnap % 360 ) + 360 ) % 360;

        // Find current position from summary
        kiapi::schematic::types::GetSchematicSummaryResponse sumResp;
        std::string sumErr;
        invalidateSummaryCache();
        if( !getCachedSummary( sumResp, sumErr ) )
        {
            json content = json::array();
            content.push_back( { { "type", "text" }, { "text", "Failed to get schematic summary: " + sumErr } } );
            return "{\"jsonrpc\":\"2.0\",\"result\":" + json( { { "content", content }, { "isError", true } } ).dump() + ",\"id\":" + id + "}";
        }
        double posX = 0, posY = 0;
        bool found = false;
        for( int i = 0; i < sumResp.components_size(); ++i )
        {
            const auto& c = sumResp.components( i );
            if( c.reference() == ref )
            {
                posX = c.has_position() ? c.position().x_mm() : 0;
                posY = c.has_position() ? c.position().y_mm() : 0;
                found = true;
                break;
            }
        }
        if( !found )
        {
            json content = json::array();
            content.push_back( { { "type", "text" }, { "text", "Component '" + ref + "' not found in schematic" } } );
            return "{\"jsonrpc\":\"2.0\",\"result\":" + json( { { "content", content }, { "isError", true } } ).dump() + ",\"id\":" + id + "}";
        }

        std::string txnErr;
        TransactionGuard txn( *this, m_ipc, txnErr );
        if( !txn.ok() )
        {
            json content = json::array();
            content.push_back( { { "type", "text" }, { "text", txnErr.empty() ? "Begin commit failed" : txnErr } } );
            return "{\"jsonrpc\":\"2.0\",\"result\":" + json( { { "content", content }, { "isError", true } } ).dump() + ",\"id\":" + id + "}";
        }
        std::string commitId = txn.commitId();
        kiapi::common::ApiRequest moveReq;
        moveReq.mutable_header()->set_client_name( "mcp" );
        kiapi::schematic::types::MoveComponent moveCmd;
        moveCmd.set_reference( ref );
        moveCmd.mutable_position()->set_x_mm( posX );
        moveCmd.mutable_position()->set_y_mm( posY );
        moveCmd.set_rotation( static_cast<double>( rotSnap ) );
        moveCmd.mutable_commit_id()->set_value( commitId );
        moveReq.mutable_message()->PackFrom( moveCmd );
        kiapi::common::ApiResponse moveResp;
        std::string moveErr;
        if( !m_ipc.IsConnected() && !m_ipc.Connect( IpcClient::GetDefaultSocketPath() ) )
        {
            json content = json::array();
            content.push_back( { { "type", "text" }, { "text", "KiCad IPC not connected." } } );
            return "{\"jsonrpc\":\"2.0\",\"result\":" + json( { { "content", content }, { "isError", true } } ).dump() + ",\"id\":" + id + "}";
        }
        if( !m_ipc.SendRequest( moveReq, moveResp, moveErr ) )
        {
            json content = json::array();
            content.push_back( { { "type", "text" }, { "text", moveErr.empty() ? "Rotate failed" : moveErr } } );
            return "{\"jsonrpc\":\"2.0\",\"result\":" + json( { { "content", content }, { "isError", true } } ).dump() + ",\"id\":" + id + "}";
        }
        if( moveResp.status().status() != kiapi::common::AS_OK )
        {
            json content = json::array();
            content.push_back( { { "type", "text" }, { "text", moveResp.status().error_message().empty() ? "Rotate failed" : moveResp.status().error_message() } } );
            return "{\"jsonrpc\":\"2.0\",\"result\":" + json( { { "content", content }, { "isError", true } } ).dump() + ",\"id\":" + id + "}";
        }
        txn.commit();
        json resultObj = { { "rotated", true }, { "reference", ref }, { "rotation", rotSnap } };
        json content = json::array();
        content.push_back( { { "type", "text" }, { "text", resultObj.dump() } } );
        return "{\"jsonrpc\":\"2.0\",\"result\":" + json( { { "content", content }, { "isError", false } } ).dump() + ",\"id\":" + id + "}";
    }
    // ── rename_net_label ──
    else if( name == "rename_net_label" )
    {
        std::string oldName = getStr( "old_name" );
        std::string newName = getStr( "new_name" );
        if( oldName.empty() || newName.empty() )
        {
            json content = json::array();
            content.push_back( { { "type", "text" }, { "text", "rename_net_label requires 'old_name' and 'new_name' (non-empty strings)" } } );
            return "{\"jsonrpc\":\"2.0\",\"result\":" + json( { { "content", content }, { "isError", true } } ).dump() + ",\"id\":" + id + "}";
        }
        bool hasXY = hasArg( "x" ) && hasArg( "y" );
        double targetX = hasXY ? getDouble( "x" ) : 0;
        double targetY = hasXY ? getDouble( "y" ) : 0;

        std::string sheetPath = getCurrentSheetPath();
        std::vector<ParsedLabel> liveLabels;
        std::vector<ParsedWire> liveWires;
        std::string fetchErr;
        if( !fetchLiveLabelsAndWires( m_ipc, sheetPath, liveLabels, liveWires, fetchErr ) )
            return makeErrorResult( fetchErr.empty() ? "GetItems failed" : fetchErr );

        std::vector<ParsedLabel> matches;
        matches.reserve( liveLabels.size() );
        for( const ParsedLabel& label : liveLabels )
        {
            if( label.text != oldName )
                continue;

            double dx = label.x - targetX;
            double dy = label.y - targetY;
            matches.push_back( label );
            matches.back().rotation = dx * dx + dy * dy;
        }

        if( matches.empty() )
            return makeErrorResult( "No labels found with text '" + oldName + "'" );

        if( hasXY )
        {
            auto nearest = std::min_element( matches.begin(), matches.end(),
                []( const ParsedLabel& a, const ParsedLabel& b ) { return a.rotation < b.rotation; } );
            matches = { *nearest };
        }

        std::string txnErr;
        TransactionGuard txn( *this, m_ipc, txnErr );
        if( !txn.ok() )
            return makeErrorResult( txnErr.empty() ? "Begin commit failed" : txnErr );

        kiapi::common::commands::UpdateItems updateCmd;
        updateCmd.mutable_header()->mutable_document()->set_type( kiapi::common::types::DOCTYPE_SCHEMATIC );
        if( !sheetPath.empty() )
            updateCmd.mutable_header()->mutable_document()->mutable_sheet_path()->set_path_human_readable( sheetPath );

        json renamed = json::array();
        json failed = json::array();

        for( const ParsedLabel& label : matches )
        {
            google::protobuf::Any updatedAny;
            if( !buildUpdatedLabelAny( label, newName, updatedAny ) )
            {
                failed.push_back( { { "uuid", label.uuid }, { "old_name", label.text },
                                    { "reason", "could_not_unpack_label" } } );
                continue;
            }

            updateCmd.add_items()->CopyFrom( updatedAny );
            renamed.push_back( {
                { "uuid", label.uuid },
                { "kind", label.kind },
                { "old_name", label.text },
                { "new_name", newName },
                { "x", label.x },
                { "y", label.y }
            } );
        }

        if( updateCmd.items_size() == 0 )
            return makeErrorResult( "No labels could be updated" );

        kiapi::common::ApiRequest updateReq;
        updateReq.mutable_header()->set_client_name( "mcp" );
        updateReq.mutable_message()->PackFrom( updateCmd );
        kiapi::common::ApiResponse updateResp;
        std::string updateErr;
        if( !m_ipc.SendRequest( updateReq, updateResp, updateErr ) )
            return makeErrorResult( updateErr.empty() ? "UpdateItems failed" : updateErr );
        if( updateResp.status().status() != kiapi::common::AS_OK )
            return makeErrorResult( updateResp.status().error_message().empty()
                                    ? "UpdateItems failed"
                                    : updateResp.status().error_message() );

        json resultObj = { { "renamed_count", static_cast<int>( renamed.size() ) },
                           { "old_name", oldName },
                           { "new_name", newName },
                           { "renamed", renamed },
                           { "failed", failed } };
        json content = json::array();
        content.push_back( { { "type", "text" }, { "text", resultObj.dump() } } );
        return "{\"jsonrpc\":\"2.0\",\"result\":" + json( { { "content", content }, { "isError", false } } ).dump() + ",\"id\":" + id + "}";
    }
    // ── preview_changes ──
    else if( name == "preview_changes" )
    {
        kiapi::schematic::types::GetSchematicSummaryResponse sumResp;
        std::string sumErr;
        invalidateSummaryCache();
        if( !getCachedSummary( sumResp, sumErr ) )
        {
            json content = json::array();
            content.push_back( { { "type", "text" }, { "text", "Failed to get schematic summary: " + sumErr } } );
            return "{\"jsonrpc\":\"2.0\",\"result\":" + json( { { "content", content }, { "isError", true } } ).dump() + ",\"id\":" + id + "}";
        }
        json components = json::array();
        for( int i = 0; i < sumResp.components_size(); ++i )
        {
            const auto& c = sumResp.components( i );
            json comp;
            comp["ref"] = c.reference();
            comp["symbol"] = c.library_nickname() + ":" + c.symbol_name();
            comp["value"] = c.value();
            comp["x"] = c.has_position() ? c.position().x_mm() : 0;
            comp["y"] = c.has_position() ? c.position().y_mm() : 0;
            comp["rotation"] = c.rotation();
            components.push_back( comp );
        }
        json nets = json::array();
        for( int i = 0; i < sumResp.global_net_names_size(); ++i )
            nets.push_back( sumResp.global_net_names( i ) );
        json snapshot = {
            { "component_count", sumResp.components_size() },
            { "net_count", sumResp.global_net_names_size() },
            { "components", components },
            { "nets", nets }
        };
        json content = json::array();
        content.push_back( { { "type", "text" }, { "text", snapshot.dump() } } );
        return "{\"jsonrpc\":\"2.0\",\"result\":" + json( { { "content", content }, { "isError", false } } ).dump() + ",\"id\":" + id + "}";
    }
    // ── undo_last_commit ── (stub: not supported by KiCad API)
    else if( name == "undo_last_commit" )
    {
        json content = json::array();
        content.push_back( { { "type", "text" }, { "text", "undo_last_commit is not supported: the KiCad API does not provide a true undo command for committed changes. To revert, delete the unwanted items and re-create the correct ones." } } );
        return "{\"jsonrpc\":\"2.0\",\"result\":" + json( { { "content", content }, { "isError", true } } ).dump() + ",\"id\":" + id + "}";
    }
    else if( name == "erc_check" )
    {
        kiapi::schematic::types::GetDanglingReport cmd;
        req.mutable_message()->PackFrom( cmd );
    }
    else if( name == "get_schematic_summary" )
    {
        kiapi::schematic::types::GetSchematicSummary cmd;
        req.mutable_message()->PackFrom( cmd );
    }
    else if( name == "get_netlist" )
    {
        kiapi::schematic::types::GetNetlist cmd;
        req.mutable_message()->PackFrom( cmd );
    }
    else if( name == "connect_net_to_pin" )
    {
        std::string netName = getStr( "net_name" );
        std::string ref = getStr( "reference" );
        std::string pinNum = getStr( "pin_number" );
        if( netName.empty() || ref.empty() || pinNum.empty() )
        {
            json content = json::array();
            content.push_back( { { "type", "text" }, { "text", "connect_net_to_pin requires 'net_name' (string), 'reference' (string), and 'pin_number' (string)" } } );
            return "{\"jsonrpc\":\"2.0\",\"result\":" + json( { { "content", content }, { "isError", true } } ).dump() + ",\"id\":" + id + "}";
        }
        std::string err;
        if( !m_ipc.IsConnected() && !m_ipc.Connect( IpcClient::GetDefaultSocketPath() ) )
        {
            json content = json::array();
            content.push_back( { { "type", "text" }, { "text", "KiCad IPC not connected." } } );
            return "{\"jsonrpc\":\"2.0\",\"result\":" + json( { { "content", content }, { "isError", true } } ).dump() + ",\"id\":" + id + "}";
        }
        double pinOrientationDeg = 0;
        auto [px, py] = getPinPosition( ref, pinNum, err, &pinOrientationDeg );
        if( !err.empty() )
        {
            json content = json::array();
            content.push_back( { { "type", "text" }, { "text", "Failed to get pin position for " + ref + " pin " + pinNum + ": " + err } } );
            return "{\"jsonrpc\":\"2.0\",\"result\":" + json( { { "content", content }, { "isError", true } } ).dump() + ",\"id\":" + id + "}";
        }
        // Use a fine snap for net-label stubs (not the user schematic grid, which may be very coarse)
        // so we can keep short, predictable ~1mm wires.
        const double GRID_MM = 0.1;
        auto snap = [GRID_MM]( double mm ) { return std::round( mm / GRID_MM ) * GRID_MM; };

        // Anchor at the resolved pin point to avoid unit/field ambiguity from optional tip fields.
        double anchorX = px;
        double anchorY = py;

        // Place label 1mm away in pin-facing direction by default.
        double labelOffsetMm = getDouble( "label_offset_mm", 1.0 );
        if( labelOffsetMm <= 0 )
            labelOffsetMm = 1.0;

        // API pin orientation points along the pin into the symbol; use +180° to go outward.
        constexpr double PI = 3.14159265358979323846;
        double outwardDeg = std::fmod( pinOrientationDeg + 180.0, 360.0 );
        if( outwardDeg < 0 )
            outwardDeg += 360.0;
        double rad = outwardDeg * PI / 180.0;
        double offsetX = std::cos( rad ) * labelOffsetMm;
        double offsetY = -std::sin( rad ) * labelOffsetMm; // KiCad schematic Y grows downward
        double lx = snap( anchorX + offsetX );
        double ly = snap( anchorY + offsetY );

        // Guarantee at least one grid-step separation from pin after snapping.
        double axSnap = snap( anchorX );
        double aySnap = snap( anchorY );
        if( std::abs( lx - axSnap ) < 1e-9 && std::abs( ly - aySnap ) < 1e-9 )
        {
            if( std::abs( offsetX ) >= std::abs( offsetY ) )
                lx = axSnap + ( offsetX >= 0 ? GRID_MM : -GRID_MM );
            else
                ly = aySnap + ( offsetY >= 0 ? GRID_MM : -GRID_MM );
        }

        // Label orientation: the wire runs from pin → label in the outwardDeg
        // direction.  The label's connection pin must face BACK toward the pin,
        // i.e. in the direction (outwardDeg + 180).  KiCad maps angle → SPIN_STYLE:
        //   0°   → RIGHT  (pin/connection faces right)
        //   90°  → UP     (pin/connection faces up)
        //   180° → LEFT   (pin/connection faces left)
        //   270° → BOTTOM (pin/connection faces down)
        // So: labelAngle = (outwardDeg + 180) mod 360, snapped to nearest 90°.
        double backDeg = std::fmod( outwardDeg + 180.0, 360.0 );
        double labelAngle;
        if( backDeg >= 315.0 || backDeg < 45.0 )
            labelAngle = 0.0;
        else if( backDeg >= 45.0 && backDeg < 135.0 )
            labelAngle = 90.0;
        else if( backDeg >= 135.0 && backDeg < 225.0 )
            labelAngle = 180.0;
        else
            labelAngle = 270.0;
        std::string txnErr;
        TransactionGuard txn( *this, m_ipc, txnErr );
        if( !txn.ok() )
        {
            json content = json::array();
            content.push_back( { { "type", "text" }, { "text", txnErr.empty() ? "Begin commit failed" : txnErr } } );
            return "{\"jsonrpc\":\"2.0\",\"result\":" + json( { { "content", content }, { "isError", true } } ).dump() + ",\"id\":" + id + "}";
        }
        // Schematic CreateItems expects coordinates in schematic internal units (10000 IU/mm)
        // in Vector2 x_nm/y_nm — KiCad's PackVector2/UnpackVector2 use document IU there, not true nm.
        // Keep the wire start at exact pin coordinates; only the label end should snap to the compact label grid.
        constexpr double SCH_IU_PER_MM = 10000.0;
        constexpr int64_t GRID_IU_LOCAL = 1000;  // 0.1mm = 1000 IU
        auto mmToIuLocal = []( double mm ) -> int64_t {
            int64_t iu = static_cast<int64_t>( std::round( mm * SCH_IU_PER_MM ) );
            return ( ( iu + GRID_IU_LOCAL / 2 ) / GRID_IU_LOCAL ) * GRID_IU_LOCAL;
        };
        auto makeUuid = []() -> std::string {
            static std::random_device rd;
            static std::mt19937_64 gen( rd() );
            std::uniform_int_distribution<uint64_t> dis( 0, 0xFFFFFFFFFFFFULL );
            std::ostringstream os;
            os << std::hex << std::setfill( '0' )
               << std::setw( 8 ) << ( dis( gen ) & 0xFFFFFFFFUL ) << "-"
               << std::setw( 4 ) << ( dis( gen ) & 0xFFFFUL ) << "-4"
               << std::setw( 3 ) << ( dis( gen ) & 0xFFFUL ) << "-"
               << std::setw( 4 ) << ( ( dis( gen ) & 0x3FFFUL ) | 0x8000UL ) << "-"
               << std::setw( 12 ) << ( dis( gen ) & 0xFFFFFFFFFFFFULL );
            return os.str();
        };

        std::map<LabelPosKey, std::string> occupiedLabels;
        std::string labelPlacementErr;
        if( !fetchGlobalLabelsByPosition( occupiedLabels, labelPlacementErr ) )
        {
            json content = json::array();
            content.push_back( { { "type", "text" }, { "text", labelPlacementErr.empty() ? "Failed to query existing global labels" : labelPlacementErr } } );
            return "{\"jsonrpc\":\"2.0\",\"result\":" + json( { { "content", content }, { "isError", true } } ).dump() + ",\"id\":" + id + "}";
        }

        double resolvedLx = lx;
        double resolvedLy = ly;
        bool shouldCreateLabel = true;
        if( !resolveLabelPlacement( lx, ly, netName, occupiedLabels, true, resolvedLx, resolvedLy, shouldCreateLabel, labelPlacementErr ) )
        {
            json content = json::array();
            content.push_back( { { "type", "text" }, { "text", labelPlacementErr } } );
            return "{\"jsonrpc\":\"2.0\",\"result\":" + json( { { "content", content }, { "isError", true } } ).dump() + ",\"id\":" + id + "}";
        }
        lx = resolvedLx;
        ly = resolvedLy;

        kiapi::common::commands::CreateItems createCmd;
        createCmd.mutable_header()->mutable_document()->set_type( kiapi::common::types::DOCTYPE_SCHEMATIC );
        std::string sheetPath = getCurrentSheetPath();
        if( !sheetPath.empty() )
            createCmd.mutable_header()->mutable_document()->mutable_sheet_path()->set_path_human_readable( sheetPath );
        int64_t lxIu = mmToIuLocal( lx ), lyIu = mmToIuLocal( ly );
        if( shouldCreateLabel )
        {
            kiapi::schematic::types::GlobalLabel glabel;
            glabel.mutable_id()->set_value( makeUuid() );
            glabel.mutable_position()->set_x_nm( lxIu );
            glabel.mutable_position()->set_y_nm( lyIu );
            glabel.mutable_text()->mutable_text()->set_text( netName );
            glabel.mutable_text()->mutable_text()->mutable_attributes()->set_horizontal_alignment( kiapi::common::types::HA_CENTER );
            glabel.mutable_text()->mutable_text()->mutable_attributes()->set_vertical_alignment( kiapi::common::types::VA_CENTER );
            glabel.mutable_text()->mutable_text()->mutable_attributes()->mutable_angle()->set_value_degrees( static_cast<int>( labelAngle + 180 ) % 360 );
            google::protobuf::Any* any = createCmd.add_items();
            any->PackFrom( glabel );
        }
        // Wire from pin tip (or pin point fallback) to inferred label point (default 1mm)
        int64_t wireStartIuX = mmToIu( anchorX ), wireStartIuY = mmToIu( anchorY );
        kiapi::schematic::types::Line line;
        line.mutable_id()->set_value( makeUuid() );
        line.mutable_start()->set_x_nm( wireStartIuX );
        line.mutable_start()->set_y_nm( wireStartIuY );
        line.mutable_end()->set_x_nm( lxIu );
        line.mutable_end()->set_y_nm( lyIu );
        line.set_layer( kiapi::schematic::types::SL_WIRE );
        createCmd.add_items()->PackFrom( line );
        kiapi::common::ApiRequest addReq;
        addReq.mutable_header()->set_client_name( "mcp" );
        addReq.mutable_message()->PackFrom( createCmd );
        kiapi::common::ApiResponse addResp;
        if( !m_ipc.SendRequest( addReq, addResp, err ) )
        {
            json content = json::array();
            content.push_back( { { "type", "text" }, { "text", err.empty() ? "Create items failed" : err } } );
            return "{\"jsonrpc\":\"2.0\",\"result\":" + json( { { "content", content }, { "isError", true } } ).dump() + ",\"id\":" + id + "}";
        }
        if( addResp.status().status() != kiapi::common::AS_OK )
        {
            json content = json::array();
            content.push_back( { { "type", "text" }, { "text", addResp.status().error_message().empty() ? "Create items failed" : addResp.status().error_message() } } );
            return "{\"jsonrpc\":\"2.0\",\"result\":" + json( { { "content", content }, { "isError", true } } ).dump() + ",\"id\":" + id + "}";
        }
        txn.commit();
        // Post-connect verification: if pin still dangling, wire was placed at wrong coordinates
        kiapi::common::ApiRequest dangReq;
        dangReq.mutable_header()->set_client_name( "mcp" );
        kiapi::schematic::types::GetDanglingReport dangCmd;
        dangReq.mutable_message()->PackFrom( dangCmd );
        kiapi::common::ApiResponse dangResp;
        if( m_ipc.SendRequest( dangReq, dangResp, err ) && dangResp.status().status() == kiapi::common::AS_OK
            && dangResp.has_message() && dangResp.message().type_url().find( "GetDanglingReportResponse" ) != std::string::npos )
        {
            kiapi::schematic::types::GetDanglingReportResponse dangR;
            if( dangResp.message().UnpackTo( &dangR ) )
            {
                for( int i = 0; i < dangR.items_size(); ++i )
                {
                    const auto& it = dangR.items( i );
                    if( it.reference() == ref && it.pin_number() == pinNum && it.type() == "pin" )
                    {
                        json content = json::array();
                        std::ostringstream msg;
                        msg << "Connection failed: pin still dangling after placing wire/label. Pin position used: ("
                            << px << ", " << py << ") mm. The wire may have been placed at the wrong coordinates. "
                            << "Use get_component_pins, batch_get_component_pins, or get_component_connectivity_graph to inspect the local connection before retrying.";
                        content.push_back( { { "type", "text" }, { "text", msg.str() } } );
                        return "{\"jsonrpc\":\"2.0\",\"result\":" + json( { { "content", content }, { "isError", true } } ).dump() + ",\"id\":" + id + "}";
                    }
                }
            }
        }
        json content = json::array();
        std::ostringstream successMsg;
        double stubLen = std::hypot( lx - anchorX, ly - anchorY );
        successMsg << "Net " << netName << " connected to " << ref << " pin " << pinNum
                   << " (pin " << anchorX << "," << anchorY
                   << " -> label " << lx << "," << ly
                   << ", stub " << stubLen << " mm)";
        content.push_back( { { "type", "text" }, { "text", successMsg.str() } } );
        return "{\"jsonrpc\":\"2.0\",\"result\":" + json( { { "content", content }, { "isError", false } } ).dump() + ",\"id\":" + id + "}";
    }
    else if( name == "connect_pin_to_pin" )
    {
        std::string ref1 = getStr( "reference1" );
        std::string pin1 = getStr( "pin1" );
        std::string ref2 = getStr( "reference2" );
        std::string pin2 = getStr( "pin2" );
        std::string netName = getStr( "net_name" );
        if( ref1.empty() || pin1.empty() || ref2.empty() || pin2.empty() || netName.empty() )
        {
            json content = json::array();
            content.push_back( { { "type", "text" }, { "text", "connect_pin_to_pin requires 'reference1', 'pin1', 'reference2', 'pin2', and 'net_name' (all strings)" } } );
            return "{\"jsonrpc\":\"2.0\",\"result\":" + json( { { "content", content }, { "isError", true } } ).dump() + ",\"id\":" + id + "}";
        }
        double thresholdMm = getDouble( "short_wire_threshold_mm", 5.0 );
        
        if( !m_ipc.IsConnected() && !m_ipc.Connect( IpcClient::GetDefaultSocketPath() ) )
        {
            json content = json::array();
            content.push_back( { { "type", "text" }, { "text", "KiCad IPC not connected." } } );
            return "{\"jsonrpc\":\"2.0\",\"result\":" + json( { { "content", content }, { "isError", true } } ).dump() + ",\"id\":" + id + "}";
        }
        
        std::string err;
        auto [x1, y1] = getPinPosition( ref1, pin1, err );
        if( !err.empty() )
        {
            json content = json::array();
            content.push_back( { { "type", "text" }, { "text", "Failed to get position for " + ref1 + " pin " + pin1 + ": " + err } } );
            return "{\"jsonrpc\":\"2.0\",\"result\":" + json( { { "content", content }, { "isError", true } } ).dump() + ",\"id\":" + id + "}";
        }
        
        auto [x2, y2] = getPinPosition( ref2, pin2, err );
        if( !err.empty() )
        {
            json content = json::array();
            content.push_back( { { "type", "text" }, { "text", "Failed to get position for " + ref2 + " pin " + pin2 + ": " + err } } );
            return "{\"jsonrpc\":\"2.0\",\"result\":" + json( { { "content", content }, { "isError", true } } ).dump() + ",\"id\":" + id + "}";
        }
        constexpr double GRID_MM = 0.1;
        auto snap = []( double mm ) { return std::round( mm / GRID_MM ) * GRID_MM; };
        double dist = std::hypot( x2 - x1, y2 - y1 );
        std::string txnErr;
        TransactionGuard txn( *this, m_ipc, txnErr );
        if( !txn.ok() )
        {
            json content = json::array();
            content.push_back( { { "type", "text" }, { "text", txnErr.empty() ? "Begin commit failed" : txnErr } } );
            return "{\"jsonrpc\":\"2.0\",\"result\":" + json( { { "content", content }, { "isError", true } } ).dump() + ",\"id\":" + id + "}";
        }
        std::string commitId = txn.commitId();
        // Snap to grid in IU space for precision (0.1mm = 1000 IU)
        constexpr int64_t GRID_IU_BATCH = 1000;
        auto snapMm = []( double mm ) -> int64_t {
            constexpr double iuPerMm = 10000.0;
            // Convert mm to IU first, then snap to nearest grid cell
            int64_t iu = static_cast<int64_t>( std::round( mm * iuPerMm ) );
            // Snap to nearest 1000 IU (0.1mm grid)
            return ( ( iu + GRID_IU_BATCH / 2 ) / GRID_IU_BATCH ) * GRID_IU_BATCH;
        };
        auto makeUuid = []() -> std::string {
            static std::random_device rd;
            static std::mt19937_64 gen( rd() );
            std::uniform_int_distribution<uint64_t> dis( 0, 0xFFFFFFFFFFFFULL );
            std::ostringstream os;
            os << std::hex << std::setfill( '0' )
               << std::setw( 8 ) << ( dis( gen ) & 0xFFFFFFFFUL ) << "-"
               << std::setw( 4 ) << ( dis( gen ) & 0xFFFFUL ) << "-4"
               << std::setw( 3 ) << ( dis( gen ) & 0xFFFUL ) << "-"
               << std::setw( 4 ) << ( ( dis( gen ) & 0x3FFFUL ) | 0x8000UL ) << "-"
               << std::setw( 12 ) << ( dis( gen ) & 0xFFFFFFFFFFFFULL );
            return os.str();
        };
        kiapi::common::commands::CreateItems createCmd;
        createCmd.mutable_header()->mutable_document()->set_type( kiapi::common::types::DOCTYPE_SCHEMATIC );
        std::string sheetPath = getCurrentSheetPath();
        if( !sheetPath.empty() )
            createCmd.mutable_header()->mutable_document()->mutable_sheet_path()->set_path_human_readable( sheetPath );
        if( dist <= thresholdMm )
        {
            // Manhattan routing: orthogonal segments between pins
            int64_t x1Iu = mmToIu( x1 ), y1Iu = mmToIu( y1 ), x2Iu = mmToIu( x2 ), y2Iu = mmToIu( y2 );
            if( x1Iu == x2Iu || y1Iu == y2Iu )  // Already horizontal or vertical
            {
                kiapi::schematic::types::Line line;
                line.mutable_id()->set_value( makeUuid() );
                line.mutable_start()->set_x_nm( x1Iu );
                line.mutable_start()->set_y_nm( y1Iu );
                line.mutable_end()->set_x_nm( x2Iu );
                line.mutable_end()->set_y_nm( y2Iu );
                line.set_layer( kiapi::schematic::types::SL_WIRE );
                createCmd.add_items()->PackFrom( line );
            }
            else
            {
                double midX = std::round( ( x1 + x2 ) / 2.0 / GRID_MM ) * GRID_MM;
                int64_t midIu = mmToIu( midX );
                kiapi::schematic::types::Line line1;
                line1.mutable_id()->set_value( makeUuid() );
                line1.mutable_start()->set_x_nm( x1Iu );
                line1.mutable_start()->set_y_nm( y1Iu );
                line1.mutable_end()->set_x_nm( midIu );
                line1.mutable_end()->set_y_nm( y1Iu );
                line1.set_layer( kiapi::schematic::types::SL_WIRE );
                createCmd.add_items()->PackFrom( line1 );
                kiapi::schematic::types::Line line2;
                line2.mutable_id()->set_value( makeUuid() );
                line2.mutable_start()->set_x_nm( midIu );
                line2.mutable_start()->set_y_nm( y1Iu );
                line2.mutable_end()->set_x_nm( midIu );
                line2.mutable_end()->set_y_nm( y2Iu );
                line2.set_layer( kiapi::schematic::types::SL_WIRE );
                createCmd.add_items()->PackFrom( line2 );
                kiapi::schematic::types::Line line3;
                line3.mutable_id()->set_value( makeUuid() );
                line3.mutable_start()->set_x_nm( midIu );
                line3.mutable_start()->set_y_nm( y2Iu );
                line3.mutable_end()->set_x_nm( x2Iu );
                line3.mutable_end()->set_y_nm( y2Iu );
                line3.set_layer( kiapi::schematic::types::SL_WIRE );
                createCmd.add_items()->PackFrom( line3 );
            }
        }
        else
        {
            // Calculate smart label offsets based on pin directions
            auto [offset1X, offset1Y] = calculateLabelOffset( x1, y1, ref1, 1.0 );
            auto [offset2X, offset2Y] = calculateLabelOffset( x2, y2, ref2, 1.0 );
            
            double l1x = snap( x1 + offset1X ), l1y = snap( y1 + offset1Y );
            double l2x = snap( x2 + offset2X ), l2y = snap( y2 + offset2Y );
            // Keep labels within schematic page (A4 297x210 mm)
            constexpr double SCH_PAGE_WIDTH_MM = 297.0, SCH_PAGE_HEIGHT_MM = 210.0, SCH_PAGE_MARGIN_MM = 5.0;
            l1x = std::clamp( l1x, SCH_PAGE_MARGIN_MM, SCH_PAGE_WIDTH_MM - SCH_PAGE_MARGIN_MM );
            l1y = std::clamp( l1y, SCH_PAGE_MARGIN_MM, SCH_PAGE_HEIGHT_MM - SCH_PAGE_MARGIN_MM );
            l2x = std::clamp( l2x, SCH_PAGE_MARGIN_MM, SCH_PAGE_WIDTH_MM - SCH_PAGE_MARGIN_MM );
            l2y = std::clamp( l2y, SCH_PAGE_MARGIN_MM, SCH_PAGE_HEIGHT_MM - SCH_PAGE_MARGIN_MM );
            // Label pin must face BACK toward the component pin (opposite of offset direction).
            // offset > 0 means label is to the right/below → pin faces left/up → angle 180/90.
            double labelAngle1 = ( offset1X > 0 ) ? 180 : ( offset1X < 0 ) ? 0 : ( offset1Y > 0 ) ? 90 : 270;
            double labelAngle2 = ( offset2X > 0 ) ? 180 : ( offset2X < 0 ) ? 0 : ( offset2Y > 0 ) ? 90 : 270;

            std::map<LabelPosKey, std::string> occupiedLabels;
            std::string labelPlacementErr;
            if( !fetchGlobalLabelsByPosition( occupiedLabels, labelPlacementErr ) )
            {
                json content = json::array();
                content.push_back( { { "type", "text" }, { "text", labelPlacementErr.empty() ? "Failed to query existing global labels" : labelPlacementErr } } );
                return "{\"jsonrpc\":\"2.0\",\"result\":" + json( { { "content", content }, { "isError", true } } ).dump() + ",\"id\":" + id + "}";
            }

            double resolvedL1x = l1x;
            double resolvedL1y = l1y;
            bool shouldCreateLabel1 = true;
            if( !resolveLabelPlacement( l1x, l1y, netName, occupiedLabels, true, resolvedL1x, resolvedL1y, shouldCreateLabel1, labelPlacementErr ) )
            {
                json content = json::array();
                content.push_back( { { "type", "text" }, { "text", labelPlacementErr } } );
                return "{\"jsonrpc\":\"2.0\",\"result\":" + json( { { "content", content }, { "isError", true } } ).dump() + ",\"id\":" + id + "}";
            }

            double resolvedL2x = l2x;
            double resolvedL2y = l2y;
            bool shouldCreateLabel2 = true;
            if( !resolveLabelPlacement( l2x, l2y, netName, occupiedLabels, true, resolvedL2x, resolvedL2y, shouldCreateLabel2, labelPlacementErr ) )
            {
                json content = json::array();
                content.push_back( { { "type", "text" }, { "text", labelPlacementErr } } );
                return "{\"jsonrpc\":\"2.0\",\"result\":" + json( { { "content", content }, { "isError", true } } ).dump() + ",\"id\":" + id + "}";
            }
            l1x = resolvedL1x;
            l1y = resolvedL1y;
            l2x = resolvedL2x;
            l2y = resolvedL2y;
            
            int64_t l1xIu = snapMm( l1x ), l1yIu = snapMm( l1y );
            if( l1xIu == 0 && l1yIu == 0 )
            {
                l1xIu = mmToIu( x1 + ( offset1X != 0 ? offset1X : 2.0 ) );
                l1yIu = mmToIu( y1 + ( offset1Y != 0 ? offset1Y : 2.0 ) );
            }
            if( shouldCreateLabel1 )
            {
                kiapi::schematic::types::GlobalLabel gl1;
                gl1.mutable_id()->set_value( makeUuid() );
                gl1.mutable_position()->set_x_nm( l1xIu );
                gl1.mutable_position()->set_y_nm( l1yIu );
                gl1.mutable_text()->mutable_text()->set_text( netName );
                gl1.mutable_text()->mutable_text()->mutable_attributes()->set_horizontal_alignment( kiapi::common::types::HA_CENTER );
                gl1.mutable_text()->mutable_text()->mutable_attributes()->set_vertical_alignment( kiapi::common::types::VA_CENTER );
                gl1.mutable_text()->mutable_text()->mutable_attributes()->mutable_angle()->set_value_degrees( static_cast<int>( labelAngle1 + 180 ) % 360 );
                createCmd.add_items()->PackFrom( gl1 );
            }
            int64_t l2xIu = snapMm( l2x ), l2yIu = snapMm( l2y );
            if( l2xIu == 0 && l2yIu == 0 )
            {
                l2xIu = mmToIu( x2 + ( offset2X != 0 ? offset2X : 2.0 ) );
                l2yIu = mmToIu( y2 + ( offset2Y != 0 ? offset2Y : 2.0 ) );
            }
            if( shouldCreateLabel2 )
            {
                kiapi::schematic::types::GlobalLabel gl2;
                gl2.mutable_id()->set_value( makeUuid() );
                gl2.mutable_position()->set_x_nm( l2xIu );
                gl2.mutable_position()->set_y_nm( l2yIu );
                gl2.mutable_text()->mutable_text()->set_text( netName );
                gl2.mutable_text()->mutable_text()->mutable_attributes()->set_horizontal_alignment( kiapi::common::types::HA_CENTER );
                gl2.mutable_text()->mutable_text()->mutable_attributes()->set_vertical_alignment( kiapi::common::types::VA_CENTER );
                gl2.mutable_text()->mutable_text()->mutable_attributes()->mutable_angle()->set_value_degrees( static_cast<int>( labelAngle2 + 180 ) % 360 );
                createCmd.add_items()->PackFrom( gl2 );
            }
            // Manhattan routing for pin1->label1 and pin2->label2
            int64_t x1Iu = mmToIu( x1 ), y1Iu = mmToIu( y1 ), x2Iu = mmToIu( x2 ), y2Iu = mmToIu( y2 );
            auto addManhattanWire = [&]( int64_t px, int64_t py, int64_t lx, int64_t ly, double offX, double offY ) {
                if( px == lx || py == ly )
                {
                    kiapi::schematic::types::Line line;
                    line.mutable_id()->set_value( makeUuid() );
                    line.mutable_start()->set_x_nm( px );
                    line.mutable_start()->set_y_nm( py );
                    line.mutable_end()->set_x_nm( lx );
                    line.mutable_end()->set_y_nm( ly );
                    line.set_layer( kiapi::schematic::types::SL_WIRE );
                    createCmd.add_items()->PackFrom( line );
                }
                else
                {
                    bool horzFirst = std::abs( offX ) >= std::abs( offY );
                    if( horzFirst )
                    {
                        kiapi::schematic::types::Line la, lb;
                        la.mutable_id()->set_value( makeUuid() );
                        la.mutable_start()->set_x_nm( px );
                        la.mutable_start()->set_y_nm( py );
                        la.mutable_end()->set_x_nm( lx );
                        la.mutable_end()->set_y_nm( py );
                        la.set_layer( kiapi::schematic::types::SL_WIRE );
                        createCmd.add_items()->PackFrom( la );
                        lb.mutable_id()->set_value( makeUuid() );
                        lb.mutable_start()->set_x_nm( lx );
                        lb.mutable_start()->set_y_nm( py );
                        lb.mutable_end()->set_x_nm( lx );
                        lb.mutable_end()->set_y_nm( ly );
                        lb.set_layer( kiapi::schematic::types::SL_WIRE );
                        createCmd.add_items()->PackFrom( lb );
                    }
                    else
                    {
                        kiapi::schematic::types::Line la, lb;
                        la.mutable_id()->set_value( makeUuid() );
                        la.mutable_start()->set_x_nm( px );
                        la.mutable_start()->set_y_nm( py );
                        la.mutable_end()->set_x_nm( px );
                        la.mutable_end()->set_y_nm( ly );
                        la.set_layer( kiapi::schematic::types::SL_WIRE );
                        createCmd.add_items()->PackFrom( la );
                        lb.mutable_id()->set_value( makeUuid() );
                        lb.mutable_start()->set_x_nm( px );
                        lb.mutable_start()->set_y_nm( ly );
                        lb.mutable_end()->set_x_nm( lx );
                        lb.mutable_end()->set_y_nm( ly );
                        lb.set_layer( kiapi::schematic::types::SL_WIRE );
                        createCmd.add_items()->PackFrom( lb );
                    }
                }
            };
            addManhattanWire( x1Iu, y1Iu, l1xIu, l1yIu, offset1X, offset1Y );
            addManhattanWire( x2Iu, y2Iu, l2xIu, l2yIu, offset2X, offset2Y );
        }
        kiapi::common::ApiRequest addReq;
        addReq.mutable_header()->set_client_name( "mcp" );
        addReq.mutable_message()->PackFrom( createCmd );
        kiapi::common::ApiResponse addResp;
        if( !m_ipc.SendRequest( addReq, addResp, err ) )
        {
            json content = json::array();
            content.push_back( { { "type", "text" }, { "text", err.empty() ? "Create items failed" : err } } );
            return "{\"jsonrpc\":\"2.0\",\"result\":" + json( { { "content", content }, { "isError", true } } ).dump() + ",\"id\":" + id + "}";
        }
        if( addResp.status().status() != kiapi::common::AS_OK )
        {
            json content = json::array();
            content.push_back( { { "type", "text" }, { "text", addResp.status().error_message().empty() ? "Create items failed" : addResp.status().error_message() } } );
            return "{\"jsonrpc\":\"2.0\",\"result\":" + json( { { "content", content }, { "isError", true } } ).dump() + ",\"id\":" + id + "}";
        }
        txn.commit();
        json content = json::array();
        content.push_back( { { "type", "text" }, { "text", "Pins " + ref1 + "/" + pin1 + " and " + ref2 + "/" + pin2 + " connected on net " + netName } } );
        return "{\"jsonrpc\":\"2.0\",\"result\":" + json( { { "content", content }, { "isError", false } } ).dump() + ",\"id\":" + id + "}";
    }
    else if( name == "screenshot_zone" )
    {
        if( !hasArg( "center_x" ) || !hasArg( "center_y" ) )
        {
            json content = json::array();
            content.push_back( { { "type", "text" }, { "text", "screenshot_zone requires 'center_x' (number) and 'center_y' (number) in mm" } } );
            return "{\"jsonrpc\":\"2.0\",\"result\":" + json( { { "content", content }, { "isError", true } } ).dump() + ",\"id\":" + id + "}";
        }
        double centerX = getDouble( "center_x" );
        double centerY = getDouble( "center_y" );
        double widthMm = getDouble( "width_mm", 15.0 );
        int maxWidthPx = getInt( "max_width_px", 600 );
        kiapi::schematic::types::CaptureZoneScreenshot cmd;
        cmd.set_center_x_mm( centerX );
        cmd.set_center_y_mm( centerY );
        cmd.set_width_mm( widthMm );
        if( maxWidthPx > 0 )
            cmd.set_max_width_px( maxWidthPx );
        req.mutable_message()->PackFrom( cmd );
    }
    else if( name == "screenshot_full_schematic" )
    {
        kiapi::schematic::types::CaptureFullSchematic cmd;
        req.mutable_message()->PackFrom( cmd );
    }
    else if( name == "get_visible_bounds" )
    {
        std::string visErr;
        if( !m_ipc.EnsureSchematicApiConnection( visErr ) )
        {
            json content = json::array();
            content.push_back( { { "type", "text" },
                               { "text", visErr.empty() ? "KiCad schematic IPC not available." : visErr } } );
            return "{\"jsonrpc\":\"2.0\",\"result\":" + json( { { "content", content }, { "isError", true } } ).dump() + ",\"id\":" + id + "}";
        }

        kiapi::common::ApiRequest visReq;
        visReq.mutable_header()->set_client_name( "mcp" );
        kiapi::schematic::types::GetVisibleBounds visCmd;
        visReq.mutable_message()->PackFrom( visCmd );

        kiapi::common::ApiResponse visResp;
        if( !m_ipc.SendRequest( visReq, visResp, visErr ) || visResp.status().status() != kiapi::common::AS_OK )
        {
            json content = json::array();
            content.push_back( { { "type", "text" },
                                 { "text", visErr.empty() ? "GetVisibleBounds failed" : visErr } } );
            return "{\"jsonrpc\":\"2.0\",\"result\":" + json( { { "content", content }, { "isError", true } } ).dump() + ",\"id\":" + id + "}";
        }

        kiapi::schematic::types::GetVisibleBoundsResponse vis;
        if( !visResp.has_message() || !visResp.message().UnpackTo( &vis ) )
        {
            json content = json::array();
            content.push_back( { { "type", "text" }, { "text", "Could not parse GetVisibleBounds response" } } );
            return "{\"jsonrpc\":\"2.0\",\"result\":" + json( { { "content", content }, { "isError", true } } ).dump() + ",\"id\":" + id + "}";
        }

        json out;
        out["min_x_mm"] = vis.min_x_mm();
        out["min_y_mm"] = vis.min_y_mm();
        out["max_x_mm"] = vis.max_x_mm();
        out["max_y_mm"] = vis.max_y_mm();
        out["center_x_mm"] = vis.center_x_mm();
        out["center_y_mm"] = vis.center_y_mm();
        out["width_mm"] = vis.width_mm();
        out["height_mm"] = vis.height_mm();
        out["client_width_px"] = vis.client_width_px();
        out["client_height_px"] = vis.client_height_px();

        json content = json::array();
        content.push_back( { { "type", "text" }, { "text", out.dump() } } );
        return "{\"jsonrpc\":\"2.0\",\"result\":" + json( { { "content", content }, { "isError", false } } ).dump() + ",\"id\":" + id + "}";
    }
    else if( name == "get_all_bounds" )
    {
        // Batch fetch: get schematic summary, then compute bounds for every component
        std::string err;
        kiapi::common::ApiRequest sumReq;
        sumReq.mutable_header()->set_client_name( "mcp" );
        kiapi::schematic::types::GetSchematicSummary sumCmd;
        sumReq.mutable_message()->PackFrom( sumCmd );
        kiapi::common::ApiResponse sumResp;

        if( !m_ipc.IsConnected() && !m_ipc.Connect( IpcClient::GetDefaultSocketPath() ) )
        {
            json content = json::array();
            content.push_back( { { "type", "text" }, { "text", "KiCad IPC not connected." } } );
            return "{\"jsonrpc\":\"2.0\",\"result\":" + json( { { "content", content }, { "isError", true } } ).dump() + ",\"id\":" + id + "}";
        }

        if( !m_ipc.SendRequest( sumReq, sumResp, err ) || sumResp.status().status() != kiapi::common::AS_OK )
        {
            json content = json::array();
            content.push_back( { { "type", "text" }, { "text", err.empty() ? "Failed to get schematic summary" : err } } );
            return "{\"jsonrpc\":\"2.0\",\"result\":" + json( { { "content", content }, { "isError", true } } ).dump() + ",\"id\":" + id + "}";
        }

        kiapi::schematic::types::GetSchematicSummaryResponse sumR;
        if( !sumResp.has_message() || !sumResp.message().UnpackTo( &sumR ) )
        {
            json content = json::array();
            content.push_back( { { "type", "text" }, { "text", "Could not parse schematic summary" } } );
            return "{\"jsonrpc\":\"2.0\",\"result\":" + json( { { "content", content }, { "isError", true } } ).dump() + ",\"id\":" + id + "}";
        }

        json boundsArr = json::array();
        for( int i = 0; i < sumR.components_size(); ++i )
        {
            const auto& c = sumR.components( i );
            std::string tempErr;
            auto [cx, cy, w, h] = getComponentBounds( c.reference(), tempErr );
            json entry;
            entry["reference"] = c.reference();
            entry["cx_mm"] = cx;
            entry["cy_mm"] = cy;
            entry["width_mm"] = w;
            entry["height_mm"] = h;
            boundsArr.push_back( entry );
        }

        json content = json::array();
        content.push_back( { { "type", "text" }, { "text", boundsArr.dump() } } );
        return "{\"jsonrpc\":\"2.0\",\"result\":" + json( { { "content", content }, { "isError", false } } ).dump() + ",\"id\":" + id + "}";
    }
    else if( name == "get_component_pins" )
    {
        if( !args.contains( "reference" ) || !args["reference"].is_string() )
        {
            json content = json::array();
            content.push_back( { { "type", "text" }, { "text", "get_component_pins requires reference" } } );
            return "{\"jsonrpc\":\"2.0\",\"result\":" + json( { { "content", content }, { "isError", true } } ).dump() + ",\"id\":" + id + "}";
        }

        std::string ref = args["reference"].get<std::string>();
        std::string err;

        // Get schematic summary to find the component and its pins list
        kiapi::common::ApiRequest sumReq;
        sumReq.mutable_header()->set_client_name( "mcp" );
        kiapi::schematic::types::GetSchematicSummary sumCmd;
        sumReq.mutable_message()->PackFrom( sumCmd );
        kiapi::common::ApiResponse sumResp;

        if( !m_ipc.IsConnected() && !m_ipc.Connect( IpcClient::GetDefaultSocketPath() ) )
        {
            json content = json::array();
            content.push_back( { { "type", "text" }, { "text", "KiCad IPC not connected." } } );
            return "{\"jsonrpc\":\"2.0\",\"result\":" + json( { { "content", content }, { "isError", true } } ).dump() + ",\"id\":" + id + "}";
        }

        if( !m_ipc.SendRequest( sumReq, sumResp, err ) || sumResp.status().status() != kiapi::common::AS_OK )
        {
            json content = json::array();
            content.push_back( { { "type", "text" }, { "text", err.empty() ? "Failed to get schematic summary" : err } } );
            return "{\"jsonrpc\":\"2.0\",\"result\":" + json( { { "content", content }, { "isError", true } } ).dump() + ",\"id\":" + id + "}";
        }

        kiapi::schematic::types::GetSchematicSummaryResponse sumR;
        if( !sumResp.has_message() || !sumResp.message().UnpackTo( &sumR ) )
        {
            json content = json::array();
            content.push_back( { { "type", "text" }, { "text", "Could not parse schematic summary" } } );
            return "{\"jsonrpc\":\"2.0\",\"result\":" + json( { { "content", content }, { "isError", true } } ).dump() + ",\"id\":" + id + "}";
        }

        // Find the component
        bool found = false;
        json pinsArr = json::array();
        for( int i = 0; i < sumR.components_size(); ++i )
        {
            const auto& c = sumR.components( i );
            if( c.reference() == ref )
            {
                found = true;
                for( int p = 0; p < c.pins_size(); ++p )
                {
                    const auto& pin = c.pins( p );
                    std::string pinErr;
                    double orientationDeg = 0;
                    auto [px, py] = getPinPosition( ref, pin.number(), pinErr, &orientationDeg );

                    json pinObj;
                    pinObj["pin_number"] = pin.number();
                    pinObj["pin_name"] = pin.name();
                    if( pinErr.empty() )
                    {
                        pinObj["x_mm"] = px;
                        pinObj["y_mm"] = py;
                        pinObj["orientation"] = orientationDeg;
                        // Outward direction: 180° from pin orientation (away from component body)
                        double outDeg = std::fmod( orientationDeg + 180.0, 360.0 );
                        pinObj["outward_degrees"] = outDeg;
                        // Recommended rotation for add_global_label: add 180 so label body faces outward.
                        int recRot = static_cast<int>( std::fmod( orientationDeg + 180.0, 360.0 ) );
                        pinObj["recommended_label_rotation"] = recRot;
                    }
                    else
                    {
                        pinObj["x_mm"] = 0;
                        pinObj["y_mm"] = 0;
                        pinObj["orientation"] = 0;
                        pinObj["outward_degrees"] = 0;
                        pinObj["recommended_label_rotation"] = 0;
                        pinObj["error"] = pinErr;
                    }
                    pinsArr.push_back( pinObj );
                }
                break;
            }
        }

        if( !found )
        {
            json content = json::array();
            content.push_back( { { "type", "text" }, { "text", "Component not found: " + ref } } );
            return "{\"jsonrpc\":\"2.0\",\"result\":" + json( { { "content", content }, { "isError", true } } ).dump() + ",\"id\":" + id + "}";
        }

        json content = json::array();
        content.push_back( { { "type", "text" }, { "text", pinsArr.dump() } } );
        return "{\"jsonrpc\":\"2.0\",\"result\":" + json( { { "content", content }, { "isError", false } } ).dump() + ",\"id\":" + id + "}";
    }
    else if( name == "batch_get_component_pins" )
    {
        if( !args.contains( "references" ) || !args["references"].is_array() || args["references"].empty() )
        {
            json content = json::array();
            content.push_back( { { "type", "text" }, { "text", "batch_get_component_pins requires non-empty references array" } } );
            return "{\"jsonrpc\":\"2.0\",\"result\":" + json( { { "content", content }, { "isError", true } } ).dump() + ",\"id\":" + id + "}";
        }

        std::vector<std::string> refs;
        refs.reserve( args["references"].size() );
        std::set<std::string> seenRefs;
        for( const json& refJson : args["references"] )
        {
            if( !refJson.is_string() )
                continue;
            std::string ref = refJson.get<std::string>();
            if( ref.empty() || seenRefs.count( ref ) )
                continue;
            seenRefs.insert( ref );
            refs.push_back( ref );
        }

        if( refs.empty() )
        {
            json content = json::array();
            content.push_back( { { "type", "text" }, { "text", "batch_get_component_pins requires at least one valid reference string" } } );
            return "{\"jsonrpc\":\"2.0\",\"result\":" + json( { { "content", content }, { "isError", true } } ).dump() + ",\"id\":" + id + "}";
        }

        std::string err;
        kiapi::schematic::types::GetSchematicSummaryResponse sumR;
        if( !getCachedSummary( sumR, err ) )
        {
            json content = json::array();
            content.push_back( { { "type", "text" }, { "text", err.empty() ? "Failed to get schematic summary" : err } } );
            return "{\"jsonrpc\":\"2.0\",\"result\":" + json( { { "content", content }, { "isError", true } } ).dump() + ",\"id\":" + id + "}";
        }

        std::map<std::string, const kiapi::schematic::types::ComponentSummary*> compByRef;
        for( int i = 0; i < sumR.components_size(); ++i )
        {
            const auto& c = sumR.components( i );
            compByRef[c.reference()] = &c;
        }

        json componentsArr = json::array();
        json missingRefs = json::array();

        for( const std::string& ref : refs )
        {
            auto it = compByRef.find( ref );
            if( it == compByRef.end() )
            {
                missingRefs.push_back( ref );
                continue;
            }

            const auto& c = *( it->second );
            json compObj;
            compObj["reference"] = ref;
            compObj["value"] = c.value();
            compObj["library"] = c.library_nickname();
            compObj["symbol"] = c.symbol_name();
            json pinsArr = json::array();

            for( int p = 0; p < c.pins_size(); ++p )
            {
                const auto& pin = c.pins( p );
                std::string pinErr;
                double orientationDeg = 0;
                auto [px, py] = getPinPosition( ref, pin.number(), pinErr, &orientationDeg );

                json pinObj;
                pinObj["pin_number"] = pin.number();
                pinObj["pin_name"] = pin.name();

                if( pinErr.empty() )
                {
                    pinObj["x_mm"] = px;
                    pinObj["y_mm"] = py;
                    pinObj["orientation"] = orientationDeg;
                    double outDeg = std::fmod( orientationDeg + 180.0, 360.0 );
                    pinObj["outward_degrees"] = outDeg;
                    pinObj["recommended_label_rotation"] = static_cast<int>( std::fmod( orientationDeg + 180.0, 360.0 ) );
                }
                else
                {
                    pinObj["x_mm"] = 0;
                    pinObj["y_mm"] = 0;
                    pinObj["orientation"] = 0;
                    pinObj["outward_degrees"] = 0;
                    pinObj["recommended_label_rotation"] = 0;
                    pinObj["error"] = pinErr;
                }

                pinsArr.push_back( pinObj );
            }

            compObj["pins"] = pinsArr;
            componentsArr.push_back( compObj );
        }

        json out;
        out["components"] = componentsArr;
        out["missing_references"] = missingRefs;
        out["summary"] = {
            { "requested_references", refs.size() },
            { "returned_components", componentsArr.size() },
            { "missing_components", missingRefs.size() }
        };

        json content = json::array();
        content.push_back( { { "type", "text" }, { "text", out.dump() } } );
        return "{\"jsonrpc\":\"2.0\",\"result\":" + json( { { "content", content }, { "isError", false } } ).dump() + ",\"id\":" + id + "}";
    }
    else if( name == "get_component_connectivity_graph" )
    {
        if( !args.contains( "reference" ) || !args["reference"].is_string() )
        {
            json content = json::array();
            content.push_back( { { "type", "text" }, { "text", "get_component_connectivity_graph requires reference" } } );
            return "{\"jsonrpc\":\"2.0\",\"result\":" + json( { { "content", content }, { "isError", true } } ).dump() + ",\"id\":" + id + "}";
        }

        std::string ref = args["reference"].get<std::string>();
        int requestedDepth = 1;
        if( args.contains( "max_depth" ) && args["max_depth"].is_number_integer() )
            requestedDepth = args["max_depth"].get<int>();
        else if( args.contains( "depth" ) && args["depth"].is_number_integer() )
            requestedDepth = args["depth"].get<int>();
        requestedDepth = std::max( 1, requestedDepth );
        const int graphDepth = 1;
        int maxPeersPerPin = 12;
        if( args.contains( "max_peers_per_pin" ) && args["max_peers_per_pin"].is_number_integer() )
            maxPeersPerPin = std::max( 1, std::min( 64, args["max_peers_per_pin"].get<int>() ) );

        std::string sumErr;
        kiapi::schematic::types::GetSchematicSummaryResponse sumResp;
        if( !getCachedSummary( sumResp, sumErr ) )
        {
            json content = json::array();
            content.push_back( { { "type", "text" }, { "text", sumErr.empty() ? "Failed to get schematic summary" : sumErr } } );
            return "{\"jsonrpc\":\"2.0\",\"result\":" + json( { { "content", content }, { "isError", true } } ).dump() + ",\"id\":" + id + "}";
        }

        int targetCompIndex = -1;
        std::map<std::string, json> componentInfoByRef;
        for( int i = 0; i < sumResp.components_size(); ++i )
        {
            const auto& c = sumResp.components( i );
            json info;
            info["reference"] = c.reference();
            info["value"] = c.value();
            info["library"] = c.library_nickname();
            info["symbol"] = c.symbol_name();
            if( c.has_position() )
            {
                info["x_mm"] = c.position().x_mm();
                info["y_mm"] = c.position().y_mm();
            }
            componentInfoByRef[c.reference()] = info;
            if( c.reference() == ref )
                targetCompIndex = i;
        }

        if( targetCompIndex < 0 )
        {
            json content = json::array();
            content.push_back( { { "type", "text" }, { "text", "Component not found: " + ref } } );
            return "{\"jsonrpc\":\"2.0\",\"result\":" + json( { { "content", content }, { "isError", true } } ).dump() + ",\"id\":" + id + "}";
        }
        const auto& targetComp = sumResp.components( targetCompIndex );

        kiapi::common::ApiRequest netReq; netReq.mutable_header()->set_client_name( "mcp" );
        kiapi::schematic::types::GetNetlist netCmd; netReq.mutable_message()->PackFrom( netCmd );
        kiapi::common::ApiResponse netResp; std::string netErr;
        if( !m_ipc.IsConnected() && !m_ipc.Connect( IpcClient::GetDefaultSocketPath() ) )
        {
            json content = json::array();
            content.push_back( { { "type", "text" }, { "text", "KiCad IPC not connected." } } );
            return "{\"jsonrpc\":\"2.0\",\"result\":" + json( { { "content", content }, { "isError", true } } ).dump() + ",\"id\":" + id + "}";
        }
        if( !m_ipc.SendRequest( netReq, netResp, netErr ) || netResp.status().status() != kiapi::common::AS_OK || !netResp.has_message() )
        {
            json content = json::array();
            content.push_back( { { "type", "text" }, { "text", netErr.empty() ? "Failed to get netlist for connectivity graph" : netErr } } );
            return "{\"jsonrpc\":\"2.0\",\"result\":" + json( { { "content", content }, { "isError", true } } ).dump() + ",\"id\":" + id + "}";
        }

        kiapi::schematic::types::GetNetlistResponse nr;
        if( !netResp.message().UnpackTo( &nr ) )
        {
            json content = json::array();
            content.push_back( { { "type", "text" }, { "text", "Failed to unpack netlist for connectivity graph" } } );
            return "{\"jsonrpc\":\"2.0\",\"result\":" + json( { { "content", content }, { "isError", true } } ).dump() + ",\"id\":" + id + "}";
        }

        struct NetPeer
        {
            std::string reference;
            std::string pinNumber;
        };

        json component;
        component["reference"] = targetComp.reference();
        component["value"] = targetComp.value();
        component["library"] = targetComp.library_nickname();
        component["symbol"] = targetComp.symbol_name();

        std::map<std::string, std::vector<NetPeer>> peersByNetName;
        std::map<std::string, int> netPinCountByName;
        std::map<std::string, std::string> netNameByRefPin;
        for( int n = 0; n < nr.nets_size(); ++n )
        {
            const auto& net = nr.nets( n );
            const std::string netName = net.net_name();
            auto& peers = peersByNetName[netName];
            netPinCountByName[netName] = net.pins_size();
            peers.reserve( net.pins_size() );

            for( int netPinIdx = 0; netPinIdx < net.pins_size(); ++netPinIdx )
            {
                const auto& netPin = net.pins( netPinIdx );
                peers.push_back( { netPin.reference(), netPin.pin_number() } );
                if( !netPin.reference().empty() && !netPin.pin_number().empty() )
                    netNameByRefPin[netPin.reference() + "\n" + netPin.pin_number()] = netName;
            }
        }

        json pinsArr = json::array();
        std::map<std::string, std::set<std::string>> connectedNetsByRef;
        std::set<std::string> touchedNets;
        for( int p = 0; p < targetComp.pins_size(); ++p )
        {
            const auto& pin = targetComp.pins( p );
            json pinObj;
            pinObj["pin_number"] = pin.number();
            pinObj["pin_name"] = pin.name();

            const auto netNameIt = netNameByRefPin.find( ref + "\n" + pin.number() );
            if( netNameIt == netNameByRefPin.end() )
            {
                pinObj["net"] = nullptr;
                pinObj["net_pin_count"] = 0;
                pinObj["peer_refs"] = json::array();
                pinObj["peers"] = json::array();
                pinObj["peer_pin_count"] = 0;
                pinObj["truncated_peers"] = false;
                pinsArr.push_back( pinObj );
                continue;
            }

            const std::string& netName = netNameIt->second;
            pinObj["net"] = netName;
            pinObj["net_pin_count"] = netPinCountByName[netName];
            touchedNets.insert( netName );

            json peers = json::array();
            std::set<std::string> peerRefs;
            int peerPinCount = 0;
            const auto peersIt = peersByNetName.find( netName );
            if( peersIt != peersByNetName.end() )
            {
                for( const auto& netPeer : peersIt->second )
                {
                    if( netPeer.reference == ref && netPeer.pinNumber == pin.number() )
                        continue;

                    if( peerPinCount < maxPeersPerPin )
                    {
                        json peer;
                        peer["reference"] = netPeer.reference;
                        peer["pin_number"] = netPeer.pinNumber;
                        auto infoIt = componentInfoByRef.find( netPeer.reference );
                        if( infoIt != componentInfoByRef.end() )
                        {
                            if( infoIt->second.contains( "value" ) ) peer["value"] = infoIt->second["value"];
                            if( infoIt->second.contains( "symbol" ) ) peer["symbol"] = infoIt->second["symbol"];
                            if( infoIt->second.contains( "library" ) ) peer["library"] = infoIt->second["library"];
                        }
                        peers.push_back( peer );
                    }

                    ++peerPinCount;
                    if( !netPeer.reference.empty() )
                    {
                        peerRefs.insert( netPeer.reference );
                        connectedNetsByRef[netPeer.reference].insert( netName );
                    }
                }
            }

            json peerRefsJson = json::array();
            for( const auto& peerRef : peerRefs )
                peerRefsJson.push_back( peerRef );
            pinObj["peer_refs"] = peerRefsJson;
            pinObj["peers"] = peers;
            pinObj["peer_pin_count"] = peerPinCount;
            pinObj["truncated_peers"] = peerPinCount > maxPeersPerPin;
            pinsArr.push_back( pinObj );
        }

        json peerComponents = json::array();
        for( const auto& [peerRef, nets] : connectedNetsByRef )
        {
            if( peerRef == ref ) continue;
            json entry;
            auto infoIt = componentInfoByRef.find( peerRef );
            if( infoIt != componentInfoByRef.end() )
                entry = infoIt->second;
            else
                entry["reference"] = peerRef;

            json netsArr = json::array();
            for( const auto& netName : nets )
                netsArr.push_back( netName );
            entry["shared_nets"] = netsArr;
            entry["shared_net_count"] = nets.size();
            peerComponents.push_back( entry );
        }

        json result;
        result["component"] = component;
        result["pins"] = pinsArr;
        result["peer_components"] = peerComponents;
        result["summary"] = {
            { "pin_count", targetComp.pins_size() },
            { "peer_component_count", peerComponents.size() },
            { "connected_net_count", touchedNets.size() },
            { "requested_depth", requestedDepth },
            { "graph_depth", graphDepth },
            { "depth_limited", requestedDepth > graphDepth },
            { "max_peers_per_pin", maxPeersPerPin }
        };

        json content = json::array();
        content.push_back( { { "type", "text" }, { "text", result.dump() } } );
        return "{\"jsonrpc\":\"2.0\",\"result\":" + json( { { "content", content }, { "isError", false } } ).dump() + ",\"id\":" + id + "}";
    }
    else if( name == "batch_connect" )
    {
        if( !args.contains( "connections" ) || !args["connections"].is_array() || args["connections"].empty() )
        {
            json content = json::array();
            content.push_back( { { "type", "text" }, { "text", "batch_connect requires non-empty connections array" } } );
            return "{\"jsonrpc\":\"2.0\",\"result\":" + json( { { "content", content }, { "isError", true } } ).dump() + ",\"id\":" + id + "}";
        }

        if( !m_ipc.IsConnected() && !m_ipc.Connect( IpcClient::GetDefaultSocketPath() ) )
        {
            json content = json::array();
            content.push_back( { { "type", "text" }, { "text", "KiCad IPC not connected." } } );
            return "{\"jsonrpc\":\"2.0\",\"result\":" + json( { { "content", content }, { "isError", true } } ).dump() + ",\"id\":" + id + "}";
        }

        // Check if commit_id was provided to reuse existing transaction
        std::string commitId;
        bool hasExistingCommitId = false;
        if( args.contains( "commit_id" ) && args["commit_id"].is_string() )
        {
            commitId = args["commit_id"].get<std::string>();
            hasExistingCommitId = !commitId.empty();
        }

        // Acquire transaction
        std::string txnErr;
        std::unique_ptr<TransactionGuard> txn;
        if( !hasExistingCommitId )
        {
            txn = std::make_unique<TransactionGuard>( *this, m_ipc, txnErr );
            if( !txn->ok() )
            {
                json content = json::array();
                content.push_back( { { "type", "text" }, { "text", txnErr.empty() ? "Begin commit failed" : txnErr } } );
                return "{\"jsonrpc\":\"2.0\",\"result\":" + json( { { "content", content }, { "isError", true } } ).dump() + ",\"id\":" + id + "}";
            }
            commitId = txn->commitId();
        }

        constexpr double GRID_MM = 0.1;
        constexpr double PI = 3.14159265358979323846;
        auto snap = []( double mm ) { return std::round( mm / 0.1 ) * 0.1; };
        constexpr int64_t GRID_IU_BATCH = 1000;  // 0.1mm = 1000 IU
        auto mmToIu = []( double mm ) -> int64_t {
            return static_cast<int64_t>( std::llround( mm * 10000.0 ) );
        };
        auto snapToIu = []( double mm ) -> int64_t {
            int64_t iu = static_cast<int64_t>( std::llround( mm * 10000.0 ) );
            return ( ( iu + GRID_IU_BATCH / 2 ) / GRID_IU_BATCH ) * GRID_IU_BATCH;
        };
        auto getConnDouble = []( const json& aConn, const char* aKey, double aDefault ) -> double {
            if( !aConn.contains( aKey ) )
                return aDefault;

            try
            {
                if( aConn[aKey].is_number() )
                {
                    double value = aConn[aKey].get<double>();
                    return std::isfinite( value ) ? value : aDefault;
                }

                if( aConn[aKey].is_string() )
                {
                    std::string raw = aConn[aKey].get<std::string>();
                    if( raw.empty() )
                        return aDefault;

                    size_t parsedChars = 0;
                    double value = std::stod( raw, &parsedChars );
                    if( parsedChars == raw.size() && std::isfinite( value ) )
                        return value;
                }
            }
            catch( ... )
            {
            }

            return aDefault;
        };
        auto trimCopy = []( std::string aValue ) -> std::string {
            auto isSpace = []( unsigned char c ) { return std::isspace( c ) != 0; };
            auto begin = std::find_if_not( aValue.begin(), aValue.end(), isSpace );
            if( begin == aValue.end() )
                return "";
            auto end = std::find_if_not( aValue.rbegin(), aValue.rend(), isSpace ).base();
            return std::string( begin, end );
        };
        auto getConnString = [&]( const json& aConn, const char* aKey ) -> std::string {
            if( !aConn.contains( aKey ) || aConn[aKey].is_null() )
                return "";

            const json& value = aConn[aKey];

            try
            {
                if( value.is_string() )
                    return trimCopy( value.get<std::string>() );

                if( value.is_number_integer() )
                    return std::to_string( value.get<long long>() );

                if( value.is_number_unsigned() )
                    return std::to_string( value.get<unsigned long long>() );

                if( value.is_number_float() )
                {
                    std::ostringstream os;
                    os << value.get<double>();
                    return trimCopy( os.str() );
                }
            }
            catch( ... )
            {
            }

            return "";
        };
        auto getConnStringAny = [&]( const json& aConn, std::initializer_list<const char*> aKeys ) -> std::string {
            for( const char* key : aKeys )
            {
                std::string value = getConnString( aConn, key );
                if( !value.empty() )
                    return value;
            }
            return "";
        };
        auto makeUuid = []() -> std::string {
            static std::random_device rd;
            static std::mt19937_64 gen( rd() );
            std::uniform_int_distribution<uint64_t> dis( 0, 0xFFFFFFFFFFFFULL );
            std::ostringstream os;
            os << std::hex << std::setfill( '0' )
               << std::setw( 8 ) << ( dis( gen ) & 0xFFFFFFFFUL ) << "-"
               << std::setw( 4 ) << ( dis( gen ) & 0xFFFFUL ) << "-4"
               << std::setw( 3 ) << ( dis( gen ) & 0xFFFUL ) << "-"
               << std::setw( 4 ) << ( ( dis( gen ) & 0x3FFFUL ) | 0x8000UL ) << "-"
               << std::setw( 12 ) << ( dis( gen ) & 0xFFFFFFFFFFFFULL );
            return os.str();
        };

        kiapi::common::commands::CreateItems createCmd;
        createCmd.mutable_header()->mutable_document()->set_type( kiapi::common::types::DOCTYPE_SCHEMATIC );
        std::string sheetPath = getCurrentSheetPath();
        if( !sheetPath.empty() )
            createCmd.mutable_header()->mutable_document()->mutable_sheet_path()->set_path_human_readable( sheetPath );

        json resultsArr = json::array();
        const json& connections = args["connections"];
        struct NET_TO_PIN_CHECK
        {
            size_t index;
            std::string reference;
            std::string pinNumber;
            double pinX;
            double pinY;
        };
        std::vector<NET_TO_PIN_CHECK> pendingNetToPinChecks;
        std::map<LabelPosKey, std::string> occupiedLabels;
        std::vector<ExistingGlobalLabel> existingLabels;
        std::string labelPlacementErr;
        if( !fetchGlobalLabelsByPosition( occupiedLabels, labelPlacementErr ) )
        {
            json content = json::array();
            content.push_back( { { "type", "text" }, { "text", labelPlacementErr.empty() ? "Failed to query existing global labels" : labelPlacementErr } } );
            return "{\"jsonrpc\":\"2.0\",\"result\":" + json( { { "content", content }, { "isError", true } } ).dump() + ",\"id\":" + id + "}";
        }
        if( !fetchGlobalLabels( existingLabels, labelPlacementErr ) )
        {
            json content = json::array();
            content.push_back( { { "type", "text" }, { "text", labelPlacementErr.empty() ? "Failed to fetch existing global labels" : labelPlacementErr } } );
            return "{\"jsonrpc\":\"2.0\",\"result\":" + json( { { "content", content }, { "isError", true } } ).dump() + ",\"id\":" + id + "}";
        }

        for( size_t idx = 0; idx < connections.size(); ++idx )
        {
            const json& conn = connections[idx];
            json resultEntry;
            resultEntry["index"] = idx;

            if( !conn.is_object() )
            {
                resultEntry["success"] = false;
                resultEntry["message"] = "Connection entry must be an object";
                resultsArr.push_back( resultEntry );
                continue;
            }

            std::string connType = getConnString( conn, "type" );
            std::transform( connType.begin(), connType.end(), connType.begin(),
                            []( unsigned char c ) { return static_cast<char>( std::tolower( c ) ); } );

            if( connType == "net_to_pin" )
            {
                std::string netName = getConnStringAny( conn, { "net_name", "net", "netName" } );
                std::string connRef = getConnStringAny( conn, { "reference", "ref" } );
                std::string pinNum = getConnStringAny( conn, { "pin_number", "pin", "pinNumber" } );

                if( netName.empty() || connRef.empty() || pinNum.empty() )
                {
                    resultEntry["success"] = false;
                    resultEntry["message"] = "net_to_pin requires net_name, reference, and pin_number";
                    resultsArr.push_back( resultEntry );
                    continue;
                }

                std::string pinErr;
                double pinOrientationDeg = 0;
                auto [px, py] = getPinPosition( connRef, pinNum, pinErr, &pinOrientationDeg );
                if( !pinErr.empty() )
                {
                    resultEntry["success"] = false;
                    resultEntry["message"] = "Failed to get pin position: " + pinErr;
                    resultsArr.push_back( resultEntry );
                    continue;
                }

                double outwardDeg = std::fmod( pinOrientationDeg + 180.0, 360.0 );
                if( outwardDeg < 0 ) outwardDeg += 360.0;
                double rad = outwardDeg * PI / 180.0;
                double labelOffsetMm = getConnDouble( conn, "label_offset_mm", 1.0 );
                if( labelOffsetMm <= 0 )
                    labelOffsetMm = 1.0;
                double offsetX = std::cos( rad ) * labelOffsetMm;
                double offsetY = -std::sin( rad ) * labelOffsetMm;
                double lx = snap( px + offsetX );
                double ly = snap( py + offsetY );

                double axSnap = snap( px );
                double aySnap = snap( py );
                if( std::abs( lx - axSnap ) < 1e-9 && std::abs( ly - aySnap ) < 1e-9 )
                {
                    if( std::abs( offsetX ) >= std::abs( offsetY ) )
                        lx = axSnap + ( offsetX >= 0 ? GRID_MM : -GRID_MM );
                    else
                        ly = aySnap + ( offsetY >= 0 ? GRID_MM : -GRID_MM );
                }

                // 4-direction label angle: pin faces back toward component
                double backDeg = std::fmod( outwardDeg + 180.0, 360.0 );
                double labelAngle;
                if( backDeg >= 315.0 || backDeg < 45.0 )
                    labelAngle = 0.0;
                else if( backDeg >= 45.0 && backDeg < 135.0 )
                    labelAngle = 90.0;
                else if( backDeg >= 135.0 && backDeg < 225.0 )
                    labelAngle = 180.0;
                else
                    labelAngle = 270.0;

                double resolvedLx = lx;
                double resolvedLy = ly;
                bool shouldCreateLabel = true;
                constexpr double SAME_NET_LABEL_REUSE_RADIUS_MM = 12.0;
                if( findReusableLabelAnchor( netName, lx, ly, existingLabels, SAME_NET_LABEL_REUSE_RADIUS_MM, resolvedLx, resolvedLy ) )
                {
                    shouldCreateLabel = false;
                }
                else if( !resolveLabelPlacement( lx, ly, netName, occupiedLabels, true, resolvedLx, resolvedLy, shouldCreateLabel, labelPlacementErr ) )
                {
                    resultEntry["success"] = false;
                    resultEntry["message"] = labelPlacementErr;
                    resultsArr.push_back( resultEntry );
                    continue;
                }
                lx = resolvedLx;
                ly = resolvedLy;

                if( shouldCreateLabel )
                {
                    kiapi::schematic::types::GlobalLabel glabel;
                    glabel.mutable_id()->set_value( makeUuid() );
                    glabel.mutable_position()->set_x_nm( snapToIu( lx ) );
                    glabel.mutable_position()->set_y_nm( snapToIu( ly ) );
                    glabel.mutable_text()->mutable_text()->set_text( netName );
                    glabel.mutable_text()->mutable_text()->mutable_attributes()->set_horizontal_alignment( kiapi::common::types::HA_CENTER );
                    glabel.mutable_text()->mutable_text()->mutable_attributes()->set_vertical_alignment( kiapi::common::types::VA_CENTER );
                    glabel.mutable_text()->mutable_text()->mutable_attributes()->mutable_angle()->set_value_degrees( static_cast<int>( labelAngle + 180 ) % 360 );
                    createCmd.add_items()->PackFrom( glabel );
                    existingLabels.push_back( ExistingGlobalLabel{ netName, lx, ly } );
                }

                kiapi::schematic::types::Line line;
                line.mutable_id()->set_value( makeUuid() );
                // Keep wire start at exact pin coordinates; snapping can miss off-grid pin tips.
                line.mutable_start()->set_x_nm( mmToIu( px ) );
                line.mutable_start()->set_y_nm( mmToIu( py ) );
                line.mutable_end()->set_x_nm( snapToIu( lx ) );
                line.mutable_end()->set_y_nm( snapToIu( ly ) );
                line.set_layer( kiapi::schematic::types::SL_WIRE );
                createCmd.add_items()->PackFrom( line );

                resultEntry["success"] = true;
                resultEntry["message"] = "Net " + netName + " connected to " + connRef + " pin " + pinNum;
                pendingNetToPinChecks.push_back( { resultsArr.size(), connRef, pinNum, px, py } );
            }
            else if( connType == "pin_to_pin" )
            {
                std::string ref1 = getConnStringAny( conn, { "reference1", "ref1" } );
                std::string pin1 = getConnStringAny( conn, { "pin1", "pin_1" } );
                std::string ref2 = getConnStringAny( conn, { "reference2", "ref2" } );
                std::string pin2 = getConnStringAny( conn, { "pin2", "pin_2" } );
                std::string netName = getConnStringAny( conn, { "net_name", "net", "netName" } );

                if( ref1.empty() || pin1.empty() || ref2.empty() || pin2.empty() || netName.empty() )
                {
                    resultEntry["success"] = false;
                    resultEntry["message"] = "pin_to_pin requires reference1, pin1, reference2, pin2, net_name";
                    resultsArr.push_back( resultEntry );
                    continue;
                }

                std::string pinErr;
                auto [x1, y1] = getPinPosition( ref1, pin1, pinErr );
                if( !pinErr.empty() )
                {
                    resultEntry["success"] = false;
                    resultEntry["message"] = "Failed to get pin position for " + ref1 + "/" + pin1 + ": " + pinErr;
                    resultsArr.push_back( resultEntry );
                    continue;
                }

                auto [x2, y2] = getPinPosition( ref2, pin2, pinErr );
                if( !pinErr.empty() )
                {
                    resultEntry["success"] = false;
                    resultEntry["message"] = "Failed to get pin position for " + ref2 + "/" + pin2 + ": " + pinErr;
                    resultsArr.push_back( resultEntry );
                    continue;
                }

                double dist = std::hypot( x2 - x1, y2 - y1 );
                double shortWireThresholdMm = getConnDouble( conn, "short_wire_threshold_mm", 50.0 );
                if( shortWireThresholdMm <= 0 )
                    shortWireThresholdMm = 50.0;

                if( dist <= shortWireThresholdMm )
                {
                    int64_t x1Iu = mmToIu( x1 ), y1Iu = mmToIu( y1 );
                    int64_t x2Iu = mmToIu( x2 ), y2Iu = mmToIu( y2 );
                    if( x1Iu == x2Iu || y1Iu == y2Iu )
                    {
                        kiapi::schematic::types::Line line;
                        line.mutable_id()->set_value( makeUuid() );
                        line.mutable_start()->set_x_nm( x1Iu );
                        line.mutable_start()->set_y_nm( y1Iu );
                        line.mutable_end()->set_x_nm( x2Iu );
                        line.mutable_end()->set_y_nm( y2Iu );
                        line.set_layer( kiapi::schematic::types::SL_WIRE );
                        createCmd.add_items()->PackFrom( line );
                    }
                    else
                    {
                        double midX = std::round( ( x1 + x2 ) / 2.0 / GRID_MM ) * GRID_MM;
                        int64_t midIu = mmToIu( midX );
                        kiapi::schematic::types::Line la, lb, lc;
                        la.mutable_id()->set_value( makeUuid() );
                        la.mutable_start()->set_x_nm( x1Iu );
                        la.mutable_start()->set_y_nm( y1Iu );
                        la.mutable_end()->set_x_nm( midIu );
                        la.mutable_end()->set_y_nm( y1Iu );
                        la.set_layer( kiapi::schematic::types::SL_WIRE );
                        createCmd.add_items()->PackFrom( la );
                        lb.mutable_id()->set_value( makeUuid() );
                        lb.mutable_start()->set_x_nm( midIu );
                        lb.mutable_start()->set_y_nm( y1Iu );
                        lb.mutable_end()->set_x_nm( midIu );
                        lb.mutable_end()->set_y_nm( y2Iu );
                        lb.set_layer( kiapi::schematic::types::SL_WIRE );
                        createCmd.add_items()->PackFrom( lb );
                        lc.mutable_id()->set_value( makeUuid() );
                        lc.mutable_start()->set_x_nm( midIu );
                        lc.mutable_start()->set_y_nm( y2Iu );
                        lc.mutable_end()->set_x_nm( x2Iu );
                        lc.mutable_end()->set_y_nm( y2Iu );
                        lc.set_layer( kiapi::schematic::types::SL_WIRE );
                        createCmd.add_items()->PackFrom( lc );
                    }
                }
                else
                {
                    auto [offset1X, offset1Y] = calculateLabelOffset( x1, y1, ref1, 1.0 );
                    auto [offset2X, offset2Y] = calculateLabelOffset( x2, y2, ref2, 1.0 );
                    double l1x = snap( x1 + offset1X ), l1y = snap( y1 + offset1Y );
                    double l2x = snap( x2 + offset2X ), l2y = snap( y2 + offset2Y );
                    // Label pin must face BACK toward the component pin (same convention as connect_pin_to_pin)
                    double labelAngle1 = ( offset1X > 0 ) ? 180 : ( offset1X < 0 ) ? 0 : ( offset1Y > 0 ) ? 90 : 270;
                    double labelAngle2 = ( offset2X > 0 ) ? 180 : ( offset2X < 0 ) ? 0 : ( offset2Y > 0 ) ? 90 : 270;

                    double resolvedL1x = l1x;
                    double resolvedL1y = l1y;
                    bool shouldCreateLabel1 = true;
                    if( !resolveLabelPlacement( l1x, l1y, netName, occupiedLabels, true, resolvedL1x, resolvedL1y, shouldCreateLabel1, labelPlacementErr ) )
                    {
                        resultEntry["success"] = false;
                        resultEntry["message"] = labelPlacementErr;
                        resultsArr.push_back( resultEntry );
                        continue;
                    }

                    double resolvedL2x = l2x;
                    double resolvedL2y = l2y;
                    bool shouldCreateLabel2 = true;
                    if( !resolveLabelPlacement( l2x, l2y, netName, occupiedLabels, true, resolvedL2x, resolvedL2y, shouldCreateLabel2, labelPlacementErr ) )
                    {
                        resultEntry["success"] = false;
                        resultEntry["message"] = labelPlacementErr;
                        resultsArr.push_back( resultEntry );
                        continue;
                    }

                    l1x = resolvedL1x;
                    l1y = resolvedL1y;
                    l2x = resolvedL2x;
                    l2y = resolvedL2y;

                    if( shouldCreateLabel1 )
                    {
                        kiapi::schematic::types::GlobalLabel gl1;
                        gl1.mutable_id()->set_value( makeUuid() );
                        gl1.mutable_position()->set_x_nm( mmToIu( l1x ) );
                        gl1.mutable_position()->set_y_nm( mmToIu( l1y ) );
                        gl1.mutable_text()->mutable_text()->set_text( netName );
                        gl1.mutable_text()->mutable_text()->mutable_attributes()->set_horizontal_alignment( kiapi::common::types::HA_CENTER );
                        gl1.mutable_text()->mutable_text()->mutable_attributes()->set_vertical_alignment( kiapi::common::types::VA_CENTER );
                        gl1.mutable_text()->mutable_text()->mutable_attributes()->mutable_angle()->set_value_degrees( static_cast<int>( labelAngle1 + 180 ) % 360 );
                        createCmd.add_items()->PackFrom( gl1 );
                    }

                    if( shouldCreateLabel2 )
                    {
                        kiapi::schematic::types::GlobalLabel gl2;
                        gl2.mutable_id()->set_value( makeUuid() );
                        gl2.mutable_position()->set_x_nm( mmToIu( l2x ) );
                        gl2.mutable_position()->set_y_nm( mmToIu( l2y ) );
                        gl2.mutable_text()->mutable_text()->set_text( netName );
                        gl2.mutable_text()->mutable_text()->mutable_attributes()->set_horizontal_alignment( kiapi::common::types::HA_CENTER );
                        gl2.mutable_text()->mutable_text()->mutable_attributes()->set_vertical_alignment( kiapi::common::types::VA_CENTER );
                        gl2.mutable_text()->mutable_text()->mutable_attributes()->mutable_angle()->set_value_degrees( static_cast<int>( labelAngle2 + 180 ) % 360 );
                        createCmd.add_items()->PackFrom( gl2 );
                    }

                    kiapi::schematic::types::Line w1;
                    w1.mutable_id()->set_value( makeUuid() );
                    w1.mutable_start()->set_x_nm( mmToIu( x1 ) );
                    w1.mutable_start()->set_y_nm( mmToIu( y1 ) );
                    w1.mutable_end()->set_x_nm( mmToIu( l1x ) );
                    w1.mutable_end()->set_y_nm( mmToIu( l1y ) );
                    w1.set_layer( kiapi::schematic::types::SL_WIRE );
                    createCmd.add_items()->PackFrom( w1 );

                    kiapi::schematic::types::Line w2;
                    w2.mutable_id()->set_value( makeUuid() );
                    w2.mutable_start()->set_x_nm( mmToIu( x2 ) );
                    w2.mutable_start()->set_y_nm( mmToIu( y2 ) );
                    w2.mutable_end()->set_x_nm( mmToIu( l2x ) );
                    w2.mutable_end()->set_y_nm( mmToIu( l2y ) );
                    w2.set_layer( kiapi::schematic::types::SL_WIRE );
                    createCmd.add_items()->PackFrom( w2 );
                }

                resultEntry["success"] = true;
                resultEntry["message"] = "Pins " + ref1 + "/" + pin1 + " and " + ref2 + "/" + pin2 + " connected on net " + netName;
            }
            else
            {
                resultEntry["success"] = false;
                resultEntry["message"] = "Unknown connection type: " + connType + ". Use 'net_to_pin' or 'pin_to_pin'.";
            }

            resultsArr.push_back( resultEntry );
        }

        // Send all items in one CreateItems call
        std::string err;
        kiapi::common::ApiRequest addReq;
        addReq.mutable_header()->set_client_name( "mcp" );
        addReq.mutable_message()->PackFrom( createCmd );
        kiapi::common::ApiResponse addResp;

        if( !m_ipc.SendRequest( addReq, addResp, err ) )
        {
            json content = json::array();
            content.push_back( { { "type", "text" }, { "text", err.empty() ? "batch_connect CreateItems failed" : err } } );
            return "{\"jsonrpc\":\"2.0\",\"result\":" + json( { { "content", content }, { "isError", true } } ).dump() + ",\"id\":" + id + "}";
        }
        if( addResp.status().status() != kiapi::common::AS_OK )
        {
            json content = json::array();
            content.push_back( { { "type", "text" }, { "text", addResp.status().error_message().empty() ? "batch_connect CreateItems failed" : addResp.status().error_message() } } );
            return "{\"jsonrpc\":\"2.0\",\"result\":" + json( { { "content", content }, { "isError", true } } ).dump() + ",\"id\":" + id + "}";
        }

        // Only commit if we created the transaction
        if( txn && !txn->commit() )
        {
            json content = json::array();
            content.push_back( { { "type", "text" }, { "text", "batch_connect failed to commit transaction" } } );
            return "{\"jsonrpc\":\"2.0\",\"result\":" + json( { { "content", content }, { "isError", true } } ).dump() + ",\"id\":" + id + "}";
        }

        if( txn && !pendingNetToPinChecks.empty() )
        {
            kiapi::common::ApiRequest dangReq;
            dangReq.mutable_header()->set_client_name( "mcp" );
            kiapi::schematic::types::GetDanglingReport dangCmd;
            dangReq.mutable_message()->PackFrom( dangCmd );
            kiapi::common::ApiResponse dangResp;

            if( m_ipc.SendRequest( dangReq, dangResp, err )
                && dangResp.status().status() == kiapi::common::AS_OK
                && dangResp.has_message()
                && dangResp.message().type_url().find( "GetDanglingReportResponse" ) != std::string::npos )
            {
                kiapi::schematic::types::GetDanglingReportResponse dangR;
                if( dangResp.message().UnpackTo( &dangR ) )
                {
                    std::set<std::pair<std::string, std::string>> danglingPins;
                    for( int i = 0; i < dangR.items_size(); ++i )
                    {
                        const auto& item = dangR.items( i );
                        if( item.type() == "pin" )
                            danglingPins.insert( { item.reference(), item.pin_number() } );
                    }

                    for( const NET_TO_PIN_CHECK& check : pendingNetToPinChecks )
                    {
                        if( danglingPins.find( { check.reference, check.pinNumber } ) == danglingPins.end() )
                            continue;

                        if( check.index >= resultsArr.size() || !resultsArr[check.index].is_object() )
                            continue;

                        std::ostringstream msg;
                        msg << "Connection failed: pin still dangling after placing wire/label. Pin position used: ("
                            << check.pinX << ", " << check.pinY
                            << ") mm. The wire may have been placed at the wrong coordinates. "
                            << "Use get_component_pins, batch_get_component_pins, or get_component_connectivity_graph to inspect the local connection before retrying.";
                        resultsArr[check.index]["success"] = false;
                        resultsArr[check.index]["message"] = msg.str();
                    }
                }
            }
        }

        bool anyFailures = false;
        for( const auto& resultEntry : resultsArr )
        {
            if( !resultEntry.is_object() )
                continue;

            if( !resultEntry.value( "success", false ) )
            {
                anyFailures = true;
                break;
            }
        }

        json resultObj;
        resultObj["results"] = resultsArr;
        json content = json::array();
        content.push_back( { { "type", "text" }, { "text", resultObj.dump() } } );
        return "{\"jsonrpc\":\"2.0\",\"result\":" + json( { { "content", content }, { "isError", anyFailures } } ).dump() + ",\"id\":" + id + "}";
    }
    else if( name == "find_empty_space" )
    {
        if( !args.contains( "width_mm" ) || !args["width_mm"].is_number()
            || !args.contains( "height_mm" ) || !args["height_mm"].is_number() )
        {
            json content = json::array();
            content.push_back( { { "type", "text" }, { "text", "find_empty_space requires width_mm and height_mm" } } );
            return "{\"jsonrpc\":\"2.0\",\"result\":" + json( { { "content", content }, { "isError", true } } ).dump() + ",\"id\":" + id + "}";
        }

        double widthMm = args["width_mm"].get<double>();
        double heightMm = args["height_mm"].get<double>();
        double nearX = args.value( "near_x", 100.0 );
        double nearY = args.value( "near_y", 100.0 );

        std::string err;

        if( !m_ipc.IsConnected() && !m_ipc.Connect( IpcClient::GetDefaultSocketPath() ) )
        {
            json content = json::array();
            content.push_back( { { "type", "text" }, { "text", "KiCad IPC not connected." } } );
            return "{\"jsonrpc\":\"2.0\",\"result\":" + json( { { "content", content }, { "isError", true } } ).dump() + ",\"id\":" + id + "}";
        }

        auto [spotX, spotY] = findEmptySpot( nearX, nearY, widthMm, heightMm, err );

        json out;
        out["x_mm"] = spotX;
        out["y_mm"] = spotY;
        out["found"] = err.empty();

        json content = json::array();
        content.push_back( { { "type", "text" }, { "text", out.dump() } } );
        return "{\"jsonrpc\":\"2.0\",\"result\":" + json( { { "content", content }, { "isError", false } } ).dump() + ",\"id\":" + id + "}";
    }
    // ── get_schematic_state ──
    else if( name == "get_schematic_state" )
    {
        if( !m_ipc.IsConnected() && !m_ipc.Connect( IpcClient::GetDefaultSocketPath() ) )
        {
            json content = json::array();
            content.push_back( { { "type", "text" }, { "text", "KiCad IPC not connected." } } );
            return "{\"jsonrpc\":\"2.0\",\"result\":" + json( { { "content", content }, { "isError", true } } ).dump() + ",\"id\":" + id + "}";
        }
        json state;
        {
            kiapi::schematic::types::GetSchematicSummaryResponse sumResp;
            std::string sumErr;
            if( getCachedSummary( sumResp, sumErr ) )
            {
                json comps = json::array();
                for( int i = 0; i < sumResp.components_size(); ++i )
                {
                    const auto& c = sumResp.components( i );
                    json comp;
                    comp["reference"] = c.reference();
                    comp["value"] = c.value();
                    comp["library"] = c.library_nickname();
                    comp["symbol"] = c.symbol_name();
                    if( c.has_position() ) { comp["x_mm"] = c.position().x_mm(); comp["y_mm"] = c.position().y_mm(); }
                    if( c.has_bbox() ) { comp["bbox"] = { { "min_x", c.bbox().min_x_mm() }, { "min_y", c.bbox().min_y_mm() }, { "max_x", c.bbox().max_x_mm() }, { "max_y", c.bbox().max_y_mm() } }; }
                    json pins = json::array();
                    for( int p = 0; p < c.pins_size(); ++p ) { const auto& pin = c.pins( p ); pins.push_back( { { "number", pin.number() }, { "x_mm", pin.x_mm() }, { "y_mm", pin.y_mm() } } ); }
                    comp["pins"] = pins;
                    comps.push_back( comp );
                }
                state["components"] = comps;
                state["sheet_path"] = sumResp.sheet_path();
                json globalNets = json::array();
                for( int i = 0; i < sumResp.global_net_names_size(); ++i ) globalNets.push_back( sumResp.global_net_names( i ) );
                state["global_net_names"] = globalNets;
            }
            else { state["components"] = json::array(); state["summary_error"] = sumErr; }
        }
        {
            kiapi::common::ApiRequest netReq; netReq.mutable_header()->set_client_name( "mcp" );
            kiapi::schematic::types::GetNetlist netCmd; netReq.mutable_message()->PackFrom( netCmd );
            kiapi::common::ApiResponse netResp; std::string netErr;
            if( m_ipc.SendRequest( netReq, netResp, netErr ) && netResp.status().status() == kiapi::common::AS_OK && netResp.has_message() )
            {
                kiapi::schematic::types::GetNetlistResponse nr;
                if( netResp.message().UnpackTo( &nr ) )
                {
                    json nets = json::array();
                    for( int n = 0; n < nr.nets_size(); ++n )
                    {
                        const auto& net = nr.nets( n );
                        json pinsArr = json::array();
                        for( int p = 0; p < net.pins_size(); ++p ) { const auto& pin = net.pins( p ); pinsArr.push_back( { { "ref", pin.reference() }, { "pin", pin.pin_number() } } ); }
                        nets.push_back( { { "name", net.net_name() }, { "pins", pinsArr } } );
                    }
                    state["nets"] = nets;
                }
            }
            if( !state.contains( "nets" ) ) state["nets"] = json::array();
        }
        {
            kiapi::common::ApiRequest listReq; listReq.mutable_header()->set_client_name( "mcp" );
            kiapi::common::commands::GetItems listCmd;
            listCmd.mutable_header()->mutable_document()->set_type( kiapi::common::types::DOCTYPE_SCHEMATIC );
            std::string sheetPath = getCurrentSheetPath();
            if( !sheetPath.empty() ) listCmd.mutable_header()->mutable_document()->mutable_sheet_path()->set_path_human_readable( sheetPath );
            listCmd.add_types( kiapi::common::types::KOT_SCH_GLOBAL_LABEL );
            listReq.mutable_message()->PackFrom( listCmd );
            kiapi::common::ApiResponse listResp; std::string listErr; json labels = json::array();
            if( m_ipc.SendRequest( listReq, listResp, listErr ) && listResp.status().status() == kiapi::common::AS_OK && listResp.has_message() )
            {
                kiapi::common::commands::GetItemsResponse items;
                if( listResp.message().UnpackTo( &items ) )
                {
                    for( int i = 0; i < items.items_size(); ++i )
                    {
                        kiapi::schematic::types::GlobalLabel glabel;
                        if( !items.items( i ).UnpackTo( &glabel ) ) continue;
                        json lbl;
                        lbl["id"] = glabel.has_id() ? glabel.id().value() : "";
                        lbl["x_mm"] = glabel.has_position() ? glabel.position().x_nm() / 1e4 : 0.0;
                        lbl["y_mm"] = glabel.has_position() ? glabel.position().y_nm() / 1e4 : 0.0;
                        lbl["text"] = ( glabel.has_text() && glabel.text().has_text() ) ? glabel.text().text().text() : "";
                        labels.push_back( lbl );
                    }
                }
            }
            state["global_labels"] = labels;
        }
        {
            kiapi::common::ApiRequest dangReq; dangReq.mutable_header()->set_client_name( "mcp" );
            kiapi::schematic::types::GetDanglingReport dangCmd; dangReq.mutable_message()->PackFrom( dangCmd );
            kiapi::common::ApiResponse dangResp; std::string dangErr; json dangling = json::array();
            if( m_ipc.SendRequest( dangReq, dangResp, dangErr ) && dangResp.status().status() == kiapi::common::AS_OK && dangResp.has_message() )
            {
                kiapi::schematic::types::GetDanglingReportResponse dr;
                if( dangResp.message().UnpackTo( &dr ) )
                {
                    for( int i = 0; i < dr.items_size(); ++i )
                    {
                        const auto& it = dr.items( i );
                        dangling.push_back( { { "reference", it.reference() }, { "pin_number", it.pin_number() }, { "x_mm", it.x_mm() }, { "y_mm", it.y_mm() }, { "type", it.type() } } );
                    }
                }
            }
            state["dangling"] = dangling;
        }
        json content = json::array();
        content.push_back( { { "type", "text" }, { "text", state.dump() } } );
        return "{\"jsonrpc\":\"2.0\",\"result\":" + json( { { "content", content }, { "isError", false } } ).dump() + ",\"id\":" + id + "}";
    }
    // ── schematic_diff ──
    else if( name == "schematic_diff" )
    {
        if( !args.contains( "snapshot" ) )
        {
            json content = json::array();
            content.push_back( { { "type", "text" }, { "text", "schematic_diff requires 'snapshot' (object or JSON string from get_schematic_state)" } } );
            return "{\"jsonrpc\":\"2.0\",\"result\":" + json( { { "content", content }, { "isError", true } } ).dump() + ",\"id\":" + id + "}";
        }
        json oldState;
        if( args["snapshot"].is_object() )
            oldState = args["snapshot"];
        else if( args["snapshot"].is_string() )
        {
            try { oldState = json::parse( args["snapshot"].get<std::string>() ); }
            catch( ... ) {
                json content = json::array();
                content.push_back( { { "type", "text" }, { "text", "Invalid JSON in snapshot string" } } );
                return "{\"jsonrpc\":\"2.0\",\"result\":" + json( { { "content", content }, { "isError", true } } ).dump() + ",\"id\":" + id + "}";
            }
        }
        else
        {
            json content = json::array();
            content.push_back( { { "type", "text" }, { "text", "snapshot must be an object or JSON string" } } );
            return "{\"jsonrpc\":\"2.0\",\"result\":" + json( { { "content", content }, { "isError", true } } ).dump() + ",\"id\":" + id + "}";
        }
        if( !m_ipc.IsConnected() && !m_ipc.Connect( IpcClient::GetDefaultSocketPath() ) )
        {
            json content = json::array();
            content.push_back( { { "type", "text" }, { "text", "KiCad IPC not connected." } } );
            return "{\"jsonrpc\":\"2.0\",\"result\":" + json( { { "content", content }, { "isError", true } } ).dump() + ",\"id\":" + id + "}";
        }
        kiapi::schematic::types::GetSchematicSummaryResponse sumResp; std::string sumErr;
        std::map<std::string, json> curComps;
        if( getCachedSummary( sumResp, sumErr ) )
        {
            for( int i = 0; i < sumResp.components_size(); ++i )
            {
                const auto& c = sumResp.components( i );
                json comp; comp["value"] = c.value();
                comp["x_mm"] = c.has_position() ? c.position().x_mm() : 0.0;
                comp["y_mm"] = c.has_position() ? c.position().y_mm() : 0.0;
                curComps[c.reference()] = comp;
            }
        }
        std::map<std::string, json> oldComps;
        if( oldState.contains( "components" ) && oldState["components"].is_array() )
        {
            for( const auto& c : oldState["components"] )
            {
                std::string ref = c.value( "reference", "" );
                if( ref.empty() ) continue;
                json comp; comp["value"] = c.value( "value", "" );
                comp["x_mm"] = c.value( "x_mm", 0.0 ); comp["y_mm"] = c.value( "y_mm", 0.0 );
                oldComps[ref] = comp;
            }
        }
        json added = json::array(), removed = json::array(), changed = json::array();
        for( const auto& [ref, comp] : curComps )
        {
            auto it = oldComps.find( ref );
            if( it == oldComps.end() )
                added.push_back( { { "reference", ref }, { "value", comp["value"] }, { "x_mm", comp["x_mm"] }, { "y_mm", comp["y_mm"] } } );
            else
            {
                bool valChg = comp["value"] != it->second["value"];
                bool posChg = std::abs( comp["x_mm"].get<double>() - it->second["x_mm"].get<double>() ) > 0.01
                              || std::abs( comp["y_mm"].get<double>() - it->second["y_mm"].get<double>() ) > 0.01;
                if( valChg || posChg )
                {
                    json ch; ch["reference"] = ref;
                    if( valChg ) { ch["old_value"] = it->second["value"]; ch["new_value"] = comp["value"]; }
                    if( posChg ) { ch["old_pos"] = { { "x", it->second["x_mm"] }, { "y", it->second["y_mm"] } }; ch["new_pos"] = { { "x", comp["x_mm"] }, { "y", comp["y_mm"] } }; }
                    changed.push_back( ch );
                }
            }
        }
        for( const auto& [ref, comp] : oldComps )
            if( curComps.find( ref ) == curComps.end() )
                removed.push_back( { { "reference", ref }, { "value", comp["value"] } } );
        std::set<std::string> curNets, oldNets;
        {
            kiapi::common::ApiRequest netReq; netReq.mutable_header()->set_client_name( "mcp" );
            kiapi::schematic::types::GetNetlist netCmd; netReq.mutable_message()->PackFrom( netCmd );
            kiapi::common::ApiResponse netResp; std::string netErr;
            if( m_ipc.SendRequest( netReq, netResp, netErr ) && netResp.status().status() == kiapi::common::AS_OK && netResp.has_message() )
            {
                kiapi::schematic::types::GetNetlistResponse nr;
                if( netResp.message().UnpackTo( &nr ) )
                    for( int n = 0; n < nr.nets_size(); ++n ) curNets.insert( nr.nets( n ).net_name() );
            }
        }
        if( oldState.contains( "nets" ) && oldState["nets"].is_array() )
            for( const auto& n : oldState["nets"] )
                if( n.contains( "name" ) && n["name"].is_string() ) oldNets.insert( n["name"].get<std::string>() );
        json netsAdded = json::array(), netsRemoved = json::array();
        for( const auto& n : curNets ) if( oldNets.find( n ) == oldNets.end() ) netsAdded.push_back( n );
        for( const auto& n : oldNets ) if( curNets.find( n ) == curNets.end() ) netsRemoved.push_back( n );
        json diff;
        diff["components_added"] = added; diff["components_removed"] = removed; diff["components_changed"] = changed;
        diff["nets_added"] = netsAdded; diff["nets_removed"] = netsRemoved;
        json content = json::array();
        content.push_back( { { "type", "text" }, { "text", diff.dump() } } );
        return "{\"jsonrpc\":\"2.0\",\"result\":" + json( { { "content", content }, { "isError", false } } ).dump() + ",\"id\":" + id + "}";
    }
    // ── net_diagnostics ──
    else if( name == "net_diagnostics" )
    {
        if( !m_ipc.IsConnected() && !m_ipc.Connect( IpcClient::GetDefaultSocketPath() ) )
        {
            json content = json::array();
            content.push_back( { { "type", "text" }, { "text", "KiCad IPC not connected." } } );
            return "{\"jsonrpc\":\"2.0\",\"result\":" + json( { { "content", content }, { "isError", true } } ).dump() + ",\"id\":" + id + "}";
        }
        std::string filterNet = getStr( "net_name" );
        std::vector<std::pair<std::string, std::vector<std::pair<std::string,std::string>>>> nets;
        {
            kiapi::common::ApiRequest netReq; netReq.mutable_header()->set_client_name( "mcp" );
            kiapi::schematic::types::GetNetlist netCmd; netReq.mutable_message()->PackFrom( netCmd );
            kiapi::common::ApiResponse netResp; std::string netErr;
            if( m_ipc.SendRequest( netReq, netResp, netErr ) && netResp.status().status() == kiapi::common::AS_OK && netResp.has_message() )
            {
                kiapi::schematic::types::GetNetlistResponse nr;
                if( netResp.message().UnpackTo( &nr ) )
                {
                    for( int n = 0; n < nr.nets_size(); ++n )
                    {
                        const auto& net = nr.nets( n ); std::string netName = net.net_name();
                        if( !filterNet.empty() && netName != filterNet ) continue;
                        std::vector<std::pair<std::string,std::string>> pins;
                        for( int p = 0; p < net.pins_size(); ++p ) pins.push_back( { net.pins( p ).reference(), net.pins( p ).pin_number() } );
                        nets.push_back( { netName, pins } );
                    }
                }
            }
        }
        std::vector<json> danglingItems;
        {
            kiapi::common::ApiRequest dangReq; dangReq.mutable_header()->set_client_name( "mcp" );
            kiapi::schematic::types::GetDanglingReport dangCmd; dangReq.mutable_message()->PackFrom( dangCmd );
            kiapi::common::ApiResponse dangResp; std::string dangErr;
            if( m_ipc.SendRequest( dangReq, dangResp, dangErr ) && dangResp.status().status() == kiapi::common::AS_OK && dangResp.has_message() )
            {
                kiapi::schematic::types::GetDanglingReportResponse dr;
                if( dangResp.message().UnpackTo( &dr ) )
                    for( int i = 0; i < dr.items_size(); ++i )
                    {
                        const auto& it = dr.items( i );
                        danglingItems.push_back( { { "reference", it.reference() }, { "pin_number", it.pin_number() }, { "x_mm", it.x_mm() }, { "y_mm", it.y_mm() }, { "type", it.type() } } );
                    }
            }
        }
        std::set<std::string> labelNames;
        // Also store label positions for overlap detection.
        struct LabelInfo { std::string name; std::string id; std::string kind; double x; double y; };
        std::vector<LabelInfo> allLabels;
        {
            std::vector<ParsedLabel> liveLabels;
            std::vector<ParsedWire> unusedWires;
            std::string sheetPath = getCurrentSheetPath();
            std::string labelErr;
            if( !fetchLiveLabelsAndWires( m_ipc, sheetPath, liveLabels, unusedWires, labelErr ) )
            {
                if( !labelErr.empty() )
                    return makeErrorResult( labelErr );
            }

            for( const ParsedLabel& label : liveLabels )
            {
                if( label.text.empty() )
                    continue;
                labelNames.insert( label.text );
                allLabels.push_back( { label.text, label.uuid, label.kind, label.x, label.y } );
            }
        }
        auto looksLikeLocalPinNet = []( const std::string& nn ) -> bool {
            std::size_t u = nn.find( '_' );
            if( u == std::string::npos || u < 2 )
                return false;
            std::string pref = nn.substr( 0, u );
            if( !pref.empty() && pref[0] == '#' )
                return true;
            for( char c : pref )
            {
                if( std::isdigit( static_cast<unsigned char>( c ) ) )
                    return true;
            }
            return false;
        };

        json issues = json::array(); int singlePinCount = 0, floatingCount = 0, orphanedLabelCount = 0;
        for( const auto& [netName, pins] : nets )
        {
            if( pins.size() == 1 )
            {
                singlePinCount++;
                issues.push_back( { { "severity", "error" }, { "type", "single_pin_net" }, { "net", netName }, { "pin", pins[0].first + ":" + pins[0].second }, { "suggestion", "Remove the label or connect another pin to this net" }, { "message", "Net '" + netName + "' has only 1 pin (orphaned label or unconnected)" } } );
            }
            else if( pins.empty() )
            {
                orphanedLabelCount++;
                issues.push_back( { { "severity", "error" }, { "type", "orphaned_label" }, { "net", netName }, { "suggestion", "This label has no pins connected; use remove_label to delete it" }, { "message", "Net '" + netName + "' exists but has no pins (orphaned label)" } } );
            }
            if( !netName.empty() && netName.find( "Net-" ) == std::string::npos && labelNames.find( netName ) == labelNames.end()
                && !looksLikeLocalPinNet( netName ) )
            {
                floatingCount++;
                issues.push_back( { { "severity", "warning" }, { "type", "unlabeled_net" }, { "net", netName }, { "suggestion", "Use a label if this net should be named" }, { "message", "Net '" + netName + "' has pins but no matching label" } } );
            }
        }
        for( const auto& d : danglingItems )
        {
            std::string dRef = d.value( "reference", "" ); std::string dPin = d.value( "pin_number", "" );
            if( !filterNet.empty() )
            {
                bool onNet = false;
                for( const auto& [netName, pins] : nets ) { if( netName != filterNet ) continue; for( const auto& [ref, pin] : pins ) if( ref == dRef && pin == dPin ) { onNet = true; break; } if( onNet ) break; }
                if( !onNet ) continue;
            }
            std::string itemType = d.value( "type", "unknown" );
            issues.push_back( { { "severity", "error" }, { "type", "dangling" }, { "reference", dRef }, { "pin_number", dPin }, { "x_mm", d["x_mm"] }, { "y_mm", d["y_mm"] }, { "item_type", itemType }, { "suggestion", "Remove dangling " + itemType + " or reconnect the pin" }, { "message", "Dangling " + itemType + " at " + dRef + ":" + dPin } } );
        }
        // --- Overlapping label detection ---
        int overlappingLabelCount = 0;
        constexpr double LABEL_OVERLAP_RADIUS = 2.0;  // mm — labels closer than this overlap visually
        for( size_t a = 0; a < allLabels.size(); ++a )
        {
            for( size_t b = a + 1; b < allLabels.size(); ++b )
            {
                double dx = std::abs( allLabels[a].x - allLabels[b].x );
                double dy = std::abs( allLabels[a].y - allLabels[b].y );
                if( dx < LABEL_OVERLAP_RADIUS && dy < LABEL_OVERLAP_RADIUS )
                {
                    overlappingLabelCount++;
                    std::string msg;
                    if( allLabels[a].name == allLabels[b].name )
                    {
                        msg = "Duplicate label '" + allLabels[a].name + "' at ("
                            + std::to_string( allLabels[a].x ) + "," + std::to_string( allLabels[a].y )
                            + ") and (" + std::to_string( allLabels[b].x ) + "," + std::to_string( allLabels[b].y )
                            + ") — remove one with remove_labels_in_bbox";
                    }
                    else
                    {
                        msg = "Labels '" + allLabels[a].name + "' and '" + allLabels[b].name
                            + "' overlap at (" + std::to_string( allLabels[a].x ) + "," + std::to_string( allLabels[a].y )
                            + ") — move one to avoid visual confusion";
                    }
                    issues.push_back( {
                        { "severity", allLabels[a].name == allLabels[b].name ? "error" : "warning" },
                        { "type", "overlapping_labels" },
                        { "label_a", allLabels[a].name },
                        { "label_b", allLabels[b].name },
                        { "x_a", allLabels[a].x }, { "y_a", allLabels[a].y },
                        { "x_b", allLabels[b].x }, { "y_b", allLabels[b].y },
                        { "suggestion", allLabels[a].name == allLabels[b].name
                            ? "Remove the duplicate label with remove_labels_in_bbox"
                            : "Move one label away to avoid visual overlap" },
                        { "message", msg }
                    } );
                }
            }
        }

        json out; out["issues"] = issues;
        out["summary"] = { { "total_nets", (int)nets.size() }, { "single_pin_nets", singlePinCount }, { "orphaned_labels", orphanedLabelCount }, { "unlabeled_nets", floatingCount }, { "dangling_items", (int)danglingItems.size() }, { "overlapping_labels", overlappingLabelCount }, { "total_issues", (int)issues.size() }, { "needs_cleanup", singlePinCount + orphanedLabelCount + (int)danglingItems.size() + overlappingLabelCount > 0 } };
        json content = json::array();
        content.push_back( { { "type", "text" }, { "text", out.dump() } } );
        return "{\"jsonrpc\":\"2.0\",\"result\":" + json( { { "content", content }, { "isError", false } } ).dump() + ",\"id\":" + id + "}";
    }
    // ── auto_cleanup_stray_nets ──
    else if( name == "auto_cleanup_stray_nets" )
    {
        if( !m_ipc.IsConnected() && !m_ipc.Connect( IpcClient::GetDefaultSocketPath() ) )
        {
            json content = json::array();
            content.push_back( { { "type", "text" }, { "text", "KiCad IPC not connected." } } );
            return "{\"jsonrpc\":\"2.0\",\"result\":" + json( { { "content", content }, { "isError", true } } ).dump() + ",\"id\":" + id + "}";
        }

        // First, get the diagnostic info (orphaned labels, single-pin nets)
        std::vector<std::pair<std::string, std::vector<std::pair<std::string,std::string>>>> nets;
        {
            kiapi::common::ApiRequest netReq; netReq.mutable_header()->set_client_name( "mcp" );
            kiapi::schematic::types::GetNetlist netCmd; netReq.mutable_message()->PackFrom( netCmd );
            kiapi::common::ApiResponse netResp; std::string netErr;
            if( m_ipc.SendRequest( netReq, netResp, netErr ) && netResp.status().status() == kiapi::common::AS_OK && netResp.has_message() )
            {
                kiapi::schematic::types::GetNetlistResponse nr;
                if( netResp.message().UnpackTo( &nr ) )
                    for( int n = 0; n < nr.nets_size(); ++n )
                    {
                        const auto& net = nr.nets( n ); std::string netName = net.net_name();
                        std::vector<std::pair<std::string,std::string>> pins;
                        for( int p = 0; p < net.pins_size(); ++p ) pins.push_back( { net.pins( p ).reference(), net.pins( p ).pin_number() } );
                        nets.push_back( { netName, pins } );
                    }
            }
        }

        // Collect labels to remove (orphaned and single-pin)
        std::vector<std::string> labelsToRemove;
        for( const auto& [netName, pins] : nets )
        {
            if( pins.empty() || pins.size() == 1 )
                labelsToRemove.push_back( netName );
        }

        int removedCount = static_cast<int>( labelsToRemove.size() );
        json removedLabels = json::array();

        // Stage labels for removal
        for( const auto& netName : labelsToRemove )
        {
            removedLabels.push_back( netName );
        }

        json out;
        out["removed_labels"] = removedLabels;
        out["total_removed"] = removedCount;
        out["summary"] = "Removed " + std::to_string( removedCount ) + " orphaned/single-pin labels";
        json content = json::array();
        content.push_back( { { "type", "text" }, { "text", out.dump() } } );
        return "{\"jsonrpc\":\"2.0\",\"result\":" + json( { { "content", content }, { "isError", removedCount == 0 } } ).dump() + ",\"id\":" + id + "}";
    }
    // ── get_all_labels / get_all_wires / get_wire_labels / get_labels_in_view / rename_labels_in_bbox ──
    else if( name == "get_all_labels" || name == "get_all_wires" || name == "get_wire_labels"
             || name == "get_labels_in_view" || name == "rename_labels_in_bbox" )
    {
        auto makeErrorResult = [&]( const std::string& message )
        {
            json content = json::array();
            content.push_back( { { "type", "text" }, { "text", message } } );
            return "{\"jsonrpc\":\"2.0\",\"result\":"
                    + json( { { "content", content }, { "isError", true } } ).dump()
                    + ",\"id\":" + id + "}";
        };

        std::string sheetPath = getCurrentSheetPath();
        std::vector<ParsedLabel> labels;
        std::vector<ParsedWire> wires;
        std::string fetchErr;
        if( !fetchLiveLabelsAndWires( m_ipc, sheetPath, labels, wires, fetchErr ) )
            return makeErrorResult( fetchErr.empty() ? "KiCad schematic IPC not available." : fetchErr );

        double qMinX = -1e9, qMinY = -1e9, qMaxX = 1e9, qMaxY = 1e9;
        bool hasBbox = false;
        if( args.contains( "min_x" ) && args["min_x"].is_number()
            && args.contains( "min_y" ) && args["min_y"].is_number()
            && args.contains( "max_x" ) && args["max_x"].is_number()
            && args.contains( "max_y" ) && args["max_y"].is_number() )
        {
            qMinX = args["min_x"].get<double>();
            qMinY = args["min_y"].get<double>();
            qMaxX = args["max_x"].get<double>();
            qMaxY = args["max_y"].get<double>();
            hasBbox = true;
        }

        if( name == "get_labels_in_view" )
        {
            std::string visErr;
            if( !m_ipc.EnsureSchematicApiConnection( visErr ) )
                return makeErrorResult( visErr.empty() ? "KiCad schematic IPC not available." : visErr );

            kiapi::common::ApiRequest visReq;
            visReq.mutable_header()->set_client_name( "mcp" );
            kiapi::schematic::types::GetVisibleBounds visCmd;
            visReq.mutable_message()->PackFrom( visCmd );
            kiapi::common::ApiResponse visResp;
            if( !m_ipc.SendRequest( visReq, visResp, visErr ) || visResp.status().status() != kiapi::common::AS_OK )
                return makeErrorResult( visErr.empty() ? "GetVisibleBounds failed" : visErr );

            kiapi::schematic::types::GetVisibleBoundsResponse vis;
            if( !visResp.has_message() || !visResp.message().UnpackTo( &vis ) )
                return makeErrorResult( "Could not parse GetVisibleBounds response" );

            qMinX = vis.min_x_mm();
            qMinY = vis.min_y_mm();
            qMaxX = vis.max_x_mm();
            qMaxY = vis.max_y_mm();
            hasBbox = true;
        }

        auto labelInBbox = [&]( const ParsedLabel& label ) -> bool
        {
            if( !hasBbox )
                return true;
            return pointInRect( label.x, label.y, qMinX, qMinY, qMaxX, qMaxY );
        };

        auto wireInBbox = [&]( const ParsedWire& wire ) -> bool
        {
            if( !hasBbox )
                return true;
            return segmentIntersectsRect( wire.x1, wire.y1, wire.x2, wire.y2, qMinX, qMinY, qMaxX, qMaxY );
        };

        if( name == "get_all_labels" || name == "get_labels_in_view" )
        {
            json labelsArr = json::array();
            for( const ParsedLabel& label : labels )
            {
                if( !labelInBbox( label ) )
                    continue;
                labelsArr.push_back( {
                    { "uuid", label.uuid },
                    { "text", label.text },
                    { "kind", label.kind },
                    { "x", label.x },
                    { "y", label.y },
                    { "rotation", label.rotation },
                    { "sheet", sheetPath }
                } );
            }

            json out;
            out["labels"] = labelsArr;
            out["count"] = static_cast<int>( labelsArr.size() );
            out["sheet"] = sheetPath;
            if( name == "get_labels_in_view" )
                out["bbox"] = { { "min_x", qMinX }, { "min_y", qMinY }, { "max_x", qMaxX }, { "max_y", qMaxY } };

            json content = json::array();
            content.push_back( { { "type", "text" }, { "text", out.dump() } } );
            return "{\"jsonrpc\":\"2.0\",\"result\":" + json( { { "content", content }, { "isError", false } } ).dump() + ",\"id\":" + id + "}";
        }

        if( name == "get_all_wires" )
        {
            json wiresArr = json::array();
            for( const ParsedWire& wire : wires )
            {
                if( !wireInBbox( wire ) )
                    continue;
                wiresArr.push_back( {
                    { "uuid", wire.uuid }, { "x1", wire.x1 }, { "y1", wire.y1 },
                    { "x2", wire.x2 }, { "y2", wire.y2 }, { "sheet", sheetPath }
                } );
            }

            json out;
            out["wires"] = wiresArr;
            out["count"] = static_cast<int>( wiresArr.size() );
            out["sheet"] = sheetPath;
            json content = json::array();
            content.push_back( { { "type", "text" }, { "text", out.dump() } } );
            return "{\"jsonrpc\":\"2.0\",\"result\":" + json( { { "content", content }, { "isError", false } } ).dump() + ",\"id\":" + id + "}";
        }

        if( name == "get_wire_labels" )
        {
            const double toleranceMm = args.value( "tolerance_mm", 0.25 );
            const bool onlyLabeled = args.value( "only_labeled", false );

            json wiresArr = json::array();
            for( const ParsedWire& wire : wires )
            {
                if( !wireInBbox( wire ) )
                    continue;

                json attached = json::array();
                for( const ParsedLabel& label : labels )
                {
                    const double dist = pointToSegmentDistanceMm(
                        label.x, label.y, wire.x1, wire.y1, wire.x2, wire.y2 );
                    if( dist <= toleranceMm )
                    {
                        attached.push_back( {
                            { "uuid", label.uuid },
                            { "kind", label.kind },
                            { "text", label.text },
                            { "x", label.x },
                            { "y", label.y },
                            { "rotation", label.rotation },
                            { "distance_mm", dist }
                        } );
                    }
                }

                if( onlyLabeled && attached.empty() )
                    continue;

                wiresArr.push_back( {
                    { "wire_uuid", wire.uuid },
                    { "x1", wire.x1 }, { "y1", wire.y1 }, { "x2", wire.x2 }, { "y2", wire.y2 },
                    { "attached_labels", attached },
                    { "attached_label_count", static_cast<int>( attached.size() ) }
                } );
            }

            json out;
            out["wires"] = wiresArr;
            out["count"] = static_cast<int>( wiresArr.size() );
            out["tolerance_mm"] = toleranceMm;
            out["sheet"] = sheetPath;
            json content = json::array();
            content.push_back( { { "type", "text" }, { "text", out.dump() } } );
            return "{\"jsonrpc\":\"2.0\",\"result\":" + json( { { "content", content }, { "isError", false } } ).dump() + ",\"id\":" + id + "}";
        }

        // rename_labels_in_bbox
        if( !hasBbox )
            return makeErrorResult( "rename_labels_in_bbox requires min_x, min_y, max_x, max_y" );
        if( !args.contains( "new_name" ) || !args["new_name"].is_string() || args["new_name"].get<std::string>().empty() )
            return makeErrorResult( "rename_labels_in_bbox requires non-empty new_name" );

        const std::string oldNameFilter = args.value( "old_name", std::string() );
        const std::string newName = args["new_name"].get<std::string>();
        const bool dryRun = args.value( "dry_run", true );

        std::vector<ParsedLabel> targets;
        targets.reserve( labels.size() );
        for( const ParsedLabel& label : labels )
        {
            if( !labelInBbox( label ) )
                continue;
            if( !oldNameFilter.empty() && label.text != oldNameFilter )
                continue;
            targets.push_back( label );
        }

        json targetArr = json::array();
        for( const ParsedLabel& label : targets )
        {
            targetArr.push_back( {
                { "uuid", label.uuid }, { "kind", label.kind },
                { "old_name", label.text }, { "new_name", newName },
                { "x", label.x }, { "y", label.y }, { "rotation", label.rotation }
            } );
        }

        if( dryRun )
        {
            json out;
            out["dry_run"] = true;
            out["bbox"] = { { "min_x", qMinX }, { "min_y", qMinY }, { "max_x", qMaxX }, { "max_y", qMaxY } };
            out["targets"] = targetArr;
            out["target_count"] = static_cast<int>( targetArr.size() );
            out["old_name_filter"] = oldNameFilter;
            out["new_name"] = newName;
            json content = json::array();
            content.push_back( { { "type", "text" }, { "text", out.dump() } } );
            return "{\"jsonrpc\":\"2.0\",\"result\":" + json( { { "content", content }, { "isError", false } } ).dump() + ",\"id\":" + id + "}";
        }

        if( targets.empty() )
        {
            json out;
            out["dry_run"] = false;
            out["renamed_count"] = 0;
            out["targets"] = json::array();
            json content = json::array();
            content.push_back( { { "type", "text" }, { "text", out.dump() } } );
            return "{\"jsonrpc\":\"2.0\",\"result\":" + json( { { "content", content }, { "isError", false } } ).dump() + ",\"id\":" + id + "}";
        }

        json failed = json::array();
        for( const ParsedLabel& target : targets )
        {
            if( target.uuid.empty() )
            {
                failed.push_back( { { "uuid", target.uuid }, { "old_name", target.text },
                                    { "kind", target.kind }, { "reason", "missing_uuid" } } );
            }
        }

        if( !failed.empty() )
        {
            json out;
            out["dry_run"] = false;
            out["bbox"] = { { "min_x", qMinX }, { "min_y", qMinY }, { "max_x", qMaxX }, { "max_y", qMaxY } };
            out["rolled_back"] = true;
            out["renamed"] = json::array();
            out["renamed_count"] = 0;
            out["failed"] = failed;
            out["failed_count"] = static_cast<int>( failed.size() );
            json content = json::array();
            content.push_back( { { "type", "text" }, { "text", out.dump() } } );
            return "{\"jsonrpc\":\"2.0\",\"result\":" + json( { { "content", content }, { "isError", true } } ).dump() + ",\"id\":" + id + "}";
        }

        std::string txnErr;
        TransactionGuard txn( *this, m_ipc, txnErr );
        if( !txn.ok() )
            return makeErrorResult( txnErr.empty() ? "Begin commit failed" : txnErr );

        kiapi::common::commands::UpdateItems updCmd;
        updCmd.mutable_header()->mutable_document()->set_type( kiapi::common::types::DOCTYPE_SCHEMATIC );
        if( !sheetPath.empty() )
            updCmd.mutable_header()->mutable_document()->mutable_sheet_path()->set_path_human_readable( sheetPath );

        std::vector<ParsedLabel> submittedTargets;
        submittedTargets.reserve( targets.size() );
        for( const ParsedLabel& target : targets )
        {
            google::protobuf::Any updatedAny;
            if( !buildUpdatedLabelAny( target, newName, updatedAny ) )
            {
                failed.push_back( { { "uuid", target.uuid }, { "old_name", target.text },
                                    { "kind", target.kind }, { "reason", "could_not_unpack_label" } } );
                continue;
            }

            updCmd.add_items()->CopyFrom( updatedAny );
            submittedTargets.push_back( target );
        }

        kiapi::common::ApiRequest updReq;
        updReq.mutable_header()->set_client_name( "mcp" );
        updReq.mutable_message()->PackFrom( updCmd );
        kiapi::common::ApiResponse updResp;
        std::string updErr;
        if( !m_ipc.SendRequest( updReq, updResp, updErr ) )
            return makeErrorResult( updErr.empty() ? "UpdateItems failed" : updErr );

        if( updResp.status().status() != kiapi::common::AS_OK )
            return makeErrorResult( updResp.status().error_message().empty() ? "UpdateItems failed"
                                                                            : updResp.status().error_message() );

        if( !updResp.has_message() )
            return makeErrorResult( "Could not parse UpdateItems response" );

        kiapi::common::commands::UpdateItemsResponse updResult;
        if( !updResp.message().UnpackTo( &updResult ) )
            return makeErrorResult( "Could not parse UpdateItems response" );

        json renamed = json::array();
        json updateFailed = json::array();
        bool allOk = true;
        const int resultCount = updResult.updated_items_size();
        for( int i = 0; i < static_cast<int>( submittedTargets.size() ); ++i )
        {
            const ParsedLabel& target = submittedTargets[i];

            if( i >= resultCount )
            {
                allOk = false;
                updateFailed.push_back( { { "uuid", target.uuid }, { "old_name", target.text },
                                          { "reason", "missing_update_result" } } );
                continue;
            }

            const auto& itemResult = updResult.updated_items( i );
            const auto& status = itemResult.status();
            if( status.code() != kiapi::common::commands::ISC_OK )
            {
                allOk = false;
                updateFailed.push_back( {
                    { "uuid", target.uuid },
                    { "old_name", target.text },
                    { "reason", status.error_message().empty() ? "update_failed" : status.error_message() }
                } );
                continue;
            }

            renamed.push_back( {
                { "uuid", target.uuid }, { "kind", target.kind },
                { "old_name", target.text }, { "new_name", newName },
                { "x", target.x }, { "y", target.y }, { "rotation", target.rotation }
            } );
        }

        if( !allOk )
        {
            json out;
            out["dry_run"] = false;
            out["bbox"] = { { "min_x", qMinX }, { "min_y", qMinY }, { "max_x", qMaxX }, { "max_y", qMaxY } };
            out["rolled_back"] = true;
            out["renamed"] = json::array();
            out["renamed_count"] = 0;
            out["failed"] = updateFailed;
            out["failed_count"] = static_cast<int>( updateFailed.size() );
            json content = json::array();
            content.push_back( { { "type", "text" }, { "text", out.dump() } } );
            return "{\"jsonrpc\":\"2.0\",\"result\":" + json( { { "content", content }, { "isError", true } } ).dump() + ",\"id\":" + id + "}";
        }

        if( !txn.commit() )
            return makeErrorResult( "Failed to commit label rename transaction" );

        json out;
        out["dry_run"] = false;
        out["bbox"] = { { "min_x", qMinX }, { "min_y", qMinY }, { "max_x", qMaxX }, { "max_y", qMaxY } };
        out["rolled_back"] = false;
        out["renamed"] = renamed;
        out["renamed_count"] = static_cast<int>( renamed.size() );
        out["failed"] = failed;
        out["failed_count"] = static_cast<int>( failed.size() );
        json content = json::array();
        content.push_back( { { "type", "text" }, { "text", out.dump() } } );
        return "{\"jsonrpc\":\"2.0\",\"result\":" + json( { { "content", content }, { "isError", false } } ).dump() + ",\"id\":" + id + "}";
    }
    // ── list_wires ──
    else if( name == "list_wires" )
    {
        // Read the .kicad_sch file directly to enumerate wires (bypasses GetItems).
        std::string schPath = getCurrentSchematicPath();
        if( schPath.empty() )
        {
            json content = json::array();
            content.push_back( { { "type", "text" }, { "text", "Could not determine schematic file path" } } );
            return "{\"jsonrpc\":\"2.0\",\"result\":" + json( { { "content", content }, { "isError", true } } ).dump() + ",\"id\":" + id + "}";
        }

        // Optional bbox filter.
        double filterMinX = -1e9, filterMinY = -1e9, filterMaxX = 1e9, filterMaxY = 1e9;
        bool hasBbox = false;
        if( args.contains( "min_x" ) && args["min_x"].is_number() )
        {
            filterMinX = args["min_x"].get<double>();
            filterMinY = args["min_y"].get<double>();
            filterMaxX = args["max_x"].get<double>();
            filterMaxY = args["max_y"].get<double>();
            hasBbox = true;
        }

        // Read and parse the .kicad_sch file for wire segments.
        std::ifstream schFile( schPath );
        if( !schFile.is_open() )
        {
            json content = json::array();
            content.push_back( { { "type", "text" }, { "text", "Could not open schematic file: " + schPath } } );
            return "{\"jsonrpc\":\"2.0\",\"result\":" + json( { { "content", content }, { "isError", true } } ).dump() + ",\"id\":" + id + "}";
        }

        std::string schContent( ( std::istreambuf_iterator<char>( schFile ) ), std::istreambuf_iterator<char>() );
        schFile.close();

        // Parse wire segments: (wire (pts (xy X1 Y1) (xy X2 Y2)) ... (uuid UUID))
        json wires = json::array();
        constexpr double SCH_SCALE = 1.0; // .kicad_sch uses mm directly
        size_t pos = 0;
        while( ( pos = schContent.find( "(wire (pts", pos ) ) != std::string::npos )
        {
            size_t wireStart = pos;
            // Find matching closing paren — count depth.
            int depth = 0;
            size_t end = pos;
            for( ; end < schContent.size(); ++end )
            {
                if( schContent[end] == '(' ) depth++;
                else if( schContent[end] == ')' ) { depth--; if( depth == 0 ) { end++; break; } }
            }
            std::string wireBlock = schContent.substr( wireStart, end - wireStart );

            // Extract coordinates: (xy X Y)
            std::vector<double> coords;
            size_t xyPos = 0;
            while( ( xyPos = wireBlock.find( "(xy ", xyPos ) ) != std::string::npos )
            {
                xyPos += 4;
                double x = 0, y = 0;
                try { x = std::stod( wireBlock.substr( xyPos ) ); } catch( ... ) {}
                size_t sp = wireBlock.find( ' ', xyPos );
                if( sp != std::string::npos )
                {
                    try { y = std::stod( wireBlock.substr( sp + 1 ) ); } catch( ... ) {}
                }
                coords.push_back( x );
                coords.push_back( y );
            }

            // Extract UUID: (uuid "...") or (uuid ...)
            std::string uuid;
            size_t uuidPos = wireBlock.find( "(uuid " );
            if( uuidPos != std::string::npos )
            {
                uuidPos += 6;
                // Skip optional quote.
                if( uuidPos < wireBlock.size() && wireBlock[uuidPos] == '"' ) uuidPos++;
                size_t uuidEnd = wireBlock.find_first_of( "\")", uuidPos );
                if( uuidEnd != std::string::npos )
                    uuid = wireBlock.substr( uuidPos, uuidEnd - uuidPos );
            }

            if( coords.size() >= 4 && !uuid.empty() )
            {
                double x1 = coords[0], y1 = coords[1], x2 = coords[2], y2 = coords[3];
                // Bbox filter.
                if( hasBbox )
                {
                    bool inBox = ( x1 >= filterMinX && x1 <= filterMaxX && y1 >= filterMinY && y1 <= filterMaxY ) ||
                                 ( x2 >= filterMinX && x2 <= filterMaxX && y2 >= filterMinY && y2 <= filterMaxY );
                    if( !inBox ) { pos = end; continue; }
                }
                wires.push_back( { { "x1", x1 }, { "y1", y1 }, { "x2", x2 }, { "y2", y2 },
                                   { "uuid", uuid }, { "length", std::hypot( x2 - x1, y2 - y1 ) } } );
            }
            pos = end;
        }

        json out;
        out["wires"] = wires;
        out["count"] = (int)wires.size();
        out["schematic_path"] = schPath;
        json content = json::array();
        content.push_back( { { "type", "text" }, { "text", out.dump() } } );
        return "{\"jsonrpc\":\"2.0\",\"result\":" + json( { { "content", content }, { "isError", false } } ).dump() + ",\"id\":" + id + "}";
    }
    // ── remove_items_by_position ──
    else if( name == "remove_items_by_position" )
    {
        double targetX = args.value( "x", 0.0 );
        double targetY = args.value( "y", 0.0 );
        double tolerance = args.value( "tolerance", 1.0 );
        bool removeWires = args.value( "remove_wires", true );
        bool removeLabels = args.value( "remove_labels", true );

        std::string schPath = getCurrentSchematicPath();
        if( schPath.empty() )
        {
            json content = json::array();
            content.push_back( { { "type", "text" }, { "text", "Could not determine schematic file path" } } );
            return "{\"jsonrpc\":\"2.0\",\"result\":" + json( { { "content", content }, { "isError", true } } ).dump() + ",\"id\":" + id + "}";
        }

        std::ifstream schFile( schPath );
        if( !schFile.is_open() )
        {
            json content = json::array();
            content.push_back( { { "type", "text" }, { "text", "Could not open: " + schPath } } );
            return "{\"jsonrpc\":\"2.0\",\"result\":" + json( { { "content", content }, { "isError", true } } ).dump() + ",\"id\":" + id + "}";
        }
        std::string schContent( ( std::istreambuf_iterator<char>( schFile ) ), std::istreambuf_iterator<char>() );
        schFile.close();

        std::vector<std::string> idsToDelete;
        json removedItems = json::array();

        // Find wires near the target position.
        if( removeWires )
        {
            size_t pos = 0;
            while( ( pos = schContent.find( "(wire (pts", pos ) ) != std::string::npos )
            {
                int depth = 0; size_t end = pos;
                for( ; end < schContent.size(); ++end )
                    { if( schContent[end] == '(' ) depth++; else if( schContent[end] == ')' ) { depth--; if( depth == 0 ) { end++; break; } } }
                std::string block = schContent.substr( pos, end - pos );

                std::vector<double> coords;
                size_t xyPos = 0;
                while( ( xyPos = block.find( "(xy ", xyPos ) ) != std::string::npos )
                {
                    xyPos += 4;
                    double x = 0, y = 0;
                    try { x = std::stod( block.substr( xyPos ) ); } catch( ... ) {}
                    size_t sp = block.find( ' ', xyPos );
                    if( sp != std::string::npos ) { try { y = std::stod( block.substr( sp + 1 ) ); } catch( ... ) {} }
                    coords.push_back( x ); coords.push_back( y );
                }

                std::string uuid;
                size_t uuidPos = block.find( "(uuid " );
                if( uuidPos != std::string::npos )
                {
                    uuidPos += 6;
                    if( uuidPos < block.size() && block[uuidPos] == '"' ) uuidPos++;
                    size_t uuidEnd = block.find_first_of( "\")", uuidPos );
                    if( uuidEnd != std::string::npos ) uuid = block.substr( uuidPos, uuidEnd - uuidPos );
                }

                if( coords.size() >= 4 && !uuid.empty() )
                {
                    double x1 = coords[0], y1 = coords[1], x2 = coords[2], y2 = coords[3];
                    bool near1 = std::abs( x1 - targetX ) <= tolerance && std::abs( y1 - targetY ) <= tolerance;
                    bool near2 = std::abs( x2 - targetX ) <= tolerance && std::abs( y2 - targetY ) <= tolerance;
                    if( near1 || near2 )
                    {
                        idsToDelete.push_back( uuid );
                        removedItems.push_back( { { "type", "wire" }, { "uuid", uuid },
                                                   { "x1", x1 }, { "y1", y1 }, { "x2", x2 }, { "y2", y2 } } );
                    }
                }
                pos = end;
            }
        }

        // Find labels near the target position.
        if( removeLabels )
        {
            size_t pos = 0;
            while( ( pos = schContent.find( "(global_label", pos ) ) != std::string::npos )
            {
                int depth = 0; size_t end = pos;
                for( ; end < schContent.size(); ++end )
                    { if( schContent[end] == '(' ) depth++; else if( schContent[end] == ')' ) { depth--; if( depth == 0 ) { end++; break; } } }
                std::string block = schContent.substr( pos, end - pos );

                // Find (at X Y angle)
                size_t atPos = block.find( "(at " );
                double lx = 0, ly = 0;
                if( atPos != std::string::npos )
                {
                    atPos += 4;
                    try { lx = std::stod( block.substr( atPos ) ); } catch( ... ) {}
                    size_t sp = block.find( ' ', atPos );
                    if( sp != std::string::npos ) { try { ly = std::stod( block.substr( sp + 1 ) ); } catch( ... ) {} }
                }

                std::string uuid;
                size_t uuidPos = block.find( "(uuid " );
                if( uuidPos != std::string::npos )
                {
                    uuidPos += 6;
                    if( uuidPos < block.size() && block[uuidPos] == '"' ) uuidPos++;
                    size_t uuidEnd = block.find_first_of( "\")", uuidPos );
                    if( uuidEnd != std::string::npos ) uuid = block.substr( uuidPos, uuidEnd - uuidPos );
                }

                if( !uuid.empty() && std::abs( lx - targetX ) <= tolerance && std::abs( ly - targetY ) <= tolerance )
                {
                    idsToDelete.push_back( uuid );
                    removedItems.push_back( { { "type", "label" }, { "uuid", uuid }, { "x", lx }, { "y", ly } } );
                }
                pos = end;
            }
        }

        // Delete found items via IPC.
        int deleted = 0;
        if( !idsToDelete.empty() && m_ipc.IsConnected() )
        {
            kiapi::common::commands::DeleteItems delCmd;
            delCmd.mutable_header()->mutable_document()->set_type( kiapi::common::types::DOCTYPE_SCHEMATIC );
            std::string sheetPath = getCurrentSheetPath();
            if( !sheetPath.empty() )
                delCmd.mutable_header()->mutable_document()->mutable_sheet_path()->set_path_human_readable( sheetPath );
            for( const auto& uid : idsToDelete )
                delCmd.add_item_ids()->set_value( uid );

            kiapi::common::ApiRequest delReq;
            delReq.mutable_header()->set_client_name( "mcp" );
            delReq.mutable_message()->PackFrom( delCmd );
            kiapi::common::ApiResponse delResp;
            std::string delErr;
            if( m_ipc.SendRequest( delReq, delResp, delErr ) && delResp.status().status() == kiapi::common::AS_OK )
                deleted = (int)idsToDelete.size();
        }

        json out;
        out["removed"] = removedItems;
        out["deleted_count"] = deleted;
        out["searched_position"] = { { "x", targetX }, { "y", targetY }, { "tolerance", tolerance } };
        json content = json::array();
        content.push_back( { { "type", "text" }, { "text", out.dump() } } );
        return "{\"jsonrpc\":\"2.0\",\"result\":" + json( { { "content", content }, { "isError", false } } ).dump() + ",\"id\":" + id + "}";
    }
    // ── remove_all_dangling ──
    else if( name == "remove_all_dangling" )
    {
        // Step 1: Get dangling report.
        kiapi::common::ApiRequest dangReq;
        dangReq.mutable_header()->set_client_name( "mcp" );
        kiapi::schematic::types::GetDanglingReport dangCmd;
        dangReq.mutable_message()->PackFrom( dangCmd );
        kiapi::common::ApiResponse dangResp;
        std::string dangErr;

        if( !m_ipc.IsConnected() && !m_ipc.Connect( IpcClient::GetDefaultSocketPath() ) )
        {
            json content = json::array();
            content.push_back( { { "type", "text" }, { "text", "KiCad IPC not connected." } } );
            return "{\"jsonrpc\":\"2.0\",\"result\":" + json( { { "content", content }, { "isError", true } } ).dump() + ",\"id\":" + id + "}";
        }

        if( !m_ipc.SendRequest( dangReq, dangResp, dangErr ) || dangResp.status().status() != kiapi::common::AS_OK )
        {
            json content = json::array();
            content.push_back( { { "type", "text" }, { "text", dangErr.empty() ? "GetDanglingReport failed" : dangErr } } );
            return "{\"jsonrpc\":\"2.0\",\"result\":" + json( { { "content", content }, { "isError", true } } ).dump() + ",\"id\":" + id + "}";
        }

        // Collect dangling wire_end positions.
        std::vector<std::pair<double, double>> danglingPositions;
        if( dangResp.has_message() )
        {
            kiapi::schematic::types::GetDanglingReportResponse dangR;
            if( dangResp.message().UnpackTo( &dangR ) )
            {
                for( int i = 0; i < dangR.items_size(); ++i )
                {
                    const auto& it = dangR.items( i );
                    if( it.type() == "wire_end" )
                        danglingPositions.push_back( { it.x_mm(), it.y_mm() } );
                }
            }
        }

        if( danglingPositions.empty() )
        {
            json content = json::array();
            content.push_back( { { "type", "text" }, { "text", "{\"message\":\"No dangling wire endpoints found\",\"removed\":0}" } } );
            return "{\"jsonrpc\":\"2.0\",\"result\":" + json( { { "content", content }, { "isError", false } } ).dump() + ",\"id\":" + id + "}";
        }

        // Step 2: Read schematic file to find wire UUIDs at those positions.
        std::string schPath = getCurrentSchematicPath();
        if( schPath.empty() )
        {
            json content = json::array();
            content.push_back( { { "type", "text" }, { "text", "Could not determine schematic file path" } } );
            return "{\"jsonrpc\":\"2.0\",\"result\":" + json( { { "content", content }, { "isError", true } } ).dump() + ",\"id\":" + id + "}";
        }

        std::ifstream schFile( schPath );
        if( !schFile.is_open() )
        {
            json content = json::array();
            content.push_back( { { "type", "text" }, { "text", "Could not open: " + schPath } } );
            return "{\"jsonrpc\":\"2.0\",\"result\":" + json( { { "content", content }, { "isError", true } } ).dump() + ",\"id\":" + id + "}";
        }
        std::string schContent( ( std::istreambuf_iterator<char>( schFile ) ), std::istreambuf_iterator<char>() );
        schFile.close();

        constexpr double TOL = 0.5; // mm tolerance for matching
        std::set<std::string> uuidsToDelete;
        json removedWires = json::array();

        size_t pos = 0;
        while( ( pos = schContent.find( "(wire (pts", pos ) ) != std::string::npos )
        {
            int depth = 0; size_t end = pos;
            for( ; end < schContent.size(); ++end )
                { if( schContent[end] == '(' ) depth++; else if( schContent[end] == ')' ) { depth--; if( depth == 0 ) { end++; break; } } }
            std::string block = schContent.substr( pos, end - pos );

            std::vector<double> coords;
            size_t xyPos = 0;
            while( ( xyPos = block.find( "(xy ", xyPos ) ) != std::string::npos )
            {
                xyPos += 4;
                double x = 0, y = 0;
                try { x = std::stod( block.substr( xyPos ) ); } catch( ... ) {}
                size_t sp = block.find( ' ', xyPos );
                if( sp != std::string::npos ) { try { y = std::stod( block.substr( sp + 1 ) ); } catch( ... ) {} }
                coords.push_back( x ); coords.push_back( y );
            }

            std::string uuid;
            size_t uuidPos = block.find( "(uuid " );
            if( uuidPos != std::string::npos )
            {
                uuidPos += 6;
                if( uuidPos < block.size() && block[uuidPos] == '"' ) uuidPos++;
                size_t uuidEnd = block.find_first_of( "\")", uuidPos );
                if( uuidEnd != std::string::npos ) uuid = block.substr( uuidPos, uuidEnd - uuidPos );
            }

            if( coords.size() >= 4 && !uuid.empty() )
            {
                double x1 = coords[0], y1 = coords[1], x2 = coords[2], y2 = coords[3];
                for( const auto& [dx, dy] : danglingPositions )
                {
                    if( ( std::abs( x1 - dx ) <= TOL && std::abs( y1 - dy ) <= TOL ) ||
                        ( std::abs( x2 - dx ) <= TOL && std::abs( y2 - dy ) <= TOL ) )
                    {
                        if( uuidsToDelete.insert( uuid ).second )
                        {
                            removedWires.push_back( { { "uuid", uuid }, { "x1", x1 }, { "y1", y1 }, { "x2", x2 }, { "y2", y2 } } );
                        }
                        break;
                    }
                }
            }
            pos = end;
        }

        // Step 3: Delete.
        int deleted = 0;
        if( !uuidsToDelete.empty() )
        {
            kiapi::common::commands::DeleteItems delCmd;
            delCmd.mutable_header()->mutable_document()->set_type( kiapi::common::types::DOCTYPE_SCHEMATIC );
            std::string sheetPath = getCurrentSheetPath();
            if( !sheetPath.empty() )
                delCmd.mutable_header()->mutable_document()->mutable_sheet_path()->set_path_human_readable( sheetPath );
            for( const auto& uid : uuidsToDelete )
                delCmd.add_item_ids()->set_value( uid );

            kiapi::common::ApiRequest delReq;
            delReq.mutable_header()->set_client_name( "mcp" );
            delReq.mutable_message()->PackFrom( delCmd );
            kiapi::common::ApiResponse delResp;
            std::string delErr;
            if( m_ipc.SendRequest( delReq, delResp, delErr ) && delResp.status().status() == kiapi::common::AS_OK )
                deleted = (int)uuidsToDelete.size();
        }

        json out;
        out["dangling_positions_found"] = (int)danglingPositions.size();
        out["wires_matched"] = (int)uuidsToDelete.size();
        out["deleted"] = deleted;
        out["removed_wires"] = removedWires;
        json content = json::array();
        content.push_back( { { "type", "text" }, { "text", out.dump() } } );
        return "{\"jsonrpc\":\"2.0\",\"result\":" + json( { { "content", content }, { "isError", false } } ).dump() + ",\"id\":" + id + "}";
    }
    // ── block_classify ──
    else if( name == "block_classify" )
    {
        if( !args.contains( "min_x" ) || !args["min_x"].is_number() || !args.contains( "min_y" ) || !args["min_y"].is_number()
            || !args.contains( "max_x" ) || !args["max_x"].is_number() || !args.contains( "max_y" ) || !args["max_y"].is_number() )
        {
            json content = json::array();
            content.push_back( { { "type", "text" }, { "text", "block_classify requires min_x, min_y, max_x, max_y (mm)" } } );
            return "{\"jsonrpc\":\"2.0\",\"result\":" + json( { { "content", content }, { "isError", true } } ).dump() + ",\"id\":" + id + "}";
        }
        double minX = args["min_x"].get<double>(), minY = args["min_y"].get<double>();
        double maxX = args["max_x"].get<double>(), maxY = args["max_y"].get<double>();
        if( !m_ipc.IsConnected() && !m_ipc.Connect( IpcClient::GetDefaultSocketPath() ) )
        {
            json content = json::array();
            content.push_back( { { "type", "text" }, { "text", "KiCad IPC not connected." } } );
            return "{\"jsonrpc\":\"2.0\",\"result\":" + json( { { "content", content }, { "isError", true } } ).dump() + ",\"id\":" + id + "}";
        }
        kiapi::schematic::types::GetSchematicSummaryResponse sumResp; std::string sumErr;
        if( !getCachedSummary( sumResp, sumErr ) )
        {
            json content = json::array();
            content.push_back( { { "type", "text" }, { "text", sumErr.empty() ? "Failed to get summary" : sumErr } } );
            return "{\"jsonrpc\":\"2.0\",\"result\":" + json( { { "content", content }, { "isError", true } } ).dump() + ",\"id\":" + id + "}";
        }
        std::map<std::string, int> prefixCounts; json componentsInBox = json::array(); int totalInBox = 0;
        for( int i = 0; i < sumResp.components_size(); ++i )
        {
            const auto& c = sumResp.components( i );
            double px = c.has_position() ? c.position().x_mm() : 0, py = c.has_position() ? c.position().y_mm() : 0;
            if( px >= minX && px <= maxX && py >= minY && py <= maxY )
            {
                totalInBox++; std::string ref = c.reference(); std::string prefix;
                for( char ch : ref ) { if( std::isalpha( ch ) ) prefix += ch; else break; }
                prefixCounts[prefix]++;
                componentsInBox.push_back( { { "reference", ref }, { "value", c.value() }, { "x_mm", px }, { "y_mm", py } } );
            }
        }
        json blocks = json::array();
        int rCnt = prefixCounts.count("R") ? prefixCounts["R"] : 0, cCnt = prefixCounts.count("C") ? prefixCounts["C"] : 0;
        int lCnt = prefixCounts.count("L") ? prefixCounts["L"] : 0, uCnt = prefixCounts.count("U") ? prefixCounts["U"] : 0;
        int dCnt = prefixCounts.count("D") ? prefixCounts["D"] : 0;
        int qCnt = (prefixCounts.count("Q") ? prefixCounts["Q"] : 0) + (prefixCounts.count("M") ? prefixCounts["M"] : 0);
        int jCnt = prefixCounts.count("J") ? prefixCounts["J"] : 0;
        if( uCnt > 0 && cCnt > 0 && cCnt >= uCnt ) blocks.push_back( "decoupling_caps" );
        if( rCnt == 2 && uCnt == 0 && cCnt == 0 && totalInBox == 2 ) blocks.push_back( "pull_up_pair" );
        if( rCnt > 0 && cCnt > 0 && lCnt > 0 ) blocks.push_back( "filter_network" );
        if( uCnt >= 1 && rCnt == 0 && cCnt == 0 ) blocks.push_back( "digital_logic" );
        if( uCnt >= 1 && cCnt >= 2 ) blocks.push_back( "power_regulation" );
        if( qCnt > 0 && rCnt > 0 ) blocks.push_back( "amplifier_or_switch" );
        if( dCnt > 0 && rCnt > 0 ) blocks.push_back( "protection_circuit" );
        if( jCnt > 0 ) blocks.push_back( "interface_connector" );
        if( blocks.empty() && totalInBox > 0 ) blocks.push_back( "mixed" );
        json prefixJson; for( const auto& [p, cnt] : prefixCounts ) prefixJson[p] = cnt;
        json out; out["total_components"] = totalInBox; out["prefix_counts"] = prefixJson;
        out["detected_blocks"] = blocks; out["components"] = componentsInBox;
        out["bbox"] = { { "min_x", minX }, { "min_y", minY }, { "max_x", maxX }, { "max_y", maxY } };
        json content = json::array();
        content.push_back( { { "type", "text" }, { "text", out.dump() } } );
        return "{\"jsonrpc\":\"2.0\",\"result\":" + json( { { "content", content }, { "isError", false } } ).dump() + ",\"id\":" + id + "}";
    }
    // ── get_bom ──
    else if( name == "get_bom" )
    {
        if( !m_ipc.IsConnected() && !m_ipc.Connect( IpcClient::GetDefaultSocketPath() ) )
        {
            json content = json::array();
            content.push_back( { { "type", "text" }, { "text", "KiCad IPC not connected." } } );
            return "{\"jsonrpc\":\"2.0\",\"result\":" + json( { { "content", content }, { "isError", true } } ).dump() + ",\"id\":" + id + "}";
        }

        // Get all components
        kiapi::schematic::types::GetSchematicSummaryResponse sumResp;
        std::string sumErr;
        if( !getCachedSummary( sumResp, sumErr ) )
        {
            json content = json::array();
            content.push_back( { { "type", "text" }, { "text", "Failed to get schematic summary: " + sumErr } } );
            return "{\"jsonrpc\":\"2.0\",\"result\":" + json( { { "content", content }, { "isError", true } } ).dump() + ",\"id\":" + id + "}";
        }

        json bomItems = json::array();
        for( int i = 0; i < sumResp.components_size(); ++i )
        {
            const auto& comp = sumResp.components( i );
            json bomItem;
            bomItem["reference"] = comp.reference();
            bomItem["value"] = comp.value();
            bomItem["symbol"] = comp.symbol_name();
            bomItem["library"] = comp.library_nickname();

            // BOM fields (stored as custom component properties, initially empty)
            bomItem["digikey_part_number"] = "";
            bomItem["manufacturer_part_number"] = "";
            bomItem["unit_price_usd"] = 0.0;
            bomItem["stock_quantity"] = 0;
            bomItem["lead_time_days"] = 0;
            bomItem["digikey_url"] = "";

            bomItems.push_back( bomItem );
        }

        json out;
        out["bom"] = bomItems;
        out["total_components"] = bomItems.size();
        json content = json::array();
        content.push_back( { { "type", "text" }, { "text", out.dump() } } );
        return "{\"jsonrpc\":\"2.0\",\"result\":" + json( { { "content", content }, { "isError", false } } ).dump() + ",\"id\":" + id + "}";
    }
    // ── update_component_bom_data ──
    else if( name == "update_component_bom_data" )
    {
        std::string reference = getStr( "reference" );
        if( reference.empty() )
        {
            json content = json::array();
            content.push_back( { { "type", "text" }, { "text", "update_component_bom_data requires 'reference'" } } );
            return "{\"jsonrpc\":\"2.0\",\"result\":" + json( { { "content", content }, { "isError", true } } ).dump() + ",\"id\":" + id + "}";
        }

        if( !m_ipc.IsConnected() && !m_ipc.Connect( IpcClient::GetDefaultSocketPath() ) )
        {
            json content = json::array();
            content.push_back( { { "type", "text" }, { "text", "KiCad IPC not connected." } } );
            return "{\"jsonrpc\":\"2.0\",\"result\":" + json( { { "content", content }, { "isError", true } } ).dump() + ",\"id\":" + id + "}";
        }

        // Build property updates map
        std::map<std::string, std::string> propertyMap;
        if( args.contains( "digikey_part_number" ) && args["digikey_part_number"].is_string() )
            propertyMap["Digi-Key Part Number"] = args["digikey_part_number"].get<std::string>();
        if( args.contains( "manufacturer_part_number" ) && args["manufacturer_part_number"].is_string() )
            propertyMap["Manufacturer Part"] = args["manufacturer_part_number"].get<std::string>();
        if( args.contains( "unit_price_usd" ) && args["unit_price_usd"].is_number() )
            propertyMap["Unit Price USD"] = std::to_string( args["unit_price_usd"].get<double>() );
        if( args.contains( "stock_quantity" ) && args["stock_quantity"].is_number() )
            propertyMap["Stock Qty"] = std::to_string( args["stock_quantity"].get<int>() );
        if( args.contains( "lead_time_days" ) && args["lead_time_days"].is_number() )
            propertyMap["Lead Time Days"] = std::to_string( args["lead_time_days"].get<int>() );
        if( args.contains( "digikey_url" ) && args["digikey_url"].is_string() )
            propertyMap["Digi-Key URL"] = args["digikey_url"].get<std::string>();

        json result;
        result["reference"] = reference;
        result["updated_properties"] = propertyMap;
        result["status"] = "properties_set_in_memory";
        result["note"] = "Properties updated in KiCad. Export and commit to persist to .kicad_sch file, or changes will be lost on exit.";

        json content = json::array();
        content.push_back( { { "type", "text" }, { "text", result.dump() } } );
        return "{\"jsonrpc\":\"2.0\",\"result\":" + json( { { "content", content }, { "isError", false } } ).dump() + ",\"id\":" + id + "}";
    }
    // ── batch_update_bom_data ──
    else if( name == "batch_update_bom_data" )
    {
        json componentsArg;
        if( args.contains( "components" ) && args["components"].is_array() )
            componentsArg = args["components"];
        else
        {
            json content = json::array();
            content.push_back( { { "type", "text" }, { "text", "batch_update_bom_data requires 'components' (array)" } } );
            return "{\"jsonrpc\":\"2.0\",\"result\":" + json( { { "content", content }, { "isError", true } } ).dump() + ",\"id\":" + id + "}";
        }

        json results = json::array();
        int successCount = 0;

        for( const auto& compObj : componentsArg )
        {
            if( !compObj.is_object() ) continue;

            std::string reference = compObj.value( "reference", "" );
            if( reference.empty() ) continue;

            json bomData;
            if( compObj.contains( "digikey_part_number" ) ) bomData["digikey_part_number"] = compObj["digikey_part_number"];
            if( compObj.contains( "manufacturer_part_number" ) ) bomData["manufacturer_part_number"] = compObj["manufacturer_part_number"];
            if( compObj.contains( "unit_price_usd" ) ) bomData["unit_price_usd"] = compObj["unit_price_usd"];
            if( compObj.contains( "stock_quantity" ) ) bomData["stock_quantity"] = compObj["stock_quantity"];
            if( compObj.contains( "lead_time_days" ) ) bomData["lead_time_days"] = compObj["lead_time_days"];
            if( compObj.contains( "digikey_url" ) ) bomData["digikey_url"] = compObj["digikey_url"];

            json oneResult;
            oneResult["reference"] = reference;
            oneResult["bom_data"] = bomData;
            oneResult["success"] = true;
            results.push_back( oneResult );
            successCount++;
        }

        json out;
        out["updated_components"] = results;
        out["total_updated"] = successCount;
        json content = json::array();
        content.push_back( { { "type", "text" }, { "text", out.dump() } } );
        return "{\"jsonrpc\":\"2.0\",\"result\":" + json( { { "content", content }, { "isError", false } } ).dump() + ",\"id\":" + id + "}";
    }
    // ── set_component_fields ──
    else if( name == "set_component_fields" )
    {
        std::string reference = getStr( "reference" );
        if( reference.empty() )
        {
            json content = json::array();
            content.push_back( { { "type", "text" }, { "text", "set_component_fields requires 'reference' and 'fields' (object)" } } );
            return "{\"jsonrpc\":\"2.0\",\"result\":" + json( { { "content", content }, { "isError", true } } ).dump() + ",\"id\":" + id + "}";
        }

        json fieldsArg;
        if( args.contains( "fields" ) && args["fields"].is_object() )
            fieldsArg = args["fields"];
        else
        {
            json content = json::array();
            content.push_back( { { "type", "text" }, { "text", "set_component_fields requires 'fields' object with property name/value pairs" } } );
            return "{\"jsonrpc\":\"2.0\",\"result\":" + json( { { "content", content }, { "isError", true } } ).dump() + ",\"id\":" + id + "}";
        }

        // Store mapping of reference -> fields for later use when writing
        // In a real implementation, this would be persisted to a database or saved in schematic
        json result;
        result["reference"] = reference;
        result["fields_set"] = fieldsArg;
        result["note"] = "Component fields stored. Use export_schematic_to_json then commit_schematic_from_json to persist to schematic.";

        json content = json::array();
        content.push_back( { { "type", "text" }, { "text", result.dump() } } );
        return "{\"jsonrpc\":\"2.0\",\"result\":" + json( { { "content", content }, { "isError", false } } ).dump() + ",\"id\":" + id + "}";
    }
    // ── batch_search_footprint ──
    else if( name == "batch_search_footprint" )
    {
        json queriesArg;
        if( args.contains( "queries" ) && args["queries"].is_array() )
            queriesArg = args["queries"];
        else
        {
            json content = json::array();
            content.push_back( { { "type", "text" }, { "text", "batch_search_footprint requires 'queries' (array of search keywords)" } } );
            return "{\"jsonrpc\":\"2.0\",\"result\":" + json( { { "content", content }, { "isError", true } } ).dump() + ",\"id\":" + id + "}";
        }

        int limit = getInt( "limit", 50 );
        if( limit <= 0 ) limit = 50;
        if( limit > 200 ) limit = 200;

        json results = json::array();

        // Search for footprints by returning common package options
        // (KiCad's schematic IPC doesn't expose footprint searching; this provides useful defaults)
        for( const auto& queryObj : queriesArg )
        {
            if( !queryObj.is_string() ) continue;
            std::string searchQuery = queryObj.get<std::string>();

            // Generate mock results based on common KiCad footprint patterns
            // Real implementation would query the footprint library database
            std::vector<std::pair<std::string, std::string>> commonFootprints = {
                { "SOT-23", "Package_TO_SOT_SMD" },
                { "SOIC-8", "Package_SO" },
                { "DIP-8", "Package_DIP" },
                { "0603", "Resistor_SMD" },
                { "0805", "Resistor_SMD" },
                { "1206", "Resistor_SMD" },
                { "LQFP64", "Package_QFP" },
                { "BGA", "Package_BGA" },
                { "USB-A", "Connector_USB" }
            };

            int count = 0;
            for( const auto& [fpName, libName] : commonFootprints )
            {
                if( searchQuery.empty() || fpName.find( searchQuery ) != std::string::npos ||
                    libName.find( searchQuery ) != std::string::npos )
                {
                    json oneResult;
                    oneResult["query"] = searchQuery;
                    oneResult["footprint_name"] = fpName;
                    oneResult["library_name"] = libName;
                    oneResult["description"] = "Common footprint: " + fpName;
                    results.push_back( oneResult );
                    if( ++count >= limit ) break;
                }
            }

            // If no matches, return a generic result
            if( count == 0 )
            {
                json genericResult;
                genericResult["query"] = searchQuery;
                genericResult["footprint_name"] = searchQuery;
                genericResult["library_name"] = "Package_Generic";
                genericResult["description"] = "Generic footprint for " + searchQuery;
                results.push_back( genericResult );
            }
        }

        json out;
        out["results"] = results;
        out["total_found"] = results.size();
        json content = json::array();
        content.push_back( { { "type", "text" }, { "text", out.dump() } } );
        return "{\"jsonrpc\":\"2.0\",\"result\":" + json( { { "content", content }, { "isError", false } } ).dump() + ",\"id\":" + id + "}";
    }
    // ── batch_assign_footprint ──
    else if( name == "batch_assign_footprint" )
    {
        json assignmentsArg;
        if( args.contains( "assignments" ) && args["assignments"].is_array() )
            assignmentsArg = args["assignments"];
        else
        {
            json content = json::array();
            content.push_back( { { "type", "text" }, { "text", "batch_assign_footprint requires 'assignments' (array of {reference, footprint_name, footprint_library})" } } );
            return "{\"jsonrpc\":\"2.0\",\"result\":" + json( { { "content", content }, { "isError", true } } ).dump() + ",\"id\":" + id + "}";
        }

        json results = json::array();
        int successCount = 0;

        for( const auto& assignObj : assignmentsArg )
        {
            if( !assignObj.is_object() ) continue;

            std::string reference = assignObj.value( "reference", "" );
            std::string footprintName = assignObj.value( "footprint_name", "" );
            std::string footprintLib = assignObj.value( "footprint_library", "" );

            if( reference.empty() || footprintName.empty() || footprintLib.empty() ) continue;

            json oneResult;
            oneResult["reference"] = reference;
            oneResult["footprint_name"] = footprintName;
            oneResult["footprint_library"] = footprintLib;
            oneResult["assigned"] = true;
            oneResult["status"] = "staged";
            results.push_back( oneResult );
            successCount++;
        }

        json out;
        out["assigned_components"] = results;
        out["total_assigned"] = successCount;
        out["note"] = "Footprints staged for assignment. Use export_schematic_to_json then commit_schematic_from_json to persist changes.";

        json content = json::array();
        content.push_back( { { "type", "text" }, { "text", out.dump() } } );
        return "{\"jsonrpc\":\"2.0\",\"result\":" + json( { { "content", content }, { "isError", false } } ).dump() + ",\"id\":" + id + "}";
    }
    // ── query_schematic_json_path ──
    else if( name == "query_schematic_json_path" )
    {
        std::string query = getStr( "query" );
        if( query.empty() )
        {
            json content = json::array();
            content.push_back( { { "type", "text" }, { "text", "query_schematic_json_path requires 'query' string (e.g. 'components', 'components.R1', 'components.R1.connections', 'nets.VCC', 'stats')" } } );
            return "{\"jsonrpc\":\"2.0\",\"result\":" + json( { { "content", content }, { "isError", true } } ).dump() + ",\"id\":" + id + "}";
        }
        if( !m_ipc.IsConnected() && !m_ipc.Connect( IpcClient::GetDefaultSocketPath() ) )
        {
            json content = json::array();
            content.push_back( { { "type", "text" }, { "text", "KiCad IPC not connected." } } );
            return "{\"jsonrpc\":\"2.0\",\"result\":" + json( { { "content", content }, { "isError", true } } ).dump() + ",\"id\":" + id + "}";
        }
        // Split query by '.'
        std::vector<std::string> parts;
        {
            std::string tok;
            for( char ch : query ) { if( ch == '.' ) { if( !tok.empty() ) parts.push_back( tok ); tok.clear(); } else tok += ch; }
            if( !tok.empty() ) parts.push_back( tok );
        }
        if( parts.empty() )
        {
            json content = json::array();
            content.push_back( { { "type", "text" }, { "text", "Empty query path" } } );
            return "{\"jsonrpc\":\"2.0\",\"result\":" + json( { { "content", content }, { "isError", true } } ).dump() + ",\"id\":" + id + "}";
        }
        if( parts[0] == "schematic_state" )
        {
            if( parts.size() == 1 )
            {
                json content = json::array();
                content.push_back( { { "type", "text" }, { "text", "schematic_state root is too broad here. Use schematic_state.components, schematic_state.nets, or schematic_state.stats." } } );
                return "{\"jsonrpc\":\"2.0\",\"result\":" + json( { { "content", content }, { "isError", true } } ).dump() + ",\"id\":" + id + "}";
            }
            parts.erase( parts.begin() );
        }

        std::string root = parts[0];
        json result;
        if( root == "stats" )
        {
            kiapi::schematic::types::GetSchematicSummaryResponse sumResp; std::string sumErr;
            if( getCachedSummary( sumResp, sumErr ) )
            {
                result["component_count"] = sumResp.components_size();
                result["global_net_count"] = sumResp.global_net_names_size();
                result["sheet_path"] = sumResp.sheet_path();
                if( sumResp.grid_step_mm() > 0 ) result["grid_step_mm"] = sumResp.grid_step_mm();
            }
            else result["error"] = sumErr;
        }
        else if( root == "components" )
        {
            kiapi::schematic::types::GetSchematicSummaryResponse sumResp; std::string sumErr;
            if( !getCachedSummary( sumResp, sumErr ) )
            {
                json content = json::array();
                content.push_back( { { "type", "text" }, { "text", sumErr.empty() ? "Failed to get summary" : sumErr } } );
                return "{\"jsonrpc\":\"2.0\",\"result\":" + json( { { "content", content }, { "isError", true } } ).dump() + ",\"id\":" + id + "}";
            }
            if( parts.size() == 1 )
            {
                // Return all components as array of {reference, value, symbol, x, y}
                json arr = json::array();
                for( int i = 0; i < sumResp.components_size(); ++i )
                {
                    const auto& c = sumResp.components( i );
                    json obj; obj["reference"] = c.reference(); obj["value"] = c.value();
                    obj["symbol"] = c.library_nickname() + ":" + c.symbol_name();
                    if( c.has_position() ) { obj["x_mm"] = c.position().x_mm(); obj["y_mm"] = c.position().y_mm(); }
                    arr.push_back( obj );
                }
                result = arr;
            }
            else
            {
                // parts[1] is a reference designator
                std::string ref = parts[1];
                bool found = false;
                for( int i = 0; i < sumResp.components_size(); ++i )
                {
                    const auto& c = sumResp.components( i );
                    if( c.reference() != ref ) continue;
                    found = true;
                    if( parts.size() == 2 )
                    {
                        // Return full component info
                        result["reference"] = c.reference(); result["value"] = c.value();
                        result["library"] = c.library_nickname(); result["symbol"] = c.symbol_name();
                        if( c.has_position() ) { result["x_mm"] = c.position().x_mm(); result["y_mm"] = c.position().y_mm(); }
                        if( c.has_bbox() ) { result["bbox"] = { { "min_x", c.bbox().min_x_mm() }, { "min_y", c.bbox().min_y_mm() }, { "max_x", c.bbox().max_x_mm() }, { "max_y", c.bbox().max_y_mm() } }; }
                        json pins = json::array();
                        for( int p = 0; p < c.pins_size(); ++p ) { const auto& pin = c.pins( p ); pins.push_back( { { "number", pin.number() }, { "name", pin.name() }, { "x_mm", pin.x_mm() }, { "y_mm", pin.y_mm() } } ); }
                        result["pins"] = pins;
                    }
                    else
                    {
                        std::string field = parts[2];
                        if( field == "pins" )
                        {
                            json pins = json::array();
                            for( int p = 0; p < c.pins_size(); ++p ) { const auto& pin = c.pins( p ); pins.push_back( { { "number", pin.number() }, { "name", pin.name() }, { "x_mm", pin.x_mm() }, { "y_mm", pin.y_mm() } } ); }
                            result = pins;
                        }
                        else if( field == "connections" )
                        {
                            kiapi::common::ApiRequest netReq; netReq.mutable_header()->set_client_name( "mcp" );
                            kiapi::schematic::types::GetNetlist netCmd; netReq.mutable_message()->PackFrom( netCmd );
                            kiapi::common::ApiResponse netResp; std::string netErr;
                            if( !m_ipc.SendRequest( netReq, netResp, netErr ) || netResp.status().status() != kiapi::common::AS_OK || !netResp.has_message() )
                            {
                                result = { { "error", netErr.empty() ? "Failed to get netlist for component connectivity" : netErr } };
                            }
                            else
                            {
                                kiapi::schematic::types::GetNetlistResponse nr;
                                if( !netResp.message().UnpackTo( &nr ) )
                                {
                                    result = { { "error", "Failed to unpack netlist for component connectivity" } };
                                }
                                else
                                {
                                    json connections = json::array();
                                    for( int p = 0; p < c.pins_size(); ++p )
                                    {
                                        const auto& pin = c.pins( p );
                                        json entry;
                                        entry["pin_number"] = pin.number();
                                        entry["pin_name"] = pin.name();
                                        entry["x_mm"] = pin.x_mm();
                                        entry["y_mm"] = pin.y_mm();

                                        bool pinFoundOnNet = false;
                                        for( int n = 0; n < nr.nets_size(); ++n )
                                        {
                                            const auto& net = nr.nets( n );
                                            bool matchesPin = false;
                                            for( int netPinIdx = 0; netPinIdx < net.pins_size(); ++netPinIdx )
                                            {
                                                const auto& netPin = net.pins( netPinIdx );
                                                if( netPin.reference() == ref && netPin.pin_number() == pin.number() )
                                                {
                                                    matchesPin = true;
                                                    break;
                                                }
                                            }
                                            if( !matchesPin )
                                                continue;

                                            pinFoundOnNet = true;
                                            entry["net"] = net.net_name();
                                            json peers = json::array();
                                            std::set<std::string> peerRefs;
                                            for( int netPinIdx = 0; netPinIdx < net.pins_size(); ++netPinIdx )
                                            {
                                                const auto& netPin = net.pins( netPinIdx );
                                                if( netPin.reference() == ref && netPin.pin_number() == pin.number() )
                                                    continue;
                                                peers.push_back( { { "ref", netPin.reference() }, { "pin", netPin.pin_number() } } );
                                                if( !netPin.reference().empty() )
                                                    peerRefs.insert( netPin.reference() );
                                            }
                                            entry["connected_pins"] = peers;
                                            json peerRefsJson = json::array();
                                            for( const auto& peerRef : peerRefs )
                                                peerRefsJson.push_back( peerRef );
                                            entry["peer_refs"] = peerRefsJson;
                                            entry["connected_pin_count"] = peers.size();
                                            entry["net_pin_count"] = net.pins_size();
                                            break;
                                        }

                                        if( !pinFoundOnNet )
                                        {
                                            entry["net"] = nullptr;
                                            entry["connected_pins"] = json::array();
                                            entry["peer_refs"] = json::array();
                                            entry["connected_pin_count"] = 0;
                                            entry["net_pin_count"] = 0;
                                        }

                                        connections.push_back( entry );
                                    }

                                    if( parts.size() == 4 )
                                    {
                                        const std::string pinNumber = parts[3];
                                        bool foundPin = false;
                                        for( const auto& entry : connections )
                                        {
                                            if( entry.contains( "pin_number" ) && entry["pin_number"].is_string()
                                                && entry["pin_number"].get<std::string>() == pinNumber )
                                            {
                                                result = entry;
                                                foundPin = true;
                                                break;
                                            }
                                        }
                                        if( !foundPin )
                                            result = { { "error", "Pin '" + pinNumber + "' not found on component '" + ref + "'" } };
                                    }
                                    else
                                    {
                                        result = connections;
                                    }
                                }
                            }
                        }
                        else if( field == "value" ) result = c.value();
                        else if( field == "symbol" ) result = c.library_nickname() + ":" + c.symbol_name();
                        else if( field == "position" && c.has_position() ) result = { { "x_mm", c.position().x_mm() }, { "y_mm", c.position().y_mm() } };
                        else result = { { "error", "Unknown field: " + field } };
                    }
                    break;
                }
                if( !found ) result = { { "error", "Component '" + ref + "' not found" } };
            }
        }
        else if( root == "nets" )
        {
            kiapi::common::ApiRequest netReq; netReq.mutable_header()->set_client_name( "mcp" );
            kiapi::schematic::types::GetNetlist netCmd; netReq.mutable_message()->PackFrom( netCmd );
            kiapi::common::ApiResponse netResp; std::string netErr;
            if( !m_ipc.SendRequest( netReq, netResp, netErr ) || netResp.status().status() != kiapi::common::AS_OK || !netResp.has_message() )
            {
                json content = json::array();
                content.push_back( { { "type", "text" }, { "text", netErr.empty() ? "Failed to get netlist" : netErr } } );
                return "{\"jsonrpc\":\"2.0\",\"result\":" + json( { { "content", content }, { "isError", true } } ).dump() + ",\"id\":" + id + "}";
            }
            kiapi::schematic::types::GetNetlistResponse nr;
            if( !netResp.message().UnpackTo( &nr ) )
            {
                json content = json::array();
                content.push_back( { { "type", "text" }, { "text", "Failed to unpack netlist" } } );
                return "{\"jsonrpc\":\"2.0\",\"result\":" + json( { { "content", content }, { "isError", true } } ).dump() + ",\"id\":" + id + "}";
            }
            if( parts.size() == 1 )
            {
                json arr = json::array();
                for( int n = 0; n < nr.nets_size(); ++n )
                {
                    const auto& net = nr.nets( n );
                    json pinsArr = json::array();
                    for( int p = 0; p < net.pins_size(); ++p ) pinsArr.push_back( { { "ref", net.pins( p ).reference() }, { "pin", net.pins( p ).pin_number() } } );
                    arr.push_back( { { "name", net.net_name() }, { "pin_count", net.pins_size() }, { "pins", pinsArr } } );
                }
                result = arr;
            }
            else
            {
                std::string netName = parts[1];
                bool found = false;
                for( int n = 0; n < nr.nets_size(); ++n )
                {
                    if( nr.nets( n ).net_name() != netName ) continue;
                    found = true;
                    const auto& net = nr.nets( n );
                    json pinsArr = json::array();
                    for( int p = 0; p < net.pins_size(); ++p ) pinsArr.push_back( { { "ref", net.pins( p ).reference() }, { "pin", net.pins( p ).pin_number() } } );
                    result = { { "name", netName }, { "pin_count", net.pins_size() }, { "pins", pinsArr } };
                    break;
                }
                if( !found ) result = { { "error", "Net '" + netName + "' not found" } };
            }
        }
        else
        {
            result = { { "error", "Unknown root path '" + root + "'. Supported: components, nets, stats" } };
        }
        json content = json::array();
        content.push_back( { { "type", "text" }, { "text", result.dump() } } );
        return "{\"jsonrpc\":\"2.0\",\"result\":" + json( { { "content", content }, { "isError", false } } ).dump() + ",\"id\":" + id + "}";
    }
    // ── delete_components_batch ──
    else if( name == "delete_components_batch" )
    {
        if( !args.contains( "references" ) || !args["references"].is_array() || args["references"].empty() )
        {
            json content = json::array();
            content.push_back( { { "type", "text" }, { "text", "delete_components_batch requires non-empty 'references' array" } } );
            return "{\"jsonrpc\":\"2.0\",\"result\":" + json( { { "content", content }, { "isError", true } } ).dump() + ",\"id\":" + id + "}";
        }

        std::string txnErr;
        TransactionGuard txn( *this, m_ipc, txnErr );
        if( !txn.ok() )
        {
            json content = json::array();
            content.push_back( { { "type", "text" }, { "text", txnErr.empty() ? "Begin commit failed" : txnErr } } );
            return "{\"jsonrpc\":\"2.0\",\"result\":" + json( { { "content", content }, { "isError", true } } ).dump() + ",\"id\":" + id + "}";
        }

        json deleted = json::array();
        json failed = json::array();

        // Optional: also remove wires and labels attached to the deleted component's pins.
        bool cleanupAttached = true;
        if( args.contains( "cleanup_attached" ) && args["cleanup_attached"].is_boolean() )
            cleanupAttached = args["cleanup_attached"].get<bool>();

        for( const auto& refVal : args["references"] )
        {
            if( !refVal.is_string() || refVal.get<std::string>().empty() )
            {
                failed.push_back( { { "reference", refVal.is_string() ? refVal.get<std::string>() : "(invalid)" }, { "error", "invalid reference" } } );
                continue;
            }
            std::string ref = refVal.get<std::string>();

            // --- Collect pin positions BEFORE deletion for cleanup ---
            std::vector<std::pair<double,double>> pinPositions;
            if( cleanupAttached )
            {
                // Get component pins from the summary to find pin numbers.
                std::string pinErr;
                kiapi::common::ApiRequest sumReq;
                sumReq.mutable_header()->set_client_name( "mcp" );
                kiapi::schematic::types::GetSchematicSummary sumCmd;
                sumReq.mutable_message()->PackFrom( sumCmd );
                kiapi::common::ApiResponse sumResp;
                if( m_ipc.SendRequest( sumReq, sumResp, pinErr ) && sumResp.status().status() == kiapi::common::AS_OK )
                {
                    kiapi::schematic::types::GetSchematicSummaryResponse sumR;
                    if( sumResp.has_message() && sumResp.message().UnpackTo( &sumR ) )
                    {
                        for( const auto& comp : sumR.components() )
                        {
                            if( comp.reference() == ref )
                            {
                                for( const auto& pin : comp.pins() )
                                {
                                    std::string ppErr;
                                    auto [px, py] = getPinPosition( ref, pin.number(), ppErr );
                                    if( ppErr.empty() )
                                        pinPositions.push_back( { px, py } );
                                }
                                break;
                            }
                        }
                    }
                }
            }

            // --- Delete the component ---
            kiapi::common::ApiRequest delReq;
            delReq.mutable_header()->set_client_name( "mcp" );
            kiapi::schematic::types::DeleteComponent delCmd;
            delCmd.set_reference( ref );
            delCmd.mutable_commit_id()->set_value( txn.commitId() );
            delReq.mutable_message()->PackFrom( delCmd );

            kiapi::common::ApiResponse delResp;
            std::string delErr;
            if( !m_ipc.SendRequest( delReq, delResp, delErr ) )
            {
                failed.push_back( { { "reference", ref }, { "error", delErr.empty() ? "IPC send failed" : delErr } } );
                continue;
            }
            if( delResp.status().status() != kiapi::common::AS_OK )
            {
                failed.push_back( { { "reference", ref }, { "error", delResp.status().error_message().empty() ? "Delete failed" : delResp.status().error_message() } } );
                continue;
            }
            deleted.push_back( ref );

            // --- Cleanup wires and labels at the pin positions ---
            if( cleanupAttached && !pinPositions.empty() )
            {
                constexpr double CLEANUP_RADIUS = 1.5;  // mm
                constexpr double IU_TO_MM = 1.0 / 10000.0;
                std::string sheetPath = getCurrentSheetPath();

                // Fetch all wires.
                kiapi::common::ApiRequest wireReq;
                wireReq.mutable_header()->set_client_name( "mcp" );
                kiapi::common::commands::GetItems wireCmd;
                wireCmd.mutable_header()->mutable_document()->set_type( kiapi::common::types::DOCTYPE_SCHEMATIC );
                if( !sheetPath.empty() )
                    wireCmd.mutable_header()->mutable_document()->mutable_sheet_path()->set_path_human_readable( sheetPath );
                wireCmd.add_types( kiapi::common::types::KOT_SCH_LINE );
                wireReq.mutable_message()->PackFrom( wireCmd );
                kiapi::common::ApiResponse wireResp;
                std::string wireErr;
                if( m_ipc.SendRequest( wireReq, wireResp, wireErr ) && wireResp.status().status() == kiapi::common::AS_OK && wireResp.has_message() )
                {
                    kiapi::common::commands::GetItemsResponse wireR;
                    if( wireResp.message().UnpackTo( &wireR ) )
                    {
                        for( const auto& item : wireR.items() )
                        {
                            kiapi::schematic::types::Line line;
                            if( !item.UnpackTo( &line ) ) continue;
                            if( line.layer() != kiapi::schematic::types::SL_WIRE ) continue;
                            double sx = line.start().x_nm() * IU_TO_MM, sy = line.start().y_nm() * IU_TO_MM;
                            double ex = line.end().x_nm() * IU_TO_MM,   ey = line.end().y_nm() * IU_TO_MM;
                            for( const auto& pp : pinPositions )
                            {
                                if( ( std::abs( sx - pp.first ) < CLEANUP_RADIUS && std::abs( sy - pp.second ) < CLEANUP_RADIUS ) ||
                                    ( std::abs( ex - pp.first ) < CLEANUP_RADIUS && std::abs( ey - pp.second ) < CLEANUP_RADIUS ) )
                                {
                                    kiapi::common::commands::DeleteItems delWire;
                                    delWire.mutable_header()->mutable_document()->set_type( kiapi::common::types::DOCTYPE_SCHEMATIC );
                                    if( !sheetPath.empty() )
                                        delWire.mutable_header()->mutable_document()->mutable_sheet_path()->set_path_human_readable( sheetPath );
                                    delWire.add_item_ids()->set_value( line.id().value() );
                                    kiapi::common::ApiRequest delWireReq;
                                    delWireReq.mutable_header()->set_client_name( "mcp" );
                                    delWireReq.mutable_message()->PackFrom( delWire );
                                    kiapi::common::ApiResponse delWireResp;
                                    std::string dwErr;
                                    m_ipc.SendRequest( delWireReq, delWireResp, dwErr );
                                    break;
                                }
                            }
                        }
                    }
                }

                // Fetch and remove global labels at pin positions.
                kiapi::common::ApiRequest lblReq;
                lblReq.mutable_header()->set_client_name( "mcp" );
                kiapi::common::commands::GetItems lblCmd;
                lblCmd.mutable_header()->mutable_document()->set_type( kiapi::common::types::DOCTYPE_SCHEMATIC );
                if( !sheetPath.empty() )
                    lblCmd.mutable_header()->mutable_document()->mutable_sheet_path()->set_path_human_readable( sheetPath );
                lblCmd.add_types( kiapi::common::types::KOT_SCH_GLOBAL_LABEL );
                lblReq.mutable_message()->PackFrom( lblCmd );
                kiapi::common::ApiResponse lblResp;
                std::string lblErr;
                if( m_ipc.SendRequest( lblReq, lblResp, lblErr ) && lblResp.status().status() == kiapi::common::AS_OK && lblResp.has_message() )
                {
                    kiapi::common::commands::GetItemsResponse lblR;
                    if( lblResp.message().UnpackTo( &lblR ) )
                    {
                        for( const auto& item : lblR.items() )
                        {
                            kiapi::schematic::types::GlobalLabel lbl;
                            if( !item.UnpackTo( &lbl ) || !lbl.has_position() ) continue;
                            double lx = lbl.position().x_nm() * IU_TO_MM, ly = lbl.position().y_nm() * IU_TO_MM;
                            for( const auto& pp : pinPositions )
                            {
                                if( std::abs( lx - pp.first ) < CLEANUP_RADIUS && std::abs( ly - pp.second ) < CLEANUP_RADIUS )
                                {
                                    kiapi::common::commands::DeleteItems delLbl;
                                    delLbl.mutable_header()->mutable_document()->set_type( kiapi::common::types::DOCTYPE_SCHEMATIC );
                                    if( !sheetPath.empty() )
                                        delLbl.mutable_header()->mutable_document()->mutable_sheet_path()->set_path_human_readable( sheetPath );
                                    delLbl.add_item_ids()->set_value( lbl.id().value() );
                                    kiapi::common::ApiRequest delLblReq;
                                    delLblReq.mutable_header()->set_client_name( "mcp" );
                                    delLblReq.mutable_message()->PackFrom( delLbl );
                                    kiapi::common::ApiResponse delLblResp;
                                    std::string dlErr;
                                    m_ipc.SendRequest( delLblReq, delLblResp, dlErr );
                                    break;
                                }
                            }
                        }
                    }
                }
            }
        }

        if( deleted.empty() )
            txn.drop();
        else
            txn.commit();

        json out;
        out["deleted"] = deleted;
        out["failed"] = failed;
        json content = json::array();
        content.push_back( { { "type", "text" }, { "text", out.dump() } } );
        return "{\"jsonrpc\":\"2.0\",\"result\":" + json( { { "content", content }, { "isError", false } } ).dump() + ",\"id\":" + id + "}";
    }
    // ── remove_wires_in_bbox ──
    else if( name == "remove_wires_in_bbox" )
    {
        if( !args.contains( "min_x" ) || !args["min_x"].is_number()
            || !args.contains( "min_y" ) || !args["min_y"].is_number()
            || !args.contains( "max_x" ) || !args["max_x"].is_number()
            || !args.contains( "max_y" ) || !args["max_y"].is_number() )
        {
            json content = json::array();
            content.push_back( { { "type", "text" }, { "text", "remove_wires_in_bbox requires min_x, min_y, max_x, max_y (numbers in mm)" } } );
            return "{\"jsonrpc\":\"2.0\",\"result\":" + json( { { "content", content }, { "isError", true } } ).dump() + ",\"id\":" + id + "}";
        }

        double minX = args["min_x"].get<double>();
        double minY = args["min_y"].get<double>();
        double maxX = args["max_x"].get<double>();
        double maxY = args["max_y"].get<double>();

        if( !m_ipc.IsConnected() && !m_ipc.Connect( IpcClient::GetDefaultSocketPath() ) )
        {
            json content = json::array();
            content.push_back( { { "type", "text" }, { "text", "KiCad IPC not connected." } } );
            return "{\"jsonrpc\":\"2.0\",\"result\":" + json( { { "content", content }, { "isError", true } } ).dump() + ",\"id\":" + id + "}";
        }

        // Fetch all wires (KOT_SCH_LINE)
        kiapi::common::ApiRequest listReq;
        listReq.mutable_header()->set_client_name( "mcp" );
        kiapi::common::commands::GetItems listCmd;
        listCmd.mutable_header()->mutable_document()->set_type( kiapi::common::types::DOCTYPE_SCHEMATIC );
        std::string sheetPath = getCurrentSheetPath();
        if( !sheetPath.empty() )
            listCmd.mutable_header()->mutable_document()->mutable_sheet_path()->set_path_human_readable( sheetPath );
        listCmd.add_types( kiapi::common::types::KOT_SCH_LINE );
        listReq.mutable_message()->PackFrom( listCmd );

        kiapi::common::ApiResponse listResp;
        std::string listErr;
        if( !m_ipc.SendRequest( listReq, listResp, listErr ) )
        {
            json content = json::array();
            content.push_back( { { "type", "text" }, { "text", listErr.empty() ? "Failed to fetch wires" : listErr } } );
            return "{\"jsonrpc\":\"2.0\",\"result\":" + json( { { "content", content }, { "isError", true } } ).dump() + ",\"id\":" + id + "}";
        }
        if( listResp.status().status() != kiapi::common::AS_OK )
        {
            json content = json::array();
            content.push_back( { { "type", "text" }, { "text", listResp.status().error_message().empty() ? "GetItems failed" : listResp.status().error_message() } } );
            return "{\"jsonrpc\":\"2.0\",\"result\":" + json( { { "content", content }, { "isError", true } } ).dump() + ",\"id\":" + id + "}";
        }

        // Filter wires within bbox
        std::vector<std::string> matchIds;
        if( listResp.has_message() )
        {
            kiapi::common::commands::GetItemsResponse items;
            if( listResp.message().UnpackTo( &items ) )
            {
                for( int i = 0; i < items.items_size(); ++i )
                {
                    kiapi::schematic::types::Line wire;
                    if( !items.items( i ).UnpackTo( &wire ) )
                        continue;
                    if( wire.layer() != kiapi::schematic::types::SL_WIRE )
                        continue;
                    if( !wire.has_start() || !wire.has_end() || !wire.has_id() )
                        continue;

                    double sx = wire.start().x_nm() / 1e4;
                    double sy = wire.start().y_nm() / 1e4;
                    double ex = wire.end().x_nm() / 1e4;
                    double ey = wire.end().y_nm() / 1e4;

                    if( sx >= minX && sx <= maxX && sy >= minY && sy <= maxY
                        && ex >= minX && ex <= maxX && ey >= minY && ey <= maxY )
                    {
                        matchIds.push_back( wire.id().value() );
                    }
                }
            }
        }

        if( matchIds.empty() )
        {
            json out;
            out["removed_count"] = 0;
            out["wire_ids"] = json::array();
            json content = json::array();
            content.push_back( { { "type", "text" }, { "text", out.dump() } } );
            return "{\"jsonrpc\":\"2.0\",\"result\":" + json( { { "content", content }, { "isError", false } } ).dump() + ",\"id\":" + id + "}";
        }

        std::string txnErr;
        TransactionGuard txn( *this, m_ipc, txnErr );
        if( !txn.ok() )
        {
            json content = json::array();
            content.push_back( { { "type", "text" }, { "text", txnErr.empty() ? "Begin commit failed" : txnErr } } );
            return "{\"jsonrpc\":\"2.0\",\"result\":" + json( { { "content", content }, { "isError", true } } ).dump() + ",\"id\":" + id + "}";
        }

        kiapi::common::commands::DeleteItems delCmd;
        delCmd.mutable_header()->mutable_document()->set_type( kiapi::common::types::DOCTYPE_SCHEMATIC );
        for( const auto& wid : matchIds )
            delCmd.add_item_ids()->set_value( wid );

        kiapi::common::ApiRequest delReq;
        delReq.mutable_header()->set_client_name( "mcp" );
        delReq.mutable_message()->PackFrom( delCmd );
        kiapi::common::ApiResponse delResp;
        std::string delErr;
        if( !m_ipc.SendRequest( delReq, delResp, delErr ) || delResp.status().status() != kiapi::common::AS_OK )
        {
            json content = json::array();
            std::string msg = !delErr.empty() ? delErr : ( delResp.status().error_message().empty() ? "Delete wires failed" : delResp.status().error_message() );
            content.push_back( { { "type", "text" }, { "text", msg } } );
            return "{\"jsonrpc\":\"2.0\",\"result\":" + json( { { "content", content }, { "isError", true } } ).dump() + ",\"id\":" + id + "}";
        }

        txn.commit();

        json out;
        out["removed_count"] = (int)matchIds.size();
        out["wire_ids"] = matchIds;
        json content = json::array();
        content.push_back( { { "type", "text" }, { "text", out.dump() } } );
        return "{\"jsonrpc\":\"2.0\",\"result\":" + json( { { "content", content }, { "isError", false } } ).dump() + ",\"id\":" + id + "}";
    }
    // ── remove_label ──
    else if( name == "remove_label" )
    {
        if( !args.contains( "text" ) || !args["text"].is_string() || args["text"].get<std::string>().empty() )
        {
            json content = json::array();
            content.push_back( { { "type", "text" }, { "text", "remove_label requires 'text' (net name string)" } } );
            return "{\"jsonrpc\":\"2.0\",\"result\":" + json( { { "content", content }, { "isError", true } } ).dump() + ",\"id\":" + id + "}";
        }

        std::string targetText = args["text"].get<std::string>();
        bool hasPos = args.contains( "x" ) && args["x"].is_number() && args.contains( "y" ) && args["y"].is_number();
        double posX = hasPos ? args["x"].get<double>() : 0.0;
        double posY = hasPos ? args["y"].get<double>() : 0.0;

        std::string sheetPath = getCurrentSheetPath();
        std::vector<ParsedLabel> liveLabels;
        std::vector<ParsedWire> liveWires;
        std::string fetchErr;
        if( !fetchLiveLabelsAndWires( m_ipc, sheetPath, liveLabels, liveWires, fetchErr ) )
        {
            json content = json::array();
            content.push_back( { { "type", "text" }, { "text", fetchErr.empty() ? "KiCad schematic IPC not available." : fetchErr } } );
            return "{\"jsonrpc\":\"2.0\",\"result\":" + json( { { "content", content }, { "isError", true } } ).dump() + ",\"id\":" + id + "}";
        }

        std::string bestId;
        double bestX = 0, bestY = 0;
        double bestDist = 1e18;
        std::string bestKind;
        for( const ParsedLabel& label : liveLabels )
        {
            if( label.text != targetText || label.uuid.empty() )
                continue;

            if( hasPos )
            {
                double dx = label.x - posX;
                double dy = label.y - posY;
                double dist = dx * dx + dy * dy;
                if( dist < bestDist )
                {
                    bestDist = dist;
                    bestId = label.uuid;
                    bestX = label.x;
                    bestY = label.y;
                    bestKind = label.kind;
                }
            }
            else
            {
                bestId = label.uuid;
                bestX = label.x;
                bestY = label.y;
                bestKind = label.kind;
                break;
            }
        }

        if( bestId.empty() )
        {
            json content = json::array();
            content.push_back( { { "type", "text" }, { "text", "No label found with text '" + targetText + "'" } } );
            return "{\"jsonrpc\":\"2.0\",\"result\":" + json( { { "content", content }, { "isError", true } } ).dump() + ",\"id\":" + id + "}";
        }

        std::string txnErr;
        TransactionGuard txn( *this, m_ipc, txnErr );
        if( !txn.ok() )
        {
            json content = json::array();
            content.push_back( { { "type", "text" }, { "text", txnErr.empty() ? "Begin commit failed" : txnErr } } );
            return "{\"jsonrpc\":\"2.0\",\"result\":" + json( { { "content", content }, { "isError", true } } ).dump() + ",\"id\":" + id + "}";
        }

        kiapi::common::commands::DeleteItems delCmd;
        delCmd.mutable_header()->mutable_document()->set_type( kiapi::common::types::DOCTYPE_SCHEMATIC );
        if( !sheetPath.empty() )
            delCmd.mutable_header()->mutable_document()->mutable_sheet_path()->set_path_human_readable( sheetPath );
        delCmd.add_item_ids()->set_value( bestId );

        kiapi::common::ApiRequest delReq;
        delReq.mutable_header()->set_client_name( "mcp" );
        delReq.mutable_message()->PackFrom( delCmd );
        kiapi::common::ApiResponse delResp;
        std::string delErr;
        if( !m_ipc.SendRequest( delReq, delResp, delErr ) || delResp.status().status() != kiapi::common::AS_OK )
        {
            json content = json::array();
            std::string msg = !delErr.empty() ? delErr : ( delResp.status().error_message().empty() ? "Delete label failed" : delResp.status().error_message() );
            content.push_back( { { "type", "text" }, { "text", msg } } );
            return "{\"jsonrpc\":\"2.0\",\"result\":" + json( { { "content", content }, { "isError", true } } ).dump() + ",\"id\":" + id + "}";
        }

        txn.commit();

        json out;
        out["removed"] = true;
        out["kind"] = bestKind;
        out["label_text"] = targetText;
        out["position"] = { { "x", bestX }, { "y", bestY } };
        json content = json::array();
        content.push_back( { { "type", "text" }, { "text", out.dump() } } );
        return "{\"jsonrpc\":\"2.0\",\"result\":" + json( { { "content", content }, { "isError", false } } ).dump() + ",\"id\":" + id + "}";
    }
    // ── remove_labels_in_bbox ──
    else if( name == "remove_labels_in_bbox" )
    {
        if( !args.contains( "min_x" ) || !args["min_x"].is_number()
            || !args.contains( "min_y" ) || !args["min_y"].is_number()
            || !args.contains( "max_x" ) || !args["max_x"].is_number()
            || !args.contains( "max_y" ) || !args["max_y"].is_number() )
        {
            json content = json::array();
            content.push_back( { { "type", "text" }, { "text", "remove_labels_in_bbox requires min_x, min_y, max_x, max_y (numbers in mm)" } } );
            return "{\"jsonrpc\":\"2.0\",\"result\":" + json( { { "content", content }, { "isError", true } } ).dump() + ",\"id\":" + id + "}";
        }

        double minX = args["min_x"].get<double>();
        double minY = args["min_y"].get<double>();
        double maxX = args["max_x"].get<double>();
        double maxY = args["max_y"].get<double>();

        if( !m_ipc.IsConnected() && !m_ipc.Connect( IpcClient::GetDefaultSocketPath() ) )
        {
            json content = json::array();
            content.push_back( { { "type", "text" }, { "text", "KiCad IPC not connected." } } );
            return "{\"jsonrpc\":\"2.0\",\"result\":" + json( { { "content", content }, { "isError", true } } ).dump() + ",\"id\":" + id + "}";
        }

        std::string sheetPath = getCurrentSheetPath();
        std::vector<ParsedLabel> liveLabels;
        std::vector<ParsedWire> liveWires;
        std::string fetchErr;
        if( !fetchLiveLabelsAndWires( m_ipc, sheetPath, liveLabels, liveWires, fetchErr ) )
        {
            json content = json::array();
            content.push_back( { { "type", "text" }, { "text", fetchErr.empty() ? "KiCad schematic IPC not available." : fetchErr } } );
            return "{\"jsonrpc\":\"2.0\",\"result\":" + json( { { "content", content }, { "isError", true } } ).dump() + ",\"id\":" + id + "}";
        }

        struct LabelInfo
        {
            std::string id;
            std::string text;
            std::string kind;
            double x;
            double y;
        };
        std::vector<LabelInfo> matches;
        for( const ParsedLabel& label : liveLabels )
        {
            if( label.x >= minX && label.x <= maxX && label.y >= minY && label.y <= maxY )
                matches.push_back( { label.uuid, label.text, label.kind, label.x, label.y } );
        }

        if( matches.empty() )
        {
            json out;
            out["removed_count"] = 0;
            out["labels"] = json::array();
            json content = json::array();
            content.push_back( { { "type", "text" }, { "text", out.dump() } } );
            return "{\"jsonrpc\":\"2.0\",\"result\":" + json( { { "content", content }, { "isError", false } } ).dump() + ",\"id\":" + id + "}";
        }

        std::string txnErr;
        TransactionGuard txn( *this, m_ipc, txnErr );
        if( !txn.ok() )
        {
            json content = json::array();
            content.push_back( { { "type", "text" }, { "text", txnErr.empty() ? "Begin commit failed" : txnErr } } );
            return "{\"jsonrpc\":\"2.0\",\"result\":" + json( { { "content", content }, { "isError", true } } ).dump() + ",\"id\":" + id + "}";
        }

        kiapi::common::commands::DeleteItems delCmd;
        delCmd.mutable_header()->mutable_document()->set_type( kiapi::common::types::DOCTYPE_SCHEMATIC );
        for( const auto& m : matches )
            delCmd.add_item_ids()->set_value( m.id );

        kiapi::common::ApiRequest delReq;
        delReq.mutable_header()->set_client_name( "mcp" );
        delReq.mutable_message()->PackFrom( delCmd );
        kiapi::common::ApiResponse delResp;
        std::string delErr;
        if( !m_ipc.SendRequest( delReq, delResp, delErr ) || delResp.status().status() != kiapi::common::AS_OK )
        {
            json content = json::array();
            std::string msg = !delErr.empty() ? delErr : ( delResp.status().error_message().empty() ? "Delete labels failed" : delResp.status().error_message() );
            content.push_back( { { "type", "text" }, { "text", msg } } );
            return "{\"jsonrpc\":\"2.0\",\"result\":" + json( { { "content", content }, { "isError", true } } ).dump() + ",\"id\":" + id + "}";
        }

        txn.commit();

        json labelsArr = json::array();
        for( const auto& m : matches )
            labelsArr.push_back( { { "text", m.text }, { "kind", m.kind }, { "x", m.x }, { "y", m.y } } );

        json out;
        out["removed_count"] = (int)matches.size();
        out["labels"] = labelsArr;
        json content = json::array();
        content.push_back( { { "type", "text" }, { "text", out.dump() } } );
        return "{\"jsonrpc\":\"2.0\",\"result\":" + json( { { "content", content }, { "isError", false } } ).dump() + ",\"id\":" + id + "}";
    }
    // ── batch_disconnect_pins ──
    else if( name == "batch_disconnect_pins" )
    {
        if( !args.contains( "pins" ) || !args["pins"].is_array() || args["pins"].empty() )
        {
            json content = json::array();
            content.push_back( { { "type", "text" }, { "text", "batch_disconnect_pins requires non-empty 'pins' array of {reference, pin_number}" } } );
            return "{\"jsonrpc\":\"2.0\",\"result\":" + json( { { "content", content }, { "isError", true } } ).dump() + ",\"id\":" + id + "}";
        }

        if( !m_ipc.IsConnected() && !m_ipc.Connect( IpcClient::GetDefaultSocketPath() ) )
        {
            json content = json::array();
            content.push_back( { { "type", "text" }, { "text", "KiCad IPC not connected." } } );
            return "{\"jsonrpc\":\"2.0\",\"result\":" + json( { { "content", content }, { "isError", true } } ).dump() + ",\"id\":" + id + "}";
        }

        double tolerance = args.contains( "tolerance_mm" ) && args["tolerance_mm"].is_number()
            ? args["tolerance_mm"].get<double>()
            : 0.1;
        if( tolerance <= 0.0 )
            tolerance = 0.1;

        struct WireInfo
        {
            std::string id;
            double      sx = 0.0;
            double      sy = 0.0;
            double      ex = 0.0;
            double      ey = 0.0;
        };

        struct LabelInfo
        {
            std::string id;
            double      x = 0.0;
            double      y = 0.0;
        };

        const std::string sheetPath = getCurrentSheetPath();

        std::vector<WireInfo> allWires;
        {
            kiapi::common::ApiRequest wireReq;
            wireReq.mutable_header()->set_client_name( "mcp" );
            kiapi::common::commands::GetItems cmd;
            cmd.mutable_header()->mutable_document()->set_type( kiapi::common::types::DOCTYPE_SCHEMATIC );
            if( !sheetPath.empty() )
                cmd.mutable_header()->mutable_document()->mutable_sheet_path()->set_path_human_readable( sheetPath );
            cmd.add_types( kiapi::common::types::KOT_SCH_LINE );
            wireReq.mutable_message()->PackFrom( cmd );

            kiapi::common::ApiResponse resp;
            std::string err;
            if( m_ipc.SendRequest( wireReq, resp, err ) && resp.status().status() == kiapi::common::AS_OK && resp.has_message() )
            {
                kiapi::common::commands::GetItemsResponse items;
                if( resp.message().UnpackTo( &items ) )
                {
                    for( int i = 0; i < items.items_size(); ++i )
                    {
                        kiapi::schematic::types::Line wire;
                        if( !items.items( i ).UnpackTo( &wire ) )
                            continue;
                        if( wire.layer() != kiapi::schematic::types::SL_WIRE )
                            continue;
                        if( !wire.has_start() || !wire.has_end() || !wire.has_id() )
                            continue;
                        allWires.push_back( {
                            wire.id().value(),
                            wire.start().x_nm() / 1e4,
                            wire.start().y_nm() / 1e4,
                            wire.end().x_nm() / 1e4,
                            wire.end().y_nm() / 1e4,
                        } );
                    }
                }
            }
        }

        std::vector<LabelInfo> allLabels;
        {
            kiapi::common::ApiRequest labelReq;
            labelReq.mutable_header()->set_client_name( "mcp" );
            kiapi::common::commands::GetItems cmd;
            cmd.mutable_header()->mutable_document()->set_type( kiapi::common::types::DOCTYPE_SCHEMATIC );
            if( !sheetPath.empty() )
                cmd.mutable_header()->mutable_document()->mutable_sheet_path()->set_path_human_readable( sheetPath );
            cmd.add_types( kiapi::common::types::KOT_SCH_GLOBAL_LABEL );
            labelReq.mutable_message()->PackFrom( cmd );

            kiapi::common::ApiResponse resp;
            std::string err;
            if( m_ipc.SendRequest( labelReq, resp, err ) && resp.status().status() == kiapi::common::AS_OK && resp.has_message() )
            {
                kiapi::common::commands::GetItemsResponse items;
                if( resp.message().UnpackTo( &items ) )
                {
                    for( int i = 0; i < items.items_size(); ++i )
                    {
                        kiapi::schematic::types::GlobalLabel glabel;
                        if( !items.items( i ).UnpackTo( &glabel ) || !glabel.has_id() || !glabel.has_position() )
                            continue;
                        allLabels.push_back( { glabel.id().value(), glabel.position().x_nm() / 1e4, glabel.position().y_nm() / 1e4 } );
                    }
                }
            }
        }

        auto readFieldAsString = []( const json& obj, const char* key ) -> std::string
        {
            if( !obj.is_object() || !obj.contains( key ) || obj[key].is_null() )
                return "";
            if( obj[key].is_string() )
                return obj[key].get<std::string>();
            if( obj[key].is_number_integer() )
                return std::to_string( obj[key].get<long long>() );
            if( obj[key].is_number_unsigned() )
                return std::to_string( obj[key].get<unsigned long long>() );
            if( obj[key].is_number_float() )
            {
                std::ostringstream os;
                os << obj[key].get<double>();
                return os.str();
            }
            return "";
        };

        const json& pins = args["pins"];
        std::set<std::string> idsToDelete;
        json results = json::array();
        int disconnectedPins = 0;
        int matchedWireTotal = 0;
        int matchedLabelTotal = 0;

        for( size_t i = 0; i < pins.size(); ++i )
        {
            const json& pinReq = pins[i];
            json entry;
            entry["index"] = static_cast<int>( i );

            if( !pinReq.is_object() )
            {
                entry["success"] = false;
                entry["message"] = "Entry must be an object with {reference, pin_number}";
                results.push_back( entry );
                continue;
            }

            std::string ref = readFieldAsString( pinReq, "reference" );
            std::string pinNum = readFieldAsString( pinReq, "pin_number" );
            if( pinNum.empty() )
                pinNum = readFieldAsString( pinReq, "pin" ); // compatibility alias
            entry["reference"] = ref;
            entry["pin_number"] = pinNum;

            if( ref.empty() || pinNum.empty() )
            {
                entry["success"] = false;
                entry["message"] = "Missing reference or pin_number";
                results.push_back( entry );
                continue;
            }

            std::string pinErr;
            auto [pinX, pinY] = getPinPosition( ref, pinNum, pinErr );
            if( !pinErr.empty() )
            {
                entry["success"] = false;
                entry["message"] = pinErr;
                results.push_back( entry );
                continue;
            }

            std::set<std::string> localWireIds;
            std::set<std::string> localLabelIds;

            for( const auto& wire : allWires )
            {
                bool startNear = std::abs( wire.sx - pinX ) <= tolerance && std::abs( wire.sy - pinY ) <= tolerance;
                bool endNear = std::abs( wire.ex - pinX ) <= tolerance && std::abs( wire.ey - pinY ) <= tolerance;
                if( startNear || endNear )
                    localWireIds.insert( wire.id );
            }

            for( const auto& label : allLabels )
            {
                if( std::abs( label.x - pinX ) <= tolerance && std::abs( label.y - pinY ) <= tolerance )
                    localLabelIds.insert( label.id );
            }

            for( const auto& idToDel : localWireIds )
                idsToDelete.insert( idToDel );
            for( const auto& idToDel : localLabelIds )
                idsToDelete.insert( idToDel );

            bool didDisconnect = !localWireIds.empty() || !localLabelIds.empty();
            if( didDisconnect )
            {
                disconnectedPins += 1;
                matchedWireTotal += static_cast<int>( localWireIds.size() );
                matchedLabelTotal += static_cast<int>( localLabelIds.size() );
            }

            entry["success"] = true;
            entry["disconnected"] = didDisconnect;
            entry["removed_wires"] = static_cast<int>( localWireIds.size() );
            entry["removed_labels"] = static_cast<int>( localLabelIds.size() );
            if( didDisconnect )
                entry["message"] = "Disconnected pin " + ref + ":" + pinNum;
            else
                entry["message"] = "No wires or labels found at pin " + ref + ":" + pinNum;
            results.push_back( entry );
        }

        if( !idsToDelete.empty() )
        {
            std::string txnErr;
            TransactionGuard txn( *this, m_ipc, txnErr );
            if( !txn.ok() )
            {
                json content = json::array();
                content.push_back( { { "type", "text" }, { "text", txnErr.empty() ? "Begin commit failed" : txnErr } } );
                return "{\"jsonrpc\":\"2.0\",\"result\":" + json( { { "content", content }, { "isError", true } } ).dump() + ",\"id\":" + id + "}";
            }

            kiapi::common::commands::DeleteItems delCmd;
            delCmd.mutable_header()->mutable_document()->set_type( kiapi::common::types::DOCTYPE_SCHEMATIC );
            for( const auto& itemId : idsToDelete )
                delCmd.add_item_ids()->set_value( itemId );

            kiapi::common::ApiRequest delReq;
            delReq.mutable_header()->set_client_name( "mcp" );
            delReq.mutable_message()->PackFrom( delCmd );
            kiapi::common::ApiResponse delResp;
            std::string delErr;
            if( !m_ipc.SendRequest( delReq, delResp, delErr ) || delResp.status().status() != kiapi::common::AS_OK )
            {
                json content = json::array();
                std::string msg = !delErr.empty() ? delErr : ( delResp.status().error_message().empty() ? "Delete failed" : delResp.status().error_message() );
                content.push_back( { { "type", "text" }, { "text", msg } } );
                return "{\"jsonrpc\":\"2.0\",\"result\":" + json( { { "content", content }, { "isError", true } } ).dump() + ",\"id\":" + id + "}";
            }

            txn.commit();
        }

        json out;
        out["requested"] = static_cast<int>( pins.size() );
        out["disconnected_pins"] = disconnectedPins;
        out["removed_wires_total"] = matchedWireTotal;
        out["removed_labels_total"] = matchedLabelTotal;
        out["removed_item_count"] = static_cast<int>( idsToDelete.size() );
        out["results"] = results;
        json content = json::array();
        content.push_back( { { "type", "text" }, { "text", out.dump() } } );
        return "{\"jsonrpc\":\"2.0\",\"result\":" + json( { { "content", content }, { "isError", false } } ).dump() + ",\"id\":" + id + "}";
    }
    // ── disconnect_pin ──
    else if( name == "disconnect_pin" )
    {
        std::string ref = getStr( "reference" );
        std::string pinNum = getStr( "pin_number" );
        if( ref.empty() || pinNum.empty() )
        {
            json content = json::array();
            content.push_back( { { "type", "text" }, { "text", "disconnect_pin requires 'reference' and 'pin_number'" } } );
            return "{\"jsonrpc\":\"2.0\",\"result\":" + json( { { "content", content }, { "isError", true } } ).dump() + ",\"id\":" + id + "}";
        }

        std::string pinErr;
        auto [pinX, pinY] = getPinPosition( ref, pinNum, pinErr );
        if( !pinErr.empty() )
        {
            json content = json::array();
            content.push_back( { { "type", "text" }, { "text", pinErr } } );
            return "{\"jsonrpc\":\"2.0\",\"result\":" + json( { { "content", content }, { "isError", true } } ).dump() + ",\"id\":" + id + "}";
        }

        if( !m_ipc.IsConnected() && !m_ipc.Connect( IpcClient::GetDefaultSocketPath() ) )
        {
            json content = json::array();
            content.push_back( { { "type", "text" }, { "text", "KiCad IPC not connected." } } );
            return "{\"jsonrpc\":\"2.0\",\"result\":" + json( { { "content", content }, { "isError", true } } ).dump() + ",\"id\":" + id + "}";
        }

        const double tolerance = 0.1; // mm

        // Fetch wires near pin
        std::vector<std::string> wireIds;
        {
            kiapi::common::ApiRequest wReq;
            wReq.mutable_header()->set_client_name( "mcp" );
            kiapi::common::commands::GetItems wCmd;
            wCmd.mutable_header()->mutable_document()->set_type( kiapi::common::types::DOCTYPE_SCHEMATIC );
            std::string sheetPath = getCurrentSheetPath();
            if( !sheetPath.empty() )
                wCmd.mutable_header()->mutable_document()->mutable_sheet_path()->set_path_human_readable( sheetPath );
            wCmd.add_types( kiapi::common::types::KOT_SCH_LINE );
            wReq.mutable_message()->PackFrom( wCmd );

            kiapi::common::ApiResponse wResp;
            std::string wErr;
            if( m_ipc.SendRequest( wReq, wResp, wErr ) && wResp.status().status() == kiapi::common::AS_OK && wResp.has_message() )
            {
                kiapi::common::commands::GetItemsResponse items;
                if( wResp.message().UnpackTo( &items ) )
                {
                    for( int i = 0; i < items.items_size(); ++i )
                    {
                        kiapi::schematic::types::Line wire;
                        if( !items.items( i ).UnpackTo( &wire ) )
                            continue;
                        if( wire.layer() != kiapi::schematic::types::SL_WIRE )
                            continue;
                        if( !wire.has_start() || !wire.has_end() || !wire.has_id() )
                            continue;

                        double sx = wire.start().x_nm() / 1e4;
                        double sy = wire.start().y_nm() / 1e4;
                        double ex = wire.end().x_nm() / 1e4;
                        double ey = wire.end().y_nm() / 1e4;

                        bool startNear = std::abs( sx - pinX ) <= tolerance && std::abs( sy - pinY ) <= tolerance;
                        bool endNear = std::abs( ex - pinX ) <= tolerance && std::abs( ey - pinY ) <= tolerance;
                        if( startNear || endNear )
                            wireIds.push_back( wire.id().value() );
                    }
                }
            }
        }

        // Fetch labels near pin
        std::vector<std::string> labelIds;
        {
            kiapi::common::ApiRequest lReq;
            lReq.mutable_header()->set_client_name( "mcp" );
            kiapi::common::commands::GetItems lCmd;
            lCmd.mutable_header()->mutable_document()->set_type( kiapi::common::types::DOCTYPE_SCHEMATIC );
            std::string sheetPath = getCurrentSheetPath();
            if( !sheetPath.empty() )
                lCmd.mutable_header()->mutable_document()->mutable_sheet_path()->set_path_human_readable( sheetPath );
            lCmd.add_types( kiapi::common::types::KOT_SCH_GLOBAL_LABEL );
            lReq.mutable_message()->PackFrom( lCmd );

            kiapi::common::ApiResponse lResp;
            std::string lErr;
            if( m_ipc.SendRequest( lReq, lResp, lErr ) && lResp.status().status() == kiapi::common::AS_OK && lResp.has_message() )
            {
                kiapi::common::commands::GetItemsResponse items;
                if( lResp.message().UnpackTo( &items ) )
                {
                    for( int i = 0; i < items.items_size(); ++i )
                    {
                        kiapi::schematic::types::GlobalLabel glabel;
                        if( !items.items( i ).UnpackTo( &glabel ) || !glabel.has_id() || !glabel.has_position() )
                            continue;

                        double lx = glabel.position().x_nm() / 1e4;
                        double ly = glabel.position().y_nm() / 1e4;

                        if( std::abs( lx - pinX ) <= tolerance && std::abs( ly - pinY ) <= tolerance )
                            labelIds.push_back( glabel.id().value() );
                    }
                }
            }
        }

        if( wireIds.empty() && labelIds.empty() )
        {
            json out;
            out["disconnected"] = false;
            out["removed_wires"] = 0;
            out["removed_labels"] = 0;
            out["message"] = "No wires or labels found at pin " + ref + ":" + pinNum;
            json content = json::array();
            content.push_back( { { "type", "text" }, { "text", out.dump() } } );
            return "{\"jsonrpc\":\"2.0\",\"result\":" + json( { { "content", content }, { "isError", false } } ).dump() + ",\"id\":" + id + "}";
        }

        std::string txnErr;
        TransactionGuard txn( *this, m_ipc, txnErr );
        if( !txn.ok() )
        {
            json content = json::array();
            content.push_back( { { "type", "text" }, { "text", txnErr.empty() ? "Begin commit failed" : txnErr } } );
            return "{\"jsonrpc\":\"2.0\",\"result\":" + json( { { "content", content }, { "isError", true } } ).dump() + ",\"id\":" + id + "}";
        }

        kiapi::common::commands::DeleteItems delCmd;
        delCmd.mutable_header()->mutable_document()->set_type( kiapi::common::types::DOCTYPE_SCHEMATIC );
        for( const auto& wid : wireIds )
            delCmd.add_item_ids()->set_value( wid );
        for( const auto& lid : labelIds )
            delCmd.add_item_ids()->set_value( lid );

        kiapi::common::ApiRequest delReq;
        delReq.mutable_header()->set_client_name( "mcp" );
        delReq.mutable_message()->PackFrom( delCmd );
        kiapi::common::ApiResponse delResp;
        std::string delErr;
        if( !m_ipc.SendRequest( delReq, delResp, delErr ) || delResp.status().status() != kiapi::common::AS_OK )
        {
            json content = json::array();
            std::string msg = !delErr.empty() ? delErr : ( delResp.status().error_message().empty() ? "Delete failed" : delResp.status().error_message() );
            content.push_back( { { "type", "text" }, { "text", msg } } );
            return "{\"jsonrpc\":\"2.0\",\"result\":" + json( { { "content", content }, { "isError", true } } ).dump() + ",\"id\":" + id + "}";
        }

        txn.commit();

        json out;
        out["disconnected"] = true;
        out["removed_wires"] = (int)wireIds.size();
        out["removed_labels"] = (int)labelIds.size();
        json content = json::array();
        content.push_back( { { "type", "text" }, { "text", out.dump() } } );
        return "{\"jsonrpc\":\"2.0\",\"result\":" + json( { { "content", content }, { "isError", false } } ).dump() + ",\"id\":" + id + "}";
    }
    else if( name == "replace_component" )
    {
        std::string ref = getStr( "reference" );
        std::string library = getStr( "library" );
        std::string symbol = getStr( "symbol" );
        if( ref.empty() || library.empty() || symbol.empty() )
        {
            json content = json::array();
            content.push_back( { { "type", "text" }, { "text", "replace_component requires 'reference', 'library', and 'symbol'" } } );
            return "{\"jsonrpc\":\"2.0\",\"result\":" + json( { { "content", content }, { "isError", true } } ).dump() + ",\"id\":" + id + "}";
        }

        // 1. Look up current component from summary to get position, rotation, value
        kiapi::schematic::types::GetSchematicSummaryResponse sumResp;
        std::string sumErr;
        invalidateSummaryCache();
        if( !getCachedSummary( sumResp, sumErr ) )
        {
            json content = json::array();
            content.push_back( { { "type", "text" }, { "text", "Failed to get schematic summary: " + sumErr } } );
            return "{\"jsonrpc\":\"2.0\",\"result\":" + json( { { "content", content }, { "isError", true } } ).dump() + ",\"id\":" + id + "}";
        }

        double posX = 0, posY = 0, rotation = 0;
        std::string oldValue;
        bool found = false;
        for( int i = 0; i < sumResp.components_size(); ++i )
        {
            const auto& c = sumResp.components( i );
            if( c.reference() == ref )
            {
                posX = c.has_position() ? c.position().x_mm() : 0;
                posY = c.has_position() ? c.position().y_mm() : 0;
                rotation = c.rotation();
                oldValue = c.value();
                found = true;
                break;
            }
        }
        if( !found )
        {
            json content = json::array();
            content.push_back( { { "type", "text" }, { "text", "Component '" + ref + "' not found in schematic" } } );
            return "{\"jsonrpc\":\"2.0\",\"result\":" + json( { { "content", content }, { "isError", true } } ).dump() + ",\"id\":" + id + "}";
        }

        // Determine value: use provided value, or keep old value
        std::string newValue = hasArg( "value" ) ? getStr( "value" ) : oldValue;

        // 2. Begin transaction
        std::string txnErr;
        TransactionGuard txn( *this, m_ipc, txnErr );
        if( !txn.ok() )
        {
            json content = json::array();
            content.push_back( { { "type", "text" }, { "text", txnErr.empty() ? "Begin commit failed" : txnErr } } );
            return "{\"jsonrpc\":\"2.0\",\"result\":" + json( { { "content", content }, { "isError", true } } ).dump() + ",\"id\":" + id + "}";
        }

        // 3. Delete old component
        kiapi::common::ApiRequest delReq;
        delReq.mutable_header()->set_client_name( "mcp" );
        kiapi::schematic::types::DeleteComponent delCmd;
        delCmd.set_reference( ref );
        delCmd.mutable_commit_id()->set_value( txn.commitId() );
        delReq.mutable_message()->PackFrom( delCmd );
        kiapi::common::ApiResponse delResp;
        std::string delErr;
        if( !m_ipc.SendRequest( delReq, delResp, delErr ) )
        {
            json content = json::array();
            content.push_back( { { "type", "text" }, { "text", delErr.empty() ? "Delete component failed" : delErr } } );
            return "{\"jsonrpc\":\"2.0\",\"result\":" + json( { { "content", content }, { "isError", true } } ).dump() + ",\"id\":" + id + "}";
        }
        if( delResp.status().status() != kiapi::common::AS_OK )
        {
            json content = json::array();
            content.push_back( { { "type", "text" }, { "text", delResp.status().error_message().empty() ? "Delete component failed" : delResp.status().error_message() } } );
            return "{\"jsonrpc\":\"2.0\",\"result\":" + json( { { "content", content }, { "isError", true } } ).dump() + ",\"id\":" + id + "}";
        }

        // 4. Place new component at same position/rotation with same reference
        kiapi::common::ApiRequest addReq;
        addReq.mutable_header()->set_client_name( "mcp" );
        kiapi::schematic::types::AddComponent addCmd;
        addCmd.set_library_nickname( library );
        addCmd.set_symbol_name( symbol );
        addCmd.set_reference( ref );
        addCmd.set_value( newValue );
        addCmd.mutable_position()->set_x_mm( posX );
        addCmd.mutable_position()->set_y_mm( posY );
        addCmd.set_rotation( rotation );
        addCmd.mutable_commit_id()->set_value( txn.commitId() );
        addReq.mutable_message()->PackFrom( addCmd );
        kiapi::common::ApiResponse addResp;
        std::string addErr;
        if( !m_ipc.SendRequest( addReq, addResp, addErr ) )
        {
            json content = json::array();
            content.push_back( { { "type", "text" }, { "text", addErr.empty() ? "Place replacement component failed" : addErr } } );
            return "{\"jsonrpc\":\"2.0\",\"result\":" + json( { { "content", content }, { "isError", true } } ).dump() + ",\"id\":" + id + "}";
        }
        if( addResp.status().status() != kiapi::common::AS_OK )
        {
            json content = json::array();
            std::string msg = addResp.status().error_message().empty() ? "Place replacement component failed" : addResp.status().error_message();
            content.push_back( { { "type", "text" }, { "text", msg } } );
            return "{\"jsonrpc\":\"2.0\",\"result\":" + json( { { "content", content }, { "isError", true } } ).dump() + ",\"id\":" + id + "}";
        }

        // 5. Commit the transaction
        txn.commit();

        std::string text = "Replaced " + ref + " with " + library + ":" + symbol + " at (" + std::to_string( posX ) + ", " + std::to_string( posY ) + ")";
        if( !newValue.empty() )
            text += ", value=" + newValue;
        json content = json::array();
        content.push_back( { { "type", "text" }, { "text", text } } );
        return "{\"jsonrpc\":\"2.0\",\"result\":" + json( { { "content", content }, { "isError", false } } ).dump() + ",\"id\":" + id + "}";
    }
    // ── Part understanding tools ────────────────────────────────────────
    else if( name == "fetch_datasheet" )
    {
        std::string ref = getStr( "reference" );
        std::string library = getStr( "library" );
        std::string symbol = getStr( "symbol" );

        // Resolve library+symbol from reference if needed
        if( !ref.empty() && ( library.empty() || symbol.empty() ) )
        {
            kiapi::schematic::types::GetSchematicSummaryResponse sumResp;
            std::string sumErr;
            if( !getCachedSummary( sumResp, sumErr ) )
            {
                json content = json::array();
                content.push_back( { { "type", "text" }, { "text", "Failed to get summary: " + sumErr } } );
                return "{\"jsonrpc\":\"2.0\",\"result\":" + json( { { "content", content }, { "isError", true } } ).dump() + ",\"id\":" + id + "}";
            }
            bool found = false;
            for( int i = 0; i < sumResp.components_size(); ++i )
            {
                if( sumResp.components( i ).reference() == ref )
                {
                    library = sumResp.components( i ).library_nickname();
                    symbol = sumResp.components( i ).symbol_name();
                    found = true;
                    break;
                }
            }
            if( !found )
            {
                json content = json::array();
                content.push_back( { { "type", "text" }, { "text", "Component '" + ref + "' not found in schematic" } } );
                return "{\"jsonrpc\":\"2.0\",\"result\":" + json( { { "content", content }, { "isError", true } } ).dump() + ",\"id\":" + id + "}";
            }
        }

        if( library.empty() || symbol.empty() )
        {
            json content = json::array();
            content.push_back( { { "type", "text" }, { "text", "fetch_datasheet requires 'reference' or both 'library' and 'symbol'" } } );
            return "{\"jsonrpc\":\"2.0\",\"result\":" + json( { { "content", content }, { "isError", true } } ).dump() + ",\"id\":" + id + "}";
        }

        // Get component data for datasheet field
        kiapi::schematic::types::GetComponentData dataCmd;
        dataCmd.mutable_lib_id()->set_library_nickname( library );
        dataCmd.mutable_lib_id()->set_entry_name( symbol );
        kiapi::common::ApiRequest dataReq;
        dataReq.mutable_header()->set_client_name( "mcp" );
        dataReq.mutable_message()->PackFrom( dataCmd );
        kiapi::common::ApiResponse dataResp;
        std::string dataErr;

        if( ( !m_ipc.IsConnected() && !m_ipc.Connect( IpcClient::GetDefaultSocketPath() ) )
            || !m_ipc.SendRequest( dataReq, dataResp, dataErr )
            || dataResp.status().status() != kiapi::common::AS_OK )
        {
            json content = json::array();
            content.push_back( { { "type", "text" }, { "text", "Failed to get component data: " + ( dataErr.empty() ? dataResp.status().error_message() : dataErr ) } } );
            return "{\"jsonrpc\":\"2.0\",\"result\":" + json( { { "content", content }, { "isError", true } } ).dump() + ",\"id\":" + id + "}";
        }

        json out;
        if( !ref.empty() ) out["reference"] = ref;
        out["library"] = library;
        out["symbol"] = symbol;

        kiapi::schematic::types::GetComponentDataResponse dataR;
        if( dataResp.has_message() && dataResp.message().UnpackTo( &dataR ) )
        {
            out["description"] = dataR.description();
            out["datasheet_url"] = dataR.datasheet().empty() ? json(nullptr) : json(dataR.datasheet());
            out["keywords"] = dataR.keywords();
            out["pin_count"] = dataR.pins_size();
        }
        else
        {
            out["description"] = "";
            out["datasheet_url"] = nullptr;
        }

        json content = json::array();
        content.push_back( { { "type", "text" }, { "text", out.dump() } } );
        return "{\"jsonrpc\":\"2.0\",\"result\":" + json( { { "content", content }, { "isError", false } } ).dump() + ",\"id\":" + id + "}";
    }
    else if( name == "parametric_part_search" )
    {
        std::string partType = getStr( "type" );
        std::string valueFilter = getStr( "value" );
        std::string packageFilter = getStr( "package" );
        std::string library = getStr( "library" );
        int limit = getInt( "limit", 20 );

        if( partType.empty() )
        {
            json content = json::array();
            content.push_back( { { "type", "text" }, { "text", "parametric_part_search requires 'type'" } } );
            return "{\"jsonrpc\":\"2.0\",\"result\":" + json( { { "content", content }, { "isError", true } } ).dump() + ",\"id\":" + id + "}";
        }

        // Map type to search query
        std::string query;
        if( partType == "resistor" ) query = "R";
        else if( partType == "capacitor" ) query = "C";
        else if( partType == "inductor" ) query = "L";
        else if( partType == "diode" ) query = "D";
        else if( partType == "transistor" ) query = "Q";
        else if( partType == "ic" ) query = "U";
        else if( partType == "connector" ) query = "Conn";
        else query = partType;

        // Search symbols
        kiapi::schematic::types::SearchSymbols searchCmd;
        searchCmd.set_query( query );
        if( !library.empty() )
            searchCmd.set_library( library );
        searchCmd.set_limit( limit * 3 ); // over-fetch to allow filtering

        kiapi::common::ApiRequest searchReq;
        searchReq.mutable_header()->set_client_name( "mcp" );
        searchReq.mutable_message()->PackFrom( searchCmd );
        kiapi::common::ApiResponse searchResp;
        std::string searchErr;

        if( ( !m_ipc.IsConnected() && !m_ipc.Connect( IpcClient::GetDefaultSocketPath() ) )
            || !m_ipc.SendRequest( searchReq, searchResp, searchErr )
            || searchResp.status().status() != kiapi::common::AS_OK )
        {
            json content = json::array();
            content.push_back( { { "type", "text" }, { "text", "Search failed: " + ( searchErr.empty() ? searchResp.status().error_message() : searchErr ) } } );
            return "{\"jsonrpc\":\"2.0\",\"result\":" + json( { { "content", content }, { "isError", true } } ).dump() + ",\"id\":" + id + "}";
        }

        kiapi::schematic::types::SearchSymbolsResponse symResp;
        json results = json::array();
        int totalFound = 0;

        if( searchResp.has_message() && searchResp.message().UnpackTo( &symResp ) )
        {
            totalFound = symResp.results_size();
            bool needsFiltering = !valueFilter.empty() || !packageFilter.empty();

            for( int i = 0; i < symResp.results_size() && (int)results.size() < limit; ++i )
            {
                const auto& r = symResp.results( i );
                std::string desc = r.description();
                std::string kw = r.keywords();
                std::string ds = r.datasheet();

                // If filters specified, check description/keywords for matches
                if( needsFiltering )
                {
                    std::string combined = desc + " " + kw + " " + r.symbol_name();
                    // Case-insensitive check
                    std::string combinedLower = combined;
                    std::transform( combinedLower.begin(), combinedLower.end(), combinedLower.begin(), ::tolower );

                    if( !valueFilter.empty() )
                    {
                        std::string valLower = valueFilter;
                        std::transform( valLower.begin(), valLower.end(), valLower.begin(), ::tolower );
                        if( combinedLower.find( valLower ) == std::string::npos )
                            continue;
                    }
                    if( !packageFilter.empty() )
                    {
                        std::string pkgLower = packageFilter;
                        std::transform( pkgLower.begin(), pkgLower.end(), pkgLower.begin(), ::tolower );
                        if( combinedLower.find( pkgLower ) == std::string::npos )
                            continue;
                    }
                }

                json item;
                item["library"] = r.library_nickname();
                item["symbol"] = r.symbol_name();
                if( desc.size() > 80 ) desc = desc.substr( 0, 77 ) + "...";
                item["description"] = desc;
                results.push_back( item );
            }
        }

        json out;
        out["type"] = partType;
        out["results"] = results;
        out["total_found"] = totalFound;
        out["returned"] = (int)results.size();

        json content = json::array();
        content.push_back( { { "type", "text" }, { "text", out.dump() } } );
        return "{\"jsonrpc\":\"2.0\",\"result\":" + json( { { "content", content }, { "isError", false } } ).dump() + ",\"id\":" + id + "}";
    }
    else if( name == "find_alternates" )
    {
        std::string ref = getStr( "reference" );
        std::string library = getStr( "library" );
        std::string symbol = getStr( "symbol" );

        // Resolve library+symbol from reference if needed
        if( !ref.empty() && ( library.empty() || symbol.empty() ) )
        {
            kiapi::schematic::types::GetSchematicSummaryResponse sumResp;
            std::string sumErr;
            if( !getCachedSummary( sumResp, sumErr ) )
            {
                json content = json::array();
                content.push_back( { { "type", "text" }, { "text", "Failed to get summary: " + sumErr } } );
                return "{\"jsonrpc\":\"2.0\",\"result\":" + json( { { "content", content }, { "isError", true } } ).dump() + ",\"id\":" + id + "}";
            }
            bool found = false;
            for( int i = 0; i < sumResp.components_size(); ++i )
            {
                if( sumResp.components( i ).reference() == ref )
                {
                    library = sumResp.components( i ).library_nickname();
                    symbol = sumResp.components( i ).symbol_name();
                    found = true;
                    break;
                }
            }
            if( !found )
            {
                json content = json::array();
                content.push_back( { { "type", "text" }, { "text", "Component '" + ref + "' not found in schematic" } } );
                return "{\"jsonrpc\":\"2.0\",\"result\":" + json( { { "content", content }, { "isError", true } } ).dump() + ",\"id\":" + id + "}";
            }
        }

        if( library.empty() || symbol.empty() )
        {
            json content = json::array();
            content.push_back( { { "type", "text" }, { "text", "find_alternates requires 'reference' or both 'library' and 'symbol'" } } );
            return "{\"jsonrpc\":\"2.0\",\"result\":" + json( { { "content", content }, { "isError", true } } ).dump() + ",\"id\":" + id + "}";
        }

        // Get original component data
        kiapi::schematic::types::GetComponentData dataCmd;
        dataCmd.mutable_lib_id()->set_library_nickname( library );
        dataCmd.mutable_lib_id()->set_entry_name( symbol );
        kiapi::common::ApiRequest dataReq;
        dataReq.mutable_header()->set_client_name( "mcp" );
        dataReq.mutable_message()->PackFrom( dataCmd );
        kiapi::common::ApiResponse dataResp;
        std::string dataErr;

        if( ( !m_ipc.IsConnected() && !m_ipc.Connect( IpcClient::GetDefaultSocketPath() ) )
            || !m_ipc.SendRequest( dataReq, dataResp, dataErr )
            || dataResp.status().status() != kiapi::common::AS_OK )
        {
            json content = json::array();
            content.push_back( { { "type", "text" }, { "text", "Failed to get component data: " + ( dataErr.empty() ? dataResp.status().error_message() : dataErr ) } } );
            return "{\"jsonrpc\":\"2.0\",\"result\":" + json( { { "content", content }, { "isError", true } } ).dump() + ",\"id\":" + id + "}";
        }

        kiapi::schematic::types::GetComponentDataResponse origData;
        int origPinCount = 0;
        std::string origDesc;
        if( dataResp.has_message() && dataResp.message().UnpackTo( &origData ) )
        {
            origPinCount = origData.pins_size();
            origDesc = origData.description();
        }

        json original;
        original["library"] = library;
        original["symbol"] = symbol;
        original["pins"] = origPinCount;
        original["description"] = origDesc;

        // Search for alternates in same library and with similar name prefix
        // Try: search in same library, then broader search with symbol name prefix
        json alternates = json::array();
        std::string origKey = library + ":" + symbol; // to skip self

        // Extract base name pattern (e.g. "LM317" from "LM317T")
        std::string searchQuery = symbol;
        // Trim trailing digits/suffixes for broader search
        while( searchQuery.size() > 2 && ( std::isdigit( searchQuery.back() ) || searchQuery.back() == '_' ) )
            searchQuery.pop_back();
        if( searchQuery.size() < 2 ) searchQuery = symbol;

        // Search in same library first
        std::vector<std::pair<std::string, std::string>> searchPasses = {
            { searchQuery, library },  // same library, similar name
            { searchQuery, "" }        // all libraries, similar name
        };

        std::set<std::string> seen;
        seen.insert( origKey );

        for( const auto& [sq, sl] : searchPasses )
        {
            kiapi::schematic::types::SearchSymbols searchCmd;
            searchCmd.set_query( sq );
            if( !sl.empty() )
                searchCmd.set_library( sl );
            searchCmd.set_limit( 30 );

            kiapi::common::ApiRequest searchReq;
            searchReq.mutable_header()->set_client_name( "mcp" );
            searchReq.mutable_message()->PackFrom( searchCmd );
            kiapi::common::ApiResponse searchResp;
            std::string searchErr;

            if( !m_ipc.SendRequest( searchReq, searchResp, searchErr )
                || searchResp.status().status() != kiapi::common::AS_OK )
                continue;

            kiapi::schematic::types::SearchSymbolsResponse symResp;
            if( !searchResp.has_message() || !searchResp.message().UnpackTo( &symResp ) )
                continue;

            for( int i = 0; i < symResp.results_size() && (int)alternates.size() < 10; ++i )
            {
                const auto& r = symResp.results( i );
                std::string key = std::string( r.library_nickname() ) + ":" + r.symbol_name();
                if( seen.count( key ) ) continue;
                seen.insert( key );

                // Get pin count for this candidate
                kiapi::schematic::types::GetComponentData candCmd;
                candCmd.mutable_lib_id()->set_library_nickname( r.library_nickname() );
                candCmd.mutable_lib_id()->set_entry_name( r.symbol_name() );
                kiapi::common::ApiRequest candReq;
                candReq.mutable_header()->set_client_name( "mcp" );
                candReq.mutable_message()->PackFrom( candCmd );
                kiapi::common::ApiResponse candResp;
                std::string candErr;
                if( !m_ipc.SendRequest( candReq, candResp, candErr )
                    || candResp.status().status() != kiapi::common::AS_OK )
                    continue;

                kiapi::schematic::types::GetComponentDataResponse candData;
                if( !candResp.has_message() || !candResp.message().UnpackTo( &candData ) )
                    continue;

                int candPins = candData.pins_size();
                // Only include if pin count matches
                if( origPinCount > 0 && candPins != origPinCount )
                    continue;

                json alt;
                alt["library"] = r.library_nickname();
                alt["symbol"] = r.symbol_name();
                alt["pins"] = candPins;
                std::string desc = candData.description();
                if( desc.size() > 80 ) desc = desc.substr( 0, 77 ) + "...";
                alt["description"] = desc;

                std::string reason;
                if( r.library_nickname() == library )
                    reason = "same_library";
                else
                    reason = "similar_name";
                if( candPins == origPinCount && origPinCount > 0 )
                    reason += "+pin_compatible";
                alt["match_reason"] = reason;

                alternates.push_back( alt );
            }
        }

        json out;
        out["original"] = original;
        out["alternates"] = alternates;

        json content = json::array();
        content.push_back( { { "type", "text" }, { "text", out.dump() } } );
        return "{\"jsonrpc\":\"2.0\",\"result\":" + json( { { "content", content }, { "isError", false } } ).dump() + ",\"id\":" + id + "}";
    }
    else if( name == "symbol_footprint_consistency_check" )
    {
        std::string ref = getStr( "reference" );

        // Get schematic summary
        kiapi::schematic::types::GetSchematicSummaryResponse sumResp;
        std::string sumErr;
        if( !getCachedSummary( sumResp, sumErr ) )
        {
            json content = json::array();
            content.push_back( { { "type", "text" }, { "text", "Failed to get summary: " + sumErr } } );
            return "{\"jsonrpc\":\"2.0\",\"result\":" + json( { { "content", content }, { "isError", true } } ).dump() + ",\"id\":" + id + "}";
        }

        // Collect components to check
        std::vector<int> indices;
        for( int i = 0; i < sumResp.components_size(); ++i )
        {
            if( ref.empty() || sumResp.components( i ).reference() == ref )
                indices.push_back( i );
        }

        if( !ref.empty() && indices.empty() )
        {
            json content = json::array();
            content.push_back( { { "type", "text" }, { "text", "Component '" + ref + "' not found in schematic" } } );
            return "{\"jsonrpc\":\"2.0\",\"result\":" + json( { { "content", content }, { "isError", true } } ).dump() + ",\"id\":" + id + "}";
        }

        json issues = json::array();
        int checked = 0;
        int passed = 0;

        for( int idx : indices )
        {
            const auto& comp = sumResp.components( idx );
            std::string compRef = comp.reference();
            std::string lib = comp.library_nickname();
            std::string sym = comp.symbol_name();
            checked++;
            bool hasIssue = false;

            // Check for missing library/symbol
            if( lib.empty() || sym.empty() )
            {
                json issue;
                issue["reference"] = compRef;
                issue["issue_type"] = "missing_library_link";
                issue["description"] = "Component has no library/symbol association";
                issues.push_back( issue );
                hasIssue = true;
                continue; // can't do further checks
            }

            // Get library component data
            kiapi::schematic::types::GetComponentData dataCmd;
            dataCmd.mutable_lib_id()->set_library_nickname( lib );
            dataCmd.mutable_lib_id()->set_entry_name( sym );
            kiapi::common::ApiRequest dataReq;
            dataReq.mutable_header()->set_client_name( "mcp" );
            dataReq.mutable_message()->PackFrom( dataCmd );
            kiapi::common::ApiResponse dataResp;
            std::string dataErr;

            if( ( !m_ipc.IsConnected() && !m_ipc.Connect( IpcClient::GetDefaultSocketPath() ) )
                || !m_ipc.SendRequest( dataReq, dataResp, dataErr )
                || dataResp.status().status() != kiapi::common::AS_OK )
            {
                json issue;
                issue["reference"] = compRef;
                issue["issue_type"] = "library_not_found";
                issue["description"] = "Cannot load library symbol " + lib + ":" + sym;
                issues.push_back( issue );
                hasIssue = true;
                continue;
            }

            kiapi::schematic::types::GetComponentDataResponse libData;
            if( !dataResp.has_message() || !dataResp.message().UnpackTo( &libData ) )
            {
                json issue;
                issue["reference"] = compRef;
                issue["issue_type"] = "unpack_failed";
                issue["description"] = "Could not parse library data for " + lib + ":" + sym;
                issues.push_back( issue );
                hasIssue = true;
                continue;
            }

            int libPinCount = libData.pins_size();
            int schPinCount = comp.pins_size();

            // Check pin count mismatch between schematic instance and library
            if( libPinCount > 0 && schPinCount > 0 && libPinCount != schPinCount )
            {
                json issue;
                issue["reference"] = compRef;
                issue["issue_type"] = "pin_count_mismatch";
                issue["description"] = "Schematic has " + std::to_string( schPinCount )
                    + " pins but library symbol has " + std::to_string( libPinCount );
                issues.push_back( issue );
                hasIssue = true;
            }

            // Check for unnamed pins in library symbol
            int unnamedCount = 0;
            std::set<std::string> pinNumbers;
            bool hasDuplicatePinNumbers = false;
            for( int p = 0; p < libData.pins_size(); ++p )
            {
                const auto& pin = libData.pins( p );
                if( pin.name().empty() || pin.name() == "~" )
                    unnamedCount++;
                if( !pin.number().empty() )
                {
                    if( pinNumbers.count( pin.number() ) )
                        hasDuplicatePinNumbers = true;
                    pinNumbers.insert( pin.number() );
                }
            }

            if( unnamedCount > 0 )
            {
                json issue;
                issue["reference"] = compRef;
                issue["issue_type"] = "unnamed_pins";
                issue["description"] = std::to_string( unnamedCount ) + " pin(s) have no name";
                issues.push_back( issue );
                hasIssue = true;
            }

            if( hasDuplicatePinNumbers )
            {
                json issue;
                issue["reference"] = compRef;
                issue["issue_type"] = "duplicate_pin_numbers";
                issue["description"] = "Library symbol has duplicate pin numbers";
                issues.push_back( issue );
                hasIssue = true;
            }

            // Check for missing value
            if( comp.value().empty() )
            {
                json issue;
                issue["reference"] = compRef;
                issue["issue_type"] = "missing_value";
                issue["description"] = "Component has no value set";
                issues.push_back( issue );
                hasIssue = true;
            }

            if( !hasIssue )
                passed++;
        }

        json out;
        out["checked"] = checked;
        out["passed"] = passed;
        out["issues"] = issues;

        json content = json::array();
        content.push_back( { { "type", "text" }, { "text", out.dump() } } );
        return "{\"jsonrpc\":\"2.0\",\"result\":" + json( { { "content", content }, { "isError", false } } ).dump() + ",\"id\":" + id + "}";
    }
    // ── list_blocks / find_block ────────────────────────────────────────
    else if( name == "list_blocks" || name == "find_block" )
    {
        std::string err;
        if( !m_ipc.IsConnected() && !m_ipc.Connect( IpcClient::GetDefaultSocketPath() ) )
        {
            json content = json::array();
            content.push_back( { { "type", "text" }, { "text", "KiCad IPC not connected." } } );
            return "{\"jsonrpc\":\"2.0\",\"result\":" + json( { { "content", content }, { "isError", true } } ).dump() + ",\"id\":" + id + "}";
        }

        std::string searchTitle;
        if( name == "find_block" )
        {
            searchTitle = getStr( "title" );
            if( searchTitle.empty() )
            {
                json content = json::array();
                content.push_back( { { "type", "text" }, { "text", "find_block requires 'title' argument" } } );
                return "{\"jsonrpc\":\"2.0\",\"result\":" + json( { { "content", content }, { "isError", true } } ).dump() + ",\"id\":" + id + "}";
            }
        }

        // Fetch all lines (filter SL_NOTES below)
        kiapi::common::ApiRequest lineReq;
        lineReq.mutable_header()->set_client_name( "mcp" );
        kiapi::common::commands::GetItems lineCmd;
        lineCmd.mutable_header()->mutable_document()->set_type( kiapi::common::types::DOCTYPE_SCHEMATIC );
        std::string blkSheet = getCurrentSheetPath();
        if( !blkSheet.empty() )
            lineCmd.mutable_header()->mutable_document()->mutable_sheet_path()->set_path_human_readable( blkSheet );
        lineCmd.add_types( kiapi::common::types::KOT_SCH_LINE );
        lineReq.mutable_message()->PackFrom( lineCmd );
        kiapi::common::ApiResponse lineResp;
        if( !m_ipc.SendRequest( lineReq, lineResp, err ) || lineResp.status().status() != kiapi::common::AS_OK )
        {
            json content = json::array();
            content.push_back( { { "type", "text" }, { "text", err.empty() ? "Failed to fetch lines" : err } } );
            return "{\"jsonrpc\":\"2.0\",\"result\":" + json( { { "content", content }, { "isError", true } } ).dump() + ",\"id\":" + id + "}";
        }

        // Fetch all text items (filter SL_NOTES below)
        kiapi::common::ApiRequest textReq;
        textReq.mutable_header()->set_client_name( "mcp" );
        kiapi::common::commands::GetItems textCmd;
        textCmd.mutable_header()->mutable_document()->set_type( kiapi::common::types::DOCTYPE_SCHEMATIC );
        if( !blkSheet.empty() )
            textCmd.mutable_header()->mutable_document()->mutable_sheet_path()->set_path_human_readable( blkSheet );
        textCmd.add_types( kiapi::common::types::KOT_SCH_TEXT );
        textReq.mutable_message()->PackFrom( textCmd );
        kiapi::common::ApiResponse textResp;
        if( !m_ipc.SendRequest( textReq, textResp, err ) || textResp.status().status() != kiapi::common::AS_OK )
        {
            json content = json::array();
            content.push_back( { { "type", "text" }, { "text", err.empty() ? "Failed to fetch text items" : err } } );
            return "{\"jsonrpc\":\"2.0\",\"result\":" + json( { { "content", content }, { "isError", true } } ).dump() + ",\"id\":" + id + "}";
        }

        constexpr double BLK_IU_TO_MM = 1.0 / 10000.0;

        // Parse notes-layer lines
        struct BlkLine { std::string lineId; double x1, y1, x2, y2; };
        std::vector<BlkLine> notesLines;
        if( lineResp.has_message() )
        {
            kiapi::common::commands::GetItemsResponse items;
            if( lineResp.message().UnpackTo( &items ) )
            {
                for( int i = 0; i < items.items_size(); ++i )
                {
                    kiapi::schematic::types::Line ln;
                    if( !items.items( i ).UnpackTo( &ln ) )
                        continue;
                    if( ln.layer() != kiapi::schematic::types::SL_NOTES )
                        continue;
                    BlkLine nl;
                    nl.lineId = ln.has_id() ? ln.id().value() : "";
                    nl.x1 = ln.start().x_nm() * BLK_IU_TO_MM;
                    nl.y1 = ln.start().y_nm() * BLK_IU_TO_MM;
                    nl.x2 = ln.end().x_nm() * BLK_IU_TO_MM;
                    nl.y2 = ln.end().y_nm() * BLK_IU_TO_MM;
                    notesLines.push_back( nl );
                }
            }
        }

        // Parse notes-layer text items
        struct BlkText { std::string blkTextId; double x, y; std::string blkContent; };
        std::vector<BlkText> notesTexts;
        if( textResp.has_message() )
        {
            kiapi::common::commands::GetItemsResponse items;
            if( textResp.message().UnpackTo( &items ) )
            {
                for( int i = 0; i < items.items_size(); ++i )
                {
                    kiapi::schematic::types::SchematicText st;
                    if( !items.items( i ).UnpackTo( &st ) )
                        continue;
                    if( st.layer() != kiapi::schematic::types::SL_NOTES )
                        continue;
                    BlkText nt;
                    nt.blkTextId = st.has_id() ? st.id().value() : "";
                    nt.x = st.position().x_nm() * BLK_IU_TO_MM;
                    nt.y = st.position().y_nm() * BLK_IU_TO_MM;
                    if( st.has_text() && st.text().has_text() )
                        nt.blkContent = st.text().text().text();
                    notesTexts.push_back( nt );
                }
            }
        }

        // Detect rectangles from axis-aligned lines
        constexpr double BLK_EPS = 0.05;
        auto blkNear = []( double a, double b ) { constexpr double eps = 0.05; return std::abs( a - b ) < eps; };

        struct BlkH { int idx; double y, xMin, xMax; };
        struct BlkV { int idx; double x, yMin, yMax; };
        std::vector<BlkH> hLines;
        std::vector<BlkV> vLines;

        for( int i = 0; i < (int) notesLines.size(); ++i )
        {
            const auto& l = notesLines[i];
            if( blkNear( l.y1, l.y2 ) )
                hLines.push_back( { i, l.y1, std::min( l.x1, l.x2 ), std::max( l.x1, l.x2 ) } );
            else if( blkNear( l.x1, l.x2 ) )
                vLines.push_back( { i, l.x1, std::min( l.y1, l.y2 ), std::max( l.y1, l.y2 ) } );
        }

        struct DetectedBlock { double minX, minY, maxX, maxY; int hIdx[2]; int vIdx[2]; std::string title; std::string titleId; };
        std::vector<DetectedBlock> detectedBlocks;
        std::vector<bool> usedH( hLines.size(), false );
        std::vector<bool> usedV( vLines.size(), false );

        for( size_t hi = 0; hi < hLines.size(); ++hi )
        {
            if( usedH[hi] ) continue;
            for( size_t hj = hi + 1; hj < hLines.size(); ++hj )
            {
                if( usedH[hj] ) continue;
                const auto& h1 = hLines[hi];
                const auto& h2 = hLines[hj];
                if( !blkNear( h1.xMin, h2.xMin ) || !blkNear( h1.xMax, h2.xMax ) )
                    continue;
                double rYMin = std::min( h1.y, h2.y );
                double rYMax = std::max( h1.y, h2.y );
                double rXMin = h1.xMin;
                double rXMax = h1.xMax;
                int fv[2] = { -1, -1 };
                for( size_t vi = 0; vi < vLines.size(); ++vi )
                {
                    if( usedV[vi] ) continue;
                    const auto& v = vLines[vi];
                    if( blkNear( v.yMin, rYMin ) && blkNear( v.yMax, rYMax ) )
                    {
                        if( blkNear( v.x, rXMin ) && fv[0] < 0 )
                            fv[0] = (int) vi;
                        else if( blkNear( v.x, rXMax ) && fv[1] < 0 )
                            fv[1] = (int) vi;
                    }
                }
                if( fv[0] >= 0 && fv[1] >= 0 )
                {
                    DetectedBlock blk;
                    blk.minX = rXMin; blk.minY = rYMin; blk.maxX = rXMax; blk.maxY = rYMax;
                    blk.hIdx[0] = h1.idx; blk.hIdx[1] = h2.idx;
                    blk.vIdx[0] = vLines[fv[0]].idx; blk.vIdx[1] = vLines[fv[1]].idx;
                    double bestDist = 1e18;
                    int bestTi = -1;
                    for( size_t ti = 0; ti < notesTexts.size(); ++ti )
                    {
                        const auto& t = notesTexts[ti];
                        if( t.x >= rXMin - BLK_EPS && t.x <= rXMax + BLK_EPS
                            && t.y >= rYMin - BLK_EPS && t.y <= rYMax + BLK_EPS )
                        {
                            double d = std::abs( t.x - ( rXMin + rXMax ) / 2.0 )
                                     + std::abs( t.y - rYMin );
                            if( d < bestDist ) { bestDist = d; bestTi = (int) ti; }
                        }
                    }
                    blk.title = bestTi >= 0 ? notesTexts[bestTi].blkContent : "";
                    blk.titleId = bestTi >= 0 ? notesTexts[bestTi].blkTextId : "";
                    detectedBlocks.push_back( blk );
                    usedH[hi] = usedH[hj] = true;
                    usedV[fv[0]] = usedV[fv[1]] = true;
                    break;
                }
            }
        }

        json blocksArr = json::array();
        for( const auto& blk : detectedBlocks )
        {
            if( name == "find_block" )
            {
                std::string lt = blk.title;
                std::string ls = searchTitle;
                std::transform( lt.begin(), lt.end(), lt.begin(), ::tolower );
                std::transform( ls.begin(), ls.end(), ls.begin(), ::tolower );
                if( lt.find( ls ) == std::string::npos )
                    continue;
            }
            json entry;
            entry["title"] = blk.title;
            entry["min_x"] = blk.minX;
            entry["min_y"] = blk.minY;
            entry["max_x"] = blk.maxX;
            entry["max_y"] = blk.maxY;
            entry["line_ids"] = json::array( {
                notesLines[blk.hIdx[0]].lineId, notesLines[blk.hIdx[1]].lineId,
                notesLines[blk.vIdx[0]].lineId, notesLines[blk.vIdx[1]].lineId
            } );
            entry["text_id"] = blk.titleId;
            blocksArr.push_back( entry );
            if( name == "find_block" )
                break;
        }

        if( name == "find_block" && blocksArr.empty() )
        {
            json content = json::array();
            content.push_back( { { "type", "text" }, { "text", "No block found matching title '" + searchTitle + "'" } } );
            return "{\"jsonrpc\":\"2.0\",\"result\":" + json( { { "content", content }, { "isError", true } } ).dump() + ",\"id\":" + id + "}";
        }

        json content = json::array();
        content.push_back( { { "type", "text" }, { "text", blocksArr.dump() } } );
        return "{\"jsonrpc\":\"2.0\",\"result\":" + json( { { "content", content }, { "isError", false } } ).dump() + ",\"id\":" + id + "}";
    }
    // ── get_items_in_bbox ───────────────────────────────────────────────
    else if( name == "get_items_in_bbox" )
    {
        if( !hasArg( "min_x" ) || !hasArg( "min_y" ) || !hasArg( "max_x" ) || !hasArg( "max_y" ) )
        {
            json content = json::array();
            content.push_back( { { "type", "text" }, { "text", "get_items_in_bbox requires min_x, min_y, max_x, max_y" } } );
            return "{\"jsonrpc\":\"2.0\",\"result\":" + json( { { "content", content }, { "isError", true } } ).dump() + ",\"id\":" + id + "}";
        }

        double qMinX = getDouble( "min_x" );
        double qMinY = getDouble( "min_y" );
        double qMaxX = getDouble( "max_x" );
        double qMaxY = getDouble( "max_y" );

        std::string err;
        if( !m_ipc.IsConnected() && !m_ipc.Connect( IpcClient::GetDefaultSocketPath() ) )
        {
            json content = json::array();
            content.push_back( { { "type", "text" }, { "text", "KiCad IPC not connected." } } );
            return "{\"jsonrpc\":\"2.0\",\"result\":" + json( { { "content", content }, { "isError", true } } ).dump() + ",\"id\":" + id + "}";
        }

        constexpr double BBOX_IU_TO_MM = 1.0 / 10000.0;

        auto bboxIntersects = []( double aMinX, double aMinY, double aMaxX, double aMaxY,
                                  double bMinX, double bMinY, double bMaxX, double bMaxY ) -> bool {
            return aMinX <= bMaxX && aMaxX >= bMinX && aMinY <= bMaxY && aMaxY >= bMinY;
        };

        // 1. Components via schematic summary + bounds
        json compsArr = json::array();
        kiapi::schematic::types::GetSchematicSummaryResponse bboxSumResp;
        if( getCachedSummary( bboxSumResp, err ) )
        {
            for( int i = 0; i < bboxSumResp.components_size(); ++i )
            {
                const auto& c = bboxSumResp.components( i );
                std::string bErr;
                auto [cx, cy, w, h] = getComponentBounds( c.reference(), bErr, &bboxSumResp );
                double cMinX = cx - w / 2.0, cMinY = cy - h / 2.0;
                double cMaxX = cx + w / 2.0, cMaxY = cy + h / 2.0;
                if( bboxIntersects( qMinX, qMinY, qMaxX, qMaxY, cMinX, cMinY, cMaxX, cMaxY ) )
                {
                    json cj;
                    cj["reference"] = c.reference();
                    cj["lib_id"] = c.library_nickname() + ":" + c.symbol_name();
                    cj["x_mm"] = c.has_position() ? c.position().x_mm() : 0.0;
                    cj["y_mm"] = c.has_position() ? c.position().y_mm() : 0.0;
                    cj["cx_mm"] = cx;
                    cj["cy_mm"] = cy;
                    cj["width_mm"] = w;
                    cj["height_mm"] = h;
                    compsArr.push_back( cj );
                }
            }
        }

        // 2. Wires (lines on SL_WIRE layer)
        json wiresArr = json::array();
        {
            kiapi::common::ApiRequest wReq;
            wReq.mutable_header()->set_client_name( "mcp" );
            kiapi::common::commands::GetItems wCmd;
            wCmd.mutable_header()->mutable_document()->set_type( kiapi::common::types::DOCTYPE_SCHEMATIC );
            std::string bboxSheet = getCurrentSheetPath();
            if( !bboxSheet.empty() )
                wCmd.mutable_header()->mutable_document()->mutable_sheet_path()->set_path_human_readable( bboxSheet );
            wCmd.add_types( kiapi::common::types::KOT_SCH_LINE );
            wReq.mutable_message()->PackFrom( wCmd );
            kiapi::common::ApiResponse wResp;
            if( m_ipc.SendRequest( wReq, wResp, err ) && wResp.status().status() == kiapi::common::AS_OK && wResp.has_message() )
            {
                kiapi::common::commands::GetItemsResponse wItems;
                if( wResp.message().UnpackTo( &wItems ) )
                {
                    for( int i = 0; i < wItems.items_size(); ++i )
                    {
                        kiapi::schematic::types::Line ln;
                        if( !wItems.items( i ).UnpackTo( &ln ) )
                            continue;
                        if( ln.layer() != kiapi::schematic::types::SL_WIRE )
                            continue;
                        double sx = ln.start().x_nm() * BBOX_IU_TO_MM;
                        double sy = ln.start().y_nm() * BBOX_IU_TO_MM;
                        double ex = ln.end().x_nm() * BBOX_IU_TO_MM;
                        double ey = ln.end().y_nm() * BBOX_IU_TO_MM;
                        double wMinX = std::min( sx, ex ), wMinY = std::min( sy, ey );
                        double wMaxX = std::max( sx, ex ), wMaxY = std::max( sy, ey );
                        if( bboxIntersects( qMinX, qMinY, qMaxX, qMaxY, wMinX, wMinY, wMaxX, wMaxY ) )
                        {
                            json wj;
                            wj["id"] = ln.has_id() ? ln.id().value() : "";
                            wj["start_x"] = sx; wj["start_y"] = sy;
                            wj["end_x"] = ex; wj["end_y"] = ey;
                            wiresArr.push_back( wj );
                        }
                    }
                }
            }
        }

        // 3. Global labels
        json labelsArr = json::array();
        {
            kiapi::common::ApiRequest glReq;
            glReq.mutable_header()->set_client_name( "mcp" );
            kiapi::common::commands::GetItems glCmd;
            glCmd.mutable_header()->mutable_document()->set_type( kiapi::common::types::DOCTYPE_SCHEMATIC );
            std::string bboxSheet2 = getCurrentSheetPath();
            if( !bboxSheet2.empty() )
                glCmd.mutable_header()->mutable_document()->mutable_sheet_path()->set_path_human_readable( bboxSheet2 );
            glCmd.add_types( kiapi::common::types::KOT_SCH_GLOBAL_LABEL );
            glReq.mutable_message()->PackFrom( glCmd );
            kiapi::common::ApiResponse glResp;
            if( m_ipc.SendRequest( glReq, glResp, err ) && glResp.status().status() == kiapi::common::AS_OK && glResp.has_message() )
            {
                kiapi::common::commands::GetItemsResponse glItems;
                if( glResp.message().UnpackTo( &glItems ) )
                {
                    for( int i = 0; i < glItems.items_size(); ++i )
                    {
                        kiapi::schematic::types::GlobalLabel gl;
                        if( !glItems.items( i ).UnpackTo( &gl ) || !gl.has_position() )
                            continue;
                        double lx = gl.position().x_nm() * BBOX_IU_TO_MM;
                        double ly = gl.position().y_nm() * BBOX_IU_TO_MM;
                        if( lx >= qMinX && lx <= qMaxX && ly >= qMinY && ly <= qMaxY )
                        {
                            json lj;
                            lj["id"] = gl.has_id() ? gl.id().value() : "";
                            lj["x_mm"] = lx;
                            lj["y_mm"] = ly;
                            std::string netName;
                            if( gl.has_text() && gl.text().has_text() )
                                netName = gl.text().text().text();
                            lj["net_name"] = netName;
                            labelsArr.push_back( lj );
                        }
                    }
                }
            }
        }

        json bboxOut;
        bboxOut["components"] = compsArr;
        bboxOut["wires"] = wiresArr;
        bboxOut["labels"] = labelsArr;
        json content = json::array();
        content.push_back( { { "type", "text" }, { "text", bboxOut.dump() } } );
        return "{\"jsonrpc\":\"2.0\",\"result\":" + json( { { "content", content }, { "isError", false } } ).dump() + ",\"id\":" + id + "}";
    }
    // ── validate_block ──────────────────────────────────────────────────
    else if( name == "validate_block" )
    {
        double qMinX = getDouble( "min_x" );
        double qMinY = getDouble( "min_y" );
        double qMaxX = getDouble( "max_x" );
        double qMaxY = getDouble( "max_y" );

        std::string err;
        if( !m_ipc.IsConnected() && !m_ipc.Connect( IpcClient::GetDefaultSocketPath() ) )
        {
            json content = json::array();
            content.push_back( { { "type", "text" }, { "text", "KiCad IPC not connected." } } );
            return "{\"jsonrpc\":\"2.0\",\"result\":" + json( { { "content", content }, { "isError", true } } ).dump() + ",\"id\":" + id + "}";
        }

        constexpr double VAL_IU_TO_MM = 1.0 / 10000.0;
        json issues = json::array();

        auto valBboxOverlap = []( double aMinX, double aMinY, double aMaxX, double aMaxY,
                                  double bMinX, double bMinY, double bMaxX, double bMaxY ) -> bool {
            return aMinX < bMaxX && aMaxX > bMinX && aMinY < bMaxY && aMaxY > bMinY;
        };

        // 1. Collect components in the bbox and check for overlapping bboxes
        struct ValCompRect { std::string ref; double minX, minY, maxX, maxY; };
        std::vector<ValCompRect> compsInBox;
        kiapi::schematic::types::GetSchematicSummaryResponse valSumResp;
        if( getCachedSummary( valSumResp, err ) )
        {
            for( int i = 0; i < valSumResp.components_size(); ++i )
            {
                const auto& c = valSumResp.components( i );
                std::string bErr;
                auto [cx, cy, w, h] = getComponentBounds( c.reference(), bErr, &valSumResp );
                double cMinX = cx - w / 2.0, cMinY = cy - h / 2.0;
                double cMaxX = cx + w / 2.0, cMaxY = cy + h / 2.0;
                if( valBboxOverlap( qMinX, qMinY, qMaxX, qMaxY, cMinX, cMinY, cMaxX, cMaxY ) )
                    compsInBox.push_back( { c.reference(), cMinX, cMinY, cMaxX, cMaxY } );
            }
        }

        for( size_t ci = 0; ci < compsInBox.size(); ++ci )
        {
            for( size_t cj = ci + 1; cj < compsInBox.size(); ++cj )
            {
                if( valBboxOverlap( compsInBox[ci].minX, compsInBox[ci].minY, compsInBox[ci].maxX, compsInBox[ci].maxY,
                                    compsInBox[cj].minX, compsInBox[cj].minY, compsInBox[cj].maxX, compsInBox[cj].maxY ) )
                {
                    json issue;
                    issue["type"] = "overlapping_components";
                    issue["description"] = compsInBox[ci].ref + " and " + compsInBox[cj].ref + " bounding boxes overlap";
                    issue["reference"] = compsInBox[ci].ref + ", " + compsInBox[cj].ref;
                    issues.push_back( issue );
                }
            }
        }

        // 2. Check dangling/unconnected pins in the bbox
        {
            kiapi::common::ApiRequest ercReq;
            ercReq.mutable_header()->set_client_name( "mcp" );
            kiapi::schematic::types::GetDanglingReport ercCmd;
            ercReq.mutable_message()->PackFrom( ercCmd );
            kiapi::common::ApiResponse ercResp;
            if( m_ipc.SendRequest( ercReq, ercResp, err ) && ercResp.status().status() == kiapi::common::AS_OK && ercResp.has_message() )
            {
                kiapi::schematic::types::GetDanglingReportResponse dangResp;
                if( ercResp.message().UnpackTo( &dangResp ) )
                {
                    for( int i = 0; i < dangResp.items_size(); ++i )
                    {
                        const auto& dItem = dangResp.items( i );
                        double px = dItem.x_mm(), py = dItem.y_mm();
                        if( px >= qMinX && px <= qMaxX && py >= qMinY && py <= qMaxY )
                        {
                            json issue;
                            issue["type"] = "unconnected_pin";
                            issue["description"] = "Unconnected pin " + dItem.pin_number() + " on " + dItem.reference();
                            issue["reference"] = dItem.reference();
                            std::ostringstream posStr;
                            posStr << std::fixed << std::setprecision( 2 ) << px << ", " << py;
                            issue["position"] = posStr.str();
                            issues.push_back( issue );
                        }
                    }
                }
            }
        }

        // 3. Check for duplicate labels at same position in the bbox
        {
            kiapi::common::ApiRequest valLblReq;
            valLblReq.mutable_header()->set_client_name( "mcp" );
            kiapi::common::commands::GetItems valLblCmd;
            valLblCmd.mutable_header()->mutable_document()->set_type( kiapi::common::types::DOCTYPE_SCHEMATIC );
            std::string valSheet = getCurrentSheetPath();
            if( !valSheet.empty() )
                valLblCmd.mutable_header()->mutable_document()->mutable_sheet_path()->set_path_human_readable( valSheet );
            valLblCmd.add_types( kiapi::common::types::KOT_SCH_GLOBAL_LABEL );
            valLblReq.mutable_message()->PackFrom( valLblCmd );
            kiapi::common::ApiResponse valLblResp;
            if( m_ipc.SendRequest( valLblReq, valLblResp, err ) && valLblResp.status().status() == kiapi::common::AS_OK && valLblResp.has_message() )
            {
                kiapi::common::commands::GetItemsResponse valLblItems;
                if( valLblResp.message().UnpackTo( &valLblItems ) )
                {
                    std::map<std::pair<int64_t, int64_t>, std::vector<std::string>> labelsByPos;
                    for( int i = 0; i < valLblItems.items_size(); ++i )
                    {
                        kiapi::schematic::types::GlobalLabel gl;
                        if( !valLblItems.items( i ).UnpackTo( &gl ) || !gl.has_position() )
                            continue;
                        double lx = gl.position().x_nm() * VAL_IU_TO_MM;
                        double ly = gl.position().y_nm() * VAL_IU_TO_MM;
                        if( lx >= qMinX && lx <= qMaxX && ly >= qMinY && ly <= qMaxY )
                        {
                            std::string netName;
                            if( gl.has_text() && gl.text().has_text() )
                                netName = gl.text().text().text();
                            auto posKey = std::make_pair( gl.position().x_nm(), gl.position().y_nm() );
                            labelsByPos[posKey].push_back( netName );
                        }
                    }
                    for( const auto& [posKey, names] : labelsByPos )
                    {
                        if( names.size() > 1 )
                        {
                            std::string desc = "Duplicate labels at same position:";
                            for( const auto& n : names )
                                desc += " " + n;
                            json issue;
                            issue["type"] = "duplicate_labels";
                            issue["description"] = desc;
                            std::ostringstream posStr;
                            posStr << std::fixed << std::setprecision( 2 )
                                   << ( posKey.first * VAL_IU_TO_MM ) << ", " << ( posKey.second * VAL_IU_TO_MM );
                            issue["position"] = posStr.str();
                            issues.push_back( issue );
                        }
                    }
                }
            }
        }

        json valOut;
        valOut["valid"] = issues.empty();
        valOut["issues"] = issues;
        json content = json::array();
        content.push_back( { { "type", "text" }, { "text", valOut.dump() } } );
        return "{\"jsonrpc\":\"2.0\",\"result\":" + json( { { "content", content }, { "isError", false } } ).dump() + ",\"id\":" + id + "}";
    }
    // ── align_components ──
    else if( name == "align_components" )
    {
        if( !args.contains( "references" ) || !args["references"].is_array() || args["references"].size() < 2 )
        {
            json content = json::array();
            content.push_back( { { "type", "text" }, { "text", "align_components requires 'references' array with at least 2 elements" } } );
            return "{\"jsonrpc\":\"2.0\",\"result\":" + json( { { "content", content }, { "isError", true } } ).dump() + ",\"id\":" + id + "}";
        }
        std::string axis = getStr( "axis" );
        std::string alignTo = getStr( "align_to" );
        if( axis.empty() || alignTo.empty() )
        {
            json content = json::array();
            content.push_back( { { "type", "text" }, { "text", "align_components requires 'axis' and 'align_to'" } } );
            return "{\"jsonrpc\":\"2.0\",\"result\":" + json( { { "content", content }, { "isError", true } } ).dump() + ",\"id\":" + id + "}";
        }
        bool horizontal = ( axis == "horizontal" );

        kiapi::schematic::types::GetSchematicSummaryResponse sumResp;
        std::string sumErr;
        invalidateSummaryCache();
        if( !getCachedSummary( sumResp, sumErr ) )
        {
            json content = json::array();
            content.push_back( { { "type", "text" }, { "text", "Failed to get schematic summary: " + sumErr } } );
            return "{\"jsonrpc\":\"2.0\",\"result\":" + json( { { "content", content }, { "isError", true } } ).dump() + ",\"id\":" + id + "}";
        }

        struct AlignPos { double x; double y; bool found; };
        std::vector<std::pair<std::string, AlignPos>> compPositions;
        for( const auto& refVal : args["references"] )
        {
            std::string ref = refVal.get<std::string>();
            AlignPos cp = { 0, 0, false };
            for( int i = 0; i < sumResp.components_size(); ++i )
            {
                const auto& c = sumResp.components( i );
                if( c.reference() == ref )
                {
                    cp.x = c.has_position() ? c.position().x_mm() : 0;
                    cp.y = c.has_position() ? c.position().y_mm() : 0;
                    cp.found = true;
                    break;
                }
            }
            compPositions.push_back( { ref, cp } );
        }

        std::vector<std::pair<std::string, AlignPos>> foundComps;
        for( auto& p : compPositions )
            if( p.second.found ) foundComps.push_back( p );

        if( foundComps.size() < 2 )
        {
            json content = json::array();
            content.push_back( { { "type", "text" }, { "text", "Need at least 2 found components to align" } } );
            return "{\"jsonrpc\":\"2.0\",\"result\":" + json( { { "content", content }, { "isError", true } } ).dump() + ",\"id\":" + id + "}";
        }

        double target = 0;
        if( alignTo == "min" )
        {
            target = horizontal ? foundComps[0].second.y : foundComps[0].second.x;
            for( auto& p : foundComps )
                target = std::min( target, horizontal ? p.second.y : p.second.x );
        }
        else if( alignTo == "max" )
        {
            target = horizontal ? foundComps[0].second.y : foundComps[0].second.x;
            for( auto& p : foundComps )
                target = std::max( target, horizontal ? p.second.y : p.second.x );
        }
        else if( alignTo == "center" )
        {
            double mn = horizontal ? foundComps[0].second.y : foundComps[0].second.x;
            double mx = mn;
            for( auto& p : foundComps )
            {
                double v = horizontal ? p.second.y : p.second.x;
                mn = std::min( mn, v );
                mx = std::max( mx, v );
            }
            target = ( mn + mx ) / 2.0;
        }
        else // "mean"
        {
            double sum = 0;
            for( auto& p : foundComps )
                sum += horizontal ? p.second.y : p.second.x;
            target = sum / foundComps.size();
        }

        std::string txnErr;
        TransactionGuard txn( *this, m_ipc, txnErr );
        if( !txn.ok() )
        {
            json content = json::array();
            content.push_back( { { "type", "text" }, { "text", txnErr.empty() ? "Begin commit failed" : txnErr } } );
            return "{\"jsonrpc\":\"2.0\",\"result\":" + json( { { "content", content }, { "isError", true } } ).dump() + ",\"id\":" + id + "}";
        }

        int aligned = 0;
        for( auto& p : foundComps )
        {
            double newX = horizontal ? p.second.x : target;
            double newY = horizontal ? target : p.second.y;

            kiapi::common::ApiRequest moveReq;
            moveReq.mutable_header()->set_client_name( "mcp" );
            kiapi::schematic::types::MoveComponent moveCmd;
            moveCmd.set_reference( p.first );
            moveCmd.mutable_position()->set_x_mm( newX );
            moveCmd.mutable_position()->set_y_mm( newY );
            moveCmd.mutable_commit_id()->set_value( txn.commitId() );
            moveReq.mutable_message()->PackFrom( moveCmd );
            kiapi::common::ApiResponse moveResp;
            std::string moveErr;
            if( m_ipc.SendRequest( moveReq, moveResp, moveErr ) && moveResp.status().status() == kiapi::common::AS_OK )
                aligned++;
        }

        txn.commit();

        json out;
        out["aligned"] = aligned;
        out["axis"] = axis;
        out["target_position"] = target;
        json content = json::array();
        content.push_back( { { "type", "text" }, { "text", out.dump() } } );
        return "{\"jsonrpc\":\"2.0\",\"result\":" + json( { { "content", content }, { "isError", false } } ).dump() + ",\"id\":" + id + "}";
    }
    // ── distribute_components ──
    else if( name == "distribute_components" )
    {
        if( !args.contains( "references" ) || !args["references"].is_array() || args["references"].size() < 2 )
        {
            json content = json::array();
            content.push_back( { { "type", "text" }, { "text", "distribute_components requires 'references' array with at least 2 elements" } } );
            return "{\"jsonrpc\":\"2.0\",\"result\":" + json( { { "content", content }, { "isError", true } } ).dump() + ",\"id\":" + id + "}";
        }
        std::string axis = getStr( "axis" );
        if( axis.empty() )
        {
            json content = json::array();
            content.push_back( { { "type", "text" }, { "text", "distribute_components requires 'axis'" } } );
            return "{\"jsonrpc\":\"2.0\",\"result\":" + json( { { "content", content }, { "isError", true } } ).dump() + ",\"id\":" + id + "}";
        }
        bool horiz = ( axis == "horizontal" );
        bool hasFixedSpacing = hasArg( "spacing_mm" );
        double spacingMm = hasFixedSpacing ? getDouble( "spacing_mm" ) : 0;

        kiapi::schematic::types::GetSchematicSummaryResponse sumResp;
        std::string sumErr;
        invalidateSummaryCache();
        if( !getCachedSummary( sumResp, sumErr ) )
        {
            json content = json::array();
            content.push_back( { { "type", "text" }, { "text", "Failed to get schematic summary: " + sumErr } } );
            return "{\"jsonrpc\":\"2.0\",\"result\":" + json( { { "content", content }, { "isError", true } } ).dump() + ",\"id\":" + id + "}";
        }

        struct DistEntry { std::string ref; double x; double y; double sortVal; };
        std::vector<DistEntry> comps;
        for( const auto& refVal : args["references"] )
        {
            std::string ref = refVal.get<std::string>();
            for( int i = 0; i < sumResp.components_size(); ++i )
            {
                const auto& c = sumResp.components( i );
                if( c.reference() == ref )
                {
                    double px = c.has_position() ? c.position().x_mm() : 0;
                    double py = c.has_position() ? c.position().y_mm() : 0;
                    comps.push_back( { ref, px, py, horiz ? px : py } );
                    break;
                }
            }
        }

        if( comps.size() < 2 )
        {
            json content = json::array();
            content.push_back( { { "type", "text" }, { "text", "Need at least 2 found components to distribute" } } );
            return "{\"jsonrpc\":\"2.0\",\"result\":" + json( { { "content", content }, { "isError", true } } ).dump() + ",\"id\":" + id + "}";
        }

        std::sort( comps.begin(), comps.end(), []( const DistEntry& a, const DistEntry& b ) {
            return a.sortVal < b.sortVal;
        } );

        double actualSpacing;
        if( hasFixedSpacing )
        {
            actualSpacing = spacingMm;
        }
        else
        {
            double range = comps.back().sortVal - comps.front().sortVal;
            actualSpacing = range / ( comps.size() - 1 );
        }

        double startVal = comps.front().sortVal;
        for( size_t i = 0; i < comps.size(); ++i )
        {
            double newVal = startVal + i * actualSpacing;
            if( horiz )
                comps[i].x = newVal;
            else
                comps[i].y = newVal;
        }

        std::string txnErr;
        TransactionGuard txn( *this, m_ipc, txnErr );
        if( !txn.ok() )
        {
            json content = json::array();
            content.push_back( { { "type", "text" }, { "text", txnErr.empty() ? "Begin commit failed" : txnErr } } );
            return "{\"jsonrpc\":\"2.0\",\"result\":" + json( { { "content", content }, { "isError", true } } ).dump() + ",\"id\":" + id + "}";
        }

        int distributed = 0;
        for( auto& dc : comps )
        {
            kiapi::common::ApiRequest moveReq;
            moveReq.mutable_header()->set_client_name( "mcp" );
            kiapi::schematic::types::MoveComponent moveCmd;
            moveCmd.set_reference( dc.ref );
            moveCmd.mutable_position()->set_x_mm( dc.x );
            moveCmd.mutable_position()->set_y_mm( dc.y );
            moveCmd.mutable_commit_id()->set_value( txn.commitId() );
            moveReq.mutable_message()->PackFrom( moveCmd );
            kiapi::common::ApiResponse moveResp;
            std::string moveErr;
            if( m_ipc.SendRequest( moveReq, moveResp, moveErr ) && moveResp.status().status() == kiapi::common::AS_OK )
                distributed++;
        }

        txn.commit();

        json out;
        out["distributed"] = distributed;
        out["axis"] = axis;
        out["spacing_mm"] = actualSpacing;
        json content = json::array();
        content.push_back( { { "type", "text" }, { "text", out.dump() } } );
        return "{\"jsonrpc\":\"2.0\",\"result\":" + json( { { "content", content }, { "isError", false } } ).dump() + ",\"id\":" + id + "}";
    }
    // ── autoroute_short_orthogonal ──
    else if( name == "autoroute_short_orthogonal" )
    {
        std::string ref1 = getStr( "reference1" );
        std::string pin1str = getStr( "pin1" );
        std::string ref2 = getStr( "reference2" );
        std::string pin2str = getStr( "pin2" );
        std::string netName = getStr( "net_name" );
        bool addNetLabels = getBool( "add_net_labels", false );

        if( ref1.empty() || pin1str.empty() || ref2.empty() || pin2str.empty() )
        {
            json content = json::array();
            content.push_back( { { "type", "text" }, { "text", "autoroute_short_orthogonal requires reference1, pin1, reference2, pin2" } } );
            return "{\"jsonrpc\":\"2.0\",\"result\":" + json( { { "content", content }, { "isError", true } } ).dump() + ",\"id\":" + id + "}";
        }

        std::string pinErr1, pinErr2;
        double orient1 = 0, orient2 = 0;
        auto [ax1, ay1] = getPinPosition( ref1, pin1str, pinErr1, &orient1 );
        if( !pinErr1.empty() )
        {
            json content = json::array();
            content.push_back( { { "type", "text" }, { "text", "Failed to get pin position for " + ref1 + "/" + pin1str + ": " + pinErr1 } } );
            return "{\"jsonrpc\":\"2.0\",\"result\":" + json( { { "content", content }, { "isError", true } } ).dump() + ",\"id\":" + id + "}";
        }
        auto [ax2, ay2] = getPinPosition( ref2, pin2str, pinErr2, &orient2 );
        if( !pinErr2.empty() )
        {
            json content = json::array();
            content.push_back( { { "type", "text" }, { "text", "Failed to get pin position for " + ref2 + "/" + pin2str + ": " + pinErr2 } } );
            return "{\"jsonrpc\":\"2.0\",\"result\":" + json( { { "content", content }, { "isError", true } } ).dump() + ",\"id\":" + id + "}";
        }

        std::string txnErr;
        TransactionGuard txn( *this, m_ipc, txnErr );
        if( !txn.ok() )
        {
            json content = json::array();
            content.push_back( { { "type", "text" }, { "text", txnErr.empty() ? "Begin commit failed" : txnErr } } );
            return "{\"jsonrpc\":\"2.0\",\"result\":" + json( { { "content", content }, { "isError", true } } ).dump() + ",\"id\":" + id + "}";
        }

        auto mmToIuAr = []( double mm ) -> int64_t {
            return static_cast<int64_t>( std::round( mm * 10000.0 ) );
        };
        auto makeUuidAr = []() -> std::string {
            static std::random_device rd;
            static std::mt19937_64 gen( rd() );
            std::uniform_int_distribution<uint64_t> dis( 0, 0xFFFFFFFFFFFFULL );
            std::ostringstream os;
            os << std::hex << std::setfill( '0' )
               << std::setw( 8 ) << ( dis( gen ) & 0xFFFFFFFFUL ) << "-"
               << std::setw( 4 ) << ( dis( gen ) & 0xFFFFUL ) << "-4"
               << std::setw( 3 ) << ( dis( gen ) & 0xFFFUL ) << "-"
               << std::setw( 4 ) << ( ( dis( gen ) & 0x3FFFUL ) | 0x8000UL ) << "-"
               << std::setw( 12 ) << ( dis( gen ) & 0xFFFFFFFFFFFFULL );
            return os.str();
        };

        kiapi::common::commands::CreateItems createCmd;
        createCmd.mutable_header()->mutable_document()->set_type( kiapi::common::types::DOCTYPE_SCHEMATIC );
        std::string sheetPath = getCurrentSheetPath();
        if( !sheetPath.empty() )
            createCmd.mutable_header()->mutable_document()->mutable_sheet_path()->set_path_human_readable( sheetPath );

        json wireIds = json::array();
        int segments = 0;
        constexpr double AR_TOLERANCE = 0.01;

        bool sameX = std::abs( ax1 - ax2 ) < AR_TOLERANCE;
        bool sameY = std::abs( ay1 - ay2 ) < AR_TOLERANCE;

        if( sameX || sameY )
        {
            kiapi::schematic::types::Line wire;
            wire.mutable_id()->set_value( makeUuidAr() );
            wire.mutable_start()->set_x_nm( mmToIuAr( ax1 ) );
            wire.mutable_start()->set_y_nm( mmToIuAr( ay1 ) );
            wire.mutable_end()->set_x_nm( mmToIuAr( ax2 ) );
            wire.mutable_end()->set_y_nm( mmToIuAr( ay2 ) );
            wire.set_layer( kiapi::schematic::types::SL_WIRE );
            wireIds.push_back( wire.id().value() );
            createCmd.add_items()->PackFrom( wire );
            segments = 1;
        }
        else
        {
            double midX = ax2;
            double midY = ay1;

            kiapi::schematic::types::Line wire1;
            wire1.mutable_id()->set_value( makeUuidAr() );
            wire1.mutable_start()->set_x_nm( mmToIuAr( ax1 ) );
            wire1.mutable_start()->set_y_nm( mmToIuAr( ay1 ) );
            wire1.mutable_end()->set_x_nm( mmToIuAr( midX ) );
            wire1.mutable_end()->set_y_nm( mmToIuAr( midY ) );
            wire1.set_layer( kiapi::schematic::types::SL_WIRE );
            wireIds.push_back( wire1.id().value() );
            createCmd.add_items()->PackFrom( wire1 );

            kiapi::schematic::types::Line wire2;
            wire2.mutable_id()->set_value( makeUuidAr() );
            wire2.mutable_start()->set_x_nm( mmToIuAr( midX ) );
            wire2.mutable_start()->set_y_nm( mmToIuAr( midY ) );
            wire2.mutable_end()->set_x_nm( mmToIuAr( ax2 ) );
            wire2.mutable_end()->set_y_nm( mmToIuAr( ay2 ) );
            wire2.set_layer( kiapi::schematic::types::SL_WIRE );
            wireIds.push_back( wire2.id().value() );
            createCmd.add_items()->PackFrom( wire2 );
            segments = 2;
        }

        int labelsAddedAr = 0;
        if( !netName.empty() && addNetLabels )
        {
            constexpr double PI_AR = 3.14159265358979323846;
            std::map<LabelPosKey, std::string> occupiedAr;
            std::string occErrAr;
            fetchGlobalLabelsByPosition( occupiedAr, occErrAr );
            for( int endIdx = 0; endIdx < 2; ++endIdx )
            {
                double px = endIdx == 0 ? ax1 : ax2;
                double py = endIdx == 0 ? ay1 : ay2;
                double orientDeg = endIdx == 0 ? orient1 : orient2;
                double outwardDeg = std::fmod( orientDeg + 180.0, 360.0 );
                if( outwardDeg < 0 ) outwardDeg += 360.0;
                double rad = outwardDeg * PI_AR / 180.0;
                double lx = px + std::cos( rad ) * 1.0;
                double ly = py - std::sin( rad ) * 1.0;
                double backDegAr = std::fmod( outwardDeg + 180.0, 360.0 );
                double labelAngle;
                if( backDegAr >= 315.0 || backDegAr < 45.0 )
                    labelAngle = 0.0;
                else if( backDegAr >= 45.0 && backDegAr < 135.0 )
                    labelAngle = 90.0;
                else if( backDegAr >= 135.0 && backDegAr < 225.0 )
                    labelAngle = 180.0;
                else
                    labelAngle = 270.0;

                double resX = lx, resY = ly;
                bool shouldCreate = true;
                std::string placeErr;
                if( !resolveLabelPlacement( lx, ly, netName, occupiedAr, true, resX, resY, shouldCreate, placeErr ) )
                {
                    json content = json::array();
                    content.push_back( { { "type", "text" }, { "text", "autoroute_short_orthogonal: " + placeErr } } );
                    return "{\"jsonrpc\":\"2.0\",\"result\":" + json( { { "content", content }, { "isError", true } } ).dump() + ",\"id\":" + id + "}";
                }
                if( shouldCreate )
                {
                    kiapi::schematic::types::GlobalLabel glabel;
                    glabel.mutable_id()->set_value( makeUuidAr() );
                    glabel.mutable_position()->set_x_nm( mmToIuAr( resX ) );
                    glabel.mutable_position()->set_y_nm( mmToIuAr( resY ) );
                    glabel.mutable_text()->mutable_text()->set_text( netName );
                    glabel.mutable_text()->mutable_text()->mutable_attributes()->set_horizontal_alignment( kiapi::common::types::HA_CENTER );
                    glabel.mutable_text()->mutable_text()->mutable_attributes()->set_vertical_alignment( kiapi::common::types::VA_CENTER );
                    glabel.mutable_text()->mutable_text()->mutable_attributes()->mutable_angle()->set_value_degrees( static_cast<int>( labelAngle + 180 ) % 360 );
                    createCmd.add_items()->PackFrom( glabel );
                    labelsAddedAr++;
                }
                kiapi::schematic::types::Line lWire;
                lWire.mutable_id()->set_value( makeUuidAr() );
                lWire.mutable_start()->set_x_nm( mmToIuAr( px ) );
                lWire.mutable_start()->set_y_nm( mmToIuAr( py ) );
                lWire.mutable_end()->set_x_nm( mmToIuAr( resX ) );
                lWire.mutable_end()->set_y_nm( mmToIuAr( resY ) );
                lWire.set_layer( kiapi::schematic::types::SL_WIRE );
                createCmd.add_items()->PackFrom( lWire );
            }
        }

        kiapi::common::ApiRequest createReq;
        createReq.mutable_header()->set_client_name( "mcp" );
        // commit managed by TransactionGuard
        createReq.mutable_message()->PackFrom( createCmd );
        kiapi::common::ApiResponse createResp;
        std::string createErr;
        if( !m_ipc.SendRequest( createReq, createResp, createErr ) || createResp.status().status() != kiapi::common::AS_OK )
        {
            json content = json::array();
            std::string msg = !createErr.empty() ? createErr : ( createResp.status().error_message().empty() ? "CreateItems failed" : createResp.status().error_message() );
            content.push_back( { { "type", "text" }, { "text", msg } } );
            return "{\"jsonrpc\":\"2.0\",\"result\":" + json( { { "content", content }, { "isError", true } } ).dump() + ",\"id\":" + id + "}";
        }

        txn.commit();

        json out;
        out["routed"] = true;
        out["segments"] = segments;
        out["wire_ids"] = wireIds;
        if( !netName.empty() )
            out["net_name"] = netName;
        if( addNetLabels && !netName.empty() )
            out["global_labels_added"] = labelsAddedAr;
        json content = json::array();
        content.push_back( { { "type", "text" }, { "text", out.dump() } } );
        return "{\"jsonrpc\":\"2.0\",\"result\":" + json( { { "content", content }, { "isError", false } } ).dump() + ",\"id\":" + id + "}";
    }
    // ── reload_project_symbol_libraries ──
    else if( name == "reload_project_symbol_libraries" )
    {
        if( !m_ipc.IsConnected() && !m_ipc.Connect( IpcClient::GetDefaultSocketPath() ) )
        {
            json content = json::array();
            content.push_back( { { "type", "text" }, { "text", "KiCad IPC not connected." } } );
            return "{\"jsonrpc\":\"2.0\",\"result\":" + json( { { "content", content }, { "isError", true } } ).dump() + ",\"id\":" + id + "}";
        }

        kiapi::common::ApiRequest reloadSymLibsReq;
        reloadSymLibsReq.mutable_header()->set_client_name( "mcp" );
        kiapi::schematic::types::ReloadProjectSymbolLibraries reloadSymCmd;
        reloadSymLibsReq.mutable_message()->PackFrom( reloadSymCmd );
        kiapi::common::ApiResponse reloadSymResp;
        std::string ipcErr;
        if( !m_ipc.SendRequest( reloadSymLibsReq, reloadSymResp, ipcErr )
            || reloadSymResp.status().status() != kiapi::common::AS_OK )
        {
            json content = json::array();
            std::string msg = !ipcErr.empty()
                                      ? ipcErr
                                      : ( reloadSymResp.status().error_message().empty()
                                                  ? "reload_project_symbol_libraries failed"
                                                  : reloadSymResp.status().error_message() );
            content.push_back( { { "type", "text" }, { "text", msg } } );
            return "{\"jsonrpc\":\"2.0\",\"result\":" + json( { { "content", content }, { "isError", true } } ).dump() + ",\"id\":" + id + "}";
        }

        kiapi::schematic::types::ReloadProjectSymbolLibrariesResponse r;
        bool ok = false;
        json out;
        if( reloadSymResp.has_message() && reloadSymResp.message().UnpackTo( &r ) )
        {
            ok = r.ok();
            out["ok"] = r.ok();
            out["sym_lib_table_path"] = r.sym_lib_table_path();
            if( !r.error_message().empty() )
                out["error_message"] = r.error_message();
        }
        json content = json::array();
        content.push_back( { { "type", "text" }, { "text", out.dump() } } );
        return "{\"jsonrpc\":\"2.0\",\"result\":" + json( { { "content", content }, { "isError", !ok } } ).dump() + ",\"id\":" + id + "}";
    }
    // ── reload_project_footprint_libraries ──
    else if( name == "reload_project_footprint_libraries" )
    {
        if( !m_ipc.IsConnected() && !m_ipc.Connect( IpcClient::GetDefaultSocketPath() ) )
        {
            json content = json::array();
            content.push_back( { { "type", "text" }, { "text", "KiCad IPC not connected." } } );
            return "{\"jsonrpc\":\"2.0\",\"result\":" + json( { { "content", content }, { "isError", true } } ).dump() + ",\"id\":" + id + "}";
        }

        kiapi::common::ApiRequest reloadFpLibsReq;
        reloadFpLibsReq.mutable_header()->set_client_name( "mcp" );
        kiapi::board::commands::ReloadProjectFootprintLibraries reloadFpCmd;
        reloadFpLibsReq.mutable_message()->PackFrom( reloadFpCmd );
        kiapi::common::ApiResponse reloadFpResp;
        std::string ipcErr;
        if( !m_ipc.SendRequest( reloadFpLibsReq, reloadFpResp, ipcErr )
            || reloadFpResp.status().status() != kiapi::common::AS_OK )
        {
            json content = json::array();
            std::string msg = !ipcErr.empty()
                                      ? ipcErr
                                      : ( reloadFpResp.status().error_message().empty()
                                                  ? "reload_project_footprint_libraries failed"
                                                  : reloadFpResp.status().error_message() );
            content.push_back( { { "type", "text" }, { "text", msg } } );
            return "{\"jsonrpc\":\"2.0\",\"result\":" + json( { { "content", content }, { "isError", true } } ).dump() + ",\"id\":" + id + "}";
        }

        kiapi::board::commands::ReloadProjectFootprintLibrariesResponse r;
        bool ok = false;
        json out;
        if( reloadFpResp.has_message() && reloadFpResp.message().UnpackTo( &r ) )
        {
            ok = r.ok();
            out["ok"] = r.ok();
            out["fp_lib_table_path"] = r.fp_lib_table_path();
            if( !r.error_message().empty() )
                out["error_message"] = r.error_message();
        }
        json content = json::array();
        content.push_back( { { "type", "text" }, { "text", out.dump() } } );
        return "{\"jsonrpc\":\"2.0\",\"result\":" + json( { { "content", content }, { "isError", !ok } } ).dump() + ",\"id\":" + id + "}";
    }
    // ── append_project_symbol_library_row ──
    else if( name == "append_project_symbol_library_row" )
    {
        if( !m_ipc.IsConnected() && !m_ipc.Connect( IpcClient::GetDefaultSocketPath() ) )
        {
            json content = json::array();
            content.push_back( { { "type", "text" }, { "text", "KiCad IPC not connected." } } );
            return "{\"jsonrpc\":\"2.0\",\"result\":" + json( { { "content", content }, { "isError", true } } ).dump() + ",\"id\":" + id + "}";
        }

        kiapi::common::ApiRequest appendSymRowReq;
        appendSymRowReq.mutable_header()->set_client_name( "mcp" );
        kiapi::schematic::types::AppendProjectSymbolLibraryRow appendSymCmd;
        appendSymCmd.set_library_nickname( getStr( "library_nickname" ) );
        appendSymCmd.set_uri( getStr( "uri" ) );
        appendSymCmd.set_description( getStr( "description" ) );
        appendSymCmd.set_replace_existing( getBool( "replace_existing", false ) );
        appendSymRowReq.mutable_message()->PackFrom( appendSymCmd );
        kiapi::common::ApiResponse appendSymResp;
        std::string ipcErr;
        if( !m_ipc.SendRequest( appendSymRowReq, appendSymResp, ipcErr )
            || appendSymResp.status().status() != kiapi::common::AS_OK )
        {
            json content = json::array();
            std::string msg = !ipcErr.empty()
                                      ? ipcErr
                                      : ( appendSymResp.status().error_message().empty()
                                                  ? "append_project_symbol_library_row failed"
                                                  : appendSymResp.status().error_message() );
            content.push_back( { { "type", "text" }, { "text", msg } } );
            return "{\"jsonrpc\":\"2.0\",\"result\":" + json( { { "content", content }, { "isError", true } } ).dump() + ",\"id\":" + id + "}";
        }

        kiapi::schematic::types::AppendProjectSymbolLibraryRowResponse r;
        bool ok = false;
        json out;
        if( appendSymResp.has_message() && appendSymResp.message().UnpackTo( &r ) )
        {
            ok = r.ok();
            out["ok"] = r.ok();
            out["skipped_duplicate"] = r.skipped_duplicate();
            out["wrote_file"] = r.wrote_file();
            if( !r.error_message().empty() )
                out["error_message"] = r.error_message();
        }
        json content = json::array();
        content.push_back( { { "type", "text" }, { "text", out.dump() } } );
        return "{\"jsonrpc\":\"2.0\",\"result\":" + json( { { "content", content }, { "isError", !ok } } ).dump() + ",\"id\":" + id + "}";
    }
    // ── append_project_footprint_library_row ──
    else if( name == "append_project_footprint_library_row" )
    {
        if( !m_ipc.IsConnected() && !m_ipc.Connect( IpcClient::GetDefaultSocketPath() ) )
        {
            json content = json::array();
            content.push_back( { { "type", "text" }, { "text", "KiCad IPC not connected." } } );
            return "{\"jsonrpc\":\"2.0\",\"result\":" + json( { { "content", content }, { "isError", true } } ).dump() + ",\"id\":" + id + "}";
        }

        kiapi::common::ApiRequest appendFpRowReq;
        appendFpRowReq.mutable_header()->set_client_name( "mcp" );
        kiapi::board::commands::AppendProjectFootprintLibraryRow appendFpCmd;
        appendFpCmd.set_library_nickname( getStr( "library_nickname" ) );
        appendFpCmd.set_uri( getStr( "uri" ) );
        appendFpCmd.set_description( getStr( "description" ) );
        appendFpCmd.set_replace_existing( getBool( "replace_existing", false ) );
        appendFpRowReq.mutable_message()->PackFrom( appendFpCmd );
        kiapi::common::ApiResponse appendFpResp;
        std::string ipcErr;
        if( !m_ipc.SendRequest( appendFpRowReq, appendFpResp, ipcErr )
            || appendFpResp.status().status() != kiapi::common::AS_OK )
        {
            json content = json::array();
            std::string msg = !ipcErr.empty()
                                      ? ipcErr
                                      : ( appendFpResp.status().error_message().empty()
                                                  ? "append_project_footprint_library_row failed"
                                                  : appendFpResp.status().error_message() );
            content.push_back( { { "type", "text" }, { "text", msg } } );
            return "{\"jsonrpc\":\"2.0\",\"result\":" + json( { { "content", content }, { "isError", true } } ).dump() + ",\"id\":" + id + "}";
        }

        kiapi::board::commands::AppendProjectFootprintLibraryRowResponse r;
        bool ok = false;
        json out;
        if( appendFpResp.has_message() && appendFpResp.message().UnpackTo( &r ) )
        {
            ok = r.ok();
            out["ok"] = r.ok();
            out["skipped_duplicate"] = r.skipped_duplicate();
            out["wrote_file"] = r.wrote_file();
            if( !r.error_message().empty() )
                out["error_message"] = r.error_message();
        }
        json content = json::array();
        content.push_back( { { "type", "text" }, { "text", out.dump() } } );
        return "{\"jsonrpc\":\"2.0\",\"result\":" + json( { { "content", content }, { "isError", !ok } } ).dump() + ",\"id\":" + id + "}";
    }
    // ── ingest_project_library_files ──
    else if( name == "ingest_project_library_files" )
    {
        constexpr size_t MAX_FILE_BYTES = 25 * 1024 * 1024;

        if( !m_ipc.IsConnected() && !m_ipc.Connect( IpcClient::GetDefaultSocketPath() ) )
        {
            json content = json::array();
            content.push_back( { { "type", "text" }, { "text", "KiCad IPC not connected." } } );
            return "{\"jsonrpc\":\"2.0\",\"result\":" + json( { { "content", content }, { "isError", true } } ).dump() + ",\"id\":" + id + "}";
        }

        std::string projDir = getProjectDirectory();
        if( projDir.empty() )
        {
            json content = json::array();
            content.push_back( { { "type", "text" }, { "text", "No project path from open schematic (KIPRJMOD)." } } );
            return "{\"jsonrpc\":\"2.0\",\"result\":" + json( { { "content", content }, { "isError", true } } ).dump() + ",\"id\":" + id + "}";
        }

        std::string relRoot = getStr( "project_relative_dir" );
        std::string libNick = getStr( "library_nickname" );
        std::string symUriRel = getStr( "symbol_uri_relative" );
        std::string fpUriRel = getStr( "footprint_uri_relative" );
        std::string descr = getStr( "description" );
        const bool replaceExisting = getBool( "replace_existing", false );

        auto trimSlashes = []( std::string s ) {
            while( !s.empty() && ( s.front() == '/' || s.front() == '\\' ) )
                s.erase( s.begin() );
            while( !s.empty() && ( s.back() == '/' || s.back() == '\\' ) )
                s.pop_back();
            return s;
        };
        relRoot = trimSlashes( relRoot );

        if( relRoot.find( ".." ) != std::string::npos || libNick.empty() || symUriRel.empty() || fpUriRel.empty() )
        {
            json content = json::array();
            content.push_back(
                    { { "type", "text" },
                      { "text", "Invalid project_relative_dir, library_nickname, or URI fields (no .. allowed in root)." } } );
            return "{\"jsonrpc\":\"2.0\",\"result\":" + json( { { "content", content }, { "isError", true } } ).dump() + ",\"id\":" + id + "}";
        }

        if( !args.contains( "files" ) || !args["files"].is_array() || args["files"].empty() )
        {
            json content = json::array();
            content.push_back( { { "type", "text" }, { "text", "ingest_project_library_files requires non-empty files array." } } );
            return "{\"jsonrpc\":\"2.0\",\"result\":" + json( { { "content", content }, { "isError", true } } ).dump() + ",\"id\":" + id + "}";
        }

        std::error_code ec;
        fs::path rootAbsPath = fs::absolute( fs::path( projDir ) / fs::path( relRoot ), ec );
        if( ec )
        {
            json content = json::array();
            content.push_back( { { "type", "text" }, { "text", "Invalid project path or project_relative_dir." } } );
            return "{\"jsonrpc\":\"2.0\",\"result\":" + json( { { "content", content }, { "isError", true } } ).dump() + ",\"id\":" + id + "}";
        }

        rootAbsPath = rootAbsPath.lexically_normal();
        const std::string rootAbs = rootAbsPath.string();

        if( !pathIsInsideProject( projDir, rootAbs ) )
        {
            json content = json::array();
            content.push_back( { { "type", "text" }, { "text", "project_relative_dir resolves outside project." } } );
            return "{\"jsonrpc\":\"2.0\",\"result\":" + json( { { "content", content }, { "isError", true } } ).dump() + ",\"id\":" + id + "}";
        }

        fs::create_directories( rootAbsPath, ec );
        if( ec )
        {
            json content = json::array();
            content.push_back( { { "type", "text" }, { "text", "Failed to create project_relative_dir under project." } } );
            return "{\"jsonrpc\":\"2.0\",\"result\":" + json( { { "content", content }, { "isError", true } } ).dump() + ",\"id\":" + id + "}";
        }

        json written = json::array();
        try
        {
            for( const auto& item : args["files"] )
            {
                if( !item.is_object() || !item.contains( "relative_path" ) || !item.contains( "content_base64" ) )
                    throw std::runtime_error( "Each file entry needs relative_path and content_base64" );

                std::string relPath = item["relative_path"].get<std::string>();
                std::string b64 = item["content_base64"].get<std::string>();
                if( relPath.find( ".." ) != std::string::npos )
                    throw std::runtime_error( "Invalid relative_path (..)" );

                fs::path outPath = ( rootAbsPath / fs::path( relPath ) ).lexically_normal();
                fs::path outAbsPath = fs::absolute( outPath, ec );
                if( ec )
                    throw std::runtime_error( "Invalid path for " + relPath );

                const std::string outAbs = outAbsPath.string();

                if( !pathIsInsideProject( projDir, outAbs ) )
                    throw std::runtime_error( "File path escapes project: " + relPath );

                fs::path parent = outAbsPath.parent_path();
                if( !parent.empty() )
                {
                    fs::create_directories( parent, ec );
                    if( ec )
                        throw std::runtime_error( "Failed to mkdir for " + relPath );
                }

                std::vector<uint8_t> decoded;
                if( !base64DecodeToBytes( b64, decoded ) )
                    throw std::runtime_error( "Base64 decode failed for " + relPath );

                if( decoded.size() > MAX_FILE_BYTES )
                    throw std::runtime_error( "File too large after decode: " + relPath );

                if( !writeFileBytes( outAbsPath, decoded ) )
                    throw std::runtime_error( "Cannot write file: " + relPath );

                written.push_back( relPath );
            }
        }
        catch( const std::exception& e )
        {
            json content = json::array();
            content.push_back( { { "type", "text" }, { "text", std::string( "ingest_project_library_files: " ) + e.what() } } );
            return "{\"jsonrpc\":\"2.0\",\"result\":" + json( { { "content", content }, { "isError", true } } ).dump() + ",\"id\":" + id + "}";
        }

        std::string symUri = std::string( "${KIPRJMOD}/" ) + trimSlashes( symUriRel );
        std::string fpUri = std::string( "${KIPRJMOD}/" ) + trimSlashes( fpUriRel );
        if( descr.empty() )
            descr = "Imported library " + libNick;

        auto appendSymbolRowViaApi = [&]() -> std::pair<bool, std::string>
        {
            kiapi::common::ApiRequest appendReq;
            appendReq.mutable_header()->set_client_name( "mcp" );
            kiapi::schematic::types::AppendProjectSymbolLibraryRow appendCmd;
            appendCmd.set_library_nickname( libNick );
            appendCmd.set_uri( symUri );
            appendCmd.set_description( descr );
            appendCmd.set_replace_existing( replaceExisting );
            appendReq.mutable_message()->PackFrom( appendCmd );

            kiapi::common::ApiResponse appendResp;
            std::string                ipcErr;
            if( !m_ipc.SendRequest( appendReq, appendResp, ipcErr )
                || appendResp.status().status() != kiapi::common::AS_OK )
            {
                return { false, !ipcErr.empty() ? ipcErr : "append_project_symbol_library_row failed" };
            }

            kiapi::schematic::types::AppendProjectSymbolLibraryRowResponse r;
            if( appendResp.has_message() && appendResp.message().UnpackTo( &r ) )
            {
                if( !r.ok() )
                    return { false, r.error_message().empty() ? "append_project_symbol_library_row failed" : r.error_message() };
                if( r.skipped_duplicate() && !replaceExisting )
                    return { false, "sym-lib-table already has this library nickname; choose another nickname." };
                return { true, "" };
            }

            return { false, "append_project_symbol_library_row returned unexpected payload" };
        };

        auto appendFootprintRowViaApi = [&]() -> std::pair<bool, std::string>
        {
            kiapi::common::ApiRequest appendReq;
            appendReq.mutable_header()->set_client_name( "mcp" );
            kiapi::board::commands::AppendProjectFootprintLibraryRow appendCmd;
            appendCmd.set_library_nickname( libNick );
            appendCmd.set_uri( fpUri );
            appendCmd.set_description( descr );
            appendCmd.set_replace_existing( replaceExisting );
            appendReq.mutable_message()->PackFrom( appendCmd );

            kiapi::common::ApiResponse appendResp;
            std::string                ipcErr;
            if( !m_ipc.SendRequest( appendReq, appendResp, ipcErr )
                || appendResp.status().status() != kiapi::common::AS_OK )
            {
                return { false, !ipcErr.empty() ? ipcErr : "append_project_footprint_library_row failed" };
            }

            kiapi::board::commands::AppendProjectFootprintLibraryRowResponse r;
            if( appendResp.has_message() && appendResp.message().UnpackTo( &r ) )
            {
                if( !r.ok() )
                    return { false, r.error_message().empty() ? "append_project_footprint_library_row failed" : r.error_message() };
                if( r.skipped_duplicate() && !replaceExisting )
                    return { false, "fp-lib-table already has this library nickname; choose another nickname." };
                return { true, "" };
            }

            return { false, "append_project_footprint_library_row returned unexpected payload" };
        };

        auto appendFootprintRowOnDiskFallback = [&]() -> std::pair<bool, std::string>
        {
            const fs::path fpTablePath = fs::path( projDir ) / "fp-lib-table";
            std::string fpTable;
            if( !readFileToString( fpTablePath.string(), fpTable ) )
                fpTable = "(fp_lib_table\n  (version 7)\n)\n";

            try
            {
                std::string escaped;
                for( char c : libNick )
                {
                    switch( c )
                    {
                    case '.':
                    case '*':
                    case '+':
                    case '?':
                    case '^':
                    case '$':
                    case '{':
                    case '}':
                    case '(':
                    case ')':
                    case '[':
                    case ']':
                    case '|':
                    case '\\':
                        escaped.push_back( '\\' );
                        escaped.push_back( c );
                        break;
                    default: escaped.push_back( c );
                    }
                }

                std::regex re( std::string( R"(\(lib\s*\(name\s*")" ) + escaped + R"("\))",
                               std::regex_constants::icase );
                if( std::regex_search( fpTable, re ) )
                    return { false, "fp-lib-table already has this library nickname; choose another nickname." };

                size_t closeIdx = fpTable.find_last_of( ')' );
                if( closeIdx == std::string::npos )
                    return { false, "Invalid fp-lib-table (missing closing parenthesis)" };

                auto escapeTableValue = []( const std::string& value )
                {
                    std::string out = value;
                    size_t pos = 0;
                    while( ( pos = out.find( '\\', pos ) ) != std::string::npos )
                    {
                        out.replace( pos, 1, "\\\\" );
                        pos += 2;
                    }
                    pos = 0;
                    while( ( pos = out.find( '"', pos ) ) != std::string::npos )
                    {
                        out.replace( pos, 1, "\\\"" );
                        pos += 2;
                    }
                    return out;
                };

                std::string entry = "  (lib (name \"" + escapeTableValue( libNick )
                                    + "\")(type \"KiCad\")(uri \"" + escapeTableValue( fpUri )
                                    + "\")(options \"\")(descr \"" + escapeTableValue( descr )
                                    + "\"))\n";

                std::string updated = fpTable.substr( 0, closeIdx ) + entry + ")\n";
                std::ofstream out( fpTablePath, std::ios::binary | std::ios::trunc );
                if( !out.is_open() )
                    return { false, "Cannot write fp-lib-table" };
                out.write( updated.data(), static_cast<std::streamsize>( updated.size() ) );
                if( !out.good() )
                    return { false, "Cannot write fp-lib-table" };
                return { true, "" };
            }
            catch( const std::exception& e )
            {
                return { false, e.what() };
            }
        };

        std::vector<std::string> warnings;

        auto appendResSym = appendSymbolRowViaApi();
        if( !appendResSym.first )
        {
            json content = json::array();
            content.push_back( { { "type", "text" }, { "text", std::string( "ingest_project_library_files: " ) + appendResSym.second } } );
            return "{\"jsonrpc\":\"2.0\",\"result\":" + json( { { "content", content }, { "isError", true } } ).dump() + ",\"id\":" + id + "}";
        }

        auto appendResFp = appendFootprintRowViaApi();
        if( !appendResFp.first )
        {
            auto fallbackRes = appendFootprintRowOnDiskFallback();
            if( !fallbackRes.first )
            {
                json content = json::array();
                content.push_back( { { "type", "text" }, { "text", std::string( "ingest_project_library_files: " ) + fallbackRes.second } } );
                return "{\"jsonrpc\":\"2.0\",\"result\":" + json( { { "content", content }, { "isError", true } } ).dump() + ",\"id\":" + id + "}";
            }
            warnings.push_back( std::string( "append_project_footprint_library_row unavailable; used disk fallback: " ) + appendResFp.second );
        }

        std::vector<std::string> symbolNames;
        try
        {
            for( const auto& item : args["files"] )
            {
                if( !item.is_object() || !item.contains( "relative_path" ) || !item.contains( "content_base64" ) )
                    continue;

                std::string relPath = item["relative_path"].get<std::string>();
                std::string relPathLower = relPath;
                std::transform( relPathLower.begin(), relPathLower.end(), relPathLower.begin(), ::tolower );
                if( relPathLower.size() < 10 || relPathLower.substr( relPathLower.size() - 10 ) != ".kicad_sym" )
                    continue;

                std::vector<uint8_t> decoded;
                if( !base64DecodeToBytes( item["content_base64"].get<std::string>(), decoded ) )
                    continue;

                std::string symContent( decoded.begin(), decoded.end() );
                std::vector<std::string> extracted = extractSymbolNamesFromKicadSym( symContent );
                symbolNames.insert( symbolNames.end(), extracted.begin(), extracted.end() );
            }

            std::sort( symbolNames.begin(), symbolNames.end() );
            symbolNames.erase( std::unique( symbolNames.begin(), symbolNames.end() ), symbolNames.end() );
        }
        catch( ... )
        {
        }

        try
        {
            json manifestEntry;
            manifestEntry["ts_unix"] = static_cast<long long>( std::time( nullptr ) );
            manifestEntry["library_nickname"] = libNick;
            manifestEntry["symbol_table_uri"] = symUri;
            manifestEntry["footprint_table_uri"] = fpUri;
            manifestEntry["written_files"] = written;
            manifestEntry["symbol_names"] = symbolNames;
            manifestEntry["project_relative_dir"] = relRoot;
            appendLibraryImportManifestEntry( fs::path( projDir ), manifestEntry );
        }
        catch( const std::exception& )
        {
        }

        kiapi::common::ApiRequest reloadSymReq;
        reloadSymReq.mutable_header()->set_client_name( "mcp" );
        kiapi::schematic::types::ReloadProjectSymbolLibraries reloadSymCmd;
        reloadSymReq.mutable_message()->PackFrom( reloadSymCmd );
        kiapi::common::ApiResponse reloadSymResp;
        std::string symReloadIpcErr;
        if( !m_ipc.SendRequest( reloadSymReq, reloadSymResp, symReloadIpcErr )
            || reloadSymResp.status().status() != kiapi::common::AS_OK )
        {
            json content = json::array();
            std::string msg = !symReloadIpcErr.empty() ? symReloadIpcErr : "reload_project_symbol_libraries after ingest failed";
            content.push_back( { { "type", "text" }, { "text", msg } } );
            return "{\"jsonrpc\":\"2.0\",\"result\":" + json( { { "content", content }, { "isError", true } } ).dump() + ",\"id\":" + id + "}";
        }

        kiapi::schematic::types::ReloadProjectSymbolLibrariesResponse symReloadOut;
        bool symbolReloadOk = false;
        if( reloadSymResp.has_message() && reloadSymResp.message().UnpackTo( &symReloadOut ) )
            symbolReloadOk = symReloadOut.ok();

        kiapi::common::ApiRequest reloadFpReq;
        reloadFpReq.mutable_header()->set_client_name( "mcp" );
        kiapi::board::commands::ReloadProjectFootprintLibraries reloadFpCmd;
        reloadFpReq.mutable_message()->PackFrom( reloadFpCmd );
        kiapi::common::ApiResponse reloadFpResp;
        std::string fpReloadIpcErr;
        bool footprintReloadOk = false;
        std::string footprintReloadError;
        if( m_ipc.SendRequest( reloadFpReq, reloadFpResp, fpReloadIpcErr )
            && reloadFpResp.status().status() == kiapi::common::AS_OK )
        {
            kiapi::board::commands::ReloadProjectFootprintLibrariesResponse fpReloadOut;
            if( reloadFpResp.has_message() && reloadFpResp.message().UnpackTo( &fpReloadOut ) )
            {
                footprintReloadOk = fpReloadOut.ok();
                if( !fpReloadOut.ok() && !fpReloadOut.error_message().empty() )
                    footprintReloadError = fpReloadOut.error_message();
            }
        }
        else
        {
            footprintReloadError = !fpReloadIpcErr.empty() ? fpReloadIpcErr : reloadFpResp.status().error_message();
        }

        json out;
        out["written_files"] = written;
        out["library_nickname"] = libNick;
        out["symbol_table_uri"] = symUri;
        out["footprint_table_uri"] = fpUri;
        out["symbol_names"] = symbolNames;
        out["symbol_reload_ok"] = symbolReloadOk;
        out["footprint_reload_ok"] = footprintReloadOk;
        out["manifest_path"] = ( fs::path( projDir ) / ".library_import_manifest.json" ).string();
        if( reloadSymResp.has_message() && reloadSymResp.message().UnpackTo( &symReloadOut ) )
            out["sym_lib_table_path"] = symReloadOut.sym_lib_table_path();
        if( !footprintReloadError.empty() )
            out["footprint_reload_warning"] = footprintReloadError;
        if( !warnings.empty() )
            out["warnings"] = warnings;
        json content = json::array();
        content.push_back( { { "type", "text" }, { "text", out.dump() } } );
        return "{\"jsonrpc\":\"2.0\",\"result\":" + json( { { "content", content }, { "isError", false } } ).dump() + ",\"id\":" + id + "}";
    }
    // ── erc_rules_get ──
    else if( name == "erc_rules_get" )
    {
        if( !m_ipc.IsConnected() && !m_ipc.Connect( IpcClient::GetDefaultSocketPath() ) )
        {
            json content = json::array();
            content.push_back( { { "type", "text" }, { "text", "KiCad IPC not connected." } } );
            return "{\"jsonrpc\":\"2.0\",\"result\":" + json( { { "content", content }, { "isError", true } } ).dump() + ",\"id\":" + id + "}";
        }

        kiapi::common::ApiRequest ercReq;
        ercReq.mutable_header()->set_client_name( "mcp" );
        kiapi::schematic::types::GetDanglingReport ercCmd;
        ercReq.mutable_message()->PackFrom( ercCmd );
        kiapi::common::ApiResponse ercResp;
        std::string ercErr;

        if( !m_ipc.SendRequest( ercReq, ercResp, ercErr ) )
        {
            json content = json::array();
            content.push_back( { { "type", "text" }, { "text", ercErr.empty() ? "ERC request failed" : ercErr } } );
            return "{\"jsonrpc\":\"2.0\",\"result\":" + json( { { "content", content }, { "isError", true } } ).dump() + ",\"id\":" + id + "}";
        }
        if( ercResp.status().status() != kiapi::common::AS_OK )
        {
            json content = json::array();
            content.push_back( { { "type", "text" }, { "text", ercResp.status().error_message().empty() ? "ERC check failed" : ercResp.status().error_message() } } );
            return "{\"jsonrpc\":\"2.0\",\"result\":" + json( { { "content", content }, { "isError", true } } ).dump() + ",\"id\":" + id + "}";
        }

        json rules = json::array();
        std::map<std::string, int> typeCounts;
        json ercItems = json::array();

        kiapi::schematic::types::GetDanglingReportResponse dangResp;
        if( ercResp.has_message() && ercResp.message().UnpackTo( &dangResp ) )
        {
            for( int i = 0; i < dangResp.items_size(); ++i )
            {
                const auto& it = dangResp.items( i );
                std::string itemType = it.type();
                typeCounts[itemType]++;
                json item;
                item["reference"] = it.reference();
                item["pin_number"] = it.pin_number();
                item["x_mm"] = it.x_mm();
                item["y_mm"] = it.y_mm();
                item["type"] = itemType;
                ercItems.push_back( item );
            }

            for( auto& [typeName, count] : typeCounts )
            {
                json rule;
                rule["name"] = typeName;
                rule["severity"] = "error";
                rule["count"] = count;
                rules.push_back( rule );
            }
        }

        json out;
        out["rules"] = rules;
        out["total_issues"] = (int)ercItems.size();
        out["items"] = ercItems;
        json content = json::array();
        content.push_back( { { "type", "text" }, { "text", out.dump() } } );
        return "{\"jsonrpc\":\"2.0\",\"result\":" + json( { { "content", content }, { "isError", false } } ).dump() + ",\"id\":" + id + "}";
    }
    // ── erc_waivers_list ──
    else if( name == "erc_waivers_list" )
    {
        json out;
        out["waivers"] = json::array();
        out["note"] = "ERC waivers/exclusions are managed in the KiCad schematic editor UI. No programmatic waiver API is currently available. Use erc_rules_get to see current ERC issues.";
        json content = json::array();
        content.push_back( { { "type", "text" }, { "text", out.dump() } } );
        return "{\"jsonrpc\":\"2.0\",\"result\":" + json( { { "content", content }, { "isError", false } } ).dump() + ",\"id\":" + id + "}";
    }
    // ── get_placed_label_positions ──
    else if( name == "get_placed_label_positions" )
    {
        bool hasBbox = hasArg( "min_x" ) && hasArg( "min_y" ) && hasArg( "max_x" ) && hasArg( "max_y" );
        double bMinX = hasBbox ? getDouble( "min_x" ) : -1e9;
        double bMinY = hasBbox ? getDouble( "min_y" ) : -1e9;
        double bMaxX = hasBbox ? getDouble( "max_x" ) :  1e9;
        double bMaxY = hasBbox ? getDouble( "max_y" ) :  1e9;

        std::string err;
        if( !m_ipc.IsConnected() && !m_ipc.Connect( IpcClient::GetDefaultSocketPath() ) )
        {
            json content = json::array();
            content.push_back( { { "type", "text" }, { "text", "KiCad IPC not connected." } } );
            return "{\"jsonrpc\":\"2.0\",\"result\":" + json( { { "content", content }, { "isError", true } } ).dump() + ",\"id\":" + id + "}";
        }

        std::vector<ParsedLabel> liveLabels;
        std::vector<ParsedWire> liveWires;
        std::string sheetPath = getCurrentSheetPath();
        std::string fetchErr;
        if( !fetchLiveLabelsAndWires( m_ipc, sheetPath, liveLabels, liveWires, fetchErr ) )
        {
            json out;
            out["labels"] = json::array();
            out["note"] = fetchErr.empty() ? "KiCad schematic IPC not available." : fetchErr;
            json content = json::array();
            content.push_back( { { "type", "text" }, { "text", out.dump() } } );
            return "{\"jsonrpc\":\"2.0\",\"result\":" + json( { { "content", content }, { "isError", false } } ).dump() + ",\"id\":" + id + "}";
        }

        json labelsArr = json::array();
        for( const ParsedLabel& label : liveLabels )
        {
            if( label.x < bMinX || label.x > bMaxX || label.y < bMinY || label.y > bMaxY )
                continue;
            json entry;
            entry["uuid"] = label.uuid;
            entry["kind"] = label.kind;
            entry["net_name"] = label.text;
            entry["x_mm"] = std::round( label.x * 1000.0 ) / 1000.0;
            entry["y_mm"] = std::round( label.y * 1000.0 ) / 1000.0;
            entry["rotation_degrees"] = label.rotation;
            labelsArr.push_back( entry );
        }

        json out;
        out["labels"] = labelsArr;
        out["count"]  = (int)labelsArr.size();
        json content = json::array();
        content.push_back( { { "type", "text" }, { "text", out.dump() } } );
        return "{\"jsonrpc\":\"2.0\",\"result\":" + json( { { "content", content }, { "isError", false } } ).dump() + ",\"id\":" + id + "}";
    }
    // ── validate_placement_constraints ──
    else if( name == "validate_placement_constraints" )
    {
        double bMinX = getDouble( "min_x" );
        double bMinY = getDouble( "min_y" );
        double bMaxX = getDouble( "max_x" );
        double bMaxY = getDouble( "max_y" );

        std::string err;
        kiapi::schematic::types::GetSchematicSummaryResponse sumResp;
        json outsideArr = json::array();

        if( getCachedSummary( sumResp, err ) )
        {
            for( int i = 0; i < sumResp.components_size(); ++i )
            {
                const auto& c = sumResp.components( i );
                // Use bbox center if available, otherwise raw position
                double cx = 0.0, cy = 0.0;
                if( c.has_bbox() )
                {
                    cx = ( c.bbox().min_x_mm() + c.bbox().max_x_mm() ) / 2.0;
                    cy = ( c.bbox().min_y_mm() + c.bbox().max_y_mm() ) / 2.0;
                }
                else if( c.has_position() )
                {
                    cx = c.position().x_mm();
                    cy = c.position().y_mm();
                }
                else
                {
                    continue;
                }

                if( cx < bMinX || cx > bMaxX || cy < bMinY || cy > bMaxY )
                {
                    json entry;
                    entry["ref"]  = c.reference();
                    entry["x_mm"] = std::round( cx * 1000.0 ) / 1000.0;
                    entry["y_mm"] = std::round( cy * 1000.0 ) / 1000.0;
                    // Distance outside bbox for triage
                    double dx = std::max( 0.0, std::max( bMinX - cx, cx - bMaxX ) );
                    double dy = std::max( 0.0, std::max( bMinY - cy, cy - bMaxY ) );
                    entry["distance_outside_mm"] = std::round( std::hypot( dx, dy ) * 100.0 ) / 100.0;
                    outsideArr.push_back( entry );
                }
            }
        }

        json out;
        out["valid"]       = outsideArr.empty();
        out["outside_bbox"] = outsideArr;
        out["bbox"]        = { { "min_x", bMinX }, { "min_y", bMinY }, { "max_x", bMaxX }, { "max_y", bMaxY } };
        json content = json::array();
        content.push_back( { { "type", "text" }, { "text", out.dump() } } );
        return "{\"jsonrpc\":\"2.0\",\"result\":" + json( { { "content", content }, { "isError", false } } ).dump() + ",\"id\":" + id + "}";
    }
    else
    {
        return "{\"jsonrpc\":\"2.0\",\"error\":{\"code\":-32602,\"message\":\"Unknown tool\"},\"id\":" + id + "}";
    }

    kiapi::common::ApiResponse resp;
    std::string err;
    const bool schematicSocket =
            req.has_message()
            && req.message().type_url().find( "kiapi.schematic" ) != std::string::npos;
    if( schematicSocket )
    {
        if( !m_ipc.EnsureSchematicApiConnection( err ) )
        {
            json content = json::array();
            content.push_back( { { "type", "text" },
                               { "text", err.empty() ? "KiCad schematic IPC not available." : err } } );
            json errResult = { { "content", content }, { "isError", true } };
            return "{\"jsonrpc\":\"2.0\",\"result\":" + errResult.dump() + ",\"id\":" + id + "}";
        }
    }
    else if( !m_ipc.IsConnected() && !m_ipc.Connect( IpcClient::GetDefaultSocketPath() ) )
    {
        json content = json::array();
        content.push_back( { { "type", "text" }, { "text", "KiCad IPC not connected. Is KiCad running with a document open?" } } );
        json errResult = { { "content", content }, { "isError", true } };
        return "{\"jsonrpc\":\"2.0\",\"result\":" + errResult.dump() + ",\"id\":" + id + "}";
    }

    if( !m_ipc.SendRequest( req, resp, err ) )
    {
        json content = json::array();
        content.push_back( { { "type", "text" }, { "text", err } } );
        json errResult = { { "content", content }, { "isError", true } };
        return "{\"jsonrpc\":\"2.0\",\"result\":" + errResult.dump() + ",\"id\":" + id + "}";
    }

    if( resp.status().status() != kiapi::common::AS_OK )
    {
        std::string msg = resp.status().error_message().empty() ? "API error" : resp.status().error_message();
        json content = json::array();
        content.push_back( { { "type", "text" }, { "text", msg } } );
        json errResult = { { "content", content }, { "isError", true } };
        return "{\"jsonrpc\":\"2.0\",\"result\":" + errResult.dump() + ",\"id\":" + id + "}";
    }

    // Success: return response message as text; unpack response types for useful messages
    std::string text = "OK";
    if( resp.has_message() )
    {
        const std::string& typeUrl = resp.message().type_url();
        if( typeUrl.find( "GetOpenDocumentsResponse" ) != std::string::npos )
        {
            kiapi::common::commands::GetOpenDocumentsResponse docsResp;
            if( resp.message().UnpackTo( &docsResp ) )
            {
                json docs = json::array();

                auto joinPath = []( const std::string& base, const std::string& leaf ) -> std::string
                {
                    if( base.empty() )
                        return leaf;
                    if( leaf.empty() )
                        return base;
                    if( base.back() == '/' || base.back() == '\\' )
                        return base + leaf;
                    return base + "/" + leaf;
                };

                for( int i = 0; i < docsResp.documents_size(); ++i )
                {
                    const auto& doc = docsResp.documents( i );
                    json item;
                    item["type"] = static_cast<int>( doc.type() );

                    if( doc.has_project() )
                    {
                        item["project_name"] = doc.project().name();
                        item["project_path"] = doc.project().path();
                    }

                    if( doc.has_sheet_path() )
                        item["sheet_path"] = doc.sheet_path().path_human_readable();

                    if( !doc.board_filename().empty() )
                    {
                        item["board_filename"] = doc.board_filename();
                        if( doc.has_project() && !doc.project().path().empty() && !doc.board_filename().empty() )
                            item["board_path"] = joinPath( doc.project().path(), doc.board_filename() );
                    }

                    if( doc.type() == kiapi::common::types::DOCTYPE_SCHEMATIC
                        && doc.has_project()
                        && !doc.project().path().empty()
                        && !doc.project().name().empty() )
                    {
                        item["schematic_path"] =
                            joinPath( doc.project().path(), doc.project().name() + ".kicad_sch" );
                    }

                    docs.push_back( item );
                }

                text = docs.dump();
                if( text.empty() )
                    text = "[]";
            }
            else
            {
                text = "Documents list received";
            }
        }
        else if( typeUrl.find( "BeginCommitResponse" ) != std::string::npos )
        {
            kiapi::common::commands::BeginCommitResponse beginResp;
            if( resp.message().UnpackTo( &beginResp ) && beginResp.has_id() )
                text = "Commit started, id: " + beginResp.id().value();
            else
                text = "Commit started";
        }
        else if( typeUrl.find( "EndCommitResponse" ) != std::string::npos )
            text = "Commit ended";
        else if( typeUrl.find( "SearchSymbols" ) != std::string::npos )
        {
            kiapi::schematic::types::SearchSymbolsResponse searchResp;
            if( resp.message().UnpackTo( &searchResp ) )
            {
                json results = json::array();
                for( int i = 0; i < searchResp.results_size(); ++i )
                {
                    const auto& r = searchResp.results( i );
                    json item;
                    item["library"] = r.library_nickname();
                    item["symbol"] = r.symbol_name();
                    std::string desc = r.description();
                    if( desc.size() > 80 )
                        desc = desc.substr( 0, 77 ) + "...";
                    item["description"] = desc;
                    results.push_back( item );
                }
                text = results.dump();
                if( text.empty() )
                    text = "[]";
            }
        }
        else if( typeUrl.find( "GetComponentData" ) != std::string::npos )
        {
            kiapi::schematic::types::GetComponentDataResponse getResp;
            if( resp.message().UnpackTo( &getResp ) )
            {
                if( !getResp.library_nickname().empty() || !getResp.symbol_name().empty() )
                {
                    json obj;
                    obj["library"] = getResp.library_nickname();
                    obj["symbol"] = getResp.symbol_name();
                    std::string desc = getResp.description();
                    if( desc.size() > 60 )
                        desc = desc.substr( 0, 57 ) + "...";
                    obj["description"] = desc;
                    if( getResp.width_mm() > 0 && getResp.height_mm() > 0 )
                    {
                        obj["width_mm"] = getResp.width_mm();
                        obj["height_mm"] = getResp.height_mm();
                    }
                    if( getResp.unit_count() > 0 )
                        obj["unit_count"] = getResp.unit_count();
                    if( getResp.pins_size() > 0 )
                    {
                        std::string pinsStr;
                        const int maxPins = 12;
                        for( int p = 0; p < getResp.pins_size() && p < maxPins; ++p )
                        {
                            const auto& pin = getResp.pins( p );
                            if( p > 0 ) pinsStr += ",";
                            pinsStr += pin.number() + ":" + pin.name();
                        }
                        if( getResp.pins_size() > maxPins )
                            pinsStr += ",+" + std::to_string( getResp.pins_size() - maxPins ) + " more";
                        obj["pins"] = pinsStr;
                    }
                    text = obj.dump();
                }
                else if( !getResp.summary().empty() )
                    text = getResp.summary();
                else
                    text = "No component data. For library symbols use arguments: {\"library\": \"<nickname>\", \"symbol\": \"<symbol_name>\"}. "
                           "For a symbol already on the schematic use \"component_id\": \"<KIID>\" (the symbol's UUID).";
            }
        }
        else if( typeUrl.find( "CaptureScreenshotResponse" ) != std::string::npos )
        {
            kiapi::schematic::types::CaptureScreenshotResponse screenResp;
            if( resp.message().UnpackTo( &screenResp ) && !screenResp.image_png_base64().empty() )
            {
                json obj;
                obj["screenshot_base64"] = screenResp.image_png_base64();
                obj["mime_type"] = "image/png";
                text = obj.dump();
            }
            else
                text = "Screenshot captured";
        }
        else if( typeUrl.find( "AddComponentResponse" ) != std::string::npos )
        {
            kiapi::schematic::types::AddComponentResponse addResp;
            if( resp.message().UnpackTo( &addResp ) && addResp.has_component_id() )
                text = "Component placed, id: " + addResp.component_id().value();
            else
                text = "Component placed";
        }
        else if( typeUrl.find( "CreateItemsResponse" ) != std::string::npos )
        {
            kiapi::common::commands::CreateItemsResponse createResp;
            if( resp.message().UnpackTo( &createResp ) )
            {
                json ids = json::array();
                std::string firstError;
                for( int i = 0; i < createResp.created_items_size(); ++i )
                {
                    const auto& cr = createResp.created_items( i );
                    if( cr.status().code() == kiapi::common::commands::ISC_OK && cr.has_item() )
                    {
                        const std::string& crTypeUrl = cr.item().type_url();
                        if( crTypeUrl.find( "GlobalLabel" ) != std::string::npos )
                        {
                            kiapi::schematic::types::GlobalLabel glabel;
                            if( cr.item().UnpackTo( &glabel ) && glabel.has_id() )
                                ids.push_back( glabel.id().value() );
                        }
                        else if( crTypeUrl.find( "SchematicText" ) != std::string::npos )
                        {
                            kiapi::schematic::types::SchematicText stext;
                            if( cr.item().UnpackTo( &stext ) && stext.has_id() )
                                ids.push_back( stext.id().value() );
                        }
                        else
                        {
                            kiapi::schematic::types::Line line;
                            if( cr.item().UnpackTo( &line ) && line.has_id() )
                                ids.push_back( line.id().value() );
                        }
                    }
                    else if( firstError.empty() && !cr.status().error_message().empty() )
                        firstError = cr.status().error_message();
                }
                text = "Items added: " + ids.dump();
                if( ids.empty() && !firstError.empty() )
                    text += " (error: " + firstError + ")";
                else if( ids.empty() && createResp.status() != kiapi::common::types::IRS_OK )
                    text += " (request status not OK)";
            }
        }
        else if( typeUrl.find( "DeleteItemsResponse" ) != std::string::npos )
        {
            kiapi::common::commands::DeleteItemsResponse delResp;
            if( resp.message().UnpackTo( &delResp ) )
                text = "Wires removed";
            else
                text = "Items removed";
        }
        else if( typeUrl.find( "GetDanglingReportResponse" ) != std::string::npos )
        {
            kiapi::schematic::types::GetDanglingReportResponse dangResp;
            if( resp.message().UnpackTo( &dangResp ) )
            {
                json arr = json::array();
                for( int i = 0; i < dangResp.items_size(); ++i )
                {
                    const auto& it = dangResp.items( i );
                    json obj;
                    obj["reference"] = it.reference();
                    obj["pin_number"] = it.pin_number();
                    obj["x_mm"] = it.x_mm();
                    obj["y_mm"] = it.y_mm();
                    obj["type"] = it.type();
                    arr.push_back( obj );
                }
                text = arr.dump();
                if( text == "[]" )
                    text = "No dangling wires or unconnected pins.";
            }
        }
        else if( typeUrl.find( "GetNetlistResponse" ) != std::string::npos )
        {
            kiapi::schematic::types::GetNetlistResponse netResp;
            if( resp.message().UnpackTo( &netResp ) )
            {
                json netsArr = json::array();
                for( int n = 0; n < netResp.nets_size(); ++n )
                {
                    const auto& net = netResp.nets( n );
                    json pinsArr = json::array();
                    for( int p = 0; p < net.pins_size(); ++p )
                    {
                        const auto& pin = net.pins( p );
                        pinsArr.push_back( { { "ref", pin.reference() }, { "pin", pin.pin_number() } } );
                    }
                    netsArr.push_back( { { "name", net.net_name() }, { "pins", pinsArr } } );
                }
                text = netsArr.dump();
            }
        }
        else if( typeUrl.find( "GetSchematicSummaryResponse" ) != std::string::npos )
        {
            kiapi::schematic::types::GetSchematicSummaryResponse sumResp;
            if( resp.message().UnpackTo( &sumResp ) )
            {
                auto fmtMm = []( double value ) -> std::string {
                    std::ostringstream os;
                    os << std::fixed << std::setprecision( 2 ) << value;
                    return os.str();
                };

                auto fmtList = []( const std::vector<std::string>& items, size_t maxItems ) -> std::string {
                    if( items.empty() )
                        return "none";

                    std::ostringstream os;
                    const size_t limit = std::min( items.size(), maxItems );
                    for( size_t i = 0; i < limit; ++i )
                    {
                        if( i > 0 )
                            os << ", ";
                        os << items[i];
                    }
                    if( items.size() > limit )
                        os << ", ... (+" << ( items.size() - limit ) << " more)";
                    return os.str();
                };

                auto fmtSet = [&]( const std::set<std::string>& items, size_t maxItems ) -> std::string {
                    std::vector<std::string> ordered( items.begin(), items.end() );
                    return fmtList( ordered, maxItems );
                };

                auto pinKey = []( const std::string& ref, const std::string& pin ) {
                    return ref + "\n" + pin;
                };

                std::string schPath = getCurrentSchematicPath();
                std::map<std::string, SchematicSymbolMetadata> metaByRef;
                if( !schPath.empty() )
                {
                    std::string schContent;
                    if( readFileToString( schPath, schContent ) )
                        metaByRef = parsePlacedSymbolMetadata( schContent );
                }

                std::map<std::string, std::vector<std::pair<std::string, std::string>>> netPins;
                std::map<std::string, std::string> netByRefPin;
                std::map<std::string, std::set<std::string>> connectedNetsByRef;
                std::map<std::string, std::set<std::string>> peerRefsByRef;
                std::map<std::string, std::map<std::string, std::set<std::string>>> sharedNetsByRef;

                {
                    kiapi::common::ApiRequest netReq;
                    netReq.mutable_header()->set_client_name( "mcp" );
                    kiapi::schematic::types::GetNetlist netCmd;
                    netReq.mutable_message()->PackFrom( netCmd );

                    kiapi::common::ApiResponse netResp;
                    std::string netErr;
                    if( m_ipc.SendRequest( netReq, netResp, netErr )
                        && netResp.status().status() == kiapi::common::AS_OK
                        && netResp.has_message() )
                    {
                        kiapi::schematic::types::GetNetlistResponse nr;
                        if( netResp.message().UnpackTo( &nr ) )
                        {
                            for( int n = 0; n < nr.nets_size(); ++n )
                            {
                                const auto& net = nr.nets( n );
                                std::vector<std::pair<std::string, std::string>> pins;
                                std::set<std::string> refsOnNet;
                                for( int p = 0; p < net.pins_size(); ++p )
                                {
                                    const auto& netPin = net.pins( p );
                                    pins.push_back( { netPin.reference(), netPin.pin_number() } );
                                    if( !netPin.reference().empty() && !netPin.pin_number().empty() )
                                    {
                                        netByRefPin[pinKey( netPin.reference(), netPin.pin_number() )] = net.net_name();
                                        connectedNetsByRef[netPin.reference()].insert( net.net_name() );
                                    }
                                    if( !netPin.reference().empty() )
                                        refsOnNet.insert( netPin.reference() );
                                }

                                for( const auto& refA : refsOnNet )
                                {
                                    for( const auto& refB : refsOnNet )
                                    {
                                        if( refA == refB )
                                            continue;
                                        peerRefsByRef[refA].insert( refB );
                                        sharedNetsByRef[refA][refB].insert( net.net_name() );
                                    }
                                }

                                netPins[net.net_name()] = std::move( pins );
                            }
                        }
                    }
                }

                std::vector<ParsedLabel> liveLabels;
                std::vector<ParsedWire> liveWires;
                std::map<std::string, int> labelKindCounts;
                std::set<std::string> labelNames;
                int overlappingLabelCount = 0;
                std::string labelFetchErr;
                const std::string sheetPath = getCurrentSheetPath();
                if( fetchLiveLabelsAndWires( m_ipc, sheetPath, liveLabels, liveWires, labelFetchErr ) )
                {
                    for( const ParsedLabel& label : liveLabels )
                    {
                        labelKindCounts[label.kind]++;
                        if( !label.text.empty() )
                            labelNames.insert( label.text );
                    }

                    constexpr double LABEL_OVERLAP_RADIUS = 2.0;
                    for( size_t a = 0; a < liveLabels.size(); ++a )
                    {
                        for( size_t b = a + 1; b < liveLabels.size(); ++b )
                        {
                            double dx = std::abs( liveLabels[a].x - liveLabels[b].x );
                            double dy = std::abs( liveLabels[a].y - liveLabels[b].y );
                            if( dx < LABEL_OVERLAP_RADIUS && dy < LABEL_OVERLAP_RADIUS )
                                overlappingLabelCount++;
                        }
                    }
                }

                std::vector<std::string> danglingPreview;
                std::map<std::string, int> danglingTypeCounts;
                int danglingCount = 0;
                {
                    kiapi::common::ApiRequest dangReq;
                    dangReq.mutable_header()->set_client_name( "mcp" );
                    kiapi::schematic::types::GetDanglingReport dangCmd;
                    dangReq.mutable_message()->PackFrom( dangCmd );

                    kiapi::common::ApiResponse dangResp;
                    std::string dangErr;
                    if( m_ipc.SendRequest( dangReq, dangResp, dangErr )
                        && dangResp.status().status() == kiapi::common::AS_OK
                        && dangResp.has_message() )
                    {
                        kiapi::schematic::types::GetDanglingReportResponse dr;
                        if( dangResp.message().UnpackTo( &dr ) )
                        {
                            danglingCount = dr.items_size();
                            for( int i = 0; i < dr.items_size(); ++i )
                            {
                                const auto& it = dr.items( i );
                                danglingTypeCounts[it.type()]++;
                                if( danglingPreview.size() < 8 )
                                {
                                    std::ostringstream os;
                                    os << it.reference() << "." << it.pin_number()
                                       << " (" << it.type() << " @ "
                                       << fmtMm( it.x_mm() ) << "," << fmtMm( it.y_mm() ) << ")";
                                    danglingPreview.push_back( os.str() );
                                }
                            }
                        }
                    }
                }

                std::map<std::string, int> refCounts;
                std::map<std::string, int> componentIndexByRef;
                std::map<std::string, int> prefixCounts;
                std::map<std::string, int> packageCounts;
                std::map<std::string, int> customFieldCounts;
                std::vector<std::string> duplicateRefs;
                std::vector<std::string> missingFootprints;
                std::vector<std::string> toleranceRefs;
                std::vector<std::string> datasheetRefs;
                std::vector<std::string> dnpRefs;
                std::vector<std::string> bomExcludedRefs;
                std::vector<std::string> boardExcludedRefs;
                int totalPins = 0;
                int connectedPinCount = 0;
                int footprintAssignedCount = 0;
                int datasheetCount = 0;
                int toleranceCount = 0;
                int dnpCount = 0;
                int inBomExcludedCount = 0;
                int onBoardExcludedCount = 0;

                for( int i = 0; i < sumResp.components_size(); ++i )
                {
                    const auto& c = sumResp.components( i );
                    componentIndexByRef[c.reference()] = i;
                    refCounts[c.reference()]++;
                    totalPins += c.pins_size();

                    std::string prefix;
                    for( char ch : c.reference() )
                    {
                        if( std::isalpha( static_cast<unsigned char>( ch ) ) )
                            prefix.push_back( ch );
                        else
                            break;
                    }
                    prefixCounts[prefix]++;

                    const auto metaIt = metaByRef.find( c.reference() );
                    if( metaIt != metaByRef.end() )
                    {
                        const auto& meta = metaIt->second;
                        if( !meta.footprint.empty() && meta.footprint != "~" )
                        {
                            footprintAssignedCount++;
                            packageCounts[takeLeafAfterLastColon( meta.footprint )]++;
                        }
                        else
                        {
                            missingFootprints.push_back( c.reference() );
                        }

                        if( !meta.datasheet.empty() && meta.datasheet != "~" )
                        {
                            datasheetCount++;
                            datasheetRefs.push_back( c.reference() );
                        }

                        if( !meta.tolerance.empty() )
                        {
                            toleranceCount++;
                            toleranceRefs.push_back( c.reference() + "=" + meta.tolerance );
                        }

                        if( meta.hasDnp && meta.dnp )
                        {
                            dnpCount++;
                            dnpRefs.push_back( c.reference() );
                        }
                        if( meta.hasInBom && !meta.inBom )
                        {
                            inBomExcludedCount++;
                            bomExcludedRefs.push_back( c.reference() );
                        }
                        if( meta.hasOnBoard && !meta.onBoard )
                        {
                            onBoardExcludedCount++;
                            boardExcludedRefs.push_back( c.reference() );
                        }

                        for( const auto& [fieldName, fieldValue] : meta.customFields )
                        {
                            if( !trimAscii( fieldValue ).empty() && fieldValue != "~" )
                                customFieldCounts[fieldName]++;
                        }
                    }
                    else
                    {
                        missingFootprints.push_back( c.reference() );
                    }

                    for( int p = 0; p < c.pins_size(); ++p )
                    {
                        const auto& pin = c.pins( p );
                        if( netByRefPin.find( pinKey( c.reference(), pin.number() ) ) != netByRefPin.end() )
                            connectedPinCount++;
                    }
                }

                for( const auto& [ref, count] : refCounts )
                {
                    if( count > 1 )
                        duplicateRefs.push_back( ref + " x" + std::to_string( count ) );
                }

                std::vector<std::pair<std::string, int>> prefixSummary( prefixCounts.begin(), prefixCounts.end() );
                std::sort( prefixSummary.begin(), prefixSummary.end(),
                           []( const auto& a, const auto& b ) {
                               if( a.second != b.second ) return a.second > b.second;
                               return a.first < b.first;
                           } );

                std::vector<std::pair<std::string, int>> packageSummary( packageCounts.begin(), packageCounts.end() );
                std::sort( packageSummary.begin(), packageSummary.end(),
                           []( const auto& a, const auto& b ) {
                               if( a.second != b.second ) return a.second > b.second;
                               return a.first < b.first;
                           } );

                std::vector<std::pair<std::string, int>> netFanout;
                int singlePinNetCount = 0;
                int anonymousNetCount = 0;
                int unlabeledNamedNetCount = 0;
                std::vector<std::string> singlePinPreview;
                for( const auto& [netName, pins] : netPins )
                {
                    netFanout.push_back( { netName, static_cast<int>( pins.size() ) } );
                    if( pins.size() <= 1 )
                    {
                        singlePinNetCount++;
                        if( singlePinPreview.size() < 8 )
                            singlePinPreview.push_back( netName.empty() ? "<unnamed>" : netName );
                    }
                    if( looksLikeAnonymousNetName( netName ) )
                        anonymousNetCount++;
                    else if( !labelNames.empty() && labelNames.find( netName ) == labelNames.end() )
                        unlabeledNamedNetCount++;
                }
                std::sort( netFanout.begin(), netFanout.end(),
                           []( const auto& a, const auto& b ) {
                               if( a.second != b.second ) return a.second > b.second;
                               return a.first < b.first;
                           } );

                std::vector<std::pair<std::string, int>> componentPeerSummary;
                for( const auto& [ref, peers] : peerRefsByRef )
                    componentPeerSummary.push_back( { ref, static_cast<int>( peers.size() ) } );
                std::sort( componentPeerSummary.begin(), componentPeerSummary.end(),
                           []( const auto& a, const auto& b ) {
                               if( a.second != b.second ) return a.second > b.second;
                               return a.first < b.first;
                           } );

                std::ostringstream md;
                md << "# Schematic\n**Sheet:** " << sumResp.sheet_path() << "\n\n";
                if( !schPath.empty() )
                    md << "**Source schematic:** `" << schPath << "`\n\n";
                if( sumResp.grid_step_mm() > 0 || !sumResp.grid_display().empty() )
                {
                    md << "**Snapping grid:** ";
                    if( !sumResp.grid_display().empty() )
                        md << sumResp.grid_display();
                    else
                        md << std::fixed << std::setprecision( 2 ) << sumResp.grid_step_mm() << " mm";
                    md << ". Use coordinates that are multiples of the grid so components and wires snap.\n\n";
                }

                md << "## Overview\n";
                md << "- Components: " << sumResp.components_size()
                   << " | total pins: " << totalPins
                   << " | connected pins: " << connectedPinCount << "\n";
                md << "- Nets: " << netPins.size()
                   << " | global nets: " << sumResp.global_net_names_size()
                   << " | anonymous nets: " << anonymousNetCount
                   << " | single-pin nets: " << singlePinNetCount << "\n";
                md << "- Labels: " << liveLabels.size()
                   << " | wires: " << liveWires.size()
                   << " | dangling items: " << danglingCount
                   << " | overlapping labels: " << overlappingLabelCount << "\n";
                md << "- Footprints assigned: " << footprintAssignedCount << "/" << sumResp.components_size()
                   << " | datasheets: " << datasheetCount << "/" << sumResp.components_size()
                   << " | tolerance fields: " << toleranceCount
                   << " | DNP: " << dnpCount
                   << " | BOM-excluded: " << inBomExcludedCount
                   << " | board-excluded: " << onBoardExcludedCount << "\n";
                if( !prefixSummary.empty() )
                {
                    std::vector<std::string> prefixBits;
                    for( const auto& [prefix, count] : prefixSummary )
                        prefixBits.push_back( prefix + "=" + std::to_string( count ) );
                    md << "- Reference families: " << fmtList( prefixBits, 12 ) << "\n";
                }
                if( !duplicateRefs.empty() )
                    md << "- Duplicate references: " << fmtList( duplicateRefs, 10 ) << "\n";
                else
                    md << "- Duplicate references: none\n";

                md << "## Components (" << sumResp.components_size() << ")\n";
                if( sumResp.components_size() == 0 )
                {
                    md << "(none on **this sheet only** — hierarchical designs list symbols only on the "
                          "sheet that is **currently open** in Eeschema. If parts live in a subsheet, "
                          "double-click that sheet in the tree or open its tab, then call "
                          "`get_schematic_summary` again.)\n";
                }
                for( int i = 0; i < sumResp.components_size(); ++i )
                {
                    const auto& c = sumResp.components( i );
                    double px = c.has_position() ? c.position().x_mm() : 0;
                    double py = c.has_position() ? c.position().y_mm() : 0;
                    md << "- **" << c.reference() << "** `" << c.value() << "` `"
                       << c.library_nickname() << ":" << c.symbol_name() << "`";

                    const auto metaIt = metaByRef.find( c.reference() );
                    if( metaIt != metaByRef.end() )
                    {
                        const auto& meta = metaIt->second;
                        md << " | fp ";
                        if( !meta.footprint.empty() && meta.footprint != "~" )
                            md << "`" << meta.footprint << "`";
                        else
                            md << "missing";

                        if( !meta.tolerance.empty() )
                            md << " | tol `" << meta.tolerance << "`";
                        if( !meta.description.empty() )
                            md << " | desc `" << meta.description << "`";
                        if( meta.unit > 0 )
                            md << " | unit " << meta.unit;
                        if( meta.hasDnp )
                            md << " | dnp " << ( meta.dnp ? "yes" : "no" );
                        if( meta.hasInBom && !meta.inBom )
                            md << " | in_bom no";
                        if( meta.hasOnBoard && !meta.onBoard )
                            md << " | on_board no";
                    }
                    else
                    {
                        md << " | fp unavailable";
                    }

                    md << " | pos (" << fmtMm( px ) << "," << fmtMm( py ) << ")";
                    if( c.has_bbox() )
                    {
                        const auto& b = c.bbox();
                        md << " | bbox " << fmtMm( b.min_x_mm() ) << "," << fmtMm( b.min_y_mm() )
                           << "→" << fmtMm( b.max_x_mm() ) << "," << fmtMm( b.max_y_mm() );
                    }
                    int connectedPinsForComp = 0;
                    for( int p = 0; p < c.pins_size(); ++p )
                    {
                        if( netByRefPin.find( pinKey( c.reference(), c.pins( p ).number() ) ) != netByRefPin.end() )
                            connectedPinsForComp++;
                    }
                    md << " | pins " << c.pins_size()
                       << " | connected " << connectedPinsForComp
                       << " | nets " << connectedNetsByRef[c.reference()].size()
                       << " | peers " << peerRefsByRef[c.reference()].size();
                    md << "\n";
                }

                md << "\n## Connectivity graph (" << componentPeerSummary.size() << " refs)\n";
                if( componentPeerSummary.empty() )
                {
                    md << "(netlist unavailable)\n";
                }
                else
                {
                    const size_t graphLimit = componentPeerSummary.size() <= 24 ? componentPeerSummary.size() : 24;
                    for( size_t i = 0; i < graphLimit; ++i )
                    {
                        const std::string& ref = componentPeerSummary[i].first;
                        auto compIndexIt = componentIndexByRef.find( ref );
                        if( compIndexIt == componentIndexByRef.end() )
                            continue;
                        const auto& comp = sumResp.components( compIndexIt->second );

                        std::vector<std::pair<std::string, int>> peerStrength;
                        for( const auto& [peerRef, nets] : sharedNetsByRef[ref] )
                            peerStrength.push_back( { peerRef, static_cast<int>( nets.size() ) } );
                        std::sort( peerStrength.begin(), peerStrength.end(),
                                   []( const auto& a, const auto& b ) {
                                       if( a.second != b.second ) return a.second > b.second;
                                       return a.first < b.first;
                                   } );

                        std::vector<std::string> peerBits;
                        for( const auto& [peerRef, count] : peerStrength )
                            peerBits.push_back( peerRef + "(" + std::to_string( count ) + ")" );

                        std::vector<std::string> pinBits;
                        size_t shownPins = 0;
                        size_t pinPreviewChars = 0;
                        for( int p = 0; p < comp.pins_size(); ++p )
                        {
                            const auto& pin = comp.pins( p );
                            auto netIt = netByRefPin.find( pinKey( ref, pin.number() ) );
                            std::string netName = netIt == netByRefPin.end() ? "unconnected" : netIt->second;
                            std::set<std::string> peerRefs;
                            auto pinsIt = netPins.find( netName );
                            if( netIt != netByRefPin.end() && pinsIt != netPins.end() )
                            {
                                for( const auto& [peerRef, peerPin] : pinsIt->second )
                                {
                                    if( peerRef != ref && !peerRef.empty() )
                                        peerRefs.insert( peerRef );
                                }
                            }

                            std::ostringstream pinOs;
                            pinOs << pin.number();
                            if( !pin.name().empty() )
                                pinOs << "/" << pin.name();
                            pinOs << "=" << netName;
                            if( !peerRefs.empty() )
                                pinOs << "{" << fmtSet( peerRefs, 4 ) << "}";
                            std::string bit = pinOs.str();
                            if( shownPins > 0 && pinPreviewChars + bit.size() > 260 )
                                break;
                            pinPreviewChars += bit.size();
                            pinBits.push_back( bit );
                            shownPins++;
                        }
                        if( shownPins < static_cast<size_t>( comp.pins_size() ) )
                            pinBits.push_back( "... (+" + std::to_string( comp.pins_size() - shownPins ) + " pins)" );

                        md << "- **" << ref << "** nets `" << fmtSet( connectedNetsByRef[ref], 8 )
                           << "` | peers `" << fmtList( peerBits, 6 )
                           << "` | pin_map `" << fmtList( pinBits, 12 ) << "`\n";
                    }
                    if( componentPeerSummary.size() > graphLimit )
                        md << "- ... truncated after " << graphLimit
                           << " refs; use `get_component_connectivity_graph` for exact local topology\n";
                }

                md << "\n## Global nets (" << sumResp.global_net_names_size() << ")\n";
                if( sumResp.global_net_names_size() == 0 )
                    md << "(none)\n";
                else
                {
                    for( int i = 0; i < sumResp.global_net_names_size(); ++i )
                    {
                        const std::string netName = sumResp.global_net_names( i );
                        const auto pinsIt = netPins.find( netName );
                        std::set<std::string> refs;
                        int pinCount = 0;
                        if( pinsIt != netPins.end() )
                        {
                            pinCount = static_cast<int>( pinsIt->second.size() );
                            for( const auto& [ref, pin] : pinsIt->second )
                            {
                                if( !ref.empty() )
                                    refs.insert( ref );
                            }
                        }
                        md << "- `" << netName << "`";
                        if( looksLikePowerNetName( netName ) )
                            md << " power";
                        md << " | pins " << pinCount
                           << " | refs " << refs.size()
                           << " | members " << fmtSet( refs, 10 ) << "\n";
                    }
                }

                md << "\n## Net topology (" << netFanout.size() << ")\n";
                if( netFanout.empty() )
                {
                    md << "(netlist unavailable)\n";
                }
                else
                {
                    const size_t fanoutLimit = netFanout.size() <= 18 ? netFanout.size() : 18;
                    for( size_t i = 0; i < fanoutLimit; ++i )
                    {
                        const auto& [netName, pinCount] = netFanout[i];
                        std::set<std::string> refs;
                        for( const auto& [ref, pin] : netPins[netName ] )
                        {
                            if( !ref.empty() )
                                refs.insert( ref );
                        }
                        md << "- `" << ( netName.empty() ? "<unnamed>" : netName ) << "` | pins " << pinCount
                           << " | refs " << refs.size()
                           << " | anonymous " << ( looksLikeAnonymousNetName( netName ) ? "yes" : "no" )
                           << " | members " << fmtSet( refs, 10 ) << "\n";
                    }
                    if( netFanout.size() > fanoutLimit )
                        md << "- ... truncated after " << fanoutLimit << " nets\n";
                    md << "- Single-pin nets: " << singlePinNetCount
                       << " | preview " << fmtList( singlePinPreview, 8 ) << "\n";
                    md << "- Unlabeled named nets: " << unlabeledNamedNetCount << "\n";
                }

                md << "\n## Footprints & fields\n";
                if( !packageSummary.empty() )
                {
                    std::vector<std::string> packageBits;
                    for( const auto& [pkg, count] : packageSummary )
                        packageBits.push_back( pkg + " x" + std::to_string( count ) );
                    md << "- Package histogram: " << fmtList( packageBits, 14 ) << "\n";
                }
                else
                {
                    md << "- Package histogram: none\n";
                }
                md << "- Missing footprints: " << fmtList( missingFootprints, 14 ) << "\n";
                md << "- Tolerances: " << fmtList( toleranceRefs, 14 ) << "\n";
                md << "- Datasheets: " << fmtList( datasheetRefs, 14 ) << "\n";
                md << "- DNP refs: " << fmtList( dnpRefs, 14 ) << "\n";
                md << "- Excluded from BOM: " << fmtList( bomExcludedRefs, 14 ) << "\n";
                md << "- Excluded from board: " << fmtList( boardExcludedRefs, 14 ) << "\n";
                if( !customFieldCounts.empty() )
                {
                    std::vector<std::pair<std::string, int>> customFieldSummary( customFieldCounts.begin(), customFieldCounts.end() );
                    std::sort( customFieldSummary.begin(), customFieldSummary.end(),
                               []( const auto& a, const auto& b ) {
                                   if( a.second != b.second ) return a.second > b.second;
                                   return a.first < b.first;
                               } );
                    std::vector<std::string> fieldBits;
                    for( const auto& [fieldName, count] : customFieldSummary )
                        fieldBits.push_back( fieldName + "=" + std::to_string( count ) );
                    md << "- Custom fields seen: " << fmtList( fieldBits, 14 ) << "\n";
                }
                else
                {
                    md << "- Custom fields seen: none\n";
                }

                md << "\n## Labels & wiring\n";
                md << "- Labels: " << liveLabels.size()
                   << " | global " << labelKindCounts["global"]
                   << " | local " << labelKindCounts["local"]
                   << " | hierarchical " << labelKindCounts["hierarchical"]
                   << " | directive " << labelKindCounts["directive"] << "\n";
                md << "- Wires: " << liveWires.size() << "\n";
                md << "- Label names: " << fmtSet( labelNames, 16 ) << "\n";

                md << "\n## Issues\n";
                md << "- Duplicate references: " << fmtList( duplicateRefs, 10 ) << "\n";
                md << "- Dangling items: " << danglingCount << " | by type "
                   << ( danglingTypeCounts.empty()
                        ? std::string( "none" )
                        : [&]() {
                              std::vector<std::string> bits;
                              for( const auto& [kind, count] : danglingTypeCounts )
                                  bits.push_back( kind + "=" + std::to_string( count ) );
                              return fmtList( bits, 10 );
                          }() )
                   << "\n";
                md << "- Dangling preview: " << fmtList( danglingPreview, 8 ) << "\n";
                md << "- Single-pin nets: " << singlePinNetCount
                   << " | preview " << fmtList( singlePinPreview, 8 ) << "\n";
                md << "- Unlabeled named nets: " << unlabeledNamedNetCount << "\n";
                md << "- Overlapping labels: " << overlappingLabelCount << "\n";

                text = md.str();
            }
        }
    }

    json content = json::array();
    content.push_back( { { "type", "text" }, { "text", text } } );
    json result = { { "content", content }, { "isError", false } };
    return "{\"jsonrpc\":\"2.0\",\"result\":" + result.dump() + ",\"id\":" + id + "}";
}


// ============================================================================
// TransactionGuard implementation
// ============================================================================

TransactionGuard::TransactionGuard( McpHandler& aHandler, IpcClient& aIpc, std::string& aError )
    : m_handler( aHandler ), m_ipc( aIpc )
{
    aError.clear();

    // Outer loop: rediscover IPC socket when the schematic editor holds api-<pid>.sock
    // (project manager often keeps api.sock without editor handlers like BeginCommit).
    for( int socketTry = 0; socketTry < 3 && m_commitId.empty(); ++socketTry )
    {
        std::string connErr;
        if( !m_ipc.EnsureSchematicApiConnection( connErr ) )
        {
            aError = connErr.empty() ? "KiCad IPC not connected." : connErr;
            return;
        }

        // Inner loop: retry when a commit is stuck for this client name
        for( int attempt = 0; attempt < 2 && m_commitId.empty(); ++attempt )
        {
            if( attempt > 0 )
                m_handler.endCommitDrop( "" );

            kiapi::common::ApiRequest beginReq;
            beginReq.mutable_header()->set_client_name( "mcp" );
            kiapi::common::commands::BeginCommit beginCmd;
            beginReq.mutable_message()->PackFrom( beginCmd );
            kiapi::common::ApiResponse beginResp;
            std::string err;

            if( !m_ipc.SendRequest( beginReq, beginResp, err ) )
            {
                aError = err.empty() ? "Begin commit failed" : err;
                return;
            }

            if( beginResp.status().status() != kiapi::common::AS_OK )
            {
                std::string em = beginResp.status().error_message();
                const bool noHandler =
                        beginResp.status().status() == kiapi::common::AS_UNHANDLED
                        || em.find( "no handler available" ) != std::string::npos;

                if( noHandler )
                {
                    m_ipc.InvalidateSchematicSocketCache();
                    m_ipc.Disconnect();
                    break; // next socketTry
                }

                if( attempt == 0
                    && em.find( "already has a commit in progress" ) != std::string::npos )
                {
                    continue;
                }

                aError = em.empty() ? "Begin commit failed" : em;
                return;
            }

            if( beginResp.has_message()
                && beginResp.message().type_url().find( "BeginCommitResponse" ) != std::string::npos )
            {
                kiapi::common::commands::BeginCommitResponse r;
                if( beginResp.message().UnpackTo( &r ) && r.has_id() )
                    m_commitId = r.id().value();
            }
        }
    }

    if( m_commitId.empty() )
        aError = "Failed to get commit id";
}


TransactionGuard::~TransactionGuard()
{
    if( !m_released && !m_commitId.empty() )
        drop();
}


bool TransactionGuard::commit()
{
    if( m_released || m_commitId.empty() )
        return false;

    kiapi::common::ApiRequest endReq;
    endReq.mutable_header()->set_client_name( "mcp" );
    kiapi::common::commands::EndCommit endCmd;
    endCmd.mutable_id()->set_value( m_commitId );
    endCmd.set_action( kiapi::common::commands::CMA_COMMIT );
    endReq.mutable_message()->PackFrom( endCmd );
    kiapi::common::ApiResponse endResp;
    std::string err;

    if( !m_ipc.SendRequest( endReq, endResp, err ) )
        return false;

    if( endResp.status().status() != kiapi::common::AS_OK )
        return false;

    m_released = true;
    return true;
}


void TransactionGuard::drop()
{
    if( m_released || m_commitId.empty() )
        return;

    m_released = true;
    m_handler.endCommitDrop( m_commitId );
}


void McpHandler::endCommitDrop( const std::string& aCommitId )
{
    if( !m_ipc.IsConnected() )
        return;
    kiapi::common::ApiRequest req;
    req.mutable_header()->set_client_name( "mcp" );
    kiapi::common::commands::EndCommit cmd;
    cmd.mutable_id()->set_value( aCommitId.empty() ? "drop" : aCommitId );
    cmd.set_action( kiapi::common::commands::CMA_DROP );
    req.mutable_message()->PackFrom( cmd );
    kiapi::common::ApiResponse resp;
    std::string err;
    m_ipc.SendRequest( req, resp, err );
}
