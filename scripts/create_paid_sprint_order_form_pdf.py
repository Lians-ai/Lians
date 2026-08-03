from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.units import inch
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas


ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "output" / "pdf" / "lians-paid-design-partnership-order-form.pdf"
OUTPUT.parent.mkdir(parents=True, exist_ok=True)

BLUE = colors.HexColor("#1877F2")
INK = colors.HexColor("#08111F")
MUTED = colors.HexColor("#52606D")
PALE = colors.HexColor("#EEF5FF")
LINE = colors.HexColor("#C8D6E5")
WHITE = colors.white


def register_fonts():
    for regular, bold in [
        ("C:/Windows/Fonts/arial.ttf", "C:/Windows/Fonts/arialbd.ttf"),
        ("C:/Windows/Fonts/calibri.ttf", "C:/Windows/Fonts/calibrib.ttf"),
    ]:
        if Path(regular).exists() and Path(bold).exists():
            pdfmetrics.registerFont(TTFont("LiansSans", regular))
            pdfmetrics.registerFont(TTFont("LiansSans-Bold", bold))
            return
    raise FileNotFoundError("No supported Windows font pair found")


register_fonts()

PAGE_W, PAGE_H = letter
MARGIN = 0.62 * inch
CONTENT_W = PAGE_W - 2 * MARGIN


def header(c, page_number):
    c.setFillColor(INK)
    c.rect(0, PAGE_H - 0.23 * inch, PAGE_W, 0.23 * inch, fill=1, stroke=0)
    c.setFillColor(BLUE)
    c.rect(0, PAGE_H - 0.27 * inch, PAGE_W, 0.04 * inch, fill=1, stroke=0)
    c.setFillColor(INK)
    c.setFont("LiansSans-Bold", 8)
    c.drawString(MARGIN, 0.36 * inch, "LIANS AI, CORP.")
    c.setFillColor(MUTED)
    c.setFont("LiansSans", 8)
    c.drawRightString(PAGE_W - MARGIN, 0.36 * inch, f"ORDER FORM  |  PAGE {page_number} OF 2")
    c.setStrokeColor(LINE)
    c.line(MARGIN, 0.55 * inch, PAGE_W - MARGIN, 0.55 * inch)


def label(c, text, x, y):
    c.setFillColor(INK)
    c.setFont("LiansSans-Bold", 8.5)
    c.drawString(x, y, text)


def field(c, name, x, y, width, height=20, multiline=False):
    flags = "multiline" if multiline else ""
    c.acroForm.textfield(
        name=name,
        x=x,
        y=y,
        width=width,
        height=height,
        borderStyle="underlined",
        borderWidth=1,
        borderColor=LINE,
        fillColor=WHITE,
        textColor=INK,
        forceBorder=True,
        fontName="Helvetica",
        fontSize=9,
        fieldFlags=flags,
    )


def wrap(c, text, x, y, width, font="LiansSans", size=8.5, leading=12, color=INK):
    words = text.split()
    lines = []
    current = ""
    for word in words:
        trial = f"{current} {word}".strip()
        if c.stringWidth(trial, font, size) <= width:
            current = trial
        else:
            lines.append(current)
            current = word
    if current:
        lines.append(current)
    c.setFont(font, size)
    c.setFillColor(color)
    for line in lines:
        c.drawString(x, y, line)
        y -= leading
    return y


def section(c, title, y):
    rendered_title = title.upper()
    c.setFillColor(BLUE)
    c.setFont("LiansSans-Bold", 9)
    c.drawString(MARGIN, y, rendered_title)
    c.setStrokeColor(LINE)
    line_start = MARGIN + c.stringWidth(rendered_title, "LiansSans-Bold", 9) + 0.14 * inch
    c.line(line_start, y + 2, PAGE_W - MARGIN, y + 2)
    return y - 18


c = canvas.Canvas(str(OUTPUT), pagesize=letter)
c.setTitle("Lians Paid Design Partnership Order Form")
c.setAuthor("Lians AI, Corp.")

# Page 1
header(c, 1)
y = PAGE_H - 0.7 * inch
c.setFillColor(BLUE)
c.setFont("LiansSans-Bold", 8)
c.drawString(MARGIN, y, "PAID DESIGN PARTNERSHIP")
y -= 28
c.setFillColor(INK)
c.setFont("LiansSans-Bold", 21)
c.drawString(MARGIN, y, "AI Evidence Readiness Sprint - Order Form")
y -= 22
y = wrap(
    c,
    "This order form creates a paid design partnership between Lians AI, Corp. "
    "(doing business as Lians) and the customer named below. It is not an unpaid "
    "pilot, evaluation, or proof of concept.",
    MARGIN,
    y,
    CONTENT_W,
    size=9,
    leading=13,
    color=MUTED,
)
y -= 10

c.setFillColor(PALE)
c.roundRect(MARGIN, y - 58, CONTENT_W, 58, 4, fill=1, stroke=0)
c.setFillColor(BLUE)
c.setFont("LiansSans-Bold", 19)
c.drawString(MARGIN + 14, y - 35, "$4,500")
c.setFillColor(INK)
c.setFont("LiansSans-Bold", 9)
c.drawString(MARGIN + 1.35 * inch, y - 22, "Two weeks  |  One sanitized workflow")
c.drawString(MARGIN + 1.35 * inch, y - 39, "$2,250 due before work begins")
c.drawRightString(PAGE_W - MARGIN - 14, y - 39, "$2,250 due within 5 business days of delivery")
y -= 78

y = section(c, "Customer and owners", y)
left_x = MARGIN
right_x = MARGIN + CONTENT_W / 2 + 10
half_w = CONTENT_W / 2 - 10

label(c, "Customer legal name", left_x, y)
field(c, "customer_legal_name", left_x, y - 25, half_w)
label(c, "Customer address", right_x, y)
field(c, "customer_address", right_x, y - 25, half_w)
y -= 55
label(c, "Billing contact and email", left_x, y)
field(c, "billing_contact", left_x, y - 25, half_w)
label(c, "Technical owner and email", right_x, y)
field(c, "technical_owner", right_x, y - 25, half_w)
y -= 62

y = section(c, "Named workflow and success", y)
label(c, "Sanitized or synthetic AI workflow", MARGIN, y)
field(c, "workflow", MARGIN, y - 48, CONTENT_W, height=42, multiline=True)
y -= 64
label(c, "Success criterion", MARGIN, y)
field(c, "success_criterion", MARGIN, y - 48, CONTENT_W, height=42, multiline=True)
y -= 64

y = section(c, "Dates and commercial approval", y)
third_w = (CONTENT_W - 20) / 3
label(c, "Target start date", MARGIN, y)
field(c, "target_start_date", MARGIN, y - 25, third_w)
label(c, "Target delivery date", MARGIN + third_w + 10, y)
field(c, "target_delivery_date", MARGIN + third_w + 10, y - 25, third_w)
label(c, "Purchase order (if required)", MARGIN + 2 * (third_w + 10), y)
field(c, "purchase_order", MARGIN + 2 * (third_w + 10), y - 25, third_w)
y -= 57
label(c, "Executive budget owner and email", MARGIN, y)
field(c, "budget_owner", MARGIN, y - 25, CONTENT_W)
y -= 50
c.setFillColor(INK)
c.setFont("LiansSans-Bold", 8.5)
c.drawString(MARGIN, y, "Work does not begin until both parties sign and the $2,250 kickoff payment clears.")

c.showPage()

# Page 2
header(c, 2)
y = PAGE_H - 0.72 * inch
c.setFillColor(INK)
c.setFont("LiansSans-Bold", 17)
c.drawString(MARGIN, y, "Scope, acceptance, and signatures")
y -= 28

y = section(c, "Included deliverables", y)
deliverables = [
    "One agreed synthetic or appropriately sanitized workflow.",
    "A source-system or OpenTelemetry-to-Lians evidence path.",
    "Point-in-time reconstruction and a verifiable evidence receipt.",
    "Checks for stale, conflicting, duplicated, unsourced, and broken memory.",
    "Downstream-impact mapping for changed or deleted evidence.",
    "Implementation gap report and executive walkthrough.",
]
c.setFont("LiansSans", 8.5)
c.setFillColor(INK)
for item in deliverables:
    c.drawString(MARGIN + 6, y, u"\u2022")
    y = wrap(c, item, MARGIN + 18, y, CONTENT_W - 18, size=8.5, leading=11)
    y -= 2
y -= 4

y = section(c, "Acceptance and data", y)
y = wrap(
    c,
    "Customer will review deliverables within five business days. They are deemed accepted unless "
    "Customer identifies a specific unmet success criterion in writing during that period. Lians will "
    "correct any confirmed deficiency within the original scope.",
    MARGIN,
    y,
    CONTENT_W,
)
y -= 7
y = wrap(
    c,
    "Customer will provide only synthetic or appropriately sanitized material. No production "
    "credentials, regulated personal data, or unredacted customer records will be supplied unless "
    "both parties first execute appropriate written security and data-processing terms.",
    MARGIN,
    y,
    CONTENT_W,
)
y -= 10

y = section(c, "Ownership, cancellation, and liability", y)
terms = [
    "Each party retains ownership of its pre-existing technology and materials. Customer owns its data. "
    "Lians may retain and reuse general skills, techniques, and non-customer-specific improvements, but "
    "not Customer confidential information.",
    "Either party may cancel before kickoff payment. After work begins, the kickoff payment is "
    "non-refundable. If Lians cancels without delivering agreed work, Lians will refund fees paid for "
    "undelivered services.",
    "Neither party is liable for indirect, incidental, or consequential damages. Lians' aggregate "
    "liability under this order form is limited to fees paid under it, except where prohibited by law.",
    "The full $4,500 sprint fee will be credited toward a longer paid Lians design partnership signed "
    "within 30 calendar days after delivery.",
]
for term in terms:
    y = wrap(c, term, MARGIN, y, CONTENT_W)
    y -= 7

y = section(c, "Agreement and signatures", y)
y = wrap(
    c,
    "This order form and its scope constitute the entire agreement for the sprint unless replaced by a "
    "mutually executed agreement. Electronic signatures and counterparts are accepted.",
    MARGIN,
    y,
    CONTENT_W,
)
y -= 13

label(c, "Lians signer name", left_x, y)
field(c, "lians_signer_name", left_x, y - 25, half_w)
label(c, "Customer signer name", right_x, y)
field(c, "customer_signer_name", right_x, y - 25, half_w)
y -= 53
label(c, "Lians title", left_x, y)
field(c, "lians_title", left_x, y - 25, half_w)
label(c, "Customer title", right_x, y)
field(c, "customer_title", right_x, y - 25, half_w)
y -= 53
label(c, "Lians electronic signature", left_x, y)
field(c, "lians_signature", left_x, y - 25, half_w)
label(c, "Customer electronic signature", right_x, y)
field(c, "customer_signature", right_x, y - 25, half_w)
y -= 53
label(c, "Date", left_x, y)
field(c, "lians_signature_date", left_x, y - 25, half_w)
label(c, "Date", right_x, y)
field(c, "customer_signature_date", right_x, y - 25, half_w)

c.save()
print(OUTPUT)
