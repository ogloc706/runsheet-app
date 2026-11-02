import streamlit as st
import pandas as pd
import re
import io
import zipfile
import pypdf
import urllib.parse
from fpdf import FPDF

st.set_page_config(page_title="Run Sheet Route Optimizer", layout="centered")

st.title("🚚 Poster Run Sheet Route Optimizer")
st.write("Upload your **Master Territory CSV** and your **PDF Run Sheets** to automatically re-order and format them by route sequence.")

st.subheader("1. Upload Master Route CSV")
csv_file = st.file_uploader("Choose your Master Route Plan CSV", type=["csv"])

st.subheader("2. Upload Weekly PDF Run Sheets")
pdf_files = st.file_uploader("Choose your PDF run sheet(s)", type=["pdf"], accept_multiple_files=True)

st.subheader("3. Options")
enable_trello = st.checkbox("Enable Trello Search Links for Placement Guides (Wellington Beta test)")


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
            identifier = t_str.split()[-1].lower()
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
    
    # MULTI-CITY STRICT SITE PATTERN: Matches WLG, PB, AL, CHCK, CHC, AKK, AK
    line_start_site_pattern = re.compile(r'^(?:\|\s*)?\b((?:WLG|PB|AL|CHCK|CHC|AKK|AK)\d+[\d\.]*(?:\s*\([A-Za-z\s]+\))?)\s*-')
    
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
    match = re.match(r'^([QIqi]?)([A-Za-z]{2,4}\d{5,8})(.*)', first_line)
    clean_id = match.group(2).upper() if match else ""
    rest_first = match.group(3).strip() if match else first_line
    
    media = ""
    notes = []
    action = "INSTALL"
    size = ""
    qty = "1"
    title_parts = [rest_first] if rest_first else []
    
    current_section = "title"
    
    for l in lines[1:]:
        l_str = l.strip()
        
        action_match = re.match(r'^(install|maintain)(.*)', l_str, re.IGNORECASE)
        
        if l_str.lower().startswith("media:"):
            media = l_str
            current_section = "media"
        elif l_str.lower().startswith("note:"):
            notes.append(l_str)
            current_section = "note"
        elif "cover up required" in l_str.lower():
            notes.append(l_str)
            current_section = "note"
        elif action_match:
            current_section = "action"
            action = action_match.group(1).upper()
            rest_of_line = action_match.group(2).strip()
            
            if rest_of_line:
                parts = rest_of_line.split()
                if re.match(r'^\d+$', parts[-1]):
                    qty = parts[-1]
                    size = " ".join(parts[:-1]) 
                else:
                    size = rest_of_line
        else:
            if current_section == "title":
                title_parts.append(l_str)
            elif current_section == "media":
                media += " " + l_str
            elif current_section == "note":
                notes[-1] += " " + l_str
            elif current_section == "action":
                if re.match(r'^\d+$', l_str):
                    qty = l_str
                else:
                    size += " " + l_str
            
    campaign_title = " ".join(title_parts)
    note_text = " | ".join(notes)
    
    return {
        'job_id': clean_id,
        'title': campaign_title,
        'media': media,
        'note': note_text,
        'action': action,
        'size': size.strip(),
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

    def draw_row_fixed(self, col_w, cell_texts, is_maintain_blue, job_url=None):
        self.set_font("Helvetica", "B", 8)
        lines_col0 = len(self.multi_cell(col_w[0], 4, cell_texts[0], split_only=True))
        self.set_font("Helvetica", "", 7.5)
        lines_col1 = len(self.multi_cell(col_w[1], 4, cell_texts[1], split_only=True))
        
        needed_h = max(lines_col0, lines_col1) * 4 + 1.5

        if self.get_y() + needed_h > 270:
            self.add_page()

        y_start = self.get_y()
        x_start = self.get_x()

        self.set_auto_page_break(False)

        # Col 0: Campaign Title & Notes
        self.set_font("Helvetica", "B", 8)
        
        if job_url:
            self.set_text_color(37, 99, 235) 
        else:
            self.set_text_color(30, 41, 59)
            
        self.multi_cell(col_w[0], 4, cell_texts[0], border=0)
        y_col0 = self.get_y()
        
        if job_url:
            self.link(x_start, y_start, col_w[0], y_col0 - y_start, job_url)

        # Col 1: Media Details
        self.set_xy(x_start + col_w[0], y_start)
        self.set_font("Helvetica", "", 7.5)
        self.set_text_color(71, 85, 105)
        self.multi_cell(col_w[1], 4, cell_texts[1], border=0)
        y_col1 = self.get_y()

        max_y = max(y_col0, y_col1, y_start + 5)

        # Col 2: Action
        self.set_xy(x_start + col_w[0] + col_w[1], y_start)
        self.set_font("Helvetica", "B", 8)
        if is_maintain_blue:
            self.set_text_color(29, 78, 216) 
        else:
            self.set_text_color(21, 128, 61) 
        self.cell(col_w[2], 4.5, cell_texts[2], align="C")

        # Col 3: Size
        self.set_xy(x_start + col_w[0] + col_w[1] + col_w[2], y_start)
        self.set_font("Helvetica", "", 8)
        self.set_text_color(51, 65, 85)
        self.cell(col_w[3], 4.5, cell_texts[3])

        # Col 4: Qty
        self.set_xy(x_start + col_w[0] + col_w[1] + col_w[2] + col_w[3], y_start)
        self.set_font("Helvetica", "B", 8.5)
        self.set_text_color(15, 23, 42)
        self.cell(col_w[4], 4.5, cell_texts[4], align="C")

        self.set_auto_page_break(True, margin=15)

        self.set_xy(x_start, max_y)
        self.set_draw_color(226, 232, 240)
        self.line(x_start, max_y, x_start + sum(col_w), max_y)
        self.set_y(max_y + 0.5)

    def draw_site_block(self, site_header, sub_str, jobs, enable_trello):
        estimated_block_h = 8 + (6 if sub_str else 0) + (10 if jobs else 0)
        if self.get_y() + estimated_block_h > 270:
            self.add_page()

        self.set_fill_color(30, 41, 59)
        self.set_text_color(255, 255, 255)
        self.set_font("Helvetica", "B", 9.5)
        self.cell(0, 6, f" {clean_txt(site_header)}", fill=True, ln=1)

        if sub_str:
            self.set_text_color(71, 85, 105)
            self.set_font("Helvetica", "I", 8)
            self.cell(0, 4.5, f" Location: {clean_txt(sub_str)}", ln=1)

        if jobs:
            col_w = [75, 55, 22, 25, 13]
            
            self.set_fill_color(248, 250, 252)
            self.set_draw_color(203, 213, 225)
            self.set_text_color(71, 85, 105)
            self.set_font("Helvetica", "B", 7.5)
            
            headers = ["CAMPAIGN", "MEDIA DETAILS", "ACTION", "SIZE", "QTY"]
            for w, h in zip(col_w, headers):
                self.cell(w, 4.5, h, border="B", fill=True)
            self.ln()

            for j in jobs:
                title_txt = clean_txt(j['title'])
                note_txt = clean_txt(j['note'])
                media_txt = clean_txt(j['media'])
                
                full_camp = title_txt
                job_url = None
                
                if note_txt:
                    full_camp += f"\n[!] {note_txt}"
                    
                    if enable_trello and "placement guide" in note_txt.lower():
                        q = re.sub(r'\(.*?\)', '', title_txt)
                        q = re.sub(r'\[.*?\]', '', q)
                        q = re.split(r'\||-', q)[0]
                        q = re.sub(r'[^a-zA-Z0-9\s]', '', q)
                        words = q.split()[:4]
                        safe_query = urllib.parse.quote(" ".join(words))
                        
                        job_url = f"https://trello.com/search?q={safe_query}"
                        full_camp += "\n>>> (Tap to Search Trello) <<<"

                is_maintain_blue = (j['action'] == "MAINTAIN" and "max a0" in media_txt.lower())
                
                self.draw_row_fixed(col_w, [full_camp, media_txt, clean_txt(j['action']), clean_txt(j['size']), clean_txt(j['qty'])], is_maintain_blue, job_url)

        self.ln(2.5)


def generate_compact_pdf(sorted_blocks, pdf_filename, enable_trello):
    clean_title = pdf_filename.replace('.pdf', '')
    pdf = CompactRunSheetFPDF(clean_title)
    pdf.add_page()
    
    job_id_pattern = re.compile(r'^[QIqi]?[A-Z]{2,4}\d{5,8}')

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
        
        pdf.draw_site_block(site_header_text, sub_str, parsed_jobs, enable_trello)

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
                        
                        pdf_bytes = generate_compact_pdf(sorted_blocks, pdf_file.name, enable_trello)
                        
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
