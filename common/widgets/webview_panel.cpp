/*
 * This program source code file is part of KiCad, a free EDA CAD application.
 *
 * Copyright (C) 2024 KiCad Developers, see AUTHORS.txt for contributors.
 *
 * This program is free software; you can redistribute it and/or
 * modify it under the terms of the GNU General Public License
 * as published by the Free Software Foundation; either version 2
 * of the License, or (at your option) any later version.
 *
 * This program is distributed in the hope that it will be useful,
 * but WITHOUT ANY WARRANTY; without even the implied warranty of
 * MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
 * GNU General Public License for more details.
 *
 * You should have received a copy of the GNU General Public License
 * along with this program; if not, you may find one here:
 * http://www.gnu.org/licenses/old-licenses/gpl-2.0.html
 * or you may search the http://www.gnu.org website for the version 2 license,
 * or you may write to the Free Software Foundation, Inc.,
 * 51 Franklin Street, Fifth Floor, Boston, MA  02110-1301, USA
 */

#include <widgets/webview_panel.h>
#ifdef KICAD_IPC_API
#include <api/api_server.h>
#include <api/common/envelope.pb.h>
#endif
#include <clipboard.h>
#include <json_common.h>
#include <kiplatform/ui.h>
#include <widgets/ui_common.h>
#ifdef KICAD_IPC_API
#include <pgm_base.h>
#endif
#include <nlohmann/json.hpp>
#include <wx/base64.h>
#include <wx/log.h>
#include <wx/sizer.h>
#include <wx/button.h>
#include <wx/toolbar.h>
#include <wx/timer.h>

using json = nlohmann::json;

namespace
{
const wxColour COPPERAI_DARK_BG( 10, 10, 10 );
const wxColour COPPERAI_DARK_PANEL_BG( 30, 30, 30 );
const wxColour COPPERAI_DARK_FG( 229, 229, 229 );

wxString ToJsStringLiteral( const wxString& aValue )
{
    return wxString::FromUTF8( json( std::string( aValue.utf8_str() ) ).dump() );
}
}


wxString WEBVIEW_PANEL::NormalizeUrlForLock( const wxString& aUrl )
{
    wxString normalized = aUrl;
    normalized.Trim( true ).Trim( false );

    // Ignore in-page fragment changes for lock checks.
    int hashIdx = normalized.Find( '#' );
    if( hashIdx != wxNOT_FOUND )
    {
        if( hashIdx == 0 )
            normalized.clear();
        else
            normalized = normalized.SubString( 0, hashIdx - 1 );
    }

    // Keep trailing slash normalization simple and deterministic.
    if( normalized.Length() > 1 && normalized.EndsWith( "/" ) )
        normalized.RemoveLast();

    return normalized;
}


static wxString UrlOriginForLock( const wxString& aUrl )
{
    wxString normalized = aUrl;
    normalized.Trim( true ).Trim( false );

    int hashIdx = normalized.Find( '#' );

    if( hashIdx != wxNOT_FOUND )
    {
        if( hashIdx == 0 )
            normalized.clear();
        else
            normalized = normalized.SubString( 0, hashIdx - 1 );
    }

    if( normalized.Length() > 1 && normalized.EndsWith( "/" ) )
        normalized.RemoveLast();

    wxString lower = normalized.Lower();
    int schemeEnd = lower.Find( wxS( "://" ) );

    if( schemeEnd == wxNOT_FOUND )
        return wxEmptyString;

    wxString scheme = lower.Left( schemeEnd );

    if( scheme != wxS( "http" ) && scheme != wxS( "https" ) )
        return wxEmptyString;

    wxString rest = normalized.Mid( schemeEnd + 3 );
    size_t end = rest.length();

    for( size_t ii = 0; ii < rest.length(); ++ii )
    {
        wxUniChar c = rest[ii];

        if( c == '/' || c == '?' )
        {
            end = ii;
            break;
        }
    }

    wxString authority = rest.Left( end ).Lower();

    if( authority.IsEmpty() )
        return wxEmptyString;

    return scheme + wxS( "://" ) + authority;
}


bool WEBVIEW_PANEL::IsNavigationToLockedUrl( const wxString& aUrl ) const
{
    if( !m_lockNavigation || m_lockedUrl.IsEmpty() )
        return true;

    wxString normalized = NormalizeUrlForLock( aUrl );

    if( normalized == m_lockedUrl )
        return true;

    wxString lockedOrigin = UrlOriginForLock( m_lockedUrl );
    wxString requestedOrigin = UrlOriginForLock( normalized );

    return !lockedOrigin.IsEmpty() && lockedOrigin == requestedOrigin;
}


void WEBVIEW_PANEL::ShowAgentConnectionErrorPage()
{
    if( !m_browser )
        return;

    wxString html =
            wxS( "<!DOCTYPE html>"
                 "<html><head><meta charset='UTF-8'></head>"
                 "<body style='margin:0;display:flex;align-items:center;justify-content:center;"
                 "height:100vh;background:#0A0A0A;color:#E5E5E5;font-family:system-ui,sans-serif;'>"
                 "<div style='font-size:20px;font-weight:600;'>Agent can't connect</div>"
                 "</body></html>" );

    m_browser->SetPage( html, "about:blank" );
}


void WEBVIEW_PANEL::SendApiCallback( const wxString& aCallbackId, bool aIsError,
                                     const wxString& aJsonData )
{
    if( !m_browser || aCallbackId.IsEmpty() )
        return;

    const wxString script = wxString::Format(
            wxS( "(function() {"
                 "  if (window.kicad && window.kicad.api && typeof "
                 "window.kicad.api._handleResponse === 'function') {"
                 "    window.kicad.api._handleResponse(%s, %s, %s);"
                 "  }"
                 "})();" ),
            ToJsStringLiteral( aCallbackId ),
            aIsError ? wxS( "true" ) : wxS( "false" ),
            ToJsStringLiteral( aJsonData ) );

    RunScriptAsync( script );
}


void WEBVIEW_PANEL::SendIpcCallback( const wxString& aCallbackId, bool aIsError,
                                     const wxString& aJsonData )
{
    if( !m_browser || aCallbackId.IsEmpty() )
        return;

    const wxString script = wxString::Format(
            wxS( "(function() {"
                 "  if (window.kicad && window.kicad.ipc && typeof "
                 "window.kicad.ipc._handleResponse === 'function') {"
                 "    window.kicad.ipc._handleResponse(%s, %s, %s);"
                 "  }"
                 "})();" ),
            ToJsStringLiteral( aCallbackId ),
            aIsError ? wxS( "true" ) : wxS( "false" ),
            ToJsStringLiteral( aJsonData ) );

    RunScriptAsync( script );
}


void WEBVIEW_PANEL::RegisterBuiltInMessageHandlers()
{
    AddMessageHandler(
            wxS( "clipboard_read_text" ),
            [this]( const wxString& aMessage )
            {
                wxString callbackId;

                try
                {
                    json payload = json::parse( std::string( aMessage.utf8_str() ) );

                    if( payload.contains( "callbackId" ) && payload["callbackId"].is_string() )
                        callbackId = wxString::FromUTF8( payload["callbackId"].get<std::string>() );
                    else if( payload.contains( "callback_id" ) && payload["callback_id"].is_string() )
                        callbackId = wxString::FromUTF8( payload["callback_id"].get<std::string>() );

                    if( callbackId.IsEmpty() )
                        return;

                    json response;
                    response["text"] = GetClipboardUTF8();
                    SendApiCallback( callbackId, false,
                                     wxString::FromUTF8( response.dump() ) );
                }
                catch( const std::exception& e )
                {
                    SendApiCallback( callbackId, true, wxString::FromUTF8( e.what() ) );
                }
            } );

    AddMessageHandler(
            wxS( "clipboard_write_text" ),
            [this]( const wxString& aMessage )
            {
                wxString callbackId;

                try
                {
                    json payload = json::parse( std::string( aMessage.utf8_str() ) );

                    if( payload.contains( "callbackId" ) && payload["callbackId"].is_string() )
                        callbackId = wxString::FromUTF8( payload["callbackId"].get<std::string>() );
                    else if( payload.contains( "callback_id" ) && payload["callback_id"].is_string() )
                        callbackId = wxString::FromUTF8( payload["callback_id"].get<std::string>() );

                    if( callbackId.IsEmpty() )
                        return;

                    std::string text;

                    if( payload.contains( "data" ) && payload["data"].is_object() )
                    {
                        const json& data = payload["data"];

                        if( data.contains( "text" ) && data["text"].is_string() )
                            text = data["text"].get<std::string>();
                    }

                    if( !SaveClipboard( text ) )
                    {
                        SendApiCallback( callbackId, true,
                                         wxS( "Failed to write clipboard text." ) );
                        return;
                    }

                    json response;
                    response["ok"] = true;
                    SendApiCallback( callbackId, false,
                                     wxString::FromUTF8( response.dump() ) );
                }
                catch( const std::exception& e )
                {
                    SendApiCallback( callbackId, true, wxString::FromUTF8( e.what() ) );
                }
            } );

    AddMessageHandler(
            wxS( "open_external_url" ),
            [this]( const wxString& aMessage )
            {
                wxString callbackId;

                try
                {
                    json payload = json::parse( std::string( aMessage.utf8_str() ) );

                    if( payload.contains( "callbackId" ) && payload["callbackId"].is_string() )
                        callbackId = wxString::FromUTF8( payload["callbackId"].get<std::string>() );
                    else if( payload.contains( "callback_id" ) && payload["callback_id"].is_string() )
                        callbackId = wxString::FromUTF8( payload["callback_id"].get<std::string>() );

                    std::string url;

                    if( payload.contains( "url" ) && payload["url"].is_string() )
                        url = payload["url"].get<std::string>();
                    else if( payload.contains( "data" ) && payload["data"].is_object() )
                    {
                        const json& data = payload["data"];

                        if( data.contains( "url" ) && data["url"].is_string() )
                            url = data["url"].get<std::string>();
                    }

                    wxString wxUrl = wxString::FromUTF8( url );
                    wxUrl.Trim( true ).Trim( false );

                    if( !wxUrl.StartsWith( wxS( "https://" ) )
                            && !wxUrl.StartsWith( wxS( "http://" ) ) )
                    {
                        if( !callbackId.IsEmpty() )
                            SendApiCallback( callbackId, true,
                                             wxS( "Only http(s) URLs can be opened externally." ) );
                        return;
                    }

                    bool opened = wxLaunchDefaultBrowser( wxUrl, wxBROWSER_NEW_WINDOW );

                    if( !callbackId.IsEmpty() )
                    {
                        json response;
                        response["ok"] = opened;
                        SendApiCallback( callbackId, !opened,
                                         opened ? wxString::FromUTF8( response.dump() )
                                                : wxS( "Failed to open system browser." ) );
                    }
                }
                catch( const std::exception& e )
                {
                    if( !callbackId.IsEmpty() )
                        SendApiCallback( callbackId, true, wxString::FromUTF8( e.what() ) );
                }
            } );
}


void WEBVIEW_PANEL::EnableKiCadIpcBridge()
{
    if( m_enableKiCadIpcBridge )
        return;

    m_enableKiCadIpcBridge = true;

    AddMessageHandler(
            wxS( "kicad_ipc_request_base64" ),
            [this]( const wxString& aMessage )
            {
                wxString callbackId;

                try
                {
                    json payload = json::parse( std::string( aMessage.utf8_str() ) );

                    if( payload.contains( "callbackId" ) && payload["callbackId"].is_string() )
                        callbackId = wxString::FromUTF8( payload["callbackId"].get<std::string>() );
                    else if( payload.contains( "callback_id" ) && payload["callback_id"].is_string() )
                        callbackId = wxString::FromUTF8( payload["callback_id"].get<std::string>() );

                    if( callbackId.IsEmpty() )
                        return;

                    if( !payload.contains( "requestBase64" ) || !payload["requestBase64"].is_string() )
                    {
                        SendIpcCallback( callbackId, true, wxS( "Missing requestBase64." ) );
                        return;
                    }

#ifndef KICAD_IPC_API
                    SendIpcCallback( callbackId, true,
                                     wxS( "KiCad IPC API support is not enabled in this build." ) );
#else
                    const std::string requestBase64 = payload["requestBase64"].get<std::string>();
                    wxMemoryBuffer decoded = wxBase64Decode( wxString::FromUTF8( requestBase64 ) );

                    if( decoded.GetDataLen() == 0 )
                    {
                        SendIpcCallback( callbackId, true, wxS( "Malformed ApiRequest base64." ) );
                        return;
                    }

                    kiapi::common::ApiRequest request;

                    if( !request.ParseFromArray( decoded.GetData(),
                                                 static_cast<int>( decoded.GetDataLen() ) ) )
                    {
                        SendIpcCallback( callbackId, true,
                                         wxS( "Malformed ApiRequest protobuf." ) );
                        return;
                    }

                    request.mutable_header()->clear_kicad_token();
                    request.mutable_header()->set_client_name( "webview-ipc-bridge" );

                    kiapi::common::ApiResponse response =
                            Pgm().GetApiServer().ProcessRequest( request );

                    response.mutable_header()->clear_kicad_token();

                    std::string responseBytes;
                    if( !response.SerializeToString( &responseBytes ) )
                    {
                        SendIpcCallback( callbackId, true,
                                         wxS( "Failed to serialize ApiResponse." ) );
                        return;
                    }

                    json result;
                    result["responseBase64"] = std::string(
                            wxBase64Encode( responseBytes.data(), responseBytes.size() ).utf8_str() );
                    SendIpcCallback( callbackId, false, wxString::FromUTF8( result.dump() ) );
#endif
                }
                catch( const std::exception& e )
                {
                    SendIpcCallback( callbackId, true, wxString::FromUTF8( e.what() ) );
                }
            } );
}


bool WEBVIEW_PANEL::HandleEditCommand( int aCommandId )
{
    if( !m_browser )
        return false;

    wxString command;

    switch( aCommandId )
    {
    case wxID_CUT:
        command = wxS( "cut" );
        break;

    case wxID_COPY:
        command = wxS( "copy" );
        break;

    case wxID_PASTE:
        command = wxS( "paste" );
        break;

    default:
        return false;
    }

    const wxString script = wxString::Format(
            wxS( "(function() {"
                 "  const command = %s;"
                 "  const promptBridge = window.__kicadPromptClipboard;"
                 "  const runBridge = function(method, arg) {"
                 "    if (!promptBridge || typeof promptBridge[method] !== 'function')"
                 "      return false;"
                 "    try {"
                 "      Promise.resolve(promptBridge[method](arg)).catch(() => {});"
                 "      return true;"
                 "    } catch (e) {"
                 "      return false;"
                 "    }"
                 "  };"
                 "  "
                 "  if (command === 'copy' && runBridge('copySelection'))"
                 "    return;"
                 "  if (command === 'cut' && runBridge('cutSelection'))"
                 "    return;"
                 "  if (command === 'paste' && window.kicad && window.kicad.api"
                 "      && typeof window.kicad.api.readClipboardText === 'function') {"
                 "    window.kicad.api.readClipboardText()"
                 "      .then((result) => {"
                 "        const text = result && typeof result.text === 'string' ? result.text : '';"
                 "        if (!text)"
                 "          return;"
                 "        if (runBridge('pasteText', text))"
                 "          return;"
                 "        const active = document.activeElement;"
                 "        const isTextControl = active &&"
                 "          (active.tagName === 'TEXTAREA'"
                 "            || (active.tagName === 'INPUT' && typeof active.value === 'string'));"
                 "        if (!isTextControl)"
                 "          return;"
                 "        const start = typeof active.selectionStart === 'number'"
                 "          ? active.selectionStart : active.value.length;"
                 "        const end = typeof active.selectionEnd === 'number'"
                 "          ? active.selectionEnd : start;"
                 "        active.focus();"
                 "        active.setRangeText(text, start, end, 'end');"
                 "        active.dispatchEvent(new Event('input', { bubbles: true }));"
                 "      })"
                 "      .catch(() => {});"
                 "    return;"
                 "  }"
                 "  "
                 "  if ((command === 'copy' || command === 'cut') && window.kicad && window.kicad.api"
                 "      && typeof window.kicad.api.writeClipboardText === 'function') {"
                 "    const active = document.activeElement;"
                 "    const isTextControl = active &&"
                 "      (active.tagName === 'TEXTAREA'"
                 "        || (active.tagName === 'INPUT' && typeof active.value === 'string'));"
                 "    if (!isTextControl)"
                 "      return;"
                 "    const start = typeof active.selectionStart === 'number' ? active.selectionStart : 0;"
                 "    const end = typeof active.selectionEnd === 'number' ? active.selectionEnd : start;"
                 "    if (start === end)"
                 "      return;"
                 "    const text = active.value.slice(start, end);"
                 "    window.kicad.api.writeClipboardText(text)"
                 "      .then(() => {"
                 "        if (command !== 'cut')"
                 "          return;"
                 "        active.focus();"
                 "        active.setRangeText('', start, end, 'start');"
                 "        active.dispatchEvent(new Event('input', { bubbles: true }));"
                 "      })"
                 "      .catch(() => {});"
                 "  }"
                 "})();" ),
            ToJsStringLiteral( command ) );

    RunScriptAsync( script );
    return true;
}


WEBVIEW_PANEL::WEBVIEW_PANEL( wxWindow* aParent, wxWindowID aId, const wxPoint& aPos,
                              const wxSize& aSize ) :
        wxPanel( aParent, aId, aPos, aSize, wxBORDER_NONE ), m_browser( nullptr ),
        m_toolbar( nullptr ), m_deferredScriptTimer( nullptr ), m_btnOpenId( wxID_ANY ),
        m_btnCloseId( wxID_ANY ), m_initialized( false ), m_loadError( false ),
        m_loadedEventBound( false ), m_handleExternalLinks( false ), m_lockNavigation( false ),
        m_enableKiCadIpcBridge( false ), m_lockedUrl( wxEmptyString )
{
    SetBackgroundColour( COPPERAI_DARK_BG );
    SetForegroundColour( COPPERAI_DARK_FG );

    m_deferredScriptTimer = new wxTimer( this );
    Bind( wxEVT_TIMER, &WEBVIEW_PANEL::OnDeferredScriptTimer, this,
          m_deferredScriptTimer->GetId() );

    // Create toolbar with open/close buttons
    m_toolbar = new wxToolBar( this, wxID_ANY, wxDefaultPosition, wxDefaultSize,
                               wxTB_HORIZONTAL | wxTB_NODIVIDER );
    m_toolbar->SetBackgroundColour( COPPERAI_DARK_PANEL_BG );
    m_toolbar->SetForegroundColour( COPPERAI_DARK_FG );

    wxWindowID openId = wxNewId();
    wxWindowID closeId = wxNewId();

    m_toolbar->AddTool( openId, wxT( "Open" ), wxNullBitmap, wxNullBitmap, wxITEM_NORMAL,
                        wxT( "Open WebView" ), wxT( "Show the webview panel" ) );
    m_toolbar->AddTool( closeId, wxT( "Close" ), wxNullBitmap, wxNullBitmap, wxITEM_NORMAL,
                        wxT( "Close WebView" ), wxT( "Hide the webview panel" ) );

    m_toolbar->Realize();

    // Store button IDs
    m_btnOpenId = openId;
    m_btnCloseId = closeId;

    // Bind toolbar events
    Bind( wxEVT_COMMAND_TOOL_CLICKED, &WEBVIEW_PANEL::OnToolbarClick, this );

    // Create the WebView
    m_browser = wxWebView::New( this, wxID_ANY, wxWebViewDefaultURLStr, wxDefaultPosition,
                                wxDefaultSize, wxWebViewBackendDefault, wxBORDER_NONE );

    if( !m_browser )
    {
        wxLogError( "Failed to create WebView" );
        return;
    }

    m_browser->SetBackgroundColour( COPPERAI_DARK_BG );

    RegisterBuiltInMessageHandlers();

    // Layout
    wxBoxSizer* sizer = new wxBoxSizer( wxVERTICAL );
    sizer->Add( m_toolbar, 0, wxEXPAND );
    sizer->Add( m_browser, 1, wxEXPAND | wxALL, 0 );
    SetSizer( sizer );

    // Ensure browser fills the available space
    if( m_browser )
    {
        m_browser->SetSizeHints( -1, -1 );
    }

    // Bind events
    Bind( wxEVT_WEBVIEW_NAVIGATING, &WEBVIEW_PANEL::OnNavigationRequest, this, m_browser->GetId() );
    Bind( wxEVT_WEBVIEW_NEWWINDOW, &WEBVIEW_PANEL::OnNewWindow, this, m_browser->GetId() );
    Bind( wxEVT_WEBVIEW_SCRIPT_MESSAGE_RECEIVED, &WEBVIEW_PANEL::OnScriptMessage, this,
          m_browser->GetId() );
    Bind( wxEVT_WEBVIEW_SCRIPT_RESULT, &WEBVIEW_PANEL::OnScriptResult, this, m_browser->GetId() );
    Bind( wxEVT_WEBVIEW_ERROR, &WEBVIEW_PANEL::OnError, this, m_browser->GetId() );
}


WEBVIEW_PANEL::~WEBVIEW_PANEL()
{
    if( m_deferredScriptTimer )
    {
        m_deferredScriptTimer->Stop();
        Unbind( wxEVT_TIMER, &WEBVIEW_PANEL::OnDeferredScriptTimer, this,
                m_deferredScriptTimer->GetId() );
        delete m_deferredScriptTimer;
        m_deferredScriptTimer = nullptr;
    }

    m_deferredScripts.clear();

    // Unbind all event handlers before destruction to prevent callbacks
    // from WebKit during shutdown from trying to process events on a destroyed object
    if( m_browser )
    {
        Unbind( wxEVT_WEBVIEW_NAVIGATING, &WEBVIEW_PANEL::OnNavigationRequest, this,
                m_browser->GetId() );
        Unbind( wxEVT_WEBVIEW_NEWWINDOW, &WEBVIEW_PANEL::OnNewWindow, this, m_browser->GetId() );
        Unbind( wxEVT_WEBVIEW_SCRIPT_MESSAGE_RECEIVED, &WEBVIEW_PANEL::OnScriptMessage, this,
                m_browser->GetId() );
        Unbind( wxEVT_WEBVIEW_SCRIPT_RESULT, &WEBVIEW_PANEL::OnScriptResult, this,
                m_browser->GetId() );
        Unbind( wxEVT_WEBVIEW_ERROR, &WEBVIEW_PANEL::OnError, this, m_browser->GetId() );

        if( m_loadedEventBound )
        {
            Unbind( wxEVT_WEBVIEW_LOADED, &WEBVIEW_PANEL::OnWebViewLoaded, this,
                    m_browser->GetId() );
        }

        // Clear message handlers to prevent any pending callbacks
        ClearMessageHandlers();
    }
}

void WEBVIEW_PANEL::BindLoadedEvent()
{
    if( m_browser && !m_loadedEventBound )
    {
        Bind( wxEVT_WEBVIEW_LOADED, &WEBVIEW_PANEL::OnWebViewLoaded, this, m_browser->GetId() );
        m_loadedEventBound = true;
    }
}


void WEBVIEW_PANEL::AddMessageHandler( const wxString&                        aHandlerName,
                                       std::function<void( const wxString& )> aCallback )
{
    m_msgHandlers[aHandlerName] = aCallback;

    // If already initialized, register immediately
    if( m_initialized && m_browser )
    {
        if( !m_browser->AddScriptMessageHandler( aHandlerName ) )
        {
            wxLogDebug( "Failed to register message handler: %s", aHandlerName );
        }
    }
}


void WEBVIEW_PANEL::ClearMessageHandlers()
{
    if( m_browser )
    {
        for( const auto& handler : m_msgHandlers )
        {
            m_browser->RemoveScriptMessageHandler( handler.first );
        }
    }
    m_msgHandlers.clear();
}


void WEBVIEW_PANEL::LoadURL( const wxString& aUrl, bool aLockNavigation )
{
    if( m_browser )
    {
        m_loadError = false;
        m_lockNavigation = aLockNavigation;

        if( m_lockNavigation )
            m_lockedUrl = NormalizeUrlForLock( aUrl );
        else
            m_lockedUrl.clear();

        m_browser->LoadURL( aUrl );
    }
}


void WEBVIEW_PANEL::SetPage( const wxString& aHtml, const wxString& aBaseUrl )
{
    if( m_browser )
    {
        m_loadError = false;
        m_browser->SetPage( aHtml, aBaseUrl );
    }
}


void WEBVIEW_PANEL::RunScriptAsync( const wxString& aScript )
{
    if( !m_browser )
        return;

    if( KIUI::IsModalDialogFocused() )
    {
        m_deferredScripts.push_back( aScript );
        ScheduleDeferredScriptRetry();
        wxLogTrace( "webview", "Deferring script execution while modal dialog is active" );
        return;
    }

    if( KIPLATFORM::UI::RunWebViewScriptFireAndForget( m_browser, aScript ) )
        return;

    m_browser->RunScriptAsync( aScript );
}


void WEBVIEW_PANEL::ScheduleDeferredScriptRetry()
{
    if( m_deferredScriptTimer && !m_deferredScriptTimer->IsRunning() )
        m_deferredScriptTimer->StartOnce( 100 );
}


void WEBVIEW_PANEL::FlushDeferredScripts()
{
    if( !m_browser )
    {
        m_deferredScripts.clear();
        return;
    }

    if( m_deferredScripts.empty() )
        return;

    if( KIUI::IsModalDialogFocused() )
    {
        ScheduleDeferredScriptRetry();
        return;
    }

    std::deque<wxString> scripts;
    scripts.swap( m_deferredScripts );

    for( const wxString& script : scripts )
    {
        if( !m_browser )
            break;

        if( !KIPLATFORM::UI::RunWebViewScriptFireAndForget( m_browser, script ) )
            m_browser->RunScriptAsync( script );
    }
}


void WEBVIEW_PANEL::OnDeferredScriptTimer( wxTimerEvent& aEvent )
{
    WXUNUSED( aEvent );
    FlushDeferredScripts();
}


void WEBVIEW_PANEL::OnNavigationRequest( wxWebViewEvent& aEvt )
{
    // Safety check: ensure the browser is still valid
    if( !m_browser )
        return;

    m_loadError = false;
    wxString url = aEvt.GetURL();
    wxLogTrace( "webview", "Navigation request to URL: %s", url );

    // Always allow localhost URLs (for development servers)
    if( url.Contains( "localhost" ) || url.Contains( "127.0.0.1" ) )
    {
        return;
    }

    // Silently block embed/iframe URLs — never open them in the system browser.
    // This must be checked before any other logic to prevent YouTube embeds,
    // Vimeo players, etc. from escaping into the external browser.
    bool isEmbedOrIframe = url.Contains( "youtube.com/embed" )
                        || url.Contains( "youtube.com/watch" )
                        || url.Contains( "player.vimeo.com" )
                        || url.Contains( "/embed" )
                        || url.Contains( "iframe" );

    if( isEmbedOrIframe )
    {
        wxLogTrace( "webview", "Silently blocked embed/iframe URL: %s", url );
        aEvt.Veto();
        return;
    }

    const bool navigationMatchesLock = IsNavigationToLockedUrl( url );
    const bool hasLockedUrl = m_lockNavigation && !m_lockedUrl.IsEmpty();

    if( !navigationMatchesLock )
    {
        if( url.StartsWith( "http" ) )
            wxLaunchDefaultBrowser( url );

        wxLogTrace( "webview", "Blocked navigation away from locked URL: %s", url );
        aEvt.Veto();
        return;
    }

    // Default behavior: open external links in the system browser
    // unless m_handleExternalLinks is true
    if( !hasLockedUrl && !m_handleExternalLinks && url.StartsWith( "http" ) )
    {
        wxLaunchDefaultBrowser( url );
        aEvt.Veto();
    }
}


void WEBVIEW_PANEL::OnWebViewLoaded( wxWebViewEvent& aEvt )
{
    // Safety check: ensure the browser is still valid
    if( !m_browser )
        return;


    // On macOS, WKWebView intercepts Cmd+C/V/X via performKeyEquivalent: even
    // when it doesn't have focus, stealing shortcuts from the schematic canvas.
    // Patch after first load when the native WKWebView is fully in the view tree.
    KIPLATFORM::UI::FixupWebViewKeyEquivalents( m_browser );

    // Use CallAfter to defer script execution, avoiding re-entrancy issues if loaded during a modal loop
    CallAfter(
            [this]()
            {
                if( !m_browser )
                    return;

                // Ensure proper container sizing without zoom
                wxString zoomScript =
                        wxS( "(function() {"
                             "  var style = document.createElement('style');"
                             "  style.innerHTML = 'html, body, #root, #__next { margin: 0; "
                             "padding: 0; width: 100%; min-height: 100%; background: #0A0A0A "
                             "!important; color: #E5E5E5; color-scheme: dark; } body { "
                             "overflow: hidden; }';"
                             "  if (document.head) { document.head.appendChild(style); }"
                             "  else { document.addEventListener('DOMContentLoaded', function() "
                             "{ document.head.appendChild(style); }); }"
                             "})();" );
                RunScriptAsync( zoomScript );

                // Inject wx_msg bridge for C++ communication
                wxString wxMsgScript = wxS(
                        "(function() {"
                        "  if (window.wx_msg) return;" // Already injected
                        "  "
                        "  window.wx_msg = {"
                        "    postMessage: function(handlerName, message) {"
                        "      if (window.webkit && window.webkit.messageHandlers && "
                        "window.webkit.messageHandlers[handlerName]) {"
                        "        window.webkit.messageHandlers[handlerName].postMessage(message);"
                        "      } else if (window.chrome && window.chrome.webview) {"
                        "        window.chrome.webview.postMessage(JSON.stringify({ handler: "
                        "handlerName, "
                        "message: message }));"
                        "      } else {"
                        "        console.warn('[wx_msg] No native bridge available');"
                        "      }"
                        "    }"
                        "  };"
                        "  "
                        "  console.log('[KiCad] wx_msg bridge injected successfully');"
                        "})();" );
                RunScriptAsync( wxMsgScript );

                // Inject window.kicad.api bridge for schematic operations
                wxString kicadApiScript = wxS(
                        "(function() {"
                        "  window.kicad = window.kicad || {};"
                        "  if (window.kicad.api && typeof window.kicad.api._callHandler === 'function') return;"
                        "  "
                        "  // Create KiCad API bridge that uses wx_msg for communication"
                        "  window.kicad.api = {"
                        "      // Helper to call C++ handlers via wx_msg"
                        "      _callHandler: function(handlerName, data) {"
                        "        return new Promise((resolve, reject) => {"
                        "          const callbackId = 'cb_' + Date.now() + '_' + Math.random();"
                        "          "
                        "          // Store callback"
                        "          window.kicad.api._callbacks = window.kicad.api._callbacks || {};"
                        "          window.kicad.api._callbacks[callbackId] = { resolve, reject };"
                        "          "
                        "          // Send message to C++"
                        "          const message = JSON.stringify({ callbackId, data });"
                        "          window.wx_msg.postMessage(handlerName, message);"
                        "          "
                        "          // Timeout after 30 seconds"
                        "          setTimeout(() => {"
                        "            if (window.kicad.api._callbacks[callbackId]) {"
                        "              delete window.kicad.api._callbacks[callbackId];"
                        "              reject(new Error('API call timeout'));"
                        "            }"
                        "          }, 30000);"
                        "        });"
                        "      },"
                        "      "
                        "      // Handle responses from C++"
                        "      _handleResponse: function(callbackId, isError, jsonData) {"
                        "        const callbacks = window.kicad.api._callbacks || {};"
                        "        if (callbacks[callbackId]) {"
                        "          const { resolve, reject } = callbacks[callbackId];"
                        "          delete callbacks[callbackId];"
                        "          "
                        "          if (isError) {"
                        "            reject(new Error(jsonData));"
                        "          } else {"
                        "            try {"
                        "              const response = JSON.parse(jsonData);"
                        "              resolve(response);"
                        "            } catch (e) {"
                        "              resolve(jsonData);"
                        "            }"
                        "          }"
                        "        }"
                        "      },"
                        "      "
                        "      // API methods"
                        "      ping: function() {"
                        "        return this._callHandler('api_ping', {});"
                        "      },"
                        "      "
                        "      getVersion: function() {"
                        "        return this._callHandler('api_getVersion', {});"
                        "      },"
                        "      "
                        "      getNetClasses: function() {"
                        "        return this._callHandler('api_getNetClasses', {});"
                        "      },"
                        "      "
                        "      request: function(messageType, messageData, typeUrl) {"
                        "        return this._callHandler('api_request', { messageType, "
                        "messageData, typeUrl });"
                        "      },"
                        "      "
                        "      readClipboardText: function() {"
                        "        return this._callHandler('clipboard_read_text', {});"
                        "      },"
                        "      "
                        "      writeClipboardText: function(text) {"
                        "        return this._callHandler('clipboard_write_text', { text: text "
                        "|| '' });"
                        "      }"
                        "  };"
                        "  "
                        "  console.log('[KiCad] window.kicad.api bridge injected successfully');"
                        "})();" );
                RunScriptAsync( kicadApiScript );

                wxString kicadHostScript = wxS(
                        "(function() {"
                        "  window.kicad = window.kicad || {};"
                        "  window.kicad.host = window.kicad.host || {};"
                        "  if (typeof window.kicad.host.openExternalUrl === 'function') return;"
                        "  window.kicad.host.openExternalUrl = function(url) {"
                        "    if (!window.wx_msg || typeof window.wx_msg.postMessage !== 'function') return false;"
                        "    window.wx_msg.postMessage('open_external_url', JSON.stringify({ url: String(url || '') }));"
                        "    return true;"
                        "  };"
                        "})();" );
                RunScriptAsync( kicadHostScript );

                if( m_enableKiCadIpcBridge )
                {
                    wxString kicadIpcScript = wxS(
                            "(function() {"
                            "  window.kicad = window.kicad || {};"
                            "  if (window.kicad.ipc && typeof window.kicad.ipc.requestBase64 === 'function')"
                            "    return;"
                            "  window.kicad.ipc = {"
                            "    _callbacks: {},"
                            "    requestBase64: function(requestBase64) {"
                            "      return new Promise((resolve, reject) => {"
                            "        const callbackId = 'ipc_' + Date.now() + '_' + Math.random();"
                            "        window.kicad.ipc._callbacks[callbackId] = { resolve, reject };"
                            "        const message = JSON.stringify({ callbackId, requestBase64 });"
                            "        window.wx_msg.postMessage('kicad_ipc_request_base64', message);"
                            "        setTimeout(() => {"
                            "          if (window.kicad.ipc._callbacks[callbackId]) {"
                            "            delete window.kicad.ipc._callbacks[callbackId];"
                            "            reject(new Error('IPC call timeout'));"
                            "          }"
                            "        }, 30000);"
                            "      });"
                            "    },"
                            "    _handleResponse: function(callbackId, isError, jsonData) {"
                            "      const callback = window.kicad.ipc._callbacks[callbackId];"
                            "      if (!callback)"
                            "        return;"
                            "      delete window.kicad.ipc._callbacks[callbackId];"
                            "      if (isError) {"
                            "        callback.reject(new Error(jsonData));"
                            "        return;"
                            "      }"
                            "      try {"
                            "        callback.resolve(JSON.parse(jsonData));"
                            "      } catch (e) {"
                            "        callback.reject(e);"
                            "      }"
                            "    }"
                            "  };"
                            "  console.log('[KiCad] window.kicad.ipc bridge injected successfully');"
                            "})();" );
                    RunScriptAsync( kicadIpcScript );
                }

                // Inject KaTeX for LaTeX math rendering.
                // Uses fetch() + blob URLs to bypass CSP restrictions that block
                // CDN <script> elements. Falls back to <script> tags if fetch fails.
                // Includes periodic re-render to survive React/SPA DOM reconciliation.
                wxString katexScript = wxS(
                        "(function() {"
                        "  if (window._katexInjected) return;"
                        "  window._katexInjected = true;"
                        "  "
                        "  var KATEX_BASE = 'https://cdn.jsdelivr.net/npm/katex@0.16.11/dist';"
                        "  var DELIMITERS = ["
                        "    {left: '$$', right: '$$', display: true},"
                        "    {left: '$', right: '$', display: false},"
                        "    {left: '\\\\(', right: '\\\\)', display: false},"
                        "    {left: '\\\\[', right: '\\\\]', display: true}"
                        "  ];"
                        "  "
                        "  function doRender(root) {"
                        "    if (typeof renderMathInElement === 'function') {"
                        "      try {"
                        "        renderMathInElement(root, { delimiters: DELIMITERS, throwOnError: false });"
                        "      } catch(e) { console.warn('[KaTeX] render error:', e); }"
                        "    }"
                        "  }"
                        "  "
                        "  function startObserver() {"
                        "    var pending = false;"
                        "    var observer = new MutationObserver(function() {"
                        "      if (pending) return;"
                        "      pending = true;"
                        "      requestAnimationFrame(function() {"
                        "        pending = false;"
                        "        doRender(document.body);"
                        "      });"
                        "    });"
                        "    observer.observe(document.body, "
                        "      {childList: true, subtree: true, characterData: true});"
                        "  }"
                        "  "
                        "  function onReady() {"
                        "    console.log('[KaTeX] Loaded successfully');"
                        "    doRender(document.body);"
                        "    startObserver();"
                        "    setInterval(function() { doRender(document.body); }, 3000);"
                        "  }"
                        "  "
                        "  // Inject CSS via fetch + blob to bypass CSP"
                        "  function loadCSS() {"
                        "    return fetch(KATEX_BASE + '/katex.min.css')"
                        "      .then(function(r) { return r.text(); })"
                        "      .then(function(css) {"
                        "        var style = document.createElement('style');"
                        "        style.textContent = css;"
                        "        document.head.appendChild(style);"
                        "      })"
                        "      .catch(function() {"
                        "        var link = document.createElement('link');"
                        "        link.rel = 'stylesheet';"
                        "        link.href = KATEX_BASE + '/katex.min.css';"
                        "        document.head.appendChild(link);"
                        "      });"
                        "  }"
                        "  "
                        "  // Load JS via fetch + blob URL to bypass script-src CSP"
                        "  function loadScript(url) {"
                        "    return fetch(url)"
                        "      .then(function(r) { return r.text(); })"
                        "      .then(function(code) {"
                        "        var blob = new Blob([code], {type: 'text/javascript'});"
                        "        var blobUrl = URL.createObjectURL(blob);"
                        "        return new Promise(function(resolve, reject) {"
                        "          var s = document.createElement('script');"
                        "          s.src = blobUrl;"
                        "          s.onload = function() { URL.revokeObjectURL(blobUrl); resolve(); };"
                        "          s.onerror = function() { URL.revokeObjectURL(blobUrl); reject(); };"
                        "          document.head.appendChild(s);"
                        "        });"
                        "      })"
                        "      .catch(function() {"
                        "        return new Promise(function(resolve, reject) {"
                        "          var s = document.createElement('script');"
                        "          s.src = url;"
                        "          s.crossOrigin = 'anonymous';"
                        "          s.onload = resolve;"
                        "          s.onerror = reject;"
                        "          document.head.appendChild(s);"
                        "        });"
                        "      });"
                        "  }"
                        "  "
                        "  loadCSS()"
                        "    .then(function() { return loadScript(KATEX_BASE + '/katex.min.js'); })"
                        "    .then(function() { return loadScript(KATEX_BASE + '/contrib/auto-render.min.js'); })"
                        "    .then(onReady)"
                        "    .catch(function(e) { console.error('[KaTeX] Failed to load:', e); });"
                        "  "
                        "  console.log('[KiCad] KaTeX injection started');"
                        "})();" );
                RunScriptAsync( katexScript );
            } );

    if( !m_initialized )
    {
        // Defer handler registration to avoid running during modal dialog/yield
        auto initFunc = [this]()
        {
            // Double-check browser is still valid after async callback
            if( !m_browser )
                return;

            for( const auto& handler : m_msgHandlers )
            {
                if( !m_browser->AddScriptMessageHandler( handler.first ) )
                {
                    wxLogDebug( "Failed to register message handler: %s", handler.first );
                }
                else
                {
                    wxLogTrace( "webview", "Registered message handler: %s", handler.first );
                }
            }

            m_initialized = true;
        };

        // Use CallAfter to defer execution
        CallAfter( initFunc );
    }

    wxLogTrace( "webview", "WebView loaded: %s", aEvt.GetURL() );
}


void WEBVIEW_PANEL::OnNewWindow( wxWebViewEvent& aEvt )
{
    // Safety check: ensure the browser is still valid
    if( !m_browser )
        return;

    wxString url = aEvt.GetURL();

    // Never navigate the embedded view on popup/new-window requests.
    // Don't open embed/iframe URLs in the browser (e.g. YouTube embeds).
    bool isEmbed = url.Contains( "youtube.com/embed" )
                || url.Contains( "player.vimeo.com" )
                || url.Contains( "/embed" );

    if( url.StartsWith( "http" ) && !isEmbed )
        wxLaunchDefaultBrowser( url );

    aEvt.Veto(); // Prevent default behavior of opening a new window
    wxLogTrace( "webview", "New window requested and blocked in panel for URL: %s", url );
}


void WEBVIEW_PANEL::OnScriptMessage( wxWebViewEvent& aEvt )
{
    // Safety check: ensure the browser is still valid
    if( !m_browser )
        return;

    wxLogTrace( "webview", "Script message received: %s for handler %s", aEvt.GetString(),
                aEvt.GetMessageHandler() );

    wxString handler = aEvt.GetMessageHandler();
    handler.Trim( true ).Trim( false );

    auto it = m_msgHandlers.find( handler );
    if( it != m_msgHandlers.end() )
    {
        try
        {
            it->second( aEvt.GetString() );
        }
        catch( const std::exception& e )
        {
            wxLogError( "Exception in message handler '%s': %s", handler, e.what() );
        }
        catch( ... )
        {
            wxLogError( "Unknown exception in message handler '%s'", handler );
        }
    }
    else
    {
        wxLogDebug( "No handler registered for: %s", handler );
    }
}


void WEBVIEW_PANEL::OnScriptResult( wxWebViewEvent& aEvt )
{
    // Safety check: ensure the browser and this object are still valid
    // This can happen if WebKit calls back during shutdown after the WebView
    // has been partially destroyed
    if( !m_browser )
        return;

    if( aEvt.IsError() )
        wxLogDebug( "Async script execution failed: %s", aEvt.GetString() );
}


void WEBVIEW_PANEL::OnError( wxWebViewEvent& aEvt )
{
    // Safety check: ensure the browser is still valid
    if( !m_browser )
        return;

    m_loadError = true;
    wxLogDebug( "WebView error: %s (url=%s)", aEvt.GetString(), aEvt.GetURL() );

    wxString msg = aEvt.GetString();
    wxString url = aEvt.GetURL();

    if( !m_lockedUrl.IsEmpty() && IsNavigationToLockedUrl( url ) )
    {
        ShowAgentConnectionErrorPage();
        return;
    }

    auto escape = []( wxString s )
    {
        s.Replace( "&", "&amp;" );
        s.Replace( "<", "&lt;" );
        s.Replace( ">", "&gt;" );
        return s;
    };

    wxString html;
    html << wxS( "<!DOCTYPE html><html><head><meta charset='UTF-8'></head><body "
                 "style='background:#0A0A0A;color:#E5E5E5;font-family:system-ui;padding:16px;'>" )
         << wxS( "<h2 style='margin:0 0 8px 0;'>WebView load error</h2>" )
         << wxS( "<div style='margin:0 0 8px 0;'><strong>URL:</strong> <code>" ) << escape( url )
         << wxS( "</code></div>" ) << wxS( "<div><strong>Message:</strong> <code>" )
         << escape( msg ) << wxS( "</code></div>" ) << wxS( "</body></html>" );

    m_browser->SetPage( html, "file://" );
}


void WEBVIEW_PANEL::OnToolbarClick( wxCommandEvent& aEvt )
{
    int id = aEvt.GetId();

    if( id == m_btnOpenId )
    {
        ShowBrowser( true );
    }
    else if( id == m_btnCloseId )
    {
        ShowBrowser( false );
    }
}


void WEBVIEW_PANEL::ShowBrowser( bool aShow )
{
    if( m_browser )
    {
        m_browser->Show( aShow );
        if( aShow )
        {
            // Ensure browser fills the available space
            m_browser->SetSize( GetClientSize() );
        }
        Layout();
        Refresh();
    }
}
