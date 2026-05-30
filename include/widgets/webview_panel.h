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

#ifndef WEBVIEW_PANEL_H
#define WEBVIEW_PANEL_H

#include <wx/panel.h>
#include <wx/webview.h>
#include <wx/toolbar.h>
#include <deque>
#include <functional>
#include <map>

class wxTimer;
class wxTimerEvent;

/**
 * @brief A reusable panel that wraps wxWebView with message handling capabilities.
 * 
 * This class provides a safe wrapper around wxWebView with proper event handling
 * and JavaScript <-> C++ communication bridge via message handlers.
 */
class WEBVIEW_PANEL : public wxPanel
{
public:
    /**
     * @brief Construct a new WEBVIEW_PANEL object
     * 
     * @param aParent Parent window
     * @param aId Window ID (default: wxID_ANY)
     * @param aPos Position (default: wxDefaultPosition)
     * @param aSize Size (default: wxDefaultSize)
     */
    WEBVIEW_PANEL( wxWindow* aParent, wxWindowID aId = wxID_ANY,
                   const wxPoint& aPos = wxDefaultPosition, const wxSize& aSize = wxDefaultSize );

    /**
     * @brief Destroy the WEBVIEW_PANEL object
     * 
     * CRITICAL: Unbinds all event handlers before destruction to prevent
     * "use-after-free" crashes if the WebView backend attempts to fire
     * events during application shutdown.
     */
    virtual ~WEBVIEW_PANEL();

    /**
     * @brief Add a message handler for JavaScript communication
     * 
     * Registers a C++ callback that will be invoked when JavaScript calls
     * window.wx_msg.postMessage() with the specified handler name.
     * 
     * @param aHandlerName Name of the message handler (matches JS handler name)
     * @param aCallback Function to call when message is received from JavaScript
     */
    void AddMessageHandler( const wxString&                        aHandlerName,
                            std::function<void( const wxString& )> aCallback );

    /**
     * @brief Remove all message handlers
     */
    void ClearMessageHandlers();

    /**
     * @brief Load a URL in the WebView
     * 
     * @param aUrl URL to load
     * @param aLockNavigation If true, lock navigation to the loaded URL (default: true)
     */
    void LoadURL( const wxString& aUrl, bool aLockNavigation = true );

    /**
     * @brief Set HTML content directly
     * 
     * @param aHtml HTML content to display
     * @param aBaseUrl Base URL for relative links (default: "")
     */
    void SetPage( const wxString& aHtml, const wxString& aBaseUrl = wxEmptyString );

    /**
     * @brief Execute JavaScript asynchronously
     * 
     * @param aScript JavaScript code to execute
     */
    void RunScriptAsync( const wxString& aScript );

    /**
     * @brief Handle host cut/copy/paste commands for the focused webview
     *
     * @param aCommandId One of wxID_CUT, wxID_COPY, or wxID_PASTE
     * @return true if the command was forwarded to the webview
     */
    bool HandleEditCommand( int aCommandId );

    /**
     * @brief Bind the loaded event handler
     * 
     * Should be called after adding message handlers but before loading content.
     */
    void BindLoadedEvent();

    /**
     * @brief Check if the last load resulted in an error
     * 
     * @return true if there was a load error, false otherwise
     */
    bool HasLoadError() const { return m_loadError; }

    /**
     * @brief Set whether to handle external links within the panel
     * 
     * @param aHandle If true, external links open in the panel; if false, in system browser
     */
    void SetHandleExternalLinks( bool aHandle ) { m_handleExternalLinks = aHandle; }

    /**
     * @brief Enable the opaque KiCad IPC bridge for this webview.
     *
     * This is intended for trusted KiCad-owned pages such as the schematic AI Agent panel.
     */
    void EnableKiCadIpcBridge();

    /**
     * @brief Show or hide the webview browser
     * 
     * @param aShow If true, show the browser; if false, hide it
     */
    void ShowBrowser( bool aShow );

    /**
     * @brief Get the toolbar for external control
     * 
     * @return Pointer to the toolbar
     */
    wxToolBar* GetToolbar() { return m_toolbar; }

protected:
    /**
     * @brief Handle navigation requests
     * 
     * @param aEvt WebView navigation event
     */
    void OnNavigationRequest( wxWebViewEvent& aEvt );

    /**
     * @brief Handle page loaded event
     * 
     * Registers message handlers after the page has loaded.
     * 
     * @param aEvt WebView loaded event
     */
    void OnWebViewLoaded( wxWebViewEvent& aEvt );

    /**
     * @brief Handle new window requests
     * 
     * @param aEvt WebView new window event
     */
    void OnNewWindow( wxWebViewEvent& aEvt );

    /**
     * @brief Handle messages from JavaScript
     * 
     * @param aEvt WebView script message event
     */
    void OnScriptMessage( wxWebViewEvent& aEvt );

    /**
     * @brief Handle script execution results
     * 
     * @param aEvt WebView script result event
     */
    void OnScriptResult( wxWebViewEvent& aEvt );

    /**
     * @brief Handle loading errors
     * 
     * Displays a custom error page with diagnostic information.
     * 
     * @param aEvt WebView error event
     */
    void OnError( wxWebViewEvent& aEvt );

    /**
     * @brief Handle toolbar button clicks
     * 
     * @param aEvt Tool event
     */
    void OnToolbarClick( wxCommandEvent& aEvt );

private:
    static wxString NormalizeUrlForLock( const wxString& aUrl );
    bool            IsNavigationToLockedUrl( const wxString& aUrl ) const;
    void            ShowAgentConnectionErrorPage();
    void            RegisterBuiltInMessageHandlers();
    void            SendApiCallback( const wxString& aCallbackId, bool aIsError,
                                     const wxString& aJsonData );
    void            SendIpcCallback( const wxString& aCallbackId, bool aIsError,
                                     const wxString& aJsonData );
    void            ScheduleDeferredScriptRetry();
    void            FlushDeferredScripts();
    void            OnDeferredScriptTimer( wxTimerEvent& aEvent );

    wxWebView* m_browser; ///< The WebView browser instance
    wxToolBar* m_toolbar; ///< Toolbar with open/close buttons
    wxTimer*   m_deferredScriptTimer; ///< Timer used to retry scripts after modal dialogs close
    wxWindowID m_btnOpenId;  ///< Open button tool ID
    wxWindowID m_btnCloseId; ///< Close button tool ID
    std::deque<wxString> m_deferredScripts; ///< Scripts waiting for modal dialogs to close
    std::map<wxString, std::function<void( const wxString& )>>
            m_msgHandlers;         ///< Message handler callbacks
    bool    m_initialized;         ///< Whether handlers have been initialized
    bool    m_loadError;           ///< Whether the last load failed
    bool    m_loadedEventBound;    ///< Whether the loaded event is bound
    bool    m_handleExternalLinks; ///< Whether to handle external links in panel
    bool    m_lockNavigation;      ///< Whether to keep WebView pinned to a single URL
    bool    m_enableKiCadIpcBridge; ///< Whether to expose window.kicad.ipc
    wxString m_lockedUrl;          ///< Normalized URL allowed when navigation is locked
};

#endif // WEBVIEW_PANEL_H
