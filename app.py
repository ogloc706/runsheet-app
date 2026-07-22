import streamlit as st
import pandas as pd
import re
import io
import zipfile
import pdfplumber

from reportlab.lib.pagesizes import A4
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, HRFlowable, KeepTogether
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib import colors

st.set_page_config(page_title="Run Sheet Route Optimizer", layout="centered")

st.title("🚚 Poster Run Sheet Route Optimizer")
st.write("Upload your **Territory Route CSV** and your **PDF Instructions** to automatically re-order and format them by route sequence.")

st.subheader("1. Upload Route CSV")
csv_file = st.file_uploader("Choose your Route Plan CSV", type=["csv"])

st.subheader("2. Upload Weekly PDF Run Sheets")
pdf_files = st.file_uploader("Choose your PDF run sheet(s)", type=["pdf"], accept_multiple_files=True)


def extract_pdf_text(uploaded_file):
    with pdfplumber.open(io.BytesIO(uploaded_file.read())) as pdf:
        full_text = "\n".join([page.extract_text() or "" for page in pdf.pages])
    return full_text


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
    
    for i, line in enumerate(lines):
        match = re.search(r'\b(AL\d+\s*\([A-Z]+\)|PB\d+\s*\([A-Z\s]+\)|WLG\d+[\d\.]*(?:\s*\([A-Z]+\))?)', line)
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


def generate_reportlab_pdf(sorted_blocks, pdf_filename):
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        rightMargin=30,
        leftMargin=30,
        topMargin=30,
        bottomMargin=30
    )
    styles = getSampleStyleSheet()

    title_style = ParagraphStyle(
        'TitleStyle',
        parent=styles['Heading1'],
        fontSize=14,
        leading=18,
        spaceAfter=12
    )

    site_header_style = ParagraphStyle(
        'SiteHeader',
        parent=styles['Heading2'],
        fontSize=11,
        leading=14,
        fontName='Helvetica-Bold',
        spaceAfter=2
    )

    site_sub_style = ParagraphStyle(
        'SiteSub',
        parent=styles['Normal'],
        fontSize=9,
        leading=11,
        textColor=colors.HexColor('#444444'),
        spaceAfter=4
    )

    item_text_style = ParagraphStyle(
        'ItemText',
        parent=styles['Normal'],
        fontSize=9,
        leading=12
    )

    story = []
    clean_title = pdf_filename.replace('.pdf', '')
    story.append(Paragraph(f"{clean_title} (Optimized Route)", title_style))

    job_id_pattern = re.compile(r'^[A-Z]{3,5}\d{5,8}\b')

    for b in sorted_blocks:
        block_elements = []
        block_text = "\n".join(b['block_lines'])
        raw_lines = [l.strip(" |") for l in block_text.split('\n') if l.strip(" |")]
        if not raw_lines:
            continue

        site_line = raw_lines[0]
        block_elements.append(Paragraph(site_line, site_header_style))

        sub_headers = []
        job_blocks = []
        current_job = None

        for line in raw_lines[1:]:
            match = job_id_pattern.match(line)
            if match:
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

        for sub in sub_headers:
            block_elements.append(Paragraph(sub, site_sub_style))

        table_rows = []
        for j_block in job_blocks:
            job_str = "<br/>".join(j_block)
            table_rows.append([Paragraph(job_str, item_text_style)])

        if table_rows:
            t = Table(table_rows, colWidths=[500])
            t.setStyle(TableStyle([
                ('VALIGN', (0,0), (-1,-1), 'TOP'),
                ('BOTTOMPADDING', (0,0), (-1,-1), 4),
                ('TOPPADDING', (0,0), (-1,-1), 4),
                ('LINEBELOW', (0,0), (-1,-2), 0.5, colors.HexColor('#E0E0E0')),
            ]))
            block_elements.append(t)

        block_elements.append(Spacer(1, 8))
        block_elements.append(HRFlowable(width="100%", thickness=0.5, color=colors.HexColor('#CCCCCC'), spaceAfter=10))

        story.append(KeepTogether(block_elements))

    doc.build(story)
    return buffer.getvalue()


if st.button("🚀 Process & Re-order PDFs", type="primary"):
    if not csv_file:
        st.error("Please upload your Route CSV file first.")
    elif not pdf_files:
        st.error("Please upload at least one PDF file.")
    else:
        with st.spinner("Processing files and optimizing routes..."):
            df = pd.read_csv(csv_file)
            csv_mapping = dict(zip(df['code'].astype(str).str.strip(), df['Coding']))
            
            zip_buffer = io.BytesIO()
            with zipfile.ZipFile(zip_buffer, "w") as zf:
                for pdf_file in pdf_files:
                    pdf_text = extract_pdf_text(pdf_file)
                    sorted_blocks = parse_and_sort_pdf(pdf_text, csv_mapping)
                    pdf_bytes = generate_reportlab_pdf(sorted_blocks, pdf_file.name)
                    
                    output_name = f"Optimized_{pdf_file.name}"
                    zf.writestr(output_name, pdf_bytes)
            
            st.success("All PDFs successfully processed!")
            st.download_button(
                label="📦 Download Optimized PDFs (.zip)",
                data=zip_buffer.getvalue(),
                file_name="Optimized_Run_Sheets.zip",
                mime="application/zip"
            )