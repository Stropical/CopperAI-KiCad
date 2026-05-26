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

#include <kiplatform/ui.h>

#import <Cocoa/Cocoa.h>
#import <WebKit/WebKit.h>
#import <objc/runtime.h>
#import <objc/message.h>

#include <wx/nonownedwnd.h>
#include <wx/toplevel.h>
#include <wx/button.h>
#include <wx/settings.h>


bool KIPLATFORM::UI::IsDarkTheme()
{
    // Disabled for now because it appears that the wxWidgets event goes out before the
    // NSAppearance name has been updated
#ifdef NOTYET
    // It appears the wxWidgets event goes out before the NSAppearance name has been updated
    NSString *appearanceName = [[NSAppearance currentAppearance] name];
    return !![appearanceName containsString:@"Dark"];
#else
    wxColour bg = wxSystemSettings::GetColour( wxSYS_COLOUR_WINDOW );

    // Weighted W3C formula
    double brightness = ( bg.Red() / 255.0 ) * 0.299 +
                        ( bg.Green() / 255.0 ) * 0.587 +
                        ( bg.Blue() / 255.0 ) * 0.117;

    return brightness < 0.5;
#endif
}


wxColour KIPLATFORM::UI::GetDialogBGColour()
{
     wxColor bg = wxSystemSettings::GetColour( wxSYS_COLOUR_BTNFACE );

    if( KIPLATFORM::UI::IsDarkTheme() )
        bg = bg.ChangeLightness( 80 );
    else
        bg = bg.ChangeLightness( 160 );

    return bg;
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
    aFGColour = wxSystemSettings::GetColour( wxSYS_COLOUR_INFOTEXT );

    // wxWidgets hard-codes wxSYS_COLOUR_INFOBK to { 0xFF, 0xFF, 0xD3 } on Mac.
    if( KIPLATFORM::UI::IsDarkTheme() )
        aBGColour = wxColour( 28, 27, 20 );
    else
        aBGColour = wxColour( 255, 249, 189 );
}


void KIPLATFORM::UI::ForceFocus( wxWindow* aWindow )
{
    // On OSX we need to forcefully give the focus to the window
    [[aWindow->GetHandle() window] makeFirstResponder: aWindow->GetHandle()];
}


bool KIPLATFORM::UI::IsWindowActive( wxWindow* aWindow )
{
    // Just always return true
    return true;
}


void KIPLATFORM::UI::ReparentModal( wxNonOwnedWindow* aWindow )
{
    wxTopLevelWindow* parent =
            static_cast<wxTopLevelWindow*>( wxGetTopLevelParent( aWindow->GetParent() ) );

    // Quietly return if no parent is found
    if( !parent )
    {
        return;
    }

    NSWindow* parentWindow = parent->GetWXWindow();
    NSWindow* theWindow    = aWindow->GetWXWindow();

    if( parentWindow && theWindow )
    {
        [parentWindow addChildWindow:theWindow ordered:NSWindowAbove];
    }
}


void KIPLATFORM::UI::FixupCancelButtonCmdKeyCollision( wxWindow *aWindow )
{
    wxButton* button = dynamic_cast<wxButton*>( wxWindow::FindWindowById( wxID_CANCEL, aWindow ) );

    if( button )
    {
        static const wxString placeholder = wxT( "{amp}" );

        wxString buttonLabel = button->GetLabel();
        buttonLabel.Replace( wxT( "&&" ), placeholder );
        buttonLabel.Replace( wxT( "&" ), wxEmptyString );
        buttonLabel.Replace( placeholder, wxT( "&" ) );
        button->SetLabel( buttonLabel );
    }
}


bool KIPLATFORM::UI::IsStockCursorOk( wxStockCursor aCursor )
{
    switch( aCursor )
    {
    case wxCURSOR_SIZING:
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
    // Native GUI resolution on Retina displays
    return GetPixelScaleFactor( aWindow ) / 2.0;
}


wxSize KIPLATFORM::UI::GetUnobscuredSize( const wxWindow* aWindow )
{
    return wxSize( aWindow->GetSize().GetX() - wxSystemSettings::GetMetric( wxSYS_VSCROLL_X ),
                   aWindow->GetSize().GetY() - wxSystemSettings::GetMetric( wxSYS_HSCROLL_Y ) );
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
}


void KIPLATFORM::UI::ImeNotifyCancelComposition( wxWindow* aWindow )
{
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
    // On OSX we need to forcefully give the focus to the window
    [[aWindow->GetHandle() window] setLevel:NSFloatingWindowLevel];
}


// ---------------------------------------------------------------------------
// WKWebView key-equivalent fix
//
// On macOS, NSView.performKeyEquivalent: recursively walks ALL subviews in the
// window (back-to-front order).  WKWebView returns YES for Cmd+C/V/X even
// when it doesn't have focus, stealing shortcuts from the schematic canvas.
//
// Fix: find the actual WKWebView instance inside the wxWebView, get its true
// runtime class (Apple uses private subclasses), and patch performKeyEquivalent:
// on that class so it only handles the event when the webview has focus.
// ---------------------------------------------------------------------------

/// Saved original performKeyEquivalent: IMP for the patched class.
static IMP s_origWKPerformKeyEquiv = nil;

/// Focus-gated replacement for -[WKWebView performKeyEquivalent:].
static BOOL kicad_WKWebView_performKeyEquivalent( id self, SEL _cmd, NSEvent* event )
{
    NSResponder* firstResponder = [[( NSView* ) self window] firstResponder];

    // If the first responder is this WKWebView or any view inside it, let the
    // original implementation handle the event normally.
    if( [firstResponder isKindOfClass:[NSView class]]
        && [( NSView* ) firstResponder isDescendantOf:( NSView* ) self] )
    {
        return ( ( BOOL ( * )( id, SEL, NSEvent* ) ) s_origWKPerformKeyEquiv )(
                self, _cmd, event );
    }

    // WKWebView doesn't have focus — don't intercept the shortcut.
    return NO;
}


/// Recursively search an NSView hierarchy for a WKWebView instance.
static WKWebView* FindWKWebView( NSView* root )
{
    if( [root isKindOfClass:[WKWebView class]] )
        return ( WKWebView* ) root;

    for( NSView* child in [root subviews] )
    {
        WKWebView* found = FindWKWebView( child );

        if( found )
            return found;
    }

    return nil;
}


void KIPLATFORM::UI::FixupWebViewKeyEquivalents( wxWindow* aWebView )
{
    static bool applied = false;

    if( applied )
        return;

    if( !aWebView )
        return;

    NSView* nativeView = aWebView->GetHandle();

    if( !nativeView )
        return;

    // Find the actual WKWebView instance (may be nested several levels deep).
    WKWebView* wkWebView = FindWKWebView( nativeView );

    if( !wkWebView )
        return;

    // Apple uses private subclasses (class clusters).  We must patch the real
    // runtime class, not the public [WKWebView class].
    Class realClass = object_getClass( wkWebView );
    SEL   sel       = @selector( performKeyEquivalent: );
    Method method   = class_getInstanceMethod( realClass, sel );

    if( !method )
        return;

    s_origWKPerformKeyEquiv = method_getImplementation( method );
    const char* types       = method_getTypeEncoding( method );

    // Add our override to this specific runtime class.  class_addMethod only
    // touches realClass and never a superclass.
    BOOL added = class_addMethod( realClass, sel,
                                  ( IMP ) kicad_WKWebView_performKeyEquivalent, types );

    if( !added )
    {
        // realClass already defines performKeyEquivalent: — swap in-place.
        method_setImplementation( method,
                                  ( IMP ) kicad_WKWebView_performKeyEquivalent );
    }

    applied = true;
}