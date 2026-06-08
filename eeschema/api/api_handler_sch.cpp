/*
 * This program source code file is part of KiCad, a free EDA CAD application.
 *
 * Copyright (C) 2024 Jon Evans <jon@craftyjon.com>
 * Copyright The KiCad Developers, see AUTHORS.txt for contributors.
 *
 * This program is free software: you can redistribute it and/or modify it
 * under the terms of the GNU General Public License as published by the
 * Free Software Foundation, either version 3 of the License, or (at your
 * option) any later version.
 *
 * This program is distributed in the hope that it will be useful, but
 * WITHOUT ANY WARRANTY; without even the implied warranty of
 * MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the GNU
 * General Public License for more details.
 *
 * You should have received a copy of the GNU General Public License along
 * with this program.  If not, see <http://www.gnu.org/licenses/>.
 */

#include <algorithm>
#include <set>
#include <api/api_handler_sch.h>
#include <api/api_sch_utils.h>
#include <api/api_utils.h>
#include <api/api_enums.h>
#include <api/schematic/schematic_commands.pb.h>
#include <base_units.h>
#include <lib_id.h>
#include <lib_symbol.h>
#include <magic_enum.hpp>
#include <project_sch.h>
#include <connection_graph.h>
#include <sch_commit.h>
#include <sch_edit_frame.h>
#include <sch_screen.h>
#include <sch_label.h>
#include <sch_line.h>
#include <sch_pin.h>
#include <sch_symbol.h>
#include <symbol.h>
#include <wx/app.h>
#include <wx/tokenzr.h>
#include <symbol_lib_table.h>
#include <wx/filename.h>
#include <wx/image.h>
#include <wx/mstream.h>
#include <class_draw_panel_gal.h>
#include <eda_draw_frame.h>
#include <frame_type.h>
#include <gal/opengl/opengl_gal.h>
#include <math/vector2wx.h>
#include <view/view.h>
#include <base_units.h>
#include <eeschema_settings.h>
#include <settings/grid_settings.h>
#include <kiway.h>
#include <mail_type.h>
#include <sch_io/sch_io_mgr.h>

#include <api/common/types/base_types.pb.h>

using namespace kiapi::common::commands;
using kiapi::common::types::CommandStatus;
using kiapi::common::types::DocumentType;
using kiapi::common::types::ItemRequestStatus;
using kiapi::schematic::types::AddComponent;
using kiapi::schematic::types::AddComponentResponse;
using kiapi::schematic::types::GetComponentData;
using kiapi::schematic::types::GetComponentDataResponse;
using kiapi::schematic::types::GetDanglingReport;
using kiapi::schematic::types::GetDanglingReportResponse;
using kiapi::schematic::types::CaptureScreenshot;
using kiapi::schematic::types::CaptureScreenshotResponse;
using kiapi::schematic::types::CaptureZoneScreenshot;
using kiapi::schematic::types::CaptureFullSchematic;
using kiapi::schematic::types::GetVisibleBounds;
using kiapi::schematic::types::GetVisibleBoundsResponse;
using kiapi::schematic::types::GetPinPosition;
using kiapi::schematic::types::GetSchematicSummary;
using kiapi::schematic::types::GetSchematicSummaryResponse;
using kiapi::schematic::types::GetNetlist;
using kiapi::schematic::types::GetNetlistResponse;
using kiapi::schematic::types::NetEntry;
using kiapi::schematic::types::NetPinRef;
using kiapi::schematic::types::GetPinPositionResponse;
using kiapi::schematic::types::MoveComponent;
using kiapi::schematic::types::MoveComponentResponse;
using kiapi::schematic::types::DeleteComponent;
using kiapi::schematic::types::DeleteComponentResponse;
using kiapi::schematic::types::ReloadProjectSymbolLibraries;
using kiapi::schematic::types::ReloadProjectSymbolLibrariesResponse;
using kiapi::schematic::types::AppendProjectSymbolLibraryRow;
using kiapi::schematic::types::AppendProjectSymbolLibraryRowResponse;
using kiapi::schematic::types::SearchSymbols;
using kiapi::schematic::types::SearchSymbolsResponse;
using kiapi::schematic::types::SymbolSearchResult;


namespace
{
void broadcastSymbolLibraryReload( SCH_EDIT_FRAME* aFrame )
{
    wxCHECK( aFrame, /*void*/ );

    std::string payload;
    aFrame->Kiway().ExpressMail( FRAME_SCH, MAIL_RELOAD_LIB, payload );
    aFrame->Kiway().ExpressMail( FRAME_SCH_SYMBOL_EDITOR, MAIL_RELOAD_LIB, payload );
    aFrame->Kiway().ExpressMail( FRAME_SCH_VIEWER, MAIL_RELOAD_LIB, payload );
}
}


API_HANDLER_SCH::API_HANDLER_SCH( SCH_EDIT_FRAME* aFrame ) :
        API_HANDLER_EDITOR( aFrame ),
        m_frame( aFrame )
{
    registerHandler<GetItems, GetItemsResponse>( &API_HANDLER_SCH::handleGetItems );
    registerHandler<GetOpenDocuments, GetOpenDocumentsResponse>(
            &API_HANDLER_SCH::handleGetOpenDocuments );
    registerHandler<SearchSymbols, SearchSymbolsResponse>( &API_HANDLER_SCH::handleSearchSymbols );
    registerHandler<GetComponentData, GetComponentDataResponse>(
            &API_HANDLER_SCH::handleGetComponentData );
    registerHandler<AddComponent, AddComponentResponse>( &API_HANDLER_SCH::handleAddComponent );
    registerHandler<GetPinPosition, GetPinPositionResponse>( &API_HANDLER_SCH::handleGetPinPosition );
    registerHandler<GetDanglingReport, GetDanglingReportResponse>(
            &API_HANDLER_SCH::handleGetDanglingReport );
    registerHandler<GetSchematicSummary, GetSchematicSummaryResponse>(
            &API_HANDLER_SCH::handleGetSchematicSummary );
    registerHandler<GetNetlist, GetNetlistResponse>( &API_HANDLER_SCH::handleGetNetlist );
    registerHandler<CaptureScreenshot, CaptureScreenshotResponse>(
            &API_HANDLER_SCH::handleCaptureScreenshot );
    registerHandler<CaptureZoneScreenshot, CaptureScreenshotResponse>(
            &API_HANDLER_SCH::handleCaptureZoneScreenshot );
    registerHandler<CaptureFullSchematic, CaptureScreenshotResponse>(
            &API_HANDLER_SCH::handleCaptureFullSchematic );
    registerHandler<GetVisibleBounds, GetVisibleBoundsResponse>(
            &API_HANDLER_SCH::handleGetVisibleBounds );
    registerHandler<MoveComponent, MoveComponentResponse>( &API_HANDLER_SCH::handleMoveComponent );
    registerHandler<DeleteComponent, DeleteComponentResponse>(
            &API_HANDLER_SCH::handleDeleteComponent );
    registerHandler<ReloadProjectSymbolLibraries, ReloadProjectSymbolLibrariesResponse>(
            &API_HANDLER_SCH::handleReloadProjectSymbolLibraries );
    registerHandler<AppendProjectSymbolLibraryRow, AppendProjectSymbolLibraryRowResponse>(
            &API_HANDLER_SCH::handleAppendProjectSymbolLibraryRow );
}


std::unique_ptr<COMMIT> API_HANDLER_SCH::createCommit()
{
    return std::make_unique<SCH_COMMIT>( m_frame );
}


bool API_HANDLER_SCH::validateDocumentInternal( const DocumentSpecifier& aDocument ) const
{
    if( aDocument.type() != DocumentType::DOCTYPE_SCHEMATIC )
        return false;

    // TODO(JE) need serdes for SCH_SHEET_PATH <> SheetPath
    return true;

    //wxString currentPath = m_frame->GetCurrentSheet().PathAsString();
    //return 0 == aDocument.sheet_path().compare( currentPath.ToStdString() );
}


HANDLER_RESULT<GetItemsResponse> API_HANDLER_SCH::handleGetItems(
        const HANDLER_CONTEXT<GetItems>& aCtx )
{
    if( std::optional<ApiResponseStatus> busy = checkForBusy() )
        return tl::unexpected( *busy );

    if( !validateItemHeaderDocument( aCtx.Request.header() ) )
    {
        ApiResponseStatus e;
        e.set_status( ApiStatusCode::AS_UNHANDLED );
        return tl::unexpected( e );
    }

    GetItemsResponse response;
    SCH_SCREEN* screen = m_frame->GetScreen();
    std::set<KICAD_T> typesRequested;
    bool handledAnything = false;

    for( int typeRaw : aCtx.Request.types() )
    {
        auto typeMessage = static_cast<kiapi::common::types::KiCadObjectType>( typeRaw );
        KICAD_T type = FromProtoEnum<KICAD_T>( typeMessage );

        if( type == TYPE_NOT_INIT )
            continue;

        typesRequested.emplace( type );

        switch( type )
        {
        case SCH_LINE_T:
        case SCH_LABEL_T:
        case SCH_GLOBAL_LABEL_T:
        case SCH_HIER_LABEL_T:
        case SCH_DIRECTIVE_LABEL_T:
            handledAnything = true;
            break;
        default:
            break;
        }
    }

    if( !handledAnything )
    {
        ApiResponseStatus e;
        e.set_status( ApiStatusCode::AS_BAD_REQUEST );
        e.set_error_message( "none of the requested types are valid for a Schematic object" );
        return tl::unexpected( e );
    }

    for( SCH_ITEM* item : screen->Items() )
    {
        if( !typesRequested.count( item->Type() ) )
            continue;

        google::protobuf::Any itemBuf;
        item->Serialize( itemBuf );
        if( !itemBuf.type_url().empty() )
            response.mutable_items()->Add( std::move( itemBuf ) );
    }

    response.set_status( kiapi::common::types::ItemRequestStatus::IRS_OK );
    return response;
}


HANDLER_RESULT<GetOpenDocumentsResponse> API_HANDLER_SCH::handleGetOpenDocuments(
        const HANDLER_CONTEXT<GetOpenDocuments>& aCtx )
{
    if( aCtx.Request.type() != DocumentType::DOCTYPE_SCHEMATIC )
    {
        ApiResponseStatus e;

        // No message needed for AS_UNHANDLED; this is an internal flag for the API server
        e.set_status( ApiStatusCode::AS_UNHANDLED );
        return tl::unexpected( e );
    }

    GetOpenDocumentsResponse response;
    common::types::DocumentSpecifier doc;

    wxFileName fn( m_frame->GetCurrentFileName() );

    doc.set_type( DocumentType::DOCTYPE_SCHEMATIC );
    doc.set_board_filename( fn.GetFullName() );

    response.mutable_documents()->Add( std::move( doc ) );
    return response;
}


HANDLER_RESULT<std::unique_ptr<EDA_ITEM>> API_HANDLER_SCH::createItemForType( KICAD_T aType,
        EDA_ITEM* aContainer )
{
    // Only require a container for item types that must be inside another item
    const bool needsContainer = ( aType == SCH_PIN_T || aType == SCH_SHEET_PIN_T
                                  || aType == SCH_FIELD_T );
    if( needsContainer && !aContainer )
    {
        ApiResponseStatus e;
        e.set_status( ApiStatusCode::AS_BAD_REQUEST );
        e.set_error_message( "Tried to create an item in a null container" );
        return tl::unexpected( e );
    }

    if( aType == SCH_PIN_T && aContainer && !dynamic_cast<SCH_SYMBOL*>( aContainer ) )
    {
        ApiResponseStatus e;
        e.set_status( ApiStatusCode::AS_BAD_REQUEST );
        e.set_error_message( fmt::format( "Tried to create a pin in {}, which is not a symbol",
                                          aContainer->GetFriendlyName().ToStdString() ) );
        return tl::unexpected( e );
    }
    else if( aType == SCH_SYMBOL_T && !dynamic_cast<SCHEMATIC*>( aContainer ) )
    {
        ApiResponseStatus e;
        e.set_status( ApiStatusCode::AS_BAD_REQUEST );
        e.set_error_message( fmt::format( "Tried to create a symbol in {}, which is not a "
                                          "schematic",
                                          aContainer->GetFriendlyName().ToStdString() ) );
        return tl::unexpected( e );
    }

    std::unique_ptr<EDA_ITEM> created = CreateItemForType( aType, aContainer );

    if( !created )
    {
        ApiResponseStatus e;
        e.set_status( ApiStatusCode::AS_BAD_REQUEST );
        e.set_error_message( fmt::format( "Tried to create an item of type {}, which is unhandled",
                                          magic_enum::enum_name( aType ) ) );
        return tl::unexpected( e );
    }

    return created;
}


HANDLER_RESULT<ItemRequestStatus> API_HANDLER_SCH::handleCreateUpdateItemsInternal( bool aCreate,
        const std::string& aClientName,
        const types::ItemHeader &aHeader,
        const google::protobuf::RepeatedPtrField<google::protobuf::Any>& aItems,
        std::function<void( ItemStatus, google::protobuf::Any )> aItemHandler )
{
    ApiResponseStatus e;

    auto containerResult = validateItemHeaderDocument( aHeader );

    if( !containerResult && containerResult.error().status() == ApiStatusCode::AS_UNHANDLED )
    {
        // No message needed for AS_UNHANDLED; this is an internal flag for the API server
        e.set_status( ApiStatusCode::AS_UNHANDLED );
        return tl::unexpected( e );
    }
    else if( !containerResult )
    {
        e.CopyFrom( containerResult.error() );
        return tl::unexpected( e );
    }

    SCH_SCREEN* screen = m_frame->GetScreen();
    EE_RTREE& screenItems = screen->Items();

    std::map<KIID, EDA_ITEM*> itemUuidMap;

    std::for_each( screenItems.begin(), screenItems.end(),
                   [&]( EDA_ITEM* aItem )
                   {
                       itemUuidMap[aItem->m_Uuid] = aItem;
                   } );

    EDA_ITEM* container = nullptr;

    if( containerResult->has_value() )
    {
        const KIID& containerId = **containerResult;

        if( itemUuidMap.count( containerId ) )
        {
            container = itemUuidMap.at( containerId );

            if( !container )
            {
                e.set_status( ApiStatusCode::AS_BAD_REQUEST );
                e.set_error_message( fmt::format(
                        "The requested container {} is not a valid schematic item container",
                        containerId.AsStdString() ) );
                return tl::unexpected( e );
            }
        }
        else
        {
            e.set_status( ApiStatusCode::AS_BAD_REQUEST );
            e.set_error_message( fmt::format(
                    "The requested container {} does not exist in this document",
                    containerId.AsStdString() ) );
            return tl::unexpected( e );
        }
    }

    COMMIT* commit = getCurrentCommit( aClientName );

    for( const google::protobuf::Any& anyItem : aItems )
    {
        ItemStatus status;
        std::optional<KICAD_T> type = TypeNameFromAny( anyItem );

        if( !type )
        {
            status.set_code( ItemStatusCode::ISC_INVALID_TYPE );
            status.set_error_message( fmt::format( "Could not decode a valid type from {}",
                                                   anyItem.type_url() ) );
            aItemHandler( status, anyItem );
            continue;
        }

        HANDLER_RESULT<std::unique_ptr<EDA_ITEM>> creationResult =
                createItemForType( *type, container );

        if( !creationResult )
        {
            status.set_code( ItemStatusCode::ISC_INVALID_TYPE );
            status.set_error_message( creationResult.error().error_message() );
            aItemHandler( status, anyItem );
            continue;
        }

        std::unique_ptr<EDA_ITEM> item( std::move( *creationResult ) );

        if( !item->Deserialize( anyItem ) )
        {
            e.set_status( ApiStatusCode::AS_BAD_REQUEST );
            e.set_error_message( fmt::format( "could not unpack {} from request",
                                              item->GetClass().ToStdString() ) );
            return tl::unexpected( e );
        }

        if( aCreate && itemUuidMap.count( item->m_Uuid ) )
        {
            status.set_code( ItemStatusCode::ISC_EXISTING );
            status.set_error_message( fmt::format( "an item with UUID {} already exists",
                                                   item->m_Uuid.AsStdString() ) );
            aItemHandler( status, anyItem );
            continue;
        }
        else if( !aCreate && !itemUuidMap.count( item->m_Uuid ) )
        {
            status.set_code( ItemStatusCode::ISC_NONEXISTENT );
            status.set_error_message( fmt::format( "an item with UUID {} does not exist",
                                                   item->m_Uuid.AsStdString() ) );
            aItemHandler( status, anyItem );
            continue;
        }

        status.set_code( ItemStatusCode::ISC_OK );
        google::protobuf::Any newItem;

        if( aCreate )
        {
            item->Serialize( newItem );
            commit->Add( item.release(), screen );

            if( !m_activeClients.count( aClientName ) )
                pushCurrentCommit( aClientName, _( "Added items via API" ) );
        }
        else
        {
            EDA_ITEM* edaItem = itemUuidMap[item->m_Uuid];

            if( SCH_ITEM* schItem = dynamic_cast<SCH_ITEM*>( edaItem ) )
            {
                schItem->SwapData( static_cast<SCH_ITEM*>( item.get() ) );
                schItem->Serialize( newItem );
                commit->Modify( schItem, screen );
            }
            else
            {
                wxASSERT( false );
            }

            if( !m_activeClients.count( aClientName ) )
                pushCurrentCommit( aClientName, _( "Created items via API" ) );
        }

        aItemHandler( status, newItem );
    }


    return ItemRequestStatus::IRS_OK;
}


void API_HANDLER_SCH::deleteItemsInternal( std::map<KIID, ItemDeletionStatus>& aItemsToDelete,
                                           const std::string& aClientName )
{
    SCH_SCREEN* screen = m_frame->GetScreen();
    if( !screen )
        return;

    std::vector<SCH_ITEM*> toRemove;
    for( std::pair<const KIID, ItemDeletionStatus>& pair : aItemsToDelete )
    {
        for( EDA_ITEM* item : screen->Items() )
        {
            if( item->m_Uuid == pair.first )
            {
                toRemove.push_back( static_cast<SCH_ITEM*>( item ) );
                pair.second = ItemDeletionStatus::IDS_OK;
                break;
            }
        }
    }

    COMMIT* commit = getCurrentCommit( aClientName );
    for( SCH_ITEM* item : toRemove )
        commit->Remove( item );

    if( !m_activeClients.count( aClientName ) )
        pushCurrentCommit( aClientName, _( "Deleted items via API" ) );
}


std::optional<EDA_ITEM*> API_HANDLER_SCH::getItemFromDocument( const DocumentSpecifier& aDocument,
                                                               const KIID& aId )
{
    if( !validateDocument( aDocument ) )
        return std::nullopt;

    SCH_SCREEN* screen = m_frame->GetScreen();
    if( !screen )
        return std::nullopt;

    for( EDA_ITEM* item : screen->Items() )
    {
        if( item->m_Uuid == aId )
            return item;
    }
    return std::nullopt;
}


HANDLER_RESULT<SearchSymbolsResponse> API_HANDLER_SCH::handleSearchSymbols(
        const HANDLER_CONTEXT<SearchSymbols>& aCtx )
{
    SearchSymbolsResponse response;

    SYMBOL_LIB_TABLE* libTable = PROJECT_SCH::SchSymbolLibTable( &m_frame->Prj() );
    if( !libTable )
        return response;

    wxString query = wxString( aCtx.Request.query().c_str(), wxConvUTF8 ).Trim();
    wxString targetLib = wxString( aCtx.Request.library().c_str(), wxConvUTF8 );
    int limit = aCtx.Request.limit() > 0 ? aCtx.Request.limit() : 100;

    // Ripgrep-style: split query into tokens (space, comma, etc.); match if ANY token matches
    std::vector<wxString> queryTokens;
    if( !query.IsEmpty() )
    {
        wxStringTokenizer tokenizer( query, wxS( " ,;\t\n" ), wxTOKEN_STRTOK );
        while( tokenizer.HasMoreTokens() )
        {
            wxString t = tokenizer.GetNextToken().Lower();
            if( !t.IsEmpty() )
                queryTokens.push_back( t );
        }
    }

    std::vector<wxString> libs;
    if( !targetLib.IsEmpty() && libTable->HasLibrary( targetLib, true ) )
        libs.push_back( targetLib );
    else if( targetLib.IsEmpty() )
        libs = libTable->GetLogicalLibs();

    for( const wxString& libNickname : libs )
    {
        if( (int) response.results_size() >= limit )
            break;

        wxArrayString aliasNames;
        try
        {
            libTable->EnumerateSymbolLib( libNickname, aliasNames, false );
        }
        catch( const IO_ERROR& )
        {
            continue;
        }

        for( size_t i = 0; i < aliasNames.GetCount() && (int) response.results_size() < limit; i++ )
        {
            wxString name = aliasNames[i];

            LIB_SYMBOL* symbol = nullptr;
            try
            {
                symbol = libTable->LoadSymbol( libNickname, name );
            }
            catch( const IO_ERROR& )
            {
                continue;
            }

            if( !symbol )
                continue;

            // Ripgrep-style: if query has tokens, match if ANY token is in name, description, or keywords
            if( !queryTokens.empty() )
            {
                wxString nameLower = name.Lower();
                wxString descLower = symbol->GetDescription().Lower();
                wxString kwLower = symbol->GetKeyWords().Lower();
                bool anyMatch = false;
                for( const wxString& tok : queryTokens )
                {
                    if( nameLower.Contains( tok ) || descLower.Contains( tok ) || kwLower.Contains( tok ) )
                    {
                        anyMatch = true;
                        break;
                    }
                }
                if( !anyMatch )
                    continue;
            }

            SymbolSearchResult* result = response.add_results();
            result->set_library_nickname( libNickname.ToStdString() );
            result->set_symbol_name( name.ToStdString() );
            result->set_description( symbol->GetDescription().ToStdString() );
            result->set_keywords( symbol->GetKeyWords().ToStdString() );
            result->set_datasheet( symbol->GetDatasheetField().GetText().ToStdString() );
        }
    }

    return response;
}


HANDLER_RESULT<GetComponentDataResponse> API_HANDLER_SCH::handleGetComponentData(
        const HANDLER_CONTEXT<GetComponentData>& aCtx )
{
    GetComponentDataResponse response;

    SYMBOL_LIB_TABLE* libTable = PROJECT_SCH::SchSymbolLibTable( &m_frame->Prj() );
    if( !libTable )
        return response;

    if( aCtx.Request.has_lib_id() )
    {
        wxString libNickname( aCtx.Request.lib_id().library_nickname().c_str(), wxConvUTF8 );
        wxString entryName( aCtx.Request.lib_id().entry_name().c_str(), wxConvUTF8 );
        LIB_SYMBOL* symbol = nullptr;
        try
        {
            symbol = libTable->LoadSymbol( libNickname, entryName );
        }
        catch( const IO_ERROR& )
        {
            ApiResponseStatus err;
            err.set_status( ApiStatusCode::AS_BAD_REQUEST );
            err.set_error_message( "Library symbol not found" );
            return tl::unexpected( err );
        }
        response.set_library_nickname( libNickname.ToStdString() );
        response.set_symbol_name( entryName.ToStdString() );
        if( symbol )
        {
            response.set_description( symbol->GetDescription().ToStdString() );
            response.set_keywords( symbol->GetKeyWords().ToStdString() );
            response.set_datasheet( symbol->GetDatasheetField().GetText().ToStdString() );
            response.set_summary( "Library symbol: " + libNickname.ToStdString() + ":"
                                 + entryName.ToStdString() + " - "
                                 + symbol->GetDescription().ToStdString() );
            response.set_unit_count( symbol->GetUnitCount() > 0 ? symbol->GetUnitCount() : 1 );
            BOX2I bbox = symbol->GetUnitBoundingBox( 0, 0 );
            double wMm = std::max<long long>( 0LL, static_cast<long long>( bbox.GetWidth() ) ) / SCH_IU_PER_MM;
            double hMm = std::max<long long>( 0LL, static_cast<long long>( bbox.GetHeight() ) ) / SCH_IU_PER_MM;
            if( wMm > 0 && hMm > 0 )
            {
                response.set_width_mm( wMm );
                response.set_height_mm( hMm );
            }
            // Predefined sizes for Device passives (override empty bbox or enforce consistency)
            std::string lib( libNickname.ToStdString() );
            std::string symName( entryName.ToStdString() );
            if( lib == "Device" )
            {
                if( symName == "R" ) { response.set_width_mm( 5.0 ); response.set_height_mm( 2.0 ); }
                else if( symName == "C" ) { response.set_width_mm( 4.0 ); response.set_height_mm( 2.0 ); }
                else if( symName == "L" ) { response.set_width_mm( 4.0 ); response.set_height_mm( 2.0 ); }
                else if( symName == "LED" ) { response.set_width_mm( 4.0 ); response.set_height_mm( 2.0 ); }
                else if( symName == "Ferrite_Bead" ) { response.set_width_mm( 4.0 ); response.set_height_mm( 2.0 ); }
            }
            // Pin list for library symbols (unit 0, body 0) so the agent can wire by pin number/name
            for( SCH_PIN* pin : symbol->GetPins( 0, 0 ) )
            {
                auto* ps = response.add_pins();
                ps->set_number( pin->GetNumber().ToStdString() );
                ps->set_name( pin->GetName().ToStdString() );
            }
        }
        else
        {
            response.set_summary( "Library symbol " + libNickname.ToStdString() + ":"
                                 + entryName.ToStdString() + " not found" );
        }
    }
    else if( aCtx.Request.has_component_id() && !aCtx.Request.component_id().value().empty() )
    {
        KIID kiid( aCtx.Request.component_id().value() );
        SCH_SCREEN* screen = m_frame->GetScreen();
        if( screen )
        {
            for( auto it = screen->Items().begin(); it != screen->Items().end(); ++it )
            {
                SCH_ITEM* item = *it;
                if( item->m_Uuid == kiid && SCH_SYMBOL::ClassOf( item ) )
                {
                    SCH_SYMBOL* sym = static_cast<SCH_SYMBOL*>( item );
                    std::string summary = "Schematic symbol: "
                                         + sym->GetRef( &m_frame->GetCurrentSheet() ).ToStdString()
                                         + " "
                                         + sym->GetValue( false, &m_frame->GetCurrentSheet(), false )
                                                   .ToStdString()
                                         + " "
                                         + sym->GetLibId().GetUniStringLibId().ToStdString();
                    response.set_summary( summary );
                    response.set_unit_count( sym->GetUnitCount() > 0 ? sym->GetUnitCount() : 1 );
                    BOX2I bbox = sym->GetBodyBoundingBox();
                    double wMm = std::max<long long>( 0LL, static_cast<long long>( bbox.GetWidth() ) ) / SCH_IU_PER_MM;
                    double hMm = std::max<long long>( 0LL, static_cast<long long>( bbox.GetHeight() ) ) / SCH_IU_PER_MM;
                    if( wMm > 0 && hMm > 0 )
                    {
                        response.set_width_mm( wMm );
                        response.set_height_mm( hMm );
                    }
                    const SCH_SHEET_PATH& sheet = m_frame->GetCurrentSheet();
                    for( SCH_PIN* pin : sym->GetPins( &sheet ) )
                    {
                        auto* ps = response.add_pins();
                        ps->set_number( pin->GetNumber().ToStdString() );
                        ps->set_name( pin->GetName().ToStdString() );
                    }
                    break;
                }
            }
        }
    }

    return response;
}


HANDLER_RESULT<AddComponentResponse> API_HANDLER_SCH::handleAddComponent(
        const HANDLER_CONTEXT<AddComponent>& aCtx )
{
    if( std::optional<ApiResponseStatus> busy = checkForBusy() )
        return tl::unexpected( *busy );

    const AddComponent& req = aCtx.Request;
    std::string commitIdStr = req.commit_id().value();

    COMMIT* commit = nullptr;
    for( auto& it : m_commits )
    {
        if( it.second.first.AsStdString() == commitIdStr )
        {
            commit = it.second.second.get();
            break;
        }
    }

    if( !commit )
    {
        ApiResponseStatus err;
        err.set_status( ApiStatusCode::AS_BAD_REQUEST );
        err.set_error_message( "Invalid or expired commit ID" );
        return tl::unexpected( err );
    }

    LIB_ID libId( wxString( req.library_nickname().c_str(), wxConvUTF8 ),
                  wxString( req.symbol_name().c_str(), wxConvUTF8 ) );
    SYMBOL_LIB_TABLE* libTable = PROJECT_SCH::SchSymbolLibTable( &m_frame->Prj() );
    if( !libTable )
    {
        ApiResponseStatus err;
        err.set_status( ApiStatusCode::AS_BAD_REQUEST );
        err.set_error_message( "No symbol library table" );
        return tl::unexpected( err );
    }

    LIB_SYMBOL* libSymbol = libTable->LoadSymbol( libId );
    if( !libSymbol )
    {
        ApiResponseStatus err;
        err.set_status( ApiStatusCode::AS_BAD_REQUEST );
        err.set_error_message( "Symbol not found in library" );
        return tl::unexpected( err );
    }

    double xMm = req.has_position() ? req.position().x_mm() : 0.0;
    double yMm = req.has_position() ? req.position().y_mm() : 0.0;
    VECTOR2I posIU( KiROUND( xMm * SCH_IU_PER_MM ), KiROUND( yMm * SCH_IU_PER_MM ) );

    const SCH_SHEET_PATH& currentSheet = m_frame->GetCurrentSheet();
    SCH_SYMBOL* symbol = new SCH_SYMBOL( *libSymbol, libId, &currentSheet, 0, 0, posIU, nullptr );

    symbol->SetRef( &currentSheet, wxString( req.reference().c_str(), wxConvUTF8 ) );
    symbol->SetValueFieldText( wxString( req.value().c_str(), wxConvUTF8 ) );

    double rotDeg = req.rotation();
    int orient = SYM_ORIENT_0;
    if( rotDeg >= 45 && rotDeg < 135 )
        orient = SYM_ORIENT_90;
    else if( rotDeg >= 135 && rotDeg < 225 )
        orient = SYM_ORIENT_180;
    else if( rotDeg >= 225 && rotDeg < 315 )
        orient = SYM_ORIENT_270;
    symbol->SetOrientation( orient );

    SCH_SCREEN* screen = m_frame->GetScreen();
    if( !screen )
    {
        delete symbol;
        ApiResponseStatus err;
        err.set_status( ApiStatusCode::AS_BAD_REQUEST );
        err.set_error_message( "No schematic screen" );
        return tl::unexpected( err );
    }

    commit->Add( symbol, screen );

    AddComponentResponse resp;
    resp.mutable_component_id()->set_value( symbol->m_Uuid.AsStdString() );
    return resp;
}


HANDLER_RESULT<GetPinPositionResponse> API_HANDLER_SCH::handleGetPinPosition(
        const HANDLER_CONTEXT<GetPinPosition>& aCtx )
{
    if( std::optional<ApiResponseStatus> busy = checkForBusy() )
        return tl::unexpected( *busy );

    const GetPinPosition& req = aCtx.Request;
    if( req.reference().empty() || req.pin_number().empty() )
    {
        ApiResponseStatus err;
        err.set_status( ApiStatusCode::AS_BAD_REQUEST );
        err.set_error_message( "reference and pin_number are required" );
        return tl::unexpected( err );
    }

    SCH_SCREEN* screen = m_frame->GetScreen();
    if( !screen )
    {
        ApiResponseStatus err;
        err.set_status( ApiStatusCode::AS_BAD_REQUEST );
        err.set_error_message( "No schematic open" );
        return tl::unexpected( err );
    }

    const SCH_SHEET_PATH& sheet = m_frame->GetCurrentSheet();
    wxString refReq( req.reference().c_str(), wxConvUTF8 );
    wxString pinNumReq( req.pin_number().c_str(), wxConvUTF8 );

    SCH_SYMBOL* symbol = nullptr;
    for( SCH_ITEM* item : screen->Items() )
    {
        if( item->Type() != SCH_SYMBOL_T )
            continue;
        SCH_SYMBOL* sym = static_cast<SCH_SYMBOL*>( item );
        if( sym->GetRef( &sheet ) == refReq )
        {
            symbol = sym;
            break;
        }
    }

    // If not on screen, look in pending commits (e.g. symbol just placed in same commit)
    if( !symbol )
    {
        for( auto& it : m_commits )
        {
            std::vector<EDA_ITEM*> staged = it.second.second->GetStagedAdds();
            for( EDA_ITEM* item : staged )
            {
                if( item->Type() != SCH_SYMBOL_T )
                    continue;
                SCH_SYMBOL* sym = static_cast<SCH_SYMBOL*>( item );
                if( sym->GetRef( &sheet ) == refReq )
                {
                    symbol = sym;
                    // Ensure pins are resolved from library (m_position, m_libPin) so GetPosition/GetPinRoot are per-pin.
                    symbol->UpdatePins();
                    break;
                }
            }
            if( symbol )
                break;
        }
    }

    if( !symbol )
    {
        ApiResponseStatus err;
        err.set_status( ApiStatusCode::AS_BAD_REQUEST );
        err.set_error_message( "Symbol not found: " + req.reference() );
        return tl::unexpected( err );
    }

    SCH_PIN* pin = symbol->GetPin( pinNumReq );
    if( !pin )
    {
        ApiResponseStatus err;
        err.set_status( ApiStatusCode::AS_BAD_REQUEST );
        err.set_error_message( "Pin not found: " + req.reference() + " pin " + req.pin_number() );
        return tl::unexpected( err );
    }

    // Prefer library pin positions (source of truth) so pin positions are correct even when
    // the symbol is in a pending commit and schematic pins are not fully resolved.
    VECTOR2I posIU;
    VECTOR2I tipIU;
    if( symbol->GetLibSymbolRef() )
    {
        LIB_SYMBOL* libPart = symbol->GetLibSymbolRef().get();
        SCH_PIN*   libPin = libPart->GetPin( pinNumReq, symbol->GetUnit(), symbol->GetBodyStyle() );
        if( libPin )
        {
            const TRANSFORM& t = symbol->GetTransform();
            VECTOR2I          symPos = symbol->GetPosition();
            posIU = t.TransformCoordinate( libPin->GetPosition() ) + symPos;
            tipIU = t.TransformCoordinate( libPin->GetPinRoot() ) + symPos;
        }
        else
        {
            posIU = pin->GetPosition();
            tipIU = pin->GetPinRoot();
        }
    }
    else
    {
        posIU = pin->GetPosition();
        tipIU = pin->GetPinRoot();
    }

    wxLogDebug( "GetPinPosition ref=%s pin=%s symPos=(%d,%d) posIU=(%d,%d) tipIU=(%d,%d) hasLibRef=%d",
                req.reference(), req.pin_number(),
                symbol->GetPosition().x, symbol->GetPosition().y,
                posIU.x, posIU.y, tipIU.x, tipIU.y,
                (int)( symbol->GetLibSymbolRef() != nullptr ) );

    GetPinPositionResponse resp;
    resp.mutable_position()->set_x_mm( posIU.x / SCH_IU_PER_MM );
    resp.mutable_position()->set_y_mm( posIU.y / SCH_IU_PER_MM );
    // Orientation for label placement: 0=right, 90=up, 180=left, 270=down
    PIN_ORIENTATION orient = pin->PinDrawOrient( symbol->GetTransform() );
    double orientDeg = 0;
    switch( orient )
    {
        case PIN_ORIENTATION::PIN_RIGHT: orientDeg = 0; break;
        case PIN_ORIENTATION::PIN_UP:    orientDeg = 90; break;
        case PIN_ORIENTATION::PIN_LEFT:  orientDeg = 180; break;
        case PIN_ORIENTATION::PIN_DOWN:  orientDeg = 270; break;
        case PIN_ORIENTATION::INHERIT:   orientDeg = 0; break;  // resolved by PinDrawOrient
        default: break;
    }
    resp.set_orientation_degrees( orientDeg );
    // Tip of the pin (for label/wire end); wire must start at position (body) to connect.
    resp.mutable_position_label()->set_x_mm( tipIU.x / SCH_IU_PER_MM );
    resp.mutable_position_label()->set_y_mm( tipIU.y / SCH_IU_PER_MM );
    return resp;
}


HANDLER_RESULT<GetDanglingReportResponse> API_HANDLER_SCH::handleGetDanglingReport(
        const HANDLER_CONTEXT<GetDanglingReport>& aCtx )
{
    (void) aCtx;
    if( std::optional<ApiResponseStatus> busy = checkForBusy() )
        return tl::unexpected( *busy );

    SCH_SCREEN* screen = m_frame->GetScreen();
    if( !screen )
    {
        ApiResponseStatus err;
        err.set_status( ApiStatusCode::AS_BAD_REQUEST );
        err.set_error_message( "No schematic open" );
        return tl::unexpected( err );
    }

    const SCH_SHEET_PATH& sheet = m_frame->GetCurrentSheet();
    screen->TestDanglingEnds( &sheet, nullptr );

    GetDanglingReportResponse resp;
    for( SCH_ITEM* item : screen->Items() )
    {
        if( item->Type() == SCH_SYMBOL_T )
        {
            SCH_SYMBOL* sym = static_cast<SCH_SYMBOL*>( item );
            for( SCH_PIN* pin : sym->GetPins( &sheet ) )
            {
                if( pin->IsDangling() )
                {
                    // pin->GetPosition() returns world coordinates for schematic pins.
                    // Do NOT use GetPinPhysicalPosition — it double-transforms schematic pins.
                    VECTOR2I posIU = pin->GetPosition();
                    auto* di = resp.add_items();
                    di->set_reference( sym->GetRef( &sheet ).ToStdString() );
                    di->set_pin_number( pin->GetNumber().ToStdString() );
                    di->set_x_mm( posIU.x / SCH_IU_PER_MM );
                    di->set_y_mm( posIU.y / SCH_IU_PER_MM );
                    di->set_type( "pin" );
                }
            }
        }
        else if( item->Type() == SCH_LINE_T )
        {
            SCH_LINE* line = static_cast<SCH_LINE*>( item );
            if( line->GetLayer() != LAYER_WIRE )
                continue;
            if( line->IsStartDangling() )
            {
                VECTOR2I pos = line->GetStartPoint();
                auto* di = resp.add_items();
                di->set_x_mm( pos.x / SCH_IU_PER_MM );
                di->set_y_mm( pos.y / SCH_IU_PER_MM );
                di->set_type( "wire_end" );
            }
            if( line->IsEndDangling() )
            {
                VECTOR2I pos = line->GetEndPoint();
                auto* di = resp.add_items();
                di->set_x_mm( pos.x / SCH_IU_PER_MM );
                di->set_y_mm( pos.y / SCH_IU_PER_MM );
                di->set_type( "wire_end" );
            }
        }
    }
    return resp;
}


HANDLER_RESULT<GetSchematicSummaryResponse> API_HANDLER_SCH::handleGetSchematicSummary(
        const HANDLER_CONTEXT<GetSchematicSummary>& aCtx )
{
    (void) aCtx;
    if( std::optional<ApiResponseStatus> busy = checkForBusy() )
        return tl::unexpected( *busy );

    SCH_SCREEN* screen = m_frame->GetScreen();
    if( !screen )
    {
        ApiResponseStatus err;
        err.set_status( ApiStatusCode::AS_BAD_REQUEST );
        err.set_error_message( "No schematic open" );
        return tl::unexpected( err );
    }

    const SCH_SHEET_PATH& sheet = m_frame->GetCurrentSheet();
    GetSchematicSummaryResponse resp;
    resp.set_sheet_path( sheet.PathAsString().ToStdString() );

    // Current snapping grid (so tools use coordinates that snap correctly)
    if( EESCHEMA_SETTINGS* settings = m_frame->eeconfig() )
    {
        const GRID_SETTINGS& gridSettings = settings->m_Window.grid;
        int idx = gridSettings.last_size_idx;
        if( idx >= 0 && idx < (int) gridSettings.grids.size() )
        {
            const GRID& g = gridSettings.grids[idx];
            VECTOR2D stepMm = g.ToDouble( m_frame->GetIuScale() );
            resp.set_grid_step_mm( stepMm.x );
            resp.set_grid_display( g.UserUnitsMessageText( m_frame ).ToStdString() );
        }
    }

    for( SCH_ITEM* item : screen->Items() )
    {
        if( item->Type() == SCH_SYMBOL_T )
        {
            SCH_SYMBOL* sym = static_cast<SCH_SYMBOL*>( item );
            auto* comp = resp.add_components();
            comp->set_reference( sym->GetRef( &sheet ).ToStdString() );
            comp->set_library_nickname( sym->GetLibId().GetLibNickname().c_str() );
            comp->set_symbol_name( sym->GetLibId().GetLibItemName().c_str() );
            comp->set_value( sym->GetValue( false, &sheet, false ).ToStdString() );
            VECTOR2I posIU = sym->GetPosition();
            comp->mutable_position()->set_x_mm( posIU.x / SCH_IU_PER_MM );
            comp->mutable_position()->set_y_mm( posIU.y / SCH_IU_PER_MM );
            int orient = sym->GetOrientation() & ( SYM_ORIENT_0 | SYM_ORIENT_90 | SYM_ORIENT_180 | SYM_ORIENT_270 );
            if( orient == SYM_ORIENT_90 )
                comp->set_rotation( 90.0 );
            else if( orient == SYM_ORIENT_180 )
                comp->set_rotation( 180.0 );
            else if( orient == SYM_ORIENT_270 )
                comp->set_rotation( 270.0 );
            else
                comp->set_rotation( 0.0 );
            BOX2I bboxIU = sym->GetBodyAndPinsBoundingBox();
            comp->mutable_bbox()->set_min_x_mm( bboxIU.GetLeft() / SCH_IU_PER_MM );
            comp->mutable_bbox()->set_min_y_mm( bboxIU.GetTop() / SCH_IU_PER_MM );
            comp->mutable_bbox()->set_max_x_mm( bboxIU.GetRight() / SCH_IU_PER_MM );
            comp->mutable_bbox()->set_max_y_mm( bboxIU.GetBottom() / SCH_IU_PER_MM );
            for( SCH_PIN* pin : sym->GetPins( &sheet ) )
            {
                auto* ps = comp->add_pins();
                ps->set_number( pin->GetNumber().ToStdString() );
                ps->set_name( pin->GetName().ToStdString() );
                // pin->GetPosition() returns world coordinates for schematic pins.
                // Do NOT use GetPinPhysicalPosition — it double-transforms schematic pins.
                VECTOR2I pinPosIU = pin->GetPosition();
                ps->set_x_mm( pinPosIU.x / SCH_IU_PER_MM );
                ps->set_y_mm( pinPosIU.y / SCH_IU_PER_MM );
            }
        }
        else if( item->Type() == SCH_GLOBAL_LABEL_T )
        {
            SCH_GLOBALLABEL* label = static_cast<SCH_GLOBALLABEL*>( item );
            resp.add_global_net_names( label->GetText().ToStdString() );
        }
    }
    return resp;
}


HANDLER_RESULT<GetNetlistResponse> API_HANDLER_SCH::handleGetNetlist(
        const HANDLER_CONTEXT<GetNetlist>& aCtx )
{
    (void) aCtx;
    GetNetlistResponse resp;
    SCHEMATIC* schematic = &m_frame->Schematic();
    CONNECTION_GRAPH* graph = schematic->ConnectionGraph();
    if( !graph )
        return resp;

    const SCH_SHEET_PATH& currentSheet = m_frame->GetCurrentSheet();
    const auto& netMap = graph->GetNetMap();

    for( const auto& [key, subgraphList] : netMap )
    {
        for( CONNECTION_SUBGRAPH* subgraph : subgraphList )
        {
            if( subgraph->GetSheet() != currentSheet )
                continue;
            wxString netName = subgraph->GetNetName();
            if( netName.IsEmpty() )
                continue;
            NetEntry* entry = resp.add_nets();
            entry->set_net_name( netName.ToStdString() );
            std::set<std::pair<std::string, std::string>> seen;
            for( SCH_ITEM* item : subgraph->GetItems() )
            {
                if( item->Type() != SCH_PIN_T )
                    continue;
                SCH_PIN* pin = static_cast<SCH_PIN*>( item );
                SYMBOL* parentSym = pin->GetParentSymbol();
                SCH_SYMBOL* schSym = parentSym ? dynamic_cast<SCH_SYMBOL*>( parentSym ) : nullptr;
                if( !schSym )
                    continue;
                std::string ref = schSym->GetRef( &subgraph->GetSheet() ).ToStdString();
                std::string pinNum = pin->GetNumber().ToStdString();
                if( seen.insert( { ref, pinNum } ).second )
                {
                    NetPinRef* pref = entry->add_pins();
                    pref->set_reference( ref );
                    pref->set_pin_number( pinNum );
                }
            }
            break; // one subgraph per net on this sheet is enough
        }
    }
    return resp;
}


HANDLER_RESULT<CaptureScreenshotResponse> API_HANDLER_SCH::handleCaptureScreenshot(
        const HANDLER_CONTEXT<CaptureScreenshot>& aCtx )
{
    if( std::optional<ApiResponseStatus> busy = checkForBusy() )
        return tl::unexpected( *busy );

    EDA_DRAW_PANEL_GAL* canvas = m_frame->GetCanvas();
    if( !canvas || !canvas->GetView() )
    {
        ApiResponseStatus err;
        err.set_status( ApiStatusCode::AS_BAD_REQUEST );
        err.set_error_message( "No schematic canvas" );
        return tl::unexpected( err );
    }

    const double centerXmm = aCtx.Request.center_x_mm();
    const double centerYmm = aCtx.Request.center_y_mm();
    const VECTOR2D centerIU( centerXmm * SCH_IU_PER_MM, centerYmm * SCH_IU_PER_MM );

    canvas->GetView()->SetCenter( centerIU );
    m_frame->GetScreen()->m_ScrollCenter = centerIU;
    canvas->ForceRefresh();

    wxImage image;
    KIGFX::GAL* gal = canvas->GetGAL();
    if( KIGFX::OPENGL_GAL* ogl = dynamic_cast<KIGFX::OPENGL_GAL*>( gal ) )
    {
        if( !ogl->SaveScreenshot( image ) || !image.IsOk() )
        {
            ApiResponseStatus err;
            err.set_status( ApiStatusCode::AS_BAD_REQUEST );
            err.set_error_message( "Screenshot capture failed (OpenGL)" );
            return tl::unexpected( err );
        }
    }
    else
    {
        ApiResponseStatus err;
        err.set_status( ApiStatusCode::AS_UNIMPLEMENTED );
        err.set_error_message( "Screenshot only supported with OpenGL canvas" );
        return tl::unexpected( err );
    }

    wxMemoryOutputStream memStream;
    if( !image.SaveFile( memStream, wxBITMAP_TYPE_PNG ) )
    {
        ApiResponseStatus err;
        err.set_status( ApiStatusCode::AS_BAD_REQUEST );
        err.set_error_message( "PNG encode failed" );
        return tl::unexpected( err );
    }

    size_t len = memStream.GetLength();
    wxMemoryBuffer buf( len );
    memStream.CopyTo( buf.GetData(), len );
    buf.SetDataLen( len );
    wxString base64 = wxBase64Encode( buf.GetData(), buf.GetDataLen() );

    CaptureScreenshotResponse resp;
    resp.set_image_png_base64( base64.ToStdString() );
    return resp;
}


HANDLER_RESULT<CaptureScreenshotResponse> API_HANDLER_SCH::handleCaptureZoneScreenshot(
        const HANDLER_CONTEXT<CaptureZoneScreenshot>& aCtx )
{
    if( std::optional<ApiResponseStatus> busy = checkForBusy() )
        return tl::unexpected( *busy );

    EDA_DRAW_PANEL_GAL* canvas = m_frame->GetCanvas();
    if( !canvas || !canvas->GetView() )
    {
        ApiResponseStatus err;
        err.set_status( ApiStatusCode::AS_BAD_REQUEST );
        err.set_error_message( "No schematic canvas" );
        return tl::unexpected( err );
    }

    KIGFX::VIEW* view = canvas->GetView();
    EDA_DRAW_FRAME* frame = static_cast<EDA_DRAW_FRAME*>( m_frame );
    const double centerXmm = aCtx.Request.center_x_mm();
    const double centerYmm = aCtx.Request.center_y_mm();
    double widthMm = aCtx.Request.width_mm();
    if( widthMm <= 0 )
        widthMm = 15.0;

    // Use full schematic view setup (same as CaptureFullSchematic), then crop to zone
    BOX2I bBox = frame->GetDocumentExtents( true );
    BOX2I defaultBox = canvas->GetDefaultViewBBox();
    if( bBox.GetWidth() == 0 || bBox.GetHeight() == 0 )
        bBox = defaultBox;

    view->SetScale( 1.0 );
    VECTOR2D screenSize = view->ToWorld( ToVECTOR2I( canvas->GetClientSize() ), false );
    VECTOR2D vsize = bBox.GetSize();
    double scale = view->GetScale()
            / std::max( fabs( vsize.x / screenSize.x ), fabs( vsize.y / screenSize.y ) );

    if( !std::isfinite( scale ) || scale <= 0 )
        view->SetCenter( VECTOR2D( 0, 0 ) );
    else
    {
        const double margin = 1.04;
        view->SetScale( scale / margin );
        view->SetCenter( bBox.Centre() );
    }

    m_frame->GetScreen()->m_ScrollCenter = view->GetCenter();
    canvas->ForceRefresh();

    wxImage image;
    KIGFX::GAL* gal = canvas->GetGAL();
    if( KIGFX::OPENGL_GAL* ogl = dynamic_cast<KIGFX::OPENGL_GAL*>( gal ) )
    {
        if( !ogl->SaveScreenshot( image ) || !image.IsOk() )
        {
            ApiResponseStatus err;
            err.set_status( ApiStatusCode::AS_BAD_REQUEST );
            err.set_error_message( "Screenshot capture failed (OpenGL)" );
            return tl::unexpected( err );
        }
    }
    else
    {
        ApiResponseStatus err;
        err.set_status( ApiStatusCode::AS_UNIMPLEMENTED );
        err.set_error_message( "Screenshot only supported with OpenGL canvas" );
        return tl::unexpected( err );
    }

    // Crop to zone: zone bbox in world IU, convert to screen pixels
    const VECTOR2D centerIU( centerXmm * SCH_IU_PER_MM, centerYmm * SCH_IU_PER_MM );
    const double halfWidthIU = ( widthMm / 2.0 ) * SCH_IU_PER_MM;
    const double aspect = ( screenSize.y > 0 ) ? ( screenSize.x / screenSize.y ) : 1.0;
    const double halfHeightIU = halfWidthIU / aspect;

    VECTOR2D corners[4] = {
        { centerIU.x - halfWidthIU, centerIU.y - halfHeightIU },
        { centerIU.x + halfWidthIU, centerIU.y - halfHeightIU },
        { centerIU.x + halfWidthIU, centerIU.y + halfHeightIU },
        { centerIU.x - halfWidthIU, centerIU.y + halfHeightIU },
    };

    double minPxX = 1e9, maxPxX = -1e9, minPxY = 1e9, maxPxY = -1e9;
    for( const VECTOR2D& corner : corners )
    {
        VECTOR2D px = view->ToScreen( corner );
        minPxX = std::min( minPxX, px.x );
        maxPxX = std::max( maxPxX, px.x );
        minPxY = std::min( minPxY, px.y );
        maxPxY = std::max( maxPxY, px.y );
    }

    int imgW = image.GetWidth();
    int imgH = image.GetHeight();
    int x0 = std::max( 0, KiROUND( minPxX ) );
    int y0 = std::max( 0, KiROUND( minPxY ) );
    int x1 = std::min( imgW, KiROUND( maxPxX ) );
    int y1 = std::min( imgH, KiROUND( maxPxY ) );
    int cropW = std::max( 1, x1 - x0 );
    int cropH = std::max( 1, y1 - y0 );

    if( cropW < imgW || cropH < imgH )
        image = image.GetSubImage( wxRect( x0, y0, cropW, cropH ) );

    int32_t maxWidthPx = aCtx.Request.max_width_px();
    if( maxWidthPx > 0 && image.GetWidth() > maxWidthPx )
    {
        int newW = maxWidthPx;
        int newH = ( image.GetHeight() * maxWidthPx ) / image.GetWidth();
        if( newH < 1 )
            newH = 1;
        image = image.Rescale( newW, newH, wxIMAGE_QUALITY_BILINEAR );
    }

    wxMemoryOutputStream memStream;
    if( !image.SaveFile( memStream, wxBITMAP_TYPE_PNG ) )
    {
        ApiResponseStatus err;
        err.set_status( ApiStatusCode::AS_BAD_REQUEST );
        err.set_error_message( "PNG encode failed" );
        return tl::unexpected( err );
    }

    size_t len = memStream.GetLength();
    wxMemoryBuffer buf( len );
    memStream.CopyTo( buf.GetData(), len );
    buf.SetDataLen( len );
    wxString base64 = wxBase64Encode( buf.GetData(), buf.GetDataLen() );

    CaptureScreenshotResponse resp;
    resp.set_image_png_base64( base64.ToStdString() );
    return resp;
}


HANDLER_RESULT<CaptureScreenshotResponse> API_HANDLER_SCH::handleCaptureFullSchematic(
        const HANDLER_CONTEXT<CaptureFullSchematic>& aCtx )
{
    if( std::optional<ApiResponseStatus> busy = checkForBusy() )
        return tl::unexpected( *busy );

    EDA_DRAW_PANEL_GAL* canvas = m_frame->GetCanvas();
    if( !canvas || !canvas->GetView() )
    {
        ApiResponseStatus err;
        err.set_status( ApiStatusCode::AS_BAD_REQUEST );
        err.set_error_message( "No schematic canvas" );
        return tl::unexpected( err );
    }

    wxSize savedSize;
    int32_t targetWidthPx = aCtx.Request.target_width_px();
    if( targetWidthPx > 0 )
    {
        savedSize = canvas->GetSize();
        int curW = savedSize.GetWidth();
        int curH = savedSize.GetHeight();
        if( curW > 0 && curH > 0 )
        {
            int newH = ( curH * targetWidthPx ) / curW;
            if( newH < 1 )
                newH = 1;
            canvas->SetSize( targetWidthPx, newH );
            if( wxTheApp )
                wxTheApp->ProcessPendingEvents();
            canvas->Refresh();
        }
    }

    KIGFX::VIEW* view = canvas->GetView();
    EDA_DRAW_FRAME* frame = static_cast<EDA_DRAW_FRAME*>( m_frame );
    BOX2I bBox = frame->GetDocumentExtents( true );
    BOX2I defaultBox = canvas->GetDefaultViewBBox();

    if( bBox.GetWidth() == 0 || bBox.GetHeight() == 0 )
        bBox = defaultBox;

    view->SetScale( 1.0 );
    VECTOR2D screenSize = view->ToWorld( ToVECTOR2I( canvas->GetClientSize() ), false );
    VECTOR2D vsize = bBox.GetSize();
    double scale = view->GetScale()
            / std::max( fabs( vsize.x / screenSize.x ), fabs( vsize.y / screenSize.y ) );

    if( !std::isfinite( scale ) || scale <= 0 )
    {
        view->SetCenter( VECTOR2D( 0, 0 ) );
    }
    else
    {
        const double margin = 1.04;
        view->SetScale( scale / margin );
        view->SetCenter( bBox.Centre() );
    }

    m_frame->GetScreen()->m_ScrollCenter = view->GetCenter();
    canvas->ForceRefresh();

    wxImage image;
    KIGFX::GAL* gal = canvas->GetGAL();
    if( KIGFX::OPENGL_GAL* ogl = dynamic_cast<KIGFX::OPENGL_GAL*>( gal ) )
    {
        if( !ogl->SaveScreenshot( image ) || !image.IsOk() )
        {
            ApiResponseStatus err;
            err.set_status( ApiStatusCode::AS_BAD_REQUEST );
            err.set_error_message( "Screenshot capture failed (OpenGL)" );
            return tl::unexpected( err );
        }
    }
    else
    {
        ApiResponseStatus err;
        err.set_status( ApiStatusCode::AS_UNIMPLEMENTED );
        err.set_error_message( "Screenshot only supported with OpenGL canvas" );
        return tl::unexpected( err );
    }

    if( targetWidthPx > 0 && savedSize.GetWidth() > 0 && savedSize.GetHeight() > 0 )
    {
        canvas->SetSize( savedSize );
        if( wxTheApp )
            wxTheApp->ProcessPendingEvents();
    }

    wxMemoryOutputStream memStream;
    if( !image.SaveFile( memStream, wxBITMAP_TYPE_PNG ) )
    {
        ApiResponseStatus err;
        err.set_status( ApiStatusCode::AS_BAD_REQUEST );
        err.set_error_message( "PNG encode failed" );
        return tl::unexpected( err );
    }

    size_t len = memStream.GetLength();
    wxMemoryBuffer buf( len );
    memStream.CopyTo( buf.GetData(), len );
    buf.SetDataLen( len );
    wxString base64 = wxBase64Encode( buf.GetData(), buf.GetDataLen() );

    CaptureScreenshotResponse resp;
    resp.set_image_png_base64( base64.ToStdString() );
    return resp;
}


HANDLER_RESULT<GetVisibleBoundsResponse> API_HANDLER_SCH::handleGetVisibleBounds(
        const HANDLER_CONTEXT<GetVisibleBounds>& aCtx )
{
    (void) aCtx;

    if( std::optional<ApiResponseStatus> busy = checkForBusy() )
        return tl::unexpected( *busy );

    EDA_DRAW_PANEL_GAL* canvas = m_frame->GetCanvas();
    if( !canvas || !canvas->GetView() )
    {
        ApiResponseStatus err;
        err.set_status( ApiStatusCode::AS_BAD_REQUEST );
        err.set_error_message( "No schematic canvas" );
        return tl::unexpected( err );
    }

    KIGFX::VIEW* view = canvas->GetView();
    const wxSize clientSize = canvas->GetClientSize();
    const VECTOR2D worldSpan = view->ToWorld( ToVECTOR2I( clientSize ), false );
    const VECTOR2D centerIU = view->GetCenter();

    const double halfWidthIu = worldSpan.x / 2.0;
    const double halfHeightIu = worldSpan.y / 2.0;

    GetVisibleBoundsResponse resp;
    resp.set_min_x_mm( ( centerIU.x - halfWidthIu ) / SCH_IU_PER_MM );
    resp.set_min_y_mm( ( centerIU.y - halfHeightIu ) / SCH_IU_PER_MM );
    resp.set_max_x_mm( ( centerIU.x + halfWidthIu ) / SCH_IU_PER_MM );
    resp.set_max_y_mm( ( centerIU.y + halfHeightIu ) / SCH_IU_PER_MM );
    resp.set_center_x_mm( centerIU.x / SCH_IU_PER_MM );
    resp.set_center_y_mm( centerIU.y / SCH_IU_PER_MM );
    resp.set_width_mm( worldSpan.x / SCH_IU_PER_MM );
    resp.set_height_mm( worldSpan.y / SCH_IU_PER_MM );
    resp.set_client_width_px( clientSize.GetWidth() );
    resp.set_client_height_px( clientSize.GetHeight() );
    return resp;
}


HANDLER_RESULT<MoveComponentResponse> API_HANDLER_SCH::handleMoveComponent(
        const HANDLER_CONTEXT<MoveComponent>& aCtx )
{
    if( std::optional<ApiResponseStatus> busy = checkForBusy() )
        return tl::unexpected( *busy );

    const MoveComponent& req = aCtx.Request;
    if( req.reference().empty() || !req.has_position() )
    {
        ApiResponseStatus err;
        err.set_status( ApiStatusCode::AS_BAD_REQUEST );
        err.set_error_message( "reference and position (x_mm, y_mm) are required" );
        return tl::unexpected( err );
    }

    std::string commitIdStr = req.commit_id().value();
    COMMIT* commit = nullptr;
    for( auto& it : m_commits )
    {
        if( it.second.first.AsStdString() == commitIdStr )
        {
            commit = it.second.second.get();
            break;
        }
    }
    if( !commit )
    {
        ApiResponseStatus err;
        err.set_status( ApiStatusCode::AS_BAD_REQUEST );
        err.set_error_message( "Invalid or expired commit ID" );
        return tl::unexpected( err );
    }

    SCH_SCREEN* screen = m_frame->GetScreen();
    if( !screen )
    {
        ApiResponseStatus err;
        err.set_status( ApiStatusCode::AS_BAD_REQUEST );
        err.set_error_message( "No schematic open" );
        return tl::unexpected( err );
    }

    const SCH_SHEET_PATH& sheet = m_frame->GetCurrentSheet();
    wxString refReq( req.reference().c_str(), wxConvUTF8 );
    SCH_SYMBOL* symbol = nullptr;
    for( SCH_ITEM* item : screen->Items() )
    {
        if( item->Type() != SCH_SYMBOL_T )
            continue;
        SCH_SYMBOL* sym = static_cast<SCH_SYMBOL*>( item );
        if( sym->GetRef( &sheet ) == refReq )
        {
            symbol = sym;
            break;
        }
    }
    if( !symbol )
    {
        ApiResponseStatus err;
        err.set_status( ApiStatusCode::AS_BAD_REQUEST );
        err.set_error_message( "Symbol not found: " + req.reference() );
        return tl::unexpected( err );
    }

    double xMm = req.position().x_mm();
    double yMm = req.position().y_mm();
    VECTOR2I newPosIU( KiROUND( xMm * SCH_IU_PER_MM ), KiROUND( yMm * SCH_IU_PER_MM ) );
    VECTOR2I oldPosIU = symbol->GetPosition();
    VECTOR2I delta = newPosIU - oldPosIU;
    symbol->Move( delta );

    if( req.has_rotation() )
    {
        int targetOrient = SYM_ORIENT_0;
        double rot = req.rotation();
        if( rot >= 45 && rot < 135 )
            targetOrient = SYM_ORIENT_90;
        else if( rot >= 135 && rot < 225 )
            targetOrient = SYM_ORIENT_180;
        else if( rot >= 225 && rot < 315 )
            targetOrient = SYM_ORIENT_270;
        int curOrient = symbol->GetOrientation() & ( SYM_ORIENT_0 | SYM_ORIENT_90 | SYM_ORIENT_180 | SYM_ORIENT_270 );
        int curDeg = ( curOrient == SYM_ORIENT_90 ) ? 90 : ( curOrient == SYM_ORIENT_180 ) ? 180 : ( curOrient == SYM_ORIENT_270 ) ? 270 : 0;
        int targetDeg = ( targetOrient == SYM_ORIENT_90 ) ? 90 : ( targetOrient == SYM_ORIENT_180 ) ? 180 : ( targetOrient == SYM_ORIENT_270 ) ? 270 : 0;
        int steps = ( ( targetDeg - curDeg + 360 ) % 360 ) / 90;
        for( int i = 0; i < steps; ++i )
            symbol->Rotate( symbol->GetPosition(), true );
    }

    commit->Modify( symbol, screen );
    return MoveComponentResponse();
}


HANDLER_RESULT<DeleteComponentResponse> API_HANDLER_SCH::handleDeleteComponent(
        const HANDLER_CONTEXT<DeleteComponent>& aCtx )
{
    if( std::optional<ApiResponseStatus> busy = checkForBusy() )
        return tl::unexpected( *busy );

    const DeleteComponent& req = aCtx.Request;
    if( req.reference().empty() )
    {
        ApiResponseStatus err;
        err.set_status( ApiStatusCode::AS_BAD_REQUEST );
        err.set_error_message( "reference is required" );
        return tl::unexpected( err );
    }

    std::string commitIdStr = req.commit_id().value();
    COMMIT* commit = nullptr;
    for( auto& it : m_commits )
    {
        if( it.second.first.AsStdString() == commitIdStr )
        {
            commit = it.second.second.get();
            break;
        }
    }
    if( !commit )
    {
        ApiResponseStatus err;
        err.set_status( ApiStatusCode::AS_BAD_REQUEST );
        err.set_error_message( "Invalid or expired commit ID" );
        return tl::unexpected( err );
    }

    SCH_SCREEN* screen = m_frame->GetScreen();
    if( !screen )
    {
        ApiResponseStatus err;
        err.set_status( ApiStatusCode::AS_BAD_REQUEST );
        err.set_error_message( "No schematic open" );
        return tl::unexpected( err );
    }

    const SCH_SHEET_PATH& sheet = m_frame->GetCurrentSheet();
    wxString refReq( req.reference().c_str(), wxConvUTF8 );
    SCH_SYMBOL* symbol = nullptr;
    for( SCH_ITEM* item : screen->Items() )
    {
        if( item->Type() != SCH_SYMBOL_T )
            continue;
        SCH_SYMBOL* sym = static_cast<SCH_SYMBOL*>( item );
        if( sym->GetRef( &sheet ) == refReq )
        {
            symbol = sym;
            break;
        }
    }
    if( !symbol )
    {
        ApiResponseStatus err;
        err.set_status( ApiStatusCode::AS_BAD_REQUEST );
        err.set_error_message( "Symbol not found: " + req.reference() );
        return tl::unexpected( err );
    }

    commit->Remove( symbol, screen );
    return DeleteComponentResponse();
}


HANDLER_RESULT<ReloadProjectSymbolLibrariesResponse>
API_HANDLER_SCH::handleReloadProjectSymbolLibraries(
        const HANDLER_CONTEXT<ReloadProjectSymbolLibraries>& aCtx )
{
    ReloadProjectSymbolLibrariesResponse response;

    PROJECT&          prj = m_frame->Prj();
    SYMBOL_LIB_TABLE* tbl = PROJECT_SCH::SchSymbolLibTable( &prj );
    wxFileName        fn( prj.GetProjectPath(), SYMBOL_LIB_TABLE::GetSymbolLibTableFileName() );

    try
    {
        if( fn.FileExists() )
            tbl->Load( fn.GetFullPath() );
    }
    catch( const IO_ERROR& ioe )
    {
        response.set_ok( false );
        response.set_error_message( ioe.What().ToUTF8() );
        response.set_sym_lib_table_path( fn.GetFullPath().ToUTF8() );
        return response;
    }

    prj.SetElem( PROJECT::ELEM::SCH_SYMBOL_LIBS, nullptr );
    broadcastSymbolLibraryReload( m_frame );

    response.set_ok( true );
    response.set_sym_lib_table_path( fn.GetFullPath().ToUTF8() );
    return response;
}


HANDLER_RESULT<AppendProjectSymbolLibraryRowResponse>
API_HANDLER_SCH::handleAppendProjectSymbolLibraryRow(
        const HANDLER_CONTEXT<AppendProjectSymbolLibraryRow>& aCtx )
{
    AppendProjectSymbolLibraryRowResponse response;
    const AppendProjectSymbolLibraryRow&  req = aCtx.Request;

    wxString nick = wxString::FromUTF8( req.library_nickname() );
    wxString uri = wxString::FromUTF8( req.uri() );
    wxString descr = wxString::FromUTF8( req.description() );

    if( nick.IsEmpty() || uri.IsEmpty() )
    {
        response.set_ok( false );
        response.set_error_message( "library_nickname and uri are required" );
        return response;
    }

    PROJECT&          prj = m_frame->Prj();
    SYMBOL_LIB_TABLE* tbl = PROJECT_SCH::SchSymbolLibTable( &prj );

    const bool replace = req.replace_existing();

    if( tbl->HasLibrary( nick, false ) && !replace )
    {
        response.set_ok( true );
        response.set_skipped_duplicate( true );
        response.set_wrote_file( false );
        return response;
    }

    SYMBOL_LIB_TABLE_ROW* row = new SYMBOL_LIB_TABLE_ROW(
            nick, uri, SCH_IO_MGR::ShowType( SCH_IO_MGR::SCH_KICAD ), wxEmptyString, descr );

    tbl->InsertRow( row, replace );

    wxFileName projectTableFn( prj.GetProjectPath(), SYMBOL_LIB_TABLE::GetSymbolLibTableFileName() );

    try
    {
        tbl->Save( projectTableFn.GetFullPath() );
    }
    catch( const IO_ERROR& ioe )
    {
        response.set_ok( false );
        response.set_error_message( ioe.What().ToUTF8() );
        return response;
    }

    prj.SetElem( PROJECT::ELEM::SCH_SYMBOL_LIBS, nullptr );
    broadcastSymbolLibraryReload( m_frame );

    response.set_ok( true );
    response.set_skipped_duplicate( false );
    response.set_wrote_file( true );
    return response;
}
