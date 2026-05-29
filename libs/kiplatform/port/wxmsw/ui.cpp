/*
 * This program source code file is part of KiCad, a free EDA CAD application.
 *
 * Copyright (C) 2020 Ian McInerney <Ian.S.McInerney at ieee.org>
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

#include <windows.h>

#include <kiplatform/ui.h>

#include <wx/cursor.h>
#include <wx/nonownedwnd.h>
#include <wx/window.h>
#include <wx/msw/registry.h>


bool KIPLATFORM::UI::IsDarkTheme()
{
    return true;
}


wxColour KIPLATFORM::UI::GetDialogBGColour()
{
    // Use dark background for dialogs in dark mode
    if( IsDarkTheme() )
    {
        // Dark grey background for dark mode
        return wxColour( 40, 40, 45 );
    }
    return wxSystemSettings::GetColour( wxSYS_COLOUR_BTNFACE );
}


wxColour KIPLATFORM::UI::GetPanelBGColour()
{
    // Use dark grey background for panels in dark mode to match Cursor's theme
    if( IsDarkTheme() )
    {
        // Dark grey background matching Cursor's dark theme
        return wxColour( 30, 30, 30 );
    }
    return wxSystemSettings::GetColour( wxSYS_COLOUR_WINDOW );
}


void KIPLATFORM::UI::GetInfoBarColours( wxColour& aFGColour, wxColour& aBGColour )
{
    if( IsDarkTheme() )
    {
        // Dark mode info bar colors
        aBGColour = wxColour( 50, 50, 55 );
        aFGColour = wxColour( 220, 220, 220 );
    }
    else
    {
        aBGColour = wxSystemSettings::GetColour( wxSYS_COLOUR_INFOBK );
        aFGColour = wxSystemSettings::GetColour( wxSYS_COLOUR_INFOTEXT );
    }
}


void KIPLATFORM::UI::ForceFocus( wxWindow* aWindow )
{
    aWindow->SetFocus();
}


bool KIPLATFORM::UI::IsWindowActive( wxWindow* aWindow )
{
    if(! aWindow )
    {
	    return false;
    }

    return ( aWindow->GetHWND() == GetForegroundWindow() );
}


void KIPLATFORM::UI::ReparentModal( wxNonOwnedWindow* aWindow )
{
    // Not needed on this platform
}


void KIPLATFORM::UI::FixupCancelButtonCmdKeyCollision( wxWindow *aWindow )
{
    // Not needed on this platform
}


bool KIPLATFORM::UI::IsStockCursorOk( wxStockCursor aCursor )
{
    switch( aCursor )
    {
    case wxCURSOR_BULLSEYE:
    case wxCURSOR_HAND:
    case wxCURSOR_ARROW:
        return true;
    default:
        return false;
    }
}


void KIPLATFORM::UI::LargeChoiceBoxHack( wxChoice* aChoice )
{
    // Not implemented
}


void KIPLATFORM::UI::EllipsizeChoiceBox( wxChoice* aChoice )
{
    // Not implemented
}


double KIPLATFORM::UI::GetPixelScaleFactor( const wxWindow* aWindow )
{
    return aWindow->GetContentScaleFactor();
}


double KIPLATFORM::UI::GetContentScaleFactor( const wxWindow* aWindow )
{
    return aWindow->GetDPIScaleFactor();
}


wxSize KIPLATFORM::UI::GetUnobscuredSize( const wxWindow* aWindow )
{
    return aWindow->GetClientSize();
}


void KIPLATFORM::UI::SetOverlayScrolling( const wxWindow* aWindow, bool overlay )
{
    // Not implemented
}


bool KIPLATFORM::UI::AllowIconsInMenus()
{
    return true;
}


wxPoint KIPLATFORM::UI::GetMousePosition()
{
    return wxGetMousePosition();
}


bool KIPLATFORM::UI::WarpPointer( wxWindow* aWindow, int aX, int aY )
{
    aWindow->WarpPointer( aX, aY );
    return true;
}


void KIPLATFORM::UI::ImmControl( wxWindow* aWindow, bool aEnable )
{
    if ( !aEnable )
    {
        ImmAssociateContext( aWindow->GetHWND(), NULL );
    }
    else
    {
        ImmAssociateContextEx( aWindow->GetHWND(), 0, IACE_DEFAULT );
    }
}


void KIPLATFORM::UI::ImeNotifyCancelComposition( wxWindow* aWindow )
{
    const HIMC himc = ImmGetContext( aWindow->GetHWND() );
    ImmNotifyIME( himc, NI_COMPOSITIONSTR, CPS_CANCEL, 0 );
    ImmReleaseContext( aWindow->GetHWND(), himc );
}


bool KIPLATFORM::UI::InfiniteDragPrepareWindow( wxWindow* aWindow )
{
    return true;
}


void KIPLATFORM::UI::InfiniteDragReleaseWindow()
{
    // Not needed on this platform
}


void KIPLATFORM::UI::SetFloatLevel( wxWindow* aWindow )
{
}


void KIPLATFORM::UI::FixupWebViewKeyEquivalents( wxWindow* aWebView )
{
    // Not needed on this platform
}
