import streamlit as st
import pandas as pd
import re
import io
import zipfile
import pypdf
from fpdf import FPDF

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


def clean_txt(s):
    if not isinstance(s, str):
        s = str(s)
    replacements = {
        '—': '-', '–': '-', '’': "'", '‘': "'",
        '“': '"', '”': '"', '…': '...', '🚨': '[!]',
        '⚠️': '[!]', '\xa0': ' '
    }
    for orig, repl in replacements.items():
        s = s.replace(orig, repl)
    return s.encode('latin-1', 'replace').decode('latin-1')


def get_mapping_for_pdf(df, pdf_filename, pdf_text):
    code_col = next((c for c in df.columns if str(c).strip().lower() in ['code', 'site code', 'sitecode', 'site_code', 'id']), df.columns[1])
    coding_col = next((c for c in df.columns if str(c).strip().lower() in ['coding', 'code order', 'order', 'sequence', 'rank', 'route']), df.columns[-2])
    territory_col = next((c for c in df.columns if str(c).strip().lower() in ['zone', 'territory', 'area', 'region', 'sheet']), None)

    if territory_col:
        first_line = pdf_text.strip().split('\n')[0] if pdf_text else ""
        unique_territories = df[territory_col].dropna().unique()
        
        matched_t = None
        for t in unique_territories:
            t_str = str(t).strip()
            identifier = t_str.split()[-1].lower() # e.g., "a", "b", "c", "cbd1"
            pdf_norm = re.sub(r'[^a-z0-9]', ' ', pdf_filename.lower())
            
            if (f"wellington {identifier}" in pdf_norm or 
                f"territory {identifier}" in pdf_norm or 
                f" {identifier} " in f" {pdf_norm} " or
                t_str.lower() in pdf_filename.lower() or 
                t_str.lower() in first_line.lower()):
                matched_t = t
                break
                
        if matched_t:
            st.info(f"📍 Matched zone **'{matched_t}'** for `{pdf_filename}`")
            filtered_df = df[df[territory_col] == matched_t]
            return dict(zip(filtered_df[code_col].astype(str).str.strip(), filtered_df[coding_col]))
            
    return dict(zip(df[code_col].astype(str).str.strip(), df[coding_col]))


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

    return sorted(blocks, key=lambda x: x['coding'])


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


class CompactRunSheetFPDF(FPDF):
    def __init__(self, doc_title):
        super().__init__()
        self.doc_title = clean_txt(doc_title)
        self.set_auto_page_break(True, margin=15)

    def header(self):
        if self.page_no() == 1:
            self.set_font("Helvetica", "B", 12)
            self.set_text_color(15, 23, 42)
            self.cell(0, 7, f"{self.doc_title} (Optimized Route)", ln=1)
            self.ln(2)

    def draw_site_block(self, site_header, sub_str, jobs):
        # Prevent orphan site header bars
        estimated_block_h = 8 + (6 if sub_str else 0) + (10 if jobs else 0)
        if self.get_y() + estimated_block_h > 275:
            self.add_page()

        # 1. Dark Navy Site Header Bar
        self.set_fill_color(30, 41, 59) # #1E293B
        self.set_text_color(255, 255, 255)
        self.set_font("Helvetica", "B", 9.5)
        self.cell(0, 6, f" {clean_txt(site_header)}", fill=True, ln=1)

        # 2. Sub-header / Location description
        if sub_str:
            self.set_text_color(71, 85, 105)
            self.set_font("Helvetica", "I", 8)
            self.cell(0, 4.5, f" Location: {clean_txt(sub_str)}", ln=1)

        # 3. 5-Column Table
        if jobs:
            col_w = [75, 55, 22, 25, 13] # Total = 190mm (A4 printable width)
            
            # Table Header
            self.set_fill_color(248, 250, 252)
            self.set_draw_color(203, 213, 225)
            self.set_text_color(71, 85, 105)
            self.set_font("Helvetica", "B", 7.5)
            
            headers = ["CAMPAIGN", "MEDIA DETAILS", "ACTION", "SIZE", "QTY"]
            for w, h in zip(col_w, headers):
                self.cell(w, 4.5, h, border="B", fill=True)
            self.ln()

            # Job Rows
            for j in jobs:
                title_txt = clean_txt(j['title'])
                note_txt = clean_txt(j['note'])
                media_txt = clean_txt(j['media'])
                
                full_camp = title_txt
                if note_txt:
                    full_camp += f"\n[!] {note_txt}"

                # Pre-calculate required row height to prevent multi_cell page break cascades
                self.set_font("Helvetica", "B", 8)
                lines_col0 = len(self.multi_cell(col_w[0], 4, full_camp, split_only=True))
                self.set_font("Helvetica", "", 7.5)
                lines_col1 = len(self.multi_cell(col_w[1], 4, media_txt, split_only=True))
                
                needed_h = max(lines_col0, lines_col1) * 4 + 1.5

                # Move to a fresh page BEFORE drawing if the row won't fit
                if self.get_y() + needed_h > 275:
                    self.add_page()

                y_start = self.get_y()
                x_start = self.get_x()

                # Col 1: Campaign Title & Notes
                self.set_font("Helvetica", "B", 8)
                self.set_text_color(30, 41, 59)
                self.multi_cell(col_w[0], 4, full_camp, border=0)
                y_end_col1 = self.get_y()

                # Col 2: Media Details
                self.set_xy(x_start + col_w[0], y_start)
                self.set_font("Helvetica", "", 7.5)
                self.set_text_color(71, 85, 105)
                self.multi_cell(col_w[1], 4, media_txt, border=0)
                y_end_col2 = self.get_y()

                max_y = max(y_end_col1, y_end_col2, y_start + 5)

                # Col 3: Action Color Logic (Slate Blue for MAINTAIN + Max A0; Green for others)
                self.set_xy(x_start + col_w[0] + col_w[1], y_start)
                self.set_font("Helvetica", "B", 8)
                if j['action'] == "MAINTAIN" and "max a0" in media_txt.lower():
                    self.set_text_color(29, 78, 216) # Blue
                else:
                    self.set_text_color(21, 128, 61) # Green
                self.cell(col_w[2], 4.5, clean_txt(j['action']), align="C")

                # Col 4: Size
                self.set_xy(x_start + col_w[0] + col_w[1] + col_w[2], y_start)
                self.set_font("Helvetica", "", 8)
                self.set_text_color(51, 65, 85)
                self.cell(col_w[3], 4.5, clean_txt(j['size']))

                # Col 5: Qty
                self.set_xy(x_start + col_w[0] + col_w[1] + col_w[2] + col_w[3], y_start)
                self.set_font("Helvetica", "B", 8.5)
                self.set_text_color(15, 23, 42)
                self.cell(col_w[4], 4.5, clean_txt(j['qty']), align="C")

                # Row Bottom Border Line
                self.set_xy(x_start, max_y)
                self.set_draw_color(226, 232, 240)
                self.line(x_start, max_y, x_start + sum(col_w), max_y)
                self.set_y(max_y + 0.5)

        self.ln(2.5)


def generate_compact_pdf(sorted_blocks, pdf_filename):
    clean_title = pdf_filename.replace('.pdf', '')
    pdf = CompactRunSheetFPDF(clean_title)
    pdf.add_page()
    
    job_id_pattern = re.compile(r'^[A-Z]{3,5}\d{5,8}')

    for b in sorted_blocks:
        block_text = "\n".join(b['block_lines'])
        raw_lines = [l.strip(" |") for l in block_text.split('\n') if l.strip(" |")]
        if not raw_lines:
            continue

        site_header_text = raw_lines[0]
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

        sub_str = " | ".join(sub_headers) if sub_headers else ""
        parsed_jobs = [parse_job_block(j_raw) for j_raw in job_blocks]
        
        pdf.draw_site_block(site_header_text, sub_str, parsed_jobs)

    out_data = pdf.output(dest="S")
    if isinstance(out_data, str):
        out_data = out_data.encode("latin1")
    return out_data


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
                        pdf_bytes = generate_compact_pdf(sorted_blocks, pdf_file.name)
                        
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
