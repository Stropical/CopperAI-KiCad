/*
 * This program source code file is part of KiCad, a free EDA CAD application.
 *
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

#include <wx/aui/aui.h>
#include <wx/aui/framemanager.h>
#include <wx/bitmap.h>
#include <wx/dc.h>
#include <wx/settings.h>

#include <kiplatform/ui.h>
#include <pgm_base.h>
#include <settings/common_settings.h>
#include <widgets/wx_aui_art_providers.h>


static void drawAuiToolbarBackground( wxDC& aDc, const wxRect& aRect )
{
    aDc.SetPen( *wxTRANSPARENT_PEN );
    aDc.SetBrush( wxBrush( KIPLATFORM::UI::GetPanelBGColour() ) );
    aDc.DrawRectangle( aRect );
}


#if wxCHECK_VERSION( 3, 3, 0 )
wxSize WX_AUI_TOOLBAR_ART::GetToolSize( wxReadOnlyDC& aDc, wxWindow* aWindow,
                                        const wxAuiToolBarItem& aItem )
#else
wxSize WX_AUI_TOOLBAR_ART::GetToolSize( wxDC& aDc, wxWindow* aWindow,
                                        const wxAuiToolBarItem& aItem )
#endif
{
    // Based on the upstream wxWidgets implementation, but simplified for our application
    int size = Pgm().GetCommonSettings()->m_Appearance.toolbar_icon_size;

#ifdef __WXMSW__
    size *= KIPLATFORM::UI::GetContentScaleFactor( aWindow );
#endif

    int width = size;
    int height = size;

    if( m_flags & wxAUI_TB_TEXT )
    {
        aDc.SetFont( m_font );
        int tx, ty;

        if( m_textOrientation == wxAUI_TBTOOL_TEXT_BOTTOM )
        {
            aDc.GetTextExtent( wxT( "ABCDHgj" ), &tx, &ty );
            height += ty;

            if( !aItem.GetLabel().empty() )
            {
                aDc.GetTextExtent( aItem.GetLabel(), &tx, &ty );
                width = wxMax( width, tx + aWindow->FromDIP( 6 ) );
            }
        }
        else if( m_textOrientation == wxAUI_TBTOOL_TEXT_RIGHT && !aItem.GetLabel().empty() )
        {
            width += aWindow->FromDIP( 3 ); // space between left border and bitmap
            width += aWindow->FromDIP( 3 ); // space between bitmap and text

            if( !aItem.GetLabel().empty() )
            {
                aDc.GetTextExtent( aItem.GetLabel(), &tx, &ty );
                width += tx;
                height = wxMax( height, ty );
            }
        }
    }

    if( aItem.HasDropDown() )
    {
        int dropdownWidth = GetElementSize( wxAUI_TBART_DROPDOWN_SIZE );
        width += dropdownWidth + aWindow->FromDIP( 4 );
    }

    return wxSize( width, height );
}


void WX_AUI_TOOLBAR_ART::DrawBackground( wxDC& aDc, wxWindow* aWindow, const wxRect& aRect )
{
    drawAuiToolbarBackground( aDc, aRect );
}


void WX_AUI_TOOLBAR_ART::DrawPlainBackground( wxDC& aDc, wxWindow* aWindow, const wxRect& aRect )
{
    drawAuiToolbarBackground( aDc, aRect );
}


void WX_AUI_TOOLBAR_ART::DrawButton( wxDC& aDc, wxWindow* aWindow, const wxAuiToolBarItem& aItem,
                                     const wxRect& aRect )
{
    drawAuiToolbarBackground( aDc, aRect );

    // Taken from upstream implementation; modified to respect tool size
    wxSize bmpSize = GetToolSize( aDc, aWindow, aItem );

    int textWidth = 0, textHeight = 0;

    if( m_flags & wxAUI_TB_TEXT )
    {
        aDc.SetFont( m_font );

        int tx, ty;

        aDc.GetTextExtent( wxT( "ABCDHgj" ), &tx, &textHeight );
        textWidth = 0;
        aDc.GetTextExtent( aItem.GetLabel(), &textWidth, &ty );
    }

    int bmpX = 0, bmpY = 0;
    int textX = 0, textY = 0;

    double scale = KIPLATFORM::UI::GetPixelScaleFactor( aWindow );
    const wxBitmapBundle& bundle = ( aItem.GetState() & wxAUI_BUTTON_STATE_DISABLED )
                                   ? aItem.GetDisabledBitmapBundle()
                                   : aItem.GetBitmapBundle();
    wxBitmap bmp = bundle.GetBitmap( bmpSize * scale );

    // wxBitmapBundle::GetBitmap thinks we need this rescaled to match the base size, which we don't
    if( bmp.IsOk() )
        bmp.SetScaleFactor( scale );

    if( m_textOrientation == wxAUI_TBTOOL_TEXT_BOTTOM )
    {
        bmpX = aRect.x + ( aRect.width / 2 ) - ( bmpSize.x / 2 );

        bmpY = aRect.y + ( ( aRect.height - textHeight ) / 2 ) - ( bmpSize.y / 2 );

        textX = aRect.x + ( aRect.width / 2 ) - ( textWidth / 2 ) + 1;
        textY = aRect.y + aRect.height - textHeight - 1;
    }
    else if( m_textOrientation == wxAUI_TBTOOL_TEXT_RIGHT )
    {
        bmpX = aRect.x + aWindow->FromDIP( 3 );

        bmpY = aRect.y + ( aRect.height / 2 ) - ( bmpSize.y / 2 );

        textX = bmpX + aWindow->FromDIP( 3 ) + bmpSize.x;
        textY = aRect.y + ( aRect.height / 2 ) - ( textHeight / 2 );
    }

    bool isThemeDark = KIPLATFORM::UI::IsDarkTheme();

    if( !( aItem.GetState() & wxAUI_BUTTON_STATE_DISABLED ) )
    {
        if( aItem.GetState() & wxAUI_BUTTON_STATE_PRESSED )
        {
            aDc.SetPen( wxPen( m_highlightColour ) );
            aDc.SetBrush( wxBrush( m_highlightColour.ChangeLightness( isThemeDark ? 20 : 150 ) ) );
            aDc.DrawRectangle( aRect );
        }
        else if( ( aItem.GetState() & wxAUI_BUTTON_STATE_HOVER ) || aItem.IsSticky() )
        {
            aDc.SetPen( wxPen( m_highlightColour ) );
            aDc.SetBrush( wxBrush( m_highlightColour.ChangeLightness( isThemeDark ? 40 : 170 ) ) );

            // draw an even lighter background for checked item hovers (since
            // the hover background is the same color as the check background)
            if( aItem.GetState() & wxAUI_BUTTON_STATE_CHECKED )
                aDc.SetBrush(
                        wxBrush( m_highlightColour.ChangeLightness( isThemeDark ? 50 : 180 ) ) );

            aDc.DrawRectangle( aRect );
        }
        else if( aItem.GetState() & wxAUI_BUTTON_STATE_CHECKED )
        {
            // it's important to put this code in an else statement after the
            // hover, otherwise hovers won't draw properly for checked items
            aDc.SetPen( wxPen( m_highlightColour ) );
            aDc.SetBrush( wxBrush( m_highlightColour.ChangeLightness( isThemeDark ? 40 : 170 ) ) );
            aDc.DrawRectangle( aRect );
        }
    }

    if( bmp.IsOk() )
        aDc.DrawBitmap( bmp, bmpX, bmpY, true );

    // set the item's text color based on if it is disabled
    aDc.SetTextForeground( wxSystemSettings::GetColour( wxSYS_COLOUR_BTNTEXT ) );

    if( aItem.GetState() & wxAUI_BUTTON_STATE_DISABLED )
    {
        aDc.SetTextForeground( wxSystemSettings::GetColour( wxSYS_COLOUR_GRAYTEXT ) );
    }

    if( ( m_flags & wxAUI_TB_TEXT ) && !aItem.GetLabel().empty() )
    {
        aDc.DrawText( aItem.GetLabel(), textX, textY );
    }
}


void WX_AUI_TOOLBAR_ART::DrawSeparator( wxDC& aDc, wxWindow* aWindow, const wxRect& aRect )
{
    drawAuiToolbarBackground( aDc, aRect );

    wxRect line = aRect;
    wxColour sepColour( 70, 70, 70 );
    aDc.SetPen( wxPen( sepColour ) );

    if( aRect.height > aRect.width )
        aDc.DrawLine( aRect.GetLeft() + aRect.width / 2, aRect.GetTop() + 3,
                      aRect.GetLeft() + aRect.width / 2, aRect.GetBottom() - 3 );
    else
        aDc.DrawLine( aRect.GetLeft() + 3, aRect.GetTop() + aRect.height / 2,
                      aRect.GetRight() - 3, aRect.GetTop() + aRect.height / 2 );
}


void WX_AUI_TOOLBAR_ART::DrawGripper( wxDC& aDc, wxWindow* aWindow, const wxRect& aRect )
{
    drawAuiToolbarBackground( aDc, aRect );
}


WX_AUI_DOCK_ART::WX_AUI_DOCK_ART() : wxAuiDefaultDockArt()
{
#if defined( _WIN32 )
    // Use normal control font, wx likes to use "small"
    m_captionFont = *wxNORMAL_FONT;

    // Increase the box the caption rests in size a bit
    m_captionSize = ( wxNORMAL_FONT->GetPixelSize().y * 7 ) / 4;
#endif

    SetColour( wxAUI_DOCKART_BACKGROUND_COLOUR, KIPLATFORM::UI::GetPanelBGColour() );
    SetColour( wxAUI_DOCKART_SASH_COLOUR, wxColour( 18, 18, 18 ) );
    SetColour( wxAUI_DOCKART_BORDER_COLOUR, wxColour( 58, 58, 58 ) );
    SetColour( wxAUI_DOCKART_GRIPPER_COLOUR, wxColour( 90, 90, 90 ) );
    SetColour( wxAUI_DOCKART_ACTIVE_CAPTION_COLOUR, wxColour( 40, 40, 40 ) );
    SetColour( wxAUI_DOCKART_ACTIVE_CAPTION_GRADIENT_COLOUR, wxColour( 40, 40, 40 ) );
    SetColour( wxAUI_DOCKART_INACTIVE_CAPTION_COLOUR, wxColour( 48, 48, 48 ) );
    SetColour( wxAUI_DOCKART_INACTIVE_CAPTION_GRADIENT_COLOUR, wxColour( 48, 48, 48 ) );
    SetColour( wxAUI_DOCKART_ACTIVE_CAPTION_TEXT_COLOUR, wxColour( 245, 245, 245 ) );
    SetColour( wxAUI_DOCKART_INACTIVE_CAPTION_TEXT_COLOUR, wxColour( 245, 245, 245 ) );

    // Turn off the ridiculous looking gradient
    m_gradientType = wxAUI_GRADIENT_NONE;
}
