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
#include <dwmapi.h>

#include <kiplatform/ui.h>

#include <wx/bmpbuttn.h>
#include <wx/button.h>
#include <wx/checkbox.h>
#include <wx/choice.h>
#include <wx/combobox.h>
#include <wx/cursor.h>
#include <wx/dataview.h>
#include <wx/dialog.h>
#include <wx/filepicker.h>
#include <wx/grid.h>
#include <wx/html/htmlwin.h>
#include <wx/listbox.h>
#include <wx/listctrl.h>
#include <wx/nonownedwnd.h>
#include <wx/notebook.h>
#include <wx/panel.h>
#include <wx/radiobut.h>
#include <wx/scrolwin.h>
#include <wx/slider.h>
#include <wx/spinctrl.h>
#include <wx/statbox.h>
#include <wx/statline.h>
#include <wx/stattext.h>
#include <wx/textctrl.h>
#include <wx/treectrl.h>
#include <wx/window.h>
#include <wx/msw/registry.h>
#include <wx/stc/stc.h>

namespace
{
enum class PREFERRED_APP_MODE
{
    Default,
    AllowDark,
    ForceDark,
    ForceLight,
    Max
};

void enableWin32DarkMenus()
{
    static bool s_done = false;

    if( s_done )
        return;

    s_done = true;

    HMODULE uxTheme = LoadLibraryW( L"uxtheme.dll" );

    if( !uxTheme )
        return;

    using REFRESH_IMMERSIVE_COLOR_POLICY_STATE = void ( WINAPI* )();
    using SET_PREFERRED_APP_MODE = PREFERRED_APP_MODE ( WINAPI* )( PREFERRED_APP_MODE );
    using FLUSH_MENU_THEMES = void ( WINAPI* )();

    auto refreshImmersiveColorPolicyState =
            reinterpret_cast<REFRESH_IMMERSIVE_COLOR_POLICY_STATE>(
                    GetProcAddress( uxTheme, MAKEINTRESOURCEA( 104 ) ) );
    auto setPreferredAppMode =
            reinterpret_cast<SET_PREFERRED_APP_MODE>( GetProcAddress( uxTheme, MAKEINTRESOURCEA( 135 ) ) );
    auto flushMenuThemes =
            reinterpret_cast<FLUSH_MENU_THEMES>( GetProcAddress( uxTheme, MAKEINTRESOURCEA( 136 ) ) );

    if( refreshImmersiveColorPolicyState )
        refreshImmersiveColorPolicyState();

    if( setPreferredAppMode )
        setPreferredAppMode( PREFERRED_APP_MODE::ForceDark );

    if( flushMenuThemes )
        flushMenuThemes();
}


void allowWindowDarkMode( HWND aHwnd )
{
    HMODULE uxTheme = GetModuleHandleW( L"uxtheme.dll" );

    if( !uxTheme )
        uxTheme = LoadLibraryW( L"uxtheme.dll" );

    if( !uxTheme )
        return;

    using ALLOW_DARK_MODE_FOR_WINDOW = BOOL ( WINAPI* )( HWND, BOOL );

    auto allowDarkModeForWindow =
            reinterpret_cast<ALLOW_DARK_MODE_FOR_WINDOW>(
                    GetProcAddress( uxTheme, MAKEINTRESOURCEA( 133 ) ) );

    if( allowDarkModeForWindow )
        allowDarkModeForWindow( aHwnd, TRUE );
}


void setDarkWindowColours( wxWindow* aWindow, const wxColour& aBg, const wxColour& aFg )
{
    aWindow->SetBackgroundColour( aBg );
    aWindow->SetOwnBackgroundColour( aBg );
    aWindow->SetForegroundColour( aFg );
    aWindow->SetOwnForegroundColour( aFg );
}
}


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


void KIPLATFORM::UI::EnableWin32DarkMode()
{
    enableWin32DarkMenus();
}


void KIPLATFORM::UI::ApplyDarkFrameTheme( wxWindow* aWindow )
{
    if( !aWindow )
        return;

    enableWin32DarkMenus();

    BOOL dark = TRUE;
    HWND hwnd = aWindow->GetHWND();

    allowWindowDarkMode( hwnd );

    // DWMWA_USE_IMMERSIVE_DARK_MODE. Attribute 20 is used on current Windows
    // builds; 19 covers older Windows 10 builds that first shipped the flag.
    DwmSetWindowAttribute( hwnd, 20, &dark, sizeof( dark ) );
    DwmSetWindowAttribute( hwnd, 19, &dark, sizeof( dark ) );
    SetWindowTheme( hwnd, L"DarkMode_Explorer", nullptr );
}


void KIPLATFORM::UI::ApplyDarkWindowTheme( wxWindow* aWindow )
{
    if( !aWindow || !IsDarkTheme() )
        return;

    ApplyDarkFrameTheme( aWindow );

    const wxColour dialogBg = GetDialogBGColour();
    const wxColour panelBg( 30, 30, 30 );
    const wxColour fieldBg( 18, 18, 18 );
    const wxColour controlBg( 28, 28, 28 );
    const wxColour border( 68, 68, 68 );
    const wxColour fg( 245, 245, 245 );
    const wxColour mutedFg( 176, 176, 176 );

    wxColour bg = panelBg;
    wxColour text = aWindow->IsEnabled() ? fg : mutedFg;

    if( dynamic_cast<wxDialog*>( aWindow ) )
    {
        bg = dialogBg;
    }
    else if( dynamic_cast<wxTextCtrl*>( aWindow ) || dynamic_cast<wxStyledTextCtrl*>( aWindow )
             || dynamic_cast<wxTreeCtrl*>( aWindow ) || dynamic_cast<wxListCtrl*>( aWindow )
             || dynamic_cast<wxListBox*>( aWindow ) || dynamic_cast<wxDataViewCtrl*>( aWindow )
             || dynamic_cast<wxGrid*>( aWindow ) || dynamic_cast<wxHtmlWindow*>( aWindow ) )
    {
        bg = fieldBg;
    }
    else if( dynamic_cast<wxNotebook*>( aWindow ) || dynamic_cast<wxChoice*>( aWindow )
             || dynamic_cast<wxComboBox*>( aWindow ) || dynamic_cast<wxSpinCtrl*>( aWindow )
             || dynamic_cast<wxSpinCtrlDouble*>( aWindow )
             || dynamic_cast<wxFilePickerCtrl*>( aWindow ) || dynamic_cast<wxButton*>( aWindow )
             || dynamic_cast<wxBitmapButton*>( aWindow ) )
    {
        bg = controlBg;
    }
    else if( dynamic_cast<wxStaticLine*>( aWindow ) )
    {
        bg = panelBg;
        text = border;
    }
    else if( dynamic_cast<wxStaticText*>( aWindow ) || dynamic_cast<wxStaticBox*>( aWindow )
             || dynamic_cast<wxCheckBox*>( aWindow ) || dynamic_cast<wxRadioButton*>( aWindow )
             || dynamic_cast<wxSlider*>( aWindow ) )
    {
        bg = panelBg;
    }
    else if( dynamic_cast<wxPanel*>( aWindow ) || dynamic_cast<wxScrolledWindow*>( aWindow ) )
    {
        bg = panelBg;
    }

    setDarkWindowColours( aWindow, bg, text );

    if( wxGrid* grid = dynamic_cast<wxGrid*>( aWindow ) )
    {
        grid->SetDefaultCellBackgroundColour( fieldBg );
        grid->SetDefaultCellTextColour( fg );
        grid->SetLabelBackgroundColour( controlBg );
        grid->SetLabelTextColour( fg );
        grid->SetGridLineColour( border );
    }
    else if( wxStyledTextCtrl* styledText = dynamic_cast<wxStyledTextCtrl*>( aWindow ) )
    {
        styledText->StyleSetBackground( wxSTC_STYLE_DEFAULT, fieldBg );
        styledText->StyleSetForeground( wxSTC_STYLE_DEFAULT, fg );
        styledText->StyleClearAll();
    }

    for( wxWindow* child : aWindow->GetChildren() )
        ApplyDarkWindowTheme( child );

    aWindow->Refresh();
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
