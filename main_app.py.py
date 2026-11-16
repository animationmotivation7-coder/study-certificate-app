import streamlit as st
import fitz  # PyMuPDF
import json
import time
import uuid
import hmac
import gspread
import pandas as pd
from google.oauth2.service_account import Credentials
from datetime import datetime
from io import BytesIO
from pydantic import BaseModel
from google import genai
from google.genai import types
from reportlab.lib.pagesizes import landscape, A4
from reportlab.pdfgen import canvas
from reportlab.lib import colors

# --- 1. GOOGLE SHEETS CONNECTION & OPERATIONS ---
SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

@st.cache_resource
def get_worksheet():
    creds = Credentials.from_service_account_info(
        dict(st.secrets["gcp_service_account"]),
        scopes=SCOPES,
    )
    client = gspread.authorize(creds)
    sheet_name = st.secrets.get("SHEET_NAME", "Issued_Certificates")
    return client.open(sheet_name).sheet1

def save_certificate(cert_id: str, student_name: str, topic: str, score: int, total: int, percentage: float, issue_date: str):
    sheet = get_worksheet()
    sheet.append_row([cert_id, student_name, topic, score, total, percentage, issue_date])

def get_all_certificates() -> list[dict]:
    sheet = get_worksheet()
    return sheet.get_all_records()

def verify_certificate(cert_id: str):
    records = get_all_certificates()
    target = cert_id.strip().upper()
    for row in records:
        if str(row.get("cert_id", "")).strip().upper() == target:
            return row
    return None

# --- 2. DATA SCHEMA FOR STRUCTURED AI OUTPUT ---
class Question(BaseModel):
    question: str
    options: list[str]
    correct_answer: str
    explanation: str

class Quiz(BaseModel):
    topic_title: str
    questions: list[Question]

# --- 3. HELPER FUNCTIONS ---
def extract_text_from_pdf(uploaded_file) -> str:
    doc = fitz.open(stream=uploaded_file.read(), filetype="pdf")
    return "".join(page.get_text() for page in doc)

def generate_quiz(api_key: str, content: str, num_questions: int, difficulty: str) -> dict:
    client = genai.Client(api_key=api_key.strip())
    prompt = f"""
    Create a {difficulty}-difficulty multiple-choice quiz with exactly {num_questions} questions 
    based strictly on the following study notes. Each question must have 4 options, 
    and 'correct_answer' must exactly match one of the strings in 'options'.
    
    NOTES:
    {content[:25000]} 
    """
    last_error = "Unknown error"
    for model_name in ["gemini-2.5-flash", "gemini-2.0-flash"]:
        try:
            response = client.models.generate_content(
                model=model_name,
                contents=prompt,
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                    response_schema=Quiz,
                    temperature=0.3,
                ),
            )
            return json.loads(response.text)
        except Exception as e:
            last_error = str(e)
            continue
    raise RuntimeError(f"Gemini API Error: {last_error}")

def generate_certificate_pdf(student_name: str, topic: str, score: int, total: int, percentage: float, cert_id: str, date_str: str) -> BytesIO:
    buffer = BytesIO()
    c = canvas.Canvas(buffer, pagesize=landscape(A4))
    width, height = landscape(A4)

    # Outer & Inner Decorative Borders
    c.setStrokeColor(colors.HexColor("#1E3A8A"))
    c.setLineWidth(6)
    c.rect(30, 30, width - 60, height - 60)
    c.setStrokeColor(colors.HexColor("#D97706"))
    c.setLineWidth(2)
    c.rect(42, 42, width - 84, height - 84)

    # Header
    c.setFont("Helvetica-Bold", 34)
    c.setFillColor(colors.HexColor("#1E3A8A"))
    c.drawCentredString(width / 2, height - 115, "CERTIFICATE OF ACHIEVEMENT")

    # Body Text
    c.setFont("Helvetica", 16)
    c.setFillColor(colors.black)
    c.drawCentredString(width / 2, height - 175, "This is to proudly certify that")

    c.setFont("Helvetica-Bold", 28)
    c.setFillColor(colors.HexColor("#0F172A"))
    c.drawCentredString(width / 2, height - 225, student_name.upper())

    c.setFont("Helvetica", 16)
    c.setFillColor(colors.black)
    c.drawCentredString(width / 2, height - 275, "has successfully completed the timed assessment on")

    c.setFont("Helvetica-Bold", 20)
    c.setFillColor(colors.HexColor("#1E3A8A"))
    c.drawCentredString(width / 2, height - 310, topic)

    c.setFont("Helvetica-Bold", 18)
    c.setFillColor(colors.HexColor("#047857"))
    c.drawCentredString(width / 2, height - 360, f"Score Achieved: {score} / {total} ({percentage}%)")

    # Footer: Left (Date Issued)
    c.setStrokeColor(colors.HexColor("#94A3B8"))
    c.setLineWidth(1)
    c.line(80, 110, 250, 110)
    c.setFont("Helvetica-Bold", 12)
    c.setFillColor(colors.HexColor("#0F172A"))
    c.drawCentredString(165, 118, date_str)
    c.setFont("Helvetica", 10)
    c.setFillColor(colors.HexColor("#475569"))
    c.drawCentredString(165, 95, "Date Issued")

    # Footer: Center (Verification ID)
    c.setFont("Helvetica-Bold", 10)
    c.setFillColor(colors.HexColor("#1E3A8A"))
    c.drawCentredString(width / 2, 95, f"Verification ID: {cert_id}")

    # Footer: Right (Director Signature Block)
    c.line(width - 250, 110, width - 80, 110)
    c.setFont("Times-BoldItalic", 16)
    c.setFillColor(colors.HexColor("#1E3A8A"))
    c.drawCentredString(width - 165, 118, "Hanzala Nazir")
    c.setFont("Helvetica-Bold", 10)
    c.setFillColor(colors.HexColor("#475569"))
    c.drawCentredString(width - 165, 95, "Director")

    c.save()
    buffer.seek(0)
    return buffer

# --- 4. STREAMLIT UI & STATE INITIALIZATION ---
st.set_page_config(page_title="AI Quiz & Certificate Portal", page_icon="🎓", layout="centered")

for key, default in {
    "stage": "setup",
    "quiz_data": None,
    "start_time": 0,
    "time_limit": 0,
    "user_answers": {},
    "issued_cert": None,
    "admin_authenticated": False,
}.items():
    if key not in st.session_state:
        st.session_state[key] = default

st.title("🎓 AI Notes-to-Quiz & Certificate Portal")

# Load API key silently from Streamlit Secrets (hidden from students)
api_key = st.secrets.get("GEMINI_API_KEY", "") if hasattr(st, "secrets") else ""

with st.sidebar:
    st.header("Quiz Controls")
    st.write("Need to restart or try another topic?")
    if st.button("🔄 Start New Quiz", use_container_width=True):
        for key in ["stage", "quiz_data", "start_time", "time_limit", "user_answers", "issued_cert"]:
            st.session_state[key] = None if key in ["quiz_data", "issued_cert"] else ({} if key == "user_answers" else ("setup" if key == "stage" else 0))
        st.rerun()

main_tab1, main_tab2, main_tab3 = st.tabs(["📝 Take Assessment", "🔍 Verify Certificate", "🔐 Admin Dashboard"])

# ==========================================
# TAB 1: ASSESSMENT WORKFLOW
# ==========================================
with main_tab1:
    if st.session_state.stage == "setup":
        student_name = st.text_input("Student Full Name (for Certificate)", placeholder="e.g., Alex Mercer")
        
        input_tab1, input_tab2 = st.tabs(["📄 Upload PDF", "✍️ Paste Notes"])
        with input_tab1:
            uploaded_pdf = st.file_uploader("Upload lecture slides or study PDF", type=["pdf"])
        with input_tab2:
            pasted_notes = st.text_area("Paste your study notes here", height=180)

        col1, col2, col3 = st.columns(3)
        with col1:
            num_q = st.slider("Questions", min_value=3, max_value=15, value=5)
        with col2:
            mins = st.slider("Minutes", min_value=1, max_value=30, value=3)
        with col3:
            difficulty = st.selectbox("Difficulty", ["Easy", "Medium", "Hard"], index=1)

        if st.button("Generate Timed Quiz", type="primary", use_container_width=True):
            if not api_key:
                st.error("System setup incomplete: GEMINI_API_KEY is missing from Streamlit Secrets.")
            elif not student_name.strip():
                st.error("Please enter your name for the certificate.")
            else:
                raw_text = extract_text_from_pdf(uploaded_pdf) if uploaded_pdf else pasted_notes
                if len(raw_text.strip()) < 100:
                    st.error("Please provide at least a paragraph of notes or a readable PDF.")
                else:
                    try:
                        with st.spinner("Analyzing notes and crafting questions..."):
                            st.session_state.quiz_data = generate_quiz(api_key, raw_text, num_q, difficulty)
                            st.session_state.student_name = student_name.strip()
                            st.session_state.time_limit = mins * 60
                            st.session_state.start_time = time.time()
                            st.session_state.user_answers = {}
                            st.session_state.issued_cert = None
                            st.session_state.stage = "quiz"
                            st.rerun()
                    except Exception as e:
                        st.error(f"Could not generate quiz: {e}")

    elif st.session_state.stage == "quiz":
        quiz = st.session_state.quiz_data
        st.subheader(f"Topic: {quiz['topic_title']}")

        @st.fragment(run_every="1s")
        def countdown_timer():
            elapsed = time.time() - st.session_state.start_time
            remaining = int(st.session_state.time_limit - elapsed)
            if remaining <= 0:
                st.session_state.stage = "results"
                st.rerun()
            m, s = divmod(remaining, 60)
            st.warning(f"⏳ Time Remaining: **{m:02d}:{s:02d}**")

        countdown_timer()

        for idx, q in enumerate(quiz["questions"]):
            st.markdown(f"**Q{idx + 1}. {q['question']}**")
            choice = st.radio(
                label=f"Select answer for Q{idx + 1}",
                options=q["options"],
                index=None,
                key=f"q_{idx}",
                label_visibility="collapsed"
            )
            st.session_state.user_answers[idx] = choice
            st.divider()

        if st.button("Submit Quiz", type="primary", use_container_width=True):
            st.session_state.stage = "results"
            st.rerun()

    elif st.session_state.stage == "results":
        quiz = st.session_state.quiz_data
        questions = quiz["questions"]
        score = sum(1 for idx, q in enumerate(questions) if st.session_state.user_answers.get(idx) == q["correct_answer"])
        total = len(questions)
        percentage = round((score / total) * 100, 1)

        st.success(f"### Quiz Completed! Score: {score}/{total} ({percentage}%)")

        if percentage >= 50:
            if st.session_state.issued_cert is None:
                cert_id = f"CERT-{uuid.uuid4().hex[:8].upper()}"
                date_str = datetime.now().strftime("%B %d, %Y")
                with st.spinner("Recording official certificate..."):
                    save_certificate(
                        cert_id,
                        st.session_state.student_name,
                        quiz["topic_title"],
                        score,
                        total,
                        percentage,
                        date_str
                    )
                st.session_state.issued_cert = (cert_id, date_str)

            cert_id, date_str = st.session_state.issued_cert
            st.info(f"🏅 Credential Recorded! Your Verification ID is **{cert_id}**")

            pdf_bytes = generate_certificate_pdf(
                st.session_state.student_name,
                quiz["topic_title"],
                score,
                total,
                percentage,
                cert_id,
                date_str
            )
            st.download_button(
                label="📥 Download Official PDF Certificate",
                data=pdf_bytes,
                file_name=f"{st.session_state.student_name.replace(' ', '_')}_{cert_id}.pdf",
                mime="application/pdf",
                type="primary",
                use_container_width=True
            )
        else:
            st.warning("Score at least 50% to earn a verified certificate. Review the explanations below and try again!")

        st.subheader("Answer Key & Explanations")
        for idx, q in enumerate(questions):
            user_ans = st.session_state.user_answers.get(idx)
            is_correct = user_ans == q["correct_answer"]
            icon = "✅" if is_correct else "❌"
            with st.expander(f"{icon} Q{idx + 1}: {q['question']}"):
                st.write(f"**Your Answer:** {user_ans or 'No answer selected'}")
                st.write(f"**Correct Answer:** {q['correct_answer']}")
                st.caption(f"**Explanation:** {q['explanation']}")

# ==========================================
# TAB 2: CERTIFICATE VERIFICATION PORTAL
# ==========================================
with main_tab2:
    st.subheader("Verify Credential Authenticity")
    st.write("Enter a Certificate Verification ID (e.g., `CERT-A1B2C3D4`) to check the official registry.")
    
    lookup_id = st.text_input("Certificate ID", placeholder="CERT-XXXXXXXX")
    if st.button("Verify Credential", type="primary"):
        if not lookup_id.strip():
            st.error("Please enter a Certificate ID.")
        else:
            with st.spinner("Searching official registry..."):
                record = verify_certificate(lookup_id)
            if record:
                st.success("✅ **Valid Certificate Verified**")
                st.markdown(f"""
                * **Certificate ID:** `{record['cert_id']}`
                * **Recipient:** {record['student_name']}
                * **Assessment Topic:** {record['topic']}
                * **Score:** {record['score']} / {record['total']} ({record['percentage']}%)
                * **Date Issued:** {record['issue_date']}
                """)
            else:
                st.error("❌ **Invalid ID:** No matching certificate found in the registry.")

# ==========================================
# TAB 3: PASSWORD-PROTECTED ADMIN DASHBOARD
# ==========================================
with main_tab3:
    st.subheader("Director's Admin Dashboard")

    if not st.session_state.admin_authenticated:
        admin_pass_input = st.text_input("Enter Admin Password", type="password")
        if st.button("Unlock Dashboard", type="primary"):
            expected_pass = st.secrets.get("ADMIN_PASSWORD", "") if hasattr(st, "secrets") else ""
            if not expected_pass:
                st.error("ADMIN_PASSWORD is not configured in Streamlit Secrets.")
            elif hmac.compare_digest(admin_pass_input, expected_pass):
                st.session_state.admin_authenticated = True
                st.rerun()
            else:
                st.error("Incorrect password.")
    else:
        top_col1, top_col2 = st.columns([4, 1])
        with top_col1:
            st.caption("Signed in as **Hanzala Nazir (Director)**")
        with top_col2:
            if st.button("Lock", use_container_width=True):
                st.session_state.admin_authenticated = False
                st.rerun()

        with st.spinner("Loading live records from Google Sheets..."):
            records = get_all_certificates()

        if not records:
            st.info("No certificates have been issued yet.")
        else:
            df = pd.DataFrame(records)
            df["percentage"] = pd.to_numeric(df["percentage"], errors="coerce")

            # Summary Metrics
            m1, m2, m3, m4 = st.columns(4)
            m1.metric("Certificates Issued", len(df))
            m2.metric("Unique Students", df["student_name"].nunique())
            m3.metric("Average Score", f"{df['percentage'].mean():.1f}%")
            m4.metric("Topics Assessed", df["topic"].nunique())

            st.divider()

            # Search & Topic Filter
            f_col1, f_col2 = st.columns(2)
            with f_col1:
                search_query = st.text_input("Search by Student Name or Certificate ID")
            with f_col2:
                topics = ["All Topics"] + sorted(df["topic"].dropna().unique().tolist())
                selected_topic = st.selectbox("Filter by Topic", topics)

            filtered_df = df.copy()
            if selected_topic != "All Topics":
                filtered_df = filtered_df[filtered_df["topic"] == selected_topic]
            if search_query.strip():
                q = search_query.strip().lower()
                filtered_df = filtered_df[
                    filtered_df["student_name"].astype(str).str.lower().str.contains(q)
                    | filtered_df["cert_id"].astype(str).str.lower().str.contains(q)
                ]

            st.dataframe(filtered_df, use_container_width=True, hide_index=True)

            # CSV Export
            csv_data = filtered_df.to_csv(index=False).encode("utf-8")
            st.download_button(
                label="📥 Export Filtered Records as CSV",
                data=csv_data,
                file_name=f"issued_certificates_{datetime.now().strftime('%Y%m%d')}.csv",
                mime="text/csv",
            )
