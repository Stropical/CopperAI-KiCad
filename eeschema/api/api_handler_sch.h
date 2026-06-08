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

#ifndef KICAD_API_HANDLER_SCH_H
#define KICAD_API_HANDLER_SCH_H

#include <api/api_handler_editor.h>
#include <api/common/commands/editor_commands.pb.h>
#include <api/schematic/schematic_commands.pb.h>
#include <kiid.h>
#include <wx/string.h>

#include <vector>

using namespace kiapi;
using namespace kiapi::common;

class SCH_EDIT_FRAME;
class SCH_ITEM;
class SYMBOL_LIB_TABLE;


class API_HANDLER_SCH : public API_HANDLER_EDITOR
{
public:
    API_HANDLER_SCH( SCH_EDIT_FRAME* aFrame );

protected:
    std::unique_ptr<COMMIT> createCommit() override;

    kiapi::common::types::DocumentType thisDocumentType() const override
    {
        return kiapi::common::types::DOCTYPE_SCHEMATIC;
    }

    bool validateDocumentInternal( const DocumentSpecifier& aDocument ) const override;

    HANDLER_RESULT<std::unique_ptr<EDA_ITEM>> createItemForType( KICAD_T aType,
                                                                 EDA_ITEM* aContainer );

    HANDLER_RESULT<types::ItemRequestStatus> handleCreateUpdateItemsInternal( bool aCreate,
            const std::string& aClientName,
            const types::ItemHeader &aHeader,
            const google::protobuf::RepeatedPtrField<google::protobuf::Any>& aItems,
            std::function<void(commands::ItemStatus, google::protobuf::Any)> aItemHandler )
            override;

    void deleteItemsInternal( std::map<KIID, ItemDeletionStatus>& aItemsToDelete,
                              const std::string& aClientName ) override;

    std::optional<EDA_ITEM*> getItemFromDocument( const DocumentSpecifier& aDocument,
                                                  const KIID& aId ) override;

private:
    HANDLER_RESULT<commands::GetItemsResponse> handleGetItems(
            const HANDLER_CONTEXT<commands::GetItems>& aCtx );

    HANDLER_RESULT<commands::GetOpenDocumentsResponse> handleGetOpenDocuments(
            const HANDLER_CONTEXT<commands::GetOpenDocuments>& aCtx );

    HANDLER_RESULT<kiapi::schematic::types::SearchSymbolsResponse> handleSearchSymbols(
            const HANDLER_CONTEXT<kiapi::schematic::types::SearchSymbols>& aCtx );

    HANDLER_RESULT<kiapi::schematic::types::GetComponentDataResponse> handleGetComponentData(
            const HANDLER_CONTEXT<kiapi::schematic::types::GetComponentData>& aCtx );

    HANDLER_RESULT<kiapi::schematic::types::AddComponentResponse> handleAddComponent(
            const HANDLER_CONTEXT<kiapi::schematic::types::AddComponent>& aCtx );

    HANDLER_RESULT<kiapi::schematic::types::GetPinPositionResponse> handleGetPinPosition(
            const HANDLER_CONTEXT<kiapi::schematic::types::GetPinPosition>& aCtx );

    HANDLER_RESULT<kiapi::schematic::types::GetDanglingReportResponse> handleGetDanglingReport(
            const HANDLER_CONTEXT<kiapi::schematic::types::GetDanglingReport>& aCtx );

    HANDLER_RESULT<kiapi::schematic::types::GetSchematicSummaryResponse> handleGetSchematicSummary(
            const HANDLER_CONTEXT<kiapi::schematic::types::GetSchematicSummary>& aCtx );

    HANDLER_RESULT<kiapi::schematic::types::GetNetlistResponse> handleGetNetlist(
            const HANDLER_CONTEXT<kiapi::schematic::types::GetNetlist>& aCtx );

    HANDLER_RESULT<kiapi::schematic::types::CaptureScreenshotResponse> handleCaptureScreenshot(
            const HANDLER_CONTEXT<kiapi::schematic::types::CaptureScreenshot>& aCtx );

    HANDLER_RESULT<kiapi::schematic::types::CaptureScreenshotResponse> handleCaptureZoneScreenshot(
            const HANDLER_CONTEXT<kiapi::schematic::types::CaptureZoneScreenshot>& aCtx );

    HANDLER_RESULT<kiapi::schematic::types::CaptureScreenshotResponse> handleCaptureFullSchematic(
            const HANDLER_CONTEXT<kiapi::schematic::types::CaptureFullSchematic>& aCtx );

    HANDLER_RESULT<kiapi::schematic::types::GetVisibleBoundsResponse> handleGetVisibleBounds(
            const HANDLER_CONTEXT<kiapi::schematic::types::GetVisibleBounds>& aCtx );

    HANDLER_RESULT<kiapi::schematic::types::MoveComponentResponse> handleMoveComponent(
            const HANDLER_CONTEXT<kiapi::schematic::types::MoveComponent>& aCtx );

    HANDLER_RESULT<kiapi::schematic::types::DeleteComponentResponse> handleDeleteComponent(
            const HANDLER_CONTEXT<kiapi::schematic::types::DeleteComponent>& aCtx );

    HANDLER_RESULT<kiapi::schematic::types::ReloadProjectSymbolLibrariesResponse>
    handleReloadProjectSymbolLibraries(
            const HANDLER_CONTEXT<kiapi::schematic::types::ReloadProjectSymbolLibraries>& aCtx );

    HANDLER_RESULT<kiapi::schematic::types::AppendProjectSymbolLibraryRowResponse>
    handleAppendProjectSymbolLibraryRow(
            const HANDLER_CONTEXT<kiapi::schematic::types::AppendProjectSymbolLibraryRow>& aCtx );

    struct SYMBOL_SEARCH_ENTRY
    {
        wxString libraryNickname;
        wxString symbolName;
        wxString symbolNameLower;
        wxString description;
        wxString descriptionLower;
        wxString keywords;
        wxString keywordsLower;
        wxString datasheet;
        bool     metadataLoaded = false;
    };

    void clearSymbolSearchCache();
    void rebuildSymbolSearchCacheIfNeeded( SYMBOL_LIB_TABLE* aLibTable );
    bool loadSymbolSearchMetadata( SYMBOL_LIB_TABLE* aLibTable, SYMBOL_SEARCH_ENTRY& aEntry );

    SCH_EDIT_FRAME* m_frame;

    SYMBOL_LIB_TABLE*                m_symbolSearchCacheTable = nullptr;
    wxString                         m_symbolSearchCacheSignature;
    std::vector<SYMBOL_SEARCH_ENTRY> m_symbolSearchCache;
};


#endif //KICAD_API_HANDLER_SCH_H
