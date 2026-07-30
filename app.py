import streamlit as st
import pandas as pd
import re
import io
import zipfile
import pypdf
import xml.sax.saxutils as saxutils

from reportlab.lib.pagesizes import A4
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, Flowable
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib import colors

st.set_page_config(page_title="Run Sheet Route Optimizer", layout="centered")

st.title("🚚 Poster Run Sheet Route Optimizer")
st.write("Upload your **Master Territory CSV** and your **PDF Run Sheets** to automatically re-order and format them by route sequence.")

st.subheader("1. Upload Master Route CSV")
csv_file = st.file_uploader("Choose your Master Route Plan CSV", type=["csv"])

st.subheader("2. Upload Weekly PDF Run Sheets")
pdf_files = st.file_uploader("Choose your PDF run sheet(s)", type=["pdf"], accept_multiple_files=True)


def extract_pdf_text(uploaded_file):
    uploaded_file.seek(0)
    reader = pypdf.PdfReader(io.BytesIO(uploaded_file.read()))
    full_text = "\n".join([page.extract_text() or "" for page in reader.pages])
    return full_text


def get_mapping_for_pdf(df, pdf_filename, pdf_text):
    territory_col = None
    for col in df.columns:
        if str(col).strip().lower() in ['zone', 'territory', 'area', 'region', 'sheet']:
            territory_col = col
            break
            
    if territory_col:
        first_line = pdf_text.strip().split('\n')[0] if pdf_text else ""
        unique_territories = df[territory_col].dropna().unique()
        
        matched_t = None
        for t in unique_territories:
            t_str = str(t).strip()
            territory_identifier = t_str.split()[-1].lower() # e.g. "a", "b", "c", "cbd1"
            
            pdf_norm = re.sub(r'[^a-z0-9]', ' ', pdf_filename.lower())
            first_norm = re.sub(r'[^a-z0-9]', ' ', first_line.lower())
            
            if (f"wellington {territory_identifier}" in pdf_norm or 
                f"territory {territory_identifier}" in pdf_norm or 
                f" {territory_identifier} " in f" {pdf_norm} " or
                t_str.lower() in pdf_filename.lower() or 
                t_str.lower() in first_line.lower()):
                matched_t = t
                break
                
        if matched_t:
            st.info(f"📍 Matched zone **'{matched_t}'** for `{pdf_filename}`")
            filtered_df = df[df[territory_col] == matched_t]
            return dict(zip(filtered_df['code'].astype(str).str.strip(), filtered_df['Coding']))
            
    return dict(zip(df['code'].astype(str).str.strip(), df['Coding']))


def find_csv_code(raw_code, csv_mapping):
    raw_code = raw_code.strip()
    if raw_code in csv_mapping:
        return raw_code, csv_mapping[raw_code]
    for csv_c in csv_mapping:
        if csv_c.replace(" ", "") == raw_code.replace(" ", ""):
            return csv_c, csv_mapping[csv_c]
        base_raw = raw_code.split()[0]
        base_csv = csv_c.split()[0]
        if base_raw == base_csv:
            return csv_c, csv_mapping[csv_c]
    return None, 9999


def parse_and_sort_pdf(pdf_text, csv_mapping):
    lines = pdf_text.strip().split('\n')
    headers_found = []
    
    # Line-anchored regex: avoids matching job artwork references (like ABU096903) in the middle of notes
    line_start_site_pattern = re.compile(r'^(?:\|\s*)?\b([A-HJ-Z][A-Z]{1,3}\d+[\d\.]*(?:\s*\([A-Z\s]+\))?)')
    
    for i, line in enumerate(lines):
        match = line_start_site_pattern.search(line)
        if match:
            headers_found.append((i, line, match.group(1)))

    blocks = []
    for i in range(len(headers_found)):
        start_line = headers_found[i][0]
        end_line = headers_found[i+1][0] if i + 1 < len(headers_found) else len(lines)
        
        site_code_raw = headers_found[i][2]
        matched_code, coding = find_csv_code(site_code_raw, csv_mapping)
        
        block_lines = lines[start_line:end_line]
        blocks.append({
            'site_code': site_code_raw,
            'coding': coding,
            'block_lines': block_lines
        })

    sorted_blocks = sorted(blocks, key=lambda x: x['coding'])
    return sorted_blocks


def parse_job_block(lines):
    first_line = lines[0]
    match = re.match(r'^([A-Z]{3,5}\d{5,8})(.*)', first_line)
    job_id = match.group(1) if match else ""
    rest_first = match.group(2).strip() if match else first_line
    
    media = ""
    notes = []
    action = "INSTALL"
    size = ""
    qty = "1"
    title_parts = [rest_first] if rest_first else []
    
    for l in lines[1:]:
        l_str = l.strip()
        if l_str.lower().startswith("media:"):
            media = l_str
        elif l_str.lower().startswith("note:"):
            notes.append(l_str)
        elif "cover up required" in l_str.lower():
            notes.append(l_str)
        elif re.match(r'^(install|maintain)\b', l_str, re.IGNORECASE):
            parts = l_str.split()
            action = parts[0].upper()
            if len(parts) >= 2:
                size = parts[1]
            if len(parts) >= 3:
                qty = parts[2]
        else:
            title_parts.append(l_str)
            
    campaign_title = " ".join(title_parts)
    note_text = " | ".join(notes)
    
    return {
        'job_id': job_id,
        'title': campaign_title,
        'media': media,
        'note': note_text,
        'action': action,
        'size': size,
        'qty': qty
    }


def generate_reportlab_pdf(sorted_blocks, pdf_filename):
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        rightMargin=25,
        leftMargin=25,
        topMargin=25,
        bottomMargin=25
    )
    styles = getSampleStyleSheet()

    title_style = ParagraphStyle(
        'DocTitle',
        parent=styles['Heading1'],
        fontSize=13,
        leading=16,
        fontName='Helvetica-Bold',
        textColor=colors.HexColor('#0F172A'),
        spaceAfter=10
    )

    site_code_style = ParagraphStyle('SiteCode', fontName='Helvetica-Bold', fontSize=10, leading=12, textColor=colors.white, keepWithNext=True)
    site_sub_style = ParagraphStyle('SiteSub', fontName='Helvetica-Oblique', fontSize=8.5, leading=11, textColor=colors.HexColor('#475569'), keepWithNext=True)
    table_header_style = ParagraphStyle('TableHeader', fontName='Helvetica-Bold', fontSize=8, leading=10, textColor=colors.HexColor('#475569'))

    job_title_style = ParagraphStyle('JobTitle', fontName='Helvetica-Bold', fontSize=8.5, leading=11, textColor=colors.HexColor('#1E293B'))
    job_media_style = ParagraphStyle('JobMedia', fontName='Helvetica', fontSize=8, leading=10, textColor=colors.HexColor('#475569'))
    
    action_green_style = ParagraphStyle('ActionGreen', fontName='Helvetica-Bold', fontSize=8, leading=10, textColor=colors.HexColor('#15803D'))
    action_blue_style = ParagraphStyle('ActionBlue', fontName='Helvetica-Bold', fontSize=8, leading=10, textColor=colors.HexColor('#1D4ED8'))
    
    size_style = ParagraphStyle('SizeStyle', fontName='Helvetica', fontSize=8.5, leading=11, textColor=colors.HexColor('#334155'))
    qty_style = ParagraphStyle('QtyStyle', fontName='Helvetica-Bold', fontSize=9, leading=11, alignment=1, textColor=colors.HexColor('#0F172A'))

    raw_story = []
    clean_title = pdf_filename.replace('.pdf', '')
    raw_story.append(Paragraph(saxutils.escape(f"{clean_title} (Optimized Route)"), title_style))

    job_id_pattern = re.compile(r'^[A-Z]{3,5}\d{5,8}')

    for b in sorted_blocks:
        block_text = "\n".join(b['block_lines'])
        raw_lines = [l.strip(" |") for l in block_text.split('\n') if l.strip(" |")]
        if not raw_lines:
            continue

        # 1. Site Header Bar
        site_header_text = saxutils.escape(raw_lines[0])
        header_table = Table([[Paragraph(site_header_text, site_code_style)]], colWidths=[545])
        header_table.setStyle(TableStyle([
            ('BACKGROUND', (0,0), (-1,-1), colors.HexColor('#1E293B')),
            ('TOPPADDING', (0,0), (-1,-1), 5),
            ('BOTTOMPADDING', (0,0), (-1,-1), 5),
            ('LEFTPADDING', (0,0), (-1,-1), 8),
            ('RIGHTPADDING', (0,0), (-1,-1), 8),
        ]))
        raw_story.append(header_table)

        # 2. Extract Sub-headers & Jobs
        sub_headers = []
        job_blocks = []
        current_job = None

        for line in raw_lines[1:]:
            match = job_id_pattern.match(line)
            if match:
                job_id = match.group(0)
                rest = line[len(job_id):]
                if rest and not rest.startswith(' '):
                    line = f"{job_id} {rest}"

                if current_job:
                    job_blocks.append(current_job)
                current_job = [line]
            else:
                if current_job:
                    current_job.append(line)
                else:
                    sub_headers.append(line)

        if current_job:
            job_blocks.append(current_job)

        if sub_headers:
            sub_str = saxutils.escape(" | ".join(sub_headers))
            raw_story.append(Spacer(1, 2))
            raw_story.append(Paragraph(sub_str, site_sub_style))

        # 3. Build 5-Column Table
        if job_blocks:
            jobs_table_data = [[
                Paragraph("CAMPAIGN", table_header_style),
                Paragraph("MEDIA DETAILS", table_header_style),
                Paragraph("ACTION", table_header_style),
                Paragraph("SIZE", table_header_style),
                Paragraph("QTY", table_header_style),
            ]]

            for j_raw in job_blocks:
                parsed = parse_job_block(j_raw)
                
                escaped_title = saxutils.escape(parsed['title'])
                campaign_html = f"{escaped_title}"
                if parsed['note']:
                    escaped_note = saxutils.escape(parsed['note'])
                    campaign_html += f"<br/><font color='#B91C1C'><b>🚨 {escaped_note}</b></font>"
                
                campaign_cell = Paragraph(campaign_html, job_title_style)
                
                escaped_media = saxutils.escape(parsed['media'])
                media_cell = Paragraph(escaped_media, job_media_style) if escaped_media else Paragraph("", job_media_style)

                if parsed['action'] == "MAINTAIN" and "max a0" in parsed['media'].lower():
                    act_style = action_blue_style
                else:
                    act_style = action_green_style

                jobs_table_data.append([
                    campaign_cell,
                    media_cell,
                    Paragraph(saxutils.escape(parsed['action']), act_style),
                    Paragraph(saxutils.escape(parsed['size']), size_style),
                    Paragraph(saxutils.escape(parsed['qty']), qty_style)
                ])

            j_table = Table(jobs_table_data, colWidths=[205, 140, 65, 90, 45])
            j_table.setStyle(TableStyle([
                ('VALIGN', (0,0), (-1,-1), 'TOP'),
                ('BACKGROUND', (0,0), (-1,0), colors.HexColor('#F8FAFC')),
                ('LINEBELOW', (0,0), (-1,0), 1, colors.HexColor('#CBD5E1')),
                ('LINEBELOW', (0,1), (-1,-1), 0.5, colors.HexColor('#E2E8F0')),
                ('TOPPADDING', (0,0), (-1,-1), 4),
                ('BOTTOMPADDING', (0,0), (-1,-1), 4),
                ('LEFTPADDING', (0,0), (-1,-1), 4),
                ('RIGHTPADDING', (0,0), (-1,-1), 4),
            ]))
            raw_story.append(Spacer(1, 4))
            raw_story.append(j_table)

        raw_story.append(Spacer(1, 12))

    clean_story = [x for x in raw_story if isinstance(x, Flowable)]
    doc.build(clean_story)
    return buffer.getvalue()


if st.button("🚀 Process & Re-order PDFs", type="primary"):
    if not csv_file:
        st.error("Please upload your Master Route CSV file first.")
    elif not pdf_files:
        st.error("Please upload at least one PDF file.")
    else:
        with st.spinner("Processing files and optimizing routes..."):
            df = pd.read_csv(csv_file)
            
            zip_buffer = io.BytesIO()
            processed_count = 0
            
            with zipfile.ZipFile(zip_buffer, "w") as zf:
                for pdf_file in pdf_files:
                    try:
                        pdf_text = extract_pdf_text(pdf_file)
                        csv_mapping = get_mapping_for_pdf(df, pdf_file.name, pdf_text)
                        sorted_blocks = parse_and_sort_pdf(pdf_text, csv_mapping)
                        pdf_bytes = generate_reportlab_pdf(sorted_blocks, pdf_file.name)
                        
                        output_name = f"Optimized_{pdf_file.name}"
                        zf.writestr(output_name, pdf_bytes)
                        processed_count += 1
                    except Exception as err:
                        st.error(f"❌ Error processing `{pdf_file.name}`: {str(err)}")
            
            if processed_count > 0:
                st.success(f"Successfully processed {processed_count} PDF(s)!")
                st.download_button(
                    label="📦 Download Optimized PDFs (.zip)",
                    data=zip_buffer.getvalue(),
                    file_name="Optimized_Run_Sheets.zip",
                    mime="application/zip"
                )
