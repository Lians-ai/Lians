from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.units import inch
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas


ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "output" / "pdf" / "lians-kickoff-invoice-template.pdf"
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
RIGHT = PAGE_W - MARGIN
CONTENT_W = PAGE_W - 2 * MARGIN


def label(c, text, x, y):
    c.setFillColor(INK)
    c.setFont("LiansSans-Bold", 8.5)
    c.drawString(x, y, text)


def field(c, name, x, y, width, height=20, multiline=False):
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
        fieldFlags="multiline" if multiline else "",
    )


def section(c, title, y):
    rendered = title.upper()
    c.setFillColor(BLUE)
    c.setFont("LiansSans-Bold", 9)
    c.drawString(MARGIN, y, rendered)
    c.setStrokeColor(LINE)
    start = MARGIN + c.stringWidth(rendered, "LiansSans-Bold", 9) + 0.14 * inch
    c.line(start, y + 2, RIGHT, y + 2)
    return y - 18


c = canvas.Canvas(str(OUTPUT), pagesize=letter)
c.setTitle("Lians Kickoff Invoice Template")
c.setAuthor("Lians AI, Corp.")

c.setFillColor(INK)
c.rect(0, PAGE_H - 0.23 * inch, PAGE_W, 0.23 * inch, fill=1, stroke=0)
c.setFillColor(BLUE)
c.rect(0, PAGE_H - 0.27 * inch, PAGE_W, 0.04 * inch, fill=1, stroke=0)

y = PAGE_H - 0.72 * inch
c.setFillColor(BLUE)
c.setFont("LiansSans-Bold", 8.5)
c.drawString(MARGIN, y, "PAID DESIGN PARTNERSHIP")
y -= 28
c.setFillColor(INK)
c.setFont("LiansSans-Bold", 23)
c.drawString(MARGIN, y, "Kickoff Invoice")
c.setFillColor(MUTED)
c.setFont("LiansSans", 9)
c.drawRightString(RIGHT, y + 3, "DUE ON RECEIPT")

y -= 32
c.setFillColor(PALE)
c.roundRect(MARGIN, y - 44, CONTENT_W, 54, 5, fill=1, stroke=0)
c.setFillColor(BLUE)
c.setFont("LiansSans-Bold", 20)
c.drawString(MARGIN + 14, y - 19, "$2,250.00 USD")
c.setFillColor(INK)
c.setFont("LiansSans-Bold", 9.5)
c.drawString(MARGIN + 185, y - 10, "50% kickoff payment")
c.setFont("LiansSans", 8.5)
c.drawString(MARGIN + 185, y - 29, "Two-week AI Evidence Readiness Sprint")

y -= 76
y = section(c, "Invoice details", y)
col_gap = 0.28 * inch
col_w = (CONTENT_W - col_gap) / 2
label(c, "Invoice number", MARGIN, y)
label(c, "Invoice date", MARGIN + col_w + col_gap, y)
field(c, "invoice_number", MARGIN, y - 29, col_w)
field(c, "invoice_date", MARGIN + col_w + col_gap, y - 29, col_w)
y -= 58
label(c, "Lians legal business address", MARGIN, y)
field(c, "provider_address", MARGIN, y - 43, CONTENT_W, height=34, multiline=True)

y -= 72
y = section(c, "Bill to", y)
label(c, "Customer legal name", MARGIN, y)
label(c, "Billing contact and email", MARGIN + col_w + col_gap, y)
field(c, "customer_legal_name", MARGIN, y - 29, col_w)
field(c, "billing_contact", MARGIN + col_w + col_gap, y - 29, col_w)
y -= 58
label(c, "Customer billing address", MARGIN, y)
label(c, "Purchase order (or NOT REQUIRED)", MARGIN + col_w + col_gap, y)
field(c, "billing_address", MARGIN, y - 43, col_w, height=34, multiline=True)
field(c, "purchase_order", MARGIN + col_w + col_gap, y - 29, col_w)

y -= 72
y = section(c, "Engagement", y)
label(c, "Signed order-form date", MARGIN, y)
field(c, "order_form_date", MARGIN, y - 29, col_w)
c.setFillColor(INK)
c.setFont("LiansSans", 9)
c.drawString(MARGIN + col_w + col_gap, y - 1, "Remaining $2,250.00 invoiced on delivery")
c.drawString(MARGIN + col_w + col_gap, y - 18, "and due within five business days.")

y -= 60
y = section(c, "Authorized payment route", y)
label(c, "Payment method (ACH / wire / Stripe invoice)", MARGIN, y)
field(c, "payment_method", MARGIN, y - 29, CONTENT_W)
y -= 58
label(c, "Secure payment instructions or link", MARGIN, y)
field(c, "payment_instructions", MARGIN, y - 49, CONTENT_W, height=40, multiline=True)

y -= 70
c.setFillColor(INK)
c.setFont("LiansSans-Bold", 9)
c.drawString(MARGIN, y, "Work begins only after the order form is signed and this payment clears.")
c.setFillColor(MUTED)
c.setFont("LiansSans", 8)
c.drawString(MARGIN, y - 18, "Please reference the invoice number with payment.")

c.setStrokeColor(LINE)
c.line(MARGIN, 0.55 * inch, RIGHT, 0.55 * inch)
c.setFillColor(INK)
c.setFont("LiansSans-Bold", 8)
c.drawString(MARGIN, 0.36 * inch, "LIANS AI, CORP.")
c.setFillColor(MUTED)
c.setFont("LiansSans", 8)
c.drawRightString(RIGHT, 0.36 * inch, "KICKOFF INVOICE TEMPLATE")

c.save()
print(OUTPUT)
