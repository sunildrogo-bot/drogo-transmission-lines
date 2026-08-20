"""
tower_report.py — Builds the per-tower "RGB Visual Inspection Report" PDF.

A different report from trans_report.py (which covers a whole project,
grouped by tower) — this one is for a SINGLE tower, triggered from that
tower's own panel on the map, with a specific fixed layout:

    Page 1  — Full-bleed navy header band, General Information table,
              RGB Defect Summary table.
    Page 2+ — Detailed Info, starting on a new page: 2 defects per page
              (verified — see build_tower_report_pdf's docstring), each
              with its details on the left and its marked-up image on
              the right.

Corporate visual style: dark navy (#1a2744) header bands, a gold
(#b8944f) accent rule, restrained color used only for severity text and
the section-break rule — chosen over more colorful alternatives after
reviewing three style directions together.

Uses the same libraries as chimney_report.py / trans_report.py
(ReportLab — already a dependency).
"""
import os
from io import BytesIO

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.platypus import (
    SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer, Image, PageBreak, KeepTogether
)
from reportlab.lib.enums import TA_CENTER, TA_LEFT

PAGE_W, PAGE_H = A4  # portrait

_FONT_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'vendor', 'fonts', 'DejaVuSans-Bold.ttf')
_font_cache = {}


def _overlay_font(size):
    """Bundled font (see vendor/fonts/) rather than relying on whatever's
    installed on the machine — not consistent between a Windows dev
    machine and the Linux server."""
    size = max(10, int(size))
    if size not in _font_cache:
        from PIL import ImageFont
        try:
            _font_cache[size] = ImageFont.truetype(_FONT_PATH, size)
        except Exception:
            _font_cache[size] = ImageFont.load_default()
    return _font_cache[size]


def _load_marked_image(full_path, overlays):
    """Opens the image and draws every overlay directly onto it — the
    same defect shapes (polygon/rect/circle) and thermal measurement
    markers (point/rect/line, with SP# + reading labels) the app itself
    draws over the photo, so the report shows exactly what was marked,
    not just a plain unmarked photo next to a details table.

    overlays: list of dicts — {'kind': 'defect'|'thermal', 'shape_type':
    'polygon'|'rect'|'circle'|'point'|'line', 'shape_coords': [{'x','y'}]
    in % of image bounds (0-100), 'label': optional text drawn near the
    shape's first point}.

    Returns a PIL Image (RGB), or None if the source file itself couldn't
    be opened — callers fall back to the plain (unmarked) image path in
    that case rather than failing the whole report."""
    try:
        from PIL import Image as PILImage, ImageDraw
    except Exception:
        return None
    try:
        img = PILImage.open(full_path)
        img.load()
        img = img.convert('RGB')
    except Exception:
        return None
    if not overlays:
        return img

    w, h = img.size
    draw = ImageDraw.Draw(img, 'RGBA')
    stroke_w = max(2, round(min(w, h) * 0.004))
    font_size = max(16, round(min(w, h) * 0.026))
    font = _overlay_font(font_size)
    DEFECT_COLOR = (224, 20, 20, 255)    # matches .lb-defect-shape's red in the app
    THERMAL_COLOR = (255, 206, 26, 255)  # matches .lb-thermal-point-ring's yellow in the app

    for ov in overlays:
        coords = ov.get('shape_coords') or []
        pts = [(c['x'] / 100.0 * w, c['y'] / 100.0 * h) for c in coords]
        stype = ov.get('shape_type')
        color = DEFECT_COLOR if ov.get('kind') == 'defect' else THERMAL_COLOR

        if stype == 'polygon' and len(pts) >= 3:
            draw.line(pts + [pts[0]], fill=color, width=stroke_w)
        elif stype == 'rect' and len(pts) == 2:
            (x1, y1), (x2, y2) = pts
            draw.rectangle([min(x1, x2), min(y1, y2), max(x1, x2), max(y1, y2)], outline=color, width=stroke_w)
        elif stype == 'circle' and len(pts) == 2:
            (cx, cy), (ex, ey) = pts
            r = ((ex - cx) ** 2 + (ey - cy) ** 2) ** 0.5
            draw.ellipse([cx - r, cy - r, cx + r, cy + r], outline=color, width=stroke_w)
        elif stype == 'line' and len(pts) == 2:
            draw.line(pts, fill=color, width=stroke_w)
        elif stype == 'point' and len(pts) == 1:
            cx, cy = pts[0]
            cross, ring_r = font_size * 0.5, font_size * 0.32
            lw = max(2, stroke_w - 1)
            draw.ellipse([cx - ring_r, cy - ring_r, cx + ring_r, cy + ring_r], outline=color, width=lw)
            draw.line([cx - cross, cy, cx + cross, cy], fill=color, width=lw)
            draw.line([cx, cy - cross, cx, cy + cross], fill=color, width=lw)

        label = ov.get('label')
        if label and pts:
            lx, ly = pts[0]
            tx, ty = lx + 8, max(4, ly - font_size - 6)
            for dx, dy in ((-1, -1), (1, -1), (-1, 1), (1, 1)):  # thin black outline so text reads over any background
                draw.text((tx + dx, ty + dy), label, font=font, fill=(0, 0, 0, 255))
            draw.text((tx, ty), label, font=font, fill=color)

    return img


NAVY = colors.HexColor('#1a2744')
GOLD = colors.HexColor('#b8944f')
GRAY = colors.HexColor('#5a5f68')
LIGHT_GRAY = colors.HexColor('#f4f5f7')
HAIRLINE = colors.HexColor('#d5d8de')

SEVERITY_COLORS = {
    'Minor':    colors.HexColor('#3a7d5c'),
    'Major':    colors.HexColor('#a8752f'),
    'Critical': colors.HexColor('#a83232'),
}

MARGIN = 15 * mm

DROGO_LOGO_REL_PATH = 'images/drogo_logo.png'
REPORT_LOGO_HEIGHT = 9 * mm
LOGO_TOP_GAP = 6 * mm  # space from the very top of the page to the top of the logos


def _draw_report_logos(static_root, client_logo_path):
    """Returns an onPage callback for doc.build() — Drogo Aerospace logo
    top-right on every page, and (if the project has one) the client's
    own logo top-left. Drawn on the canvas so they sit on top of the
    existing full-bleed navy header band rather than needing to rework
    that band's own layout. The Drogo logo is genuinely transparent
    (checked directly — corner pixel alpha is 0) so it's safe straight
    on the navy background; a client logo's colours are unknown, so it
    gets a small white rounded backing chip behind it for guaranteed
    contrast regardless of what colours it uses."""
    def _draw(canvas, doc):
        canvas.saveState()
        top_y = PAGE_H - LOGO_TOP_GAP - REPORT_LOGO_HEIGHT

        drogo_path = os.path.join(static_root, DROGO_LOGO_REL_PATH)
        if os.path.exists(drogo_path):
            try:
                from PIL import Image as PILImage
                with PILImage.open(drogo_path) as pil_img:
                    iw, ih = pil_img.size
                w = REPORT_LOGO_HEIGHT * (iw / float(ih))
                canvas.drawImage(drogo_path, PAGE_W - 10 * mm - w, top_y, width=w, height=REPORT_LOGO_HEIGHT,
                                  preserveAspectRatio=True, mask='auto')
            except Exception:
                pass

        if client_logo_path:
            full_client_path = os.path.join(static_root, client_logo_path)
            if os.path.exists(full_client_path):
                try:
                    from PIL import Image as PILImage
                    with PILImage.open(full_client_path) as pil_img:
                        iw, ih = pil_img.size
                    w = REPORT_LOGO_HEIGHT * (iw / float(ih))
                    chip_pad = 2 * mm
                    canvas.setFillColor(colors.white)
                    canvas.roundRect(10 * mm - chip_pad, top_y - chip_pad, w + 2 * chip_pad,
                                      REPORT_LOGO_HEIGHT + 2 * chip_pad, 2, fill=1, stroke=0)
                    canvas.drawImage(full_client_path, 10 * mm, top_y, width=w, height=REPORT_LOGO_HEIGHT,
                                      preserveAspectRatio=True, mask='auto')
                except Exception:
                    pass

        canvas.restoreState()
    return _draw


def _draw_footer(canvas, doc):
    """Page number + a thin hairline rule, bottom of every page — the
    small detail that makes a multi-page PDF read as a finished report
    rather than a stack of loose pages."""
    canvas.saveState()
    canvas.setStrokeColor(HAIRLINE)
    canvas.setLineWidth(0.5)
    canvas.line(MARGIN, 14 * mm, PAGE_W - MARGIN, 14 * mm)
    canvas.setFont('Helvetica', 8)
    canvas.setFillColor(GRAY)
    canvas.drawString(MARGIN, 9 * mm, 'Drogo Aerospace — Confidential')
    canvas.drawRightString(PAGE_W - MARGIN, 9 * mm, f'Page {canvas.getPageNumber()}')
    canvas.restoreState()


def _styles():
    styles = getSampleStyleSheet()
    styles.add(ParagraphStyle(
        name='CellText', fontSize=8, leading=10.5, alignment=TA_CENTER))
    styles.add(ParagraphStyle(
        name='CellTextBig', fontSize=13, leading=16, fontName='Helvetica-Bold', alignment=TA_CENTER))
    styles.add(ParagraphStyle(
        name='CellTextLeft', fontSize=8.5, leading=11, alignment=0))
    styles.add(ParagraphStyle(
        name='CorpTitle', fontSize=22, leading=26, fontName='Helvetica-Bold', textColor=colors.white, alignment=TA_CENTER))
    styles.add(ParagraphStyle(
        name='CorpTitleSmall', fontSize=15, leading=18, fontName='Helvetica-Bold', textColor=colors.white, alignment=TA_CENTER))
    styles.add(ParagraphStyle(
        name='CorpSubtitle', fontSize=10.5, leading=14, textColor=colors.HexColor('#c9d2e3'), alignment=TA_CENTER))
    styles.add(ParagraphStyle(
        name='SectionHead', fontSize=13, leading=16, fontName='Helvetica-Bold',
        textColor=NAVY, spaceBefore=4, spaceAfter=10))
    styles.add(ParagraphStyle(
        name='GenInfoLabel', fontSize=8.5, leading=12, fontName='Helvetica-Bold', textColor=GRAY))
    styles.add(ParagraphStyle(
        name='GenInfoValue', fontSize=9, leading=13, alignment=TA_LEFT, textColor=colors.HexColor('#333333')))
    styles.add(ParagraphStyle(
        name='DetailLabel', fontSize=8.5, leading=12, fontName='Helvetica-Bold', textColor=GRAY))
    styles.add(ParagraphStyle(
        name='DetailLabelSm', fontSize=7.5, leading=9.5, fontName='Helvetica-Bold', textColor=GRAY))
    styles.add(ParagraphStyle(
        name='DetailValue', fontSize=9, leading=13, alignment=TA_LEFT, textColor=colors.HexColor('#333333')))
    styles.add(ParagraphStyle(
        name='DetailValueSm', fontSize=7.5, leading=9.5, alignment=TA_LEFT, textColor=colors.HexColor('#333333')))
    styles.add(ParagraphStyle(
        name='CardLine', fontSize=7.8, leading=10.5, alignment=TA_LEFT, textColor=colors.HexColor('#333333')))
    return styles


def _indent(flow, width=None):
    """Wraps a flowable so it sits within the body margins — needed
    because the header band below is deliberately full-bleed (edge to
    edge), which means the document's own margins can't just be set
    globally without also indenting the header."""
    w = width if width is not None else (PAGE_W - 2 * MARGIN)
    t = Table([[flow]], colWidths=[w])
    t.setStyle(TableStyle([
        ('LEFTPADDING', (0, 0), (-1, -1), 0), ('RIGHTPADDING', (0, 0), (-1, -1), 0),
        ('TOPPADDING', (0, 0), (-1, -1), 0), ('BOTTOMPADDING', (0, 0), (-1, -1), 0),
    ]))
    return t


def _header_band(tower_id, styles, title='RGB TOWER INSPECTION REPORT', small=False, show_subtitle=True):
    """The navy title band. Used both as the report's main title (page 1,
    full-size, with the DROGO AEROSPACE/Tower subtitle) and as a shorter,
    title-only divider band re-announcing the report before the Thermal
    section starts (small=True, show_subtitle=False) — same look, just
    noticeably less vertical padding and no subtitle line."""
    rows = [[Paragraph(title, styles['CorpTitle'] if not small else styles['CorpTitleSmall'])]]
    if show_subtitle:
        rows.append([Paragraph(f'DROGO AEROSPACE &nbsp;·&nbsp; Tower {tower_id}', styles['CorpSubtitle'])])
    content = Table(rows, colWidths=[PAGE_W - 2 * MARGIN])
    band = Table([[content]], colWidths=[PAGE_W])
    band.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, -1), NAVY),
        ('LINEBELOW', (0, 0), (-1, -1), 2, GOLD),
        ('LEFTPADDING', (0, 0), (-1, -1), MARGIN),
        ('TOPPADDING', (0, 0), (-1, -1), 2.5 * mm if small else 3.5 * mm),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 3 * mm if small else 4 * mm),
    ]))
    return band


def _general_info_table(info, styles):
    rows = [
        ['Line Name', info['line_name']],
        ['Tower ID', info['tower_id']],
        ['Voltage Level', info['voltage_level'] or '—'],
        ['Coordinates', info['coordinates']],
        ['Survey Date', info['survey_date'] or '—'],
        ['Pilot Name', info['pilot_name'] or '—'],
        ['Inspection Name', info['inspection_name'] or '—'],
        ['Report Generation Date', info['report_date']],
    ]
    table = Table(
        [[Paragraph(k, styles['GenInfoLabel']), Paragraph(str(v), styles['GenInfoValue'])]
         for k, v in rows],
        colWidths=[48 * mm, (PAGE_W - 2 * MARGIN) - 48 * mm],
    )
    table.setStyle(TableStyle([
        ('TOPPADDING', (0, 0), (-1, -1), 6),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 6),
        ('LINEBELOW', (0, 0), (-1, -1), 0.4, HAIRLINE),
        ('LEFTPADDING', (0, 0), (-1, -1), 0),
    ]))
    return table


def _inspection_list_table(info, styles):
    """Admin / Pilot / SME sign-off — who touched this tower's inspection
    and when. Rows with no name on file still show (as '—') rather than
    disappearing, so the table's shape stays consistent whether or not
    every role has been filled in yet."""
    rows = [
        ['1', info.get('admin_name') or '—', 'Admin', info.get('report_date') or '—'],
        ['2', info.get('pilot_name') or '—', 'Pilot', info.get('survey_date') or '—'],
        ['3', info.get('sme_name') or '—', 'SME', info.get('sme_date') or '—'],
    ]
    header = ['S.No', 'Name', 'Qualification', 'Date']
    col_widths = [16 * mm, 60 * mm, 40 * mm, (PAGE_W - 2 * MARGIN) - 116 * mm]
    data = [header] + [[Paragraph(c, styles['CellText']) for c in row] for row in rows]
    table = Table(data, colWidths=col_widths, repeatRows=1)
    table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), NAVY),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 0), (-1, 0), 8),
        ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('GRID', (0, 0), (-1, -1), 0.4, HAIRLINE),
        ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, LIGHT_GRAY]),
        ('TOPPADDING', (0, 0), (-1, -1), 6),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 6),
    ]))
    return table


TOWER_VIEW_IMG_W = 100 * mm
TOWER_VIEW_IMG_H = 68 * mm


def _tower_view_block(info, static_root, styles):
    """Cover photo + a simple prev/current/next tower position strip
    beside it — a lighter version of the arrow-diagram idea (no absolute-
    position overlay on the image itself, which ReportLab doesn't do as
    naturally as CSS does; a clean side column reads just as clearly)."""
    img_cell = Paragraph('No image available', styles['CellText'])
    image_path = info.get('cover_image_path') or ''
    if image_path:
        full_path = os.path.join(static_root, image_path)
        if os.path.exists(full_path):
            try:
                from PIL import Image as PILImage
                with PILImage.open(full_path) as pil_img:
                    pil_img.verify()
                with PILImage.open(full_path) as pil_img:
                    iw, ih = pil_img.size
                aspect = ih / float(iw) if iw else 0.75
                img_w, img_h = TOWER_VIEW_IMG_W, TOWER_VIEW_IMG_W * aspect
                if img_h > TOWER_VIEW_IMG_H:
                    img_h = TOWER_VIEW_IMG_H
                    img_w = img_h / aspect
                img_cell = Image(full_path, width=img_w, height=img_h)
            except Exception:
                img_cell = Paragraph('Image unavailable', styles['CellText'])

    prev_t = info.get('prev_tower') or '—'
    next_t = info.get('next_tower') or '—'
    this_t = info.get('tower_id') or '—'
    position_rows = [
        [Paragraph(f'<font color="{GRAY.hexval()}">▲ Next</font>', styles['CellText'])],
        [Paragraph(f'<b>{next_t}</b>', styles['CellText'])],
        [Paragraph(f'<font color="{GOLD.hexval()}"><b>{this_t}</b></font>', styles['CellTextBig'])],
        [Paragraph(f'<b>{prev_t}</b>', styles['CellText'])],
        [Paragraph(f'<font color="{GRAY.hexval()}">▼ Previous</font>', styles['CellText'])],
    ]
    position_col = Table(position_rows, colWidths=[45 * mm])
    position_col.setStyle(TableStyle([
        ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('TOPPADDING', (0, 0), (-1, -1), 3), ('BOTTOMPADDING', (0, 0), (-1, -1), 3),
        ('BOX', (0, 2), (-1, 2), 0.5, HAIRLINE),
        ('TOPPADDING', (0, 2), (-1, 2), 6), ('BOTTOMPADDING', (0, 2), (-1, 2), 6),
    ]))

    outer = Table([[img_cell, position_col]], colWidths=[TOWER_VIEW_IMG_W + 10, (PAGE_W - 2 * MARGIN) - TOWER_VIEW_IMG_W - 10])
    outer.setStyle(TableStyle([
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('ALIGN', (0, 0), (0, 0), 'CENTER'),
        ('LEFTPADDING', (0, 0), (-1, -1), 0), ('RIGHTPADDING', (0, 0), (-1, -1), 0),
    ]))

    caption = Paragraph(
        f"Tower {this_t} is positioned between the previous tower {prev_t} and the next tower {next_t}.",
        styles['CellTextLeft'],
    )
    wrapper = Table([[outer], [Spacer(1, 3 * mm)], [caption]], colWidths=[PAGE_W - 2 * MARGIN])
    wrapper.setStyle(TableStyle([
        ('LEFTPADDING', (0, 0), (-1, -1), 0), ('RIGHTPADDING', (0, 0), (-1, -1), 0),
        ('TOPPADDING', (0, 0), (-1, -1), 0), ('BOTTOMPADDING', (0, 0), (-1, -1), 0),
    ]))
    return wrapper


def _defect_summary_table(defects, styles):
    header = ['S.No', 'Defect ID', 'Component Name', 'Defect Type', 'Severity', 'Status']
    col_widths = [14 * mm, 22 * mm, 44 * mm, 44 * mm, 24 * mm, 22 * mm]
    data = [header]
    for i, d in enumerate(defects, start=1):
        sev = d.get('severity') or 'Minor'
        sev_color = SEVERITY_COLORS.get(sev, colors.HexColor('#333333'))
        sev_para = Paragraph(f'<font color="{sev_color.hexval()}"><b>{sev}</b></font>', styles['CellText'])
        data.append([
            Paragraph(str(i), styles['CellText']),
            Paragraph(f"D{i}", styles['CellText']),
            Paragraph(d.get('component_name') or '—', styles['CellText']),
            Paragraph(d.get('defect_type') or '—', styles['CellText']),
            sev_para,
            Paragraph(d.get('status') or 'OK', styles['CellText']),
        ])
    table = Table(data, colWidths=col_widths, repeatRows=1)
    table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), NAVY),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 0), (-1, 0), 8),
        ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('GRID', (0, 0), (-1, -1), 0.4, HAIRLINE),
        ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, LIGHT_GRAY]),
        ('TOPPADDING', (0, 0), (-1, -1), 6),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 6),
    ]))
    return table


def _thermal_summary_table(thermal_photos, styles):
    header = ['S.No', 'Photo', 'Measurements', 'Lowest', 'Highest']
    col_widths = [14 * mm, 30 * mm, 30 * mm, 45 * mm, 51 * mm]
    data = [header]
    for i, tp in enumerate(thermal_photos, start=1):
        temps = [pt['avg_c'] for pt in tp['points'] if pt.get('avg_c') is not None]
        lowest = f'{min(temps):.1f}°C' if temps else '—'
        highest = f'{max(temps):.1f}°C' if temps else '—'
        data.append([
            Paragraph(str(i), styles['CellText']),
            Paragraph(f"T{i}", styles['CellText']),
            Paragraph(str(len(tp['points'])), styles['CellText']),
            Paragraph(lowest, styles['CellText']),
            Paragraph(highest, styles['CellText']),
        ])
    table = Table(data, colWidths=col_widths, repeatRows=1)
    table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), NAVY),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 0), (-1, 0), 8),
        ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('GRID', (0, 0), (-1, -1), 0.4, HAIRLINE),
        ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, LIGHT_GRAY]),
        ('TOPPADDING', (0, 0), (-1, -1), 6),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 6),
    ]))
    return table


# Grid cards: several photos per page (2 columns x 2 rows = 4), each
# compact — image on top, its details/measurements below — instead of one
# photo taking most/all of a page. Also fixes photo duplication: RGB
# cards are built per PHOTO (grouping every defect marked on that same
# photo into one card), not per defect, so a photo with 2+ defects on it
# shows once, not once per defect.
GRID_COLS = 1
GRID_ROWS_PER_PAGE = 1
CARD_GAP = 6 * mm
CARD_W = ((PAGE_W - 2 * MARGIN) - (GRID_COLS - 1) * CARD_GAP) / GRID_COLS
CARD_IMG_H = 110 * mm

# Defect card split: details on the left (wider — it's holding a full
# field table), a tight image on the right (not stretched to fill).
LEFT_COL_W = CARD_W * 0.40 - 16
RIGHT_COL_W = CARD_W * 0.60 - 16


def _card_image(image_path, static_root, styles, max_h=None, col_w=None):
    max_h = max_h if max_h is not None else CARD_IMG_H
    col_w = col_w if col_w is not None else (CARD_W - 12)
    if not image_path:
        return Paragraph('No image available', styles['CellText'])
    full_path = os.path.join(static_root, image_path)
    if not os.path.exists(full_path):
        return Paragraph('No image available', styles['CellText'])
    try:
        from PIL import Image as PILImage
        with PILImage.open(full_path) as pil_img:
            pil_img.verify()
        with PILImage.open(full_path) as pil_img:
            iw, ih = pil_img.size
        aspect = ih / float(iw) if iw else 0.75
        img_w = col_w
        img_h = img_w * aspect
        if img_h > max_h:
            img_h = max_h
            img_w = img_h / aspect
        return Image(full_path, width=img_w, height=img_h)
    except Exception:
        return Paragraph('Image unavailable', styles['CellText'])


def _card_image_cover(image_path, static_root, styles, box_w, box_h, overlays=None):
    """Fills the box completely — no letterboxing/empty gaps — by
    center-cropping the source image to the box's own aspect ratio first
    (same idea as CSS object-fit: cover), then scaling that crop to
    exactly box_w x box_h. Falls back to the plain fit-inside version if
    anything goes wrong reading/cropping the file.

    overlays (optional): passed straight to _load_marked_image — the
    marked defect shape / thermal measurement marker(s) get drawn onto
    the image BEFORE the cover-crop, so what's cropped/shown already has
    them on it, positioned correctly since they're both in the same %-
    of-original-image-bounds coordinate space."""
    if not image_path:
        return Paragraph('No image available', styles['CellText'])
    full_path = os.path.join(static_root, image_path)
    if not os.path.exists(full_path):
        return Paragraph('No image available', styles['CellText'])
    try:
        from io import BytesIO as _BytesIO
        pil_img = _load_marked_image(full_path, overlays or [])
        if pil_img is None:
            raise ValueError('could not open source image')
        iw, ih = pil_img.size
        target_aspect = box_h / float(box_w)
        src_aspect = ih / float(iw)
        if src_aspect > target_aspect:
            # source is relatively taller than the box — crop top/bottom
            crop_h = int(iw * target_aspect)
            top = (ih - crop_h) // 2
            box = (0, top, iw, top + crop_h)
        else:
            # source is relatively wider than the box — crop left/right
            crop_w = int(ih / target_aspect)
            left = (iw - crop_w) // 2
            box = (left, 0, left + crop_w, ih)
        cropped = pil_img.crop(box)
        buf = _BytesIO()
        cropped.save(buf, format='JPEG', quality=88)
        buf.seek(0)
        return Image(buf, width=box_w, height=box_h)
    except Exception:
        return _card_image(image_path, static_root, styles, max_h=box_h, col_w=box_w)


# Each defect/thermal-photo block on a detail page gets EXACTLY a third of
# the page's vertical space, computed from the more constrained case (a
# page that also carries the section heading) and applied uniformly — so
# 3 blocks always fill the full page, on every page, heading or not.
_HEADING_BLOCK_H = 16 + 4 + 10 + (2 * mm)  # SectionHead leading/spaceBefore/spaceAfter + the Spacer after it
_TOP_MARGIN = LOGO_TOP_GAP + REPORT_LOGO_HEIGHT + 4 * mm
_BOTTOM_MARGIN = 20 * mm
_AVAILABLE_WITH_HEADING = PAGE_H - _TOP_MARGIN - _HEADING_BLOCK_H - _BOTTOM_MARGIN
# Small breathing room between stacked cards on the same page — the
# block height itself shrinks slightly to make room for it (2 gaps
# between 3 cards), so 3 cards + 2 gaps still fill the page exactly.
CARD_GAP_V = 3 * mm
THIRD_PAGE_BLOCK_H = (_AVAILABLE_WITH_HEADING - 2 * CARD_GAP_V) / 3 - (0.5 * mm)


def _defect_card(defect, static_root, styles):
    """One defect = one card, own image every time (even if 2 defects
    share the same source photo — each gets its own copy here, not
    deduped), sized to exactly half a page so 2 cards always fill the
    page fully. Left column: a clean bordered field table. Right column:
    the image, center-cropped to completely fill its box — no empty
    gaps around it."""
    sev = defect.get('severity') or 'Minor'
    sev_color = SEVERITY_COLORS.get(sev, colors.HexColor('#333333'))
    field_rows = [
        ['Defect ID', defect['label']],
        ['Component Name', defect.get('component_name') or '—'],
        ['Defect Type', defect.get('defect_type') or '—'],
        ['Location', defect.get('location') or '—'],
        ['Severity', f'<font color="{sev_color.hexval()}"><b>{sev}</b></font>'],
        ['Status', defect.get('status') or 'OK'],
    ]
    if defect.get('comments'):
        field_rows.append(['Comments', defect['comments']])
    field_table = Table(
        [[Paragraph(k, styles['DetailLabel']), Paragraph(str(v), styles['DetailValue'])] for k, v in field_rows],
        colWidths=[30 * mm, LEFT_COL_W - 30 * mm],
    )
    field_table.setStyle(TableStyle([
        ('TOPPADDING', (0, 0), (-1, -1), 5), ('BOTTOMPADDING', (0, 0), (-1, -1), 5),
        ('LEFTPADDING', (0, 0), (-1, -1), 6), ('RIGHTPADDING', (0, 0), (-1, -1), 6),
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('LINEBELOW', (0, 0), (-1, -2), 0.4, HAIRLINE),  # thin row dividers only — the outer card box is the border
        ('BACKGROUND', (0, 0), (0, -1), LIGHT_GRAY),
    ]))

    img_box_w, img_box_h = RIGHT_COL_W + 16, THIRD_PAGE_BLOCK_H
    overlays = [{
        'kind': 'defect',
        'shape_type': defect.get('shape_type'),
        'shape_coords': defect.get('shape_coords') or [],
        'label': defect['label'],
    }] if defect.get('shape_type') and defect.get('shape_coords') else []
    img = _card_image_cover(defect.get('image_path', ''), static_root, styles, img_box_w, img_box_h, overlays)

    outer = Table([[field_table, img]], colWidths=[LEFT_COL_W + 16, RIGHT_COL_W + 16], rowHeights=[THIRD_PAGE_BLOCK_H])
    outer.setStyle(TableStyle([
        ('BOX', (0, 0), (-1, -1), 0.5, HAIRLINE),
        ('LINEAFTER', (0, 0), (0, 0), 0.4, HAIRLINE),
        ('VALIGN', (0, 0), (0, 0), 'TOP'),
        ('VALIGN', (1, 0), (1, 0), 'MIDDLE'),
        ('ALIGN', (1, 0), (1, 0), 'CENTER'),
        ('LEFTPADDING', (0, 0), (0, 0), 10), ('RIGHTPADDING', (0, 0), (0, 0), 10),
        ('LEFTPADDING', (1, 0), (1, 0), 0), ('RIGHTPADDING', (1, 0), (1, 0), 0),
        ('TOPPADDING', (0, 0), (0, 0), 10), ('BOTTOMPADDING', (0, 0), (0, 0), 10),
        ('TOPPADDING', (1, 0), (1, 0), 0), ('BOTTOMPADDING', (1, 0), (1, 0), 0),
    ]))
    return outer


def _thermal_card(thermal_photo, seq_no, static_root, styles):
    """One thermal photo + all its SP measurements, sized to exactly half
    a page (same rule as _defect_card — 2 per page, full page filled;
    same clean bordered table + full-bleed cropped image treatment)."""
    type_label = {'point': 'Point', 'rect': 'Rect', 'line': 'Line'}
    sp_rows = [['Photo', f'T{seq_no}']]
    for i, pt in enumerate(thermal_photo['points'], start=1):
        label = type_label.get(pt.get('shape_type'), pt.get('shape_type'))
        if pt.get('avg_c') is None:
            value = 'N/A'
        elif pt.get('min_c') == pt.get('max_c'):
            value = f"{pt['avg_c']:.1f}°C"
        else:
            value = f"{pt['avg_c']:.1f}°C ({pt['min_c']:.1f}–{pt['max_c']:.1f})"
        sp_rows.append([f'SP{i} ({label})', value])

    sp_table = Table(
        [[Paragraph(k, styles['DetailLabel']), Paragraph(str(v), styles['DetailValue'])] for k, v in sp_rows],
        colWidths=[36 * mm, LEFT_COL_W - 36 * mm],
    )
    sp_table.setStyle(TableStyle([
        ('TOPPADDING', (0, 0), (-1, -1), 5), ('BOTTOMPADDING', (0, 0), (-1, -1), 5),
        ('LEFTPADDING', (0, 0), (-1, -1), 6), ('RIGHTPADDING', (0, 0), (-1, -1), 6),
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('LINEBELOW', (0, 0), (-1, -2), 0.4, HAIRLINE),  # thin row dividers only — the outer card box is the border
        ('BACKGROUND', (0, 0), (0, -1), LIGHT_GRAY),
    ]))

    img_box_w, img_box_h = RIGHT_COL_W + 16, THIRD_PAGE_BLOCK_H
    overlays = [
        {
            'kind': 'thermal',
            'shape_type': pt.get('shape_type'),
            'shape_coords': pt.get('shape_coords') or [],
            'label': f'SP{i}',
        }
        for i, pt in enumerate(thermal_photo['points'], start=1)
        if pt.get('shape_type') and pt.get('shape_coords')
    ]
    img = _card_image_cover(thermal_photo['image_path'], static_root, styles, img_box_w, img_box_h, overlays)

    outer = Table([[sp_table, img]], colWidths=[LEFT_COL_W + 16, RIGHT_COL_W + 16], rowHeights=[THIRD_PAGE_BLOCK_H])
    outer.setStyle(TableStyle([
        ('BOX', (0, 0), (-1, -1), 0.5, HAIRLINE),
        ('LINEAFTER', (0, 0), (0, 0), 0.4, HAIRLINE),
        ('VALIGN', (0, 0), (0, 0), 'TOP'),
        ('VALIGN', (1, 0), (1, 0), 'MIDDLE'),
        ('ALIGN', (1, 0), (1, 0), 'CENTER'),
        ('LEFTPADDING', (0, 0), (0, 0), 10), ('RIGHTPADDING', (0, 0), (0, 0), 10),
        ('LEFTPADDING', (1, 0), (1, 0), 0), ('RIGHTPADDING', (1, 0), (1, 0), 0),
        ('TOPPADDING', (0, 0), (0, 0), 10), ('BOTTOMPADDING', (0, 0), (0, 0), 10),
        ('TOPPADDING', (1, 0), (1, 0), 0), ('BOTTOMPADDING', (1, 0), (1, 0), 0),
    ]))
    return outer


def _paged_flowables(cards, per_page=3):
    """Stacks cards in a single column, forcing a PageBreak after every
    Nth card (3 by default), with a small gap between cards that share a
    page. Since each card is a fixed THIRD_PAGE_BLOCK_H tall (already
    sized to leave room for those gaps), 3 cards + 2 gaps fill the page
    exactly rather than just usually fitting. Each card is wrapped in
    KeepTogether so it never splits awkwardly across a page boundary."""
    out = []
    total = len(cards)
    for i, card in enumerate(cards, start=1):
        out.append(KeepTogether([_indent(card, width=CARD_W)]))
        ends_page = (i % per_page == 0) or (i == total)
        if not ends_page:
            out.append(Spacer(1, CARD_GAP_V))
        if i % per_page == 0 and i != total:
            out.append(PageBreak())
    return out


def build_tower_report_pdf(info, defects, static_root, client_logo_path='', thermal_photos=None, inspection_types=None):
    """Return a BytesIO containing the finished PDF.

    info: dict with line_name, tower_id, voltage_level, coordinates,
          survey_date, pilot_name, inspection_name, report_date.
    defects: list of dicts (component_name, defect_type, location,
             severity, status, observation, comments, image_path).
    thermal_photos: optional list of dicts (image_path, points — each
             point a dict with shape_type/avg_c/min_c/max_c/error), one
             entry per thermal photo that has at least one measurement.
    client_logo_path: optional, relative to static_root — the project's
             own logo, shown top-left on every page.
    inspection_types: which of 'rgb'/'thermal' this project actually
             does — some clients only want one or the other. Missing/None
             means both (matches every report built before this existed).
             Section numbering renumbers itself to whatever's actually
             included, so an RGB-only report doesn't have a gap where
             "3. Thermal..." would have been.

    Detail blocks flow naturally (each kept together on one page via
    KeepTogether, but multiple can share a page if they fit, and a short
    report doesn't leave a large empty gap the way a fixed "2 per page"
    layout did) rather than always claiming exactly half a page each.
    """
    thermal_photos = thermal_photos or []
    inspection_types = inspection_types or ['rgb', 'thermal']
    include_rgb = 'rgb' in inspection_types
    include_thermal = 'thermal' in inspection_types

    buf = BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=A4,
        leftMargin=0, rightMargin=0, topMargin=LOGO_TOP_GAP + REPORT_LOGO_HEIGHT + 4 * mm, bottomMargin=20 * mm,
        title=f"Tower {info['tower_id']} — Inspection Report",
    )
    styles = _styles()
    story = []

    if include_rgb and include_thermal:
        main_title = 'TOWER INSPECTION REPORT'
    elif include_rgb:
        main_title = 'RGB TOWER INSPECTION REPORT'
    else:
        main_title = 'THERMAL TOWER INSPECTION REPORT'

    # White space for the logos is now reserved automatically on EVERY
    # page via doc's topMargin (set above) — logos are drawn via canvas
    # (_draw_report_logos) at a matching fixed position, so this stays
    # consistent whether a page starts with an explicit section break or
    # content just happens to flow onto a new page naturally.
    story.append(_header_band(info['tower_id'], styles, title=main_title))
    story.append(Spacer(1, 10 * mm))

    section_no = 1
    story.append(_indent(Paragraph(f'{section_no}. General Information', styles['SectionHead'])))
    story.append(_indent(_general_info_table(info, styles)))
    story.append(Spacer(1, 6 * mm))
    section_no += 1

    if include_rgb:
        story.append(_indent(Paragraph(f'{section_no}. RGB Defect Summary', styles['SectionHead'])))
        if defects:
            story.append(_indent(_defect_summary_table(defects, styles)))
        else:
            story.append(_indent(Paragraph('No defects have been marked on this tower yet.', styles['CellTextLeft'])))
        story.append(Spacer(1, 6 * mm))
        section_no += 1

    if include_thermal:
        # When both are included, Thermal Summary follows RGB Summary
        # directly — both quick-reference tables together, before
        # either's detailed (image-heavy) evidence.
        story.append(_indent(Paragraph(f'{section_no}. Thermal Defect Summary', styles['SectionHead'])))
        if thermal_photos:
            story.append(_indent(_thermal_summary_table(thermal_photos, styles)))
        else:
            story.append(_indent(Paragraph('No thermal measurements have been taken on this tower yet.', styles['CellTextLeft'])))
        story.append(Spacer(1, 6 * mm))
        section_no += 1

    if include_rgb and defects:
        story.append(PageBreak())
        story.append(_indent(Paragraph(f'{section_no}. RGB Defect Details', styles['SectionHead'])))
        story.append(Spacer(1, 2 * mm))

        # One card PER DEFECT — each gets its own image (even if 2
        # defects share the same source photo, that photo appears twice
        # here, once per defect), 3 cards per page filling it exactly,
        # with a small gap between each so they don't sit flush together.
        cards = []
        for i, d in enumerate(defects, start=1):
            entry = dict(d)
            entry['label'] = f'D{i}'
            cards.append(_defect_card(entry, static_root, styles))
        story.extend(_paged_flowables(cards, per_page=3))
        section_no += 1

    if include_thermal and thermal_photos:
        story.append(PageBreak())
        if include_rgb:
            # A compact, title-only divider (no subtitle line) marks the
            # start of thermal's own detailed evidence, right where the
            # report shifts from RGB images to thermal images — only
            # needed when RGB content came before it in this same report.
            story.append(_header_band(info['tower_id'], styles, title='THERMAL INSPECTION REPORT', small=True, show_subtitle=False))
            story.append(Spacer(1, 6 * mm))
        story.append(_indent(Paragraph(f'{section_no}. Thermal Details', styles['SectionHead'])))
        story.append(Spacer(1, 2 * mm))

        cards = [_thermal_card(tp, i, static_root, styles) for i, tp in enumerate(thermal_photos, start=1)]
        story.extend(_paged_flowables(cards, per_page=3))
        section_no += 1

    # Logos now sit in plain white space above the navy band on EVERY
    # page (including page 1 — see the Spacer before _header_band above),
    # so the same canvas draw is used consistently everywhere instead of
    # page 1 getting a different (flowable-based) mechanism than the rest.
    def _on_every_page(c, d):
        _draw_report_logos(static_root, client_logo_path)(c, d)
        _draw_footer(c, d)
    doc.build(story, onFirstPage=_on_every_page, onLaterPages=_on_every_page)
    buf.seek(0)
    return buf
