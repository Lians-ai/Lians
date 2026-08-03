from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import inch
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfbase import pdfmetrics
from reportlab.platypus import (
    BaseDocTemplate,
    Frame,
    KeepTogether,
    PageTemplate,
    Paragraph,
    Spacer,
    Table,
    TableStyle,
)


ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "output" / "pdf" / "lians-paid-design-partnership.pdf"
OUTPUT.parent.mkdir(parents=True, exist_ok=True)

BLUE = colors.HexColor("#1877F2")
INK = colors.HexColor("#08111F")
MUTED = colors.HexColor("#52606D")
PALE = colors.HexColor("#EEF5FF")
LINE = colors.HexColor("#D8E4F2")
WHITE = colors.white


def register_fonts():
    candidates = [
        (
            Path("C:/Windows/Fonts/arial.ttf"),
            Path("C:/Windows/Fonts/arialbd.ttf"),
        ),
        (
            Path("C:/Windows/Fonts/calibri.ttf"),
            Path("C:/Windows/Fonts/calibrib.ttf"),
        ),
    ]
    for regular, bold in candidates:
        if regular.exists() and bold.exists():
            pdfmetrics.registerFont(TTFont("LiansSans", str(regular)))
            pdfmetrics.registerFont(TTFont("LiansSans-Bold", str(bold)))
            return
    raise FileNotFoundError("No supported Windows font pair found")


register_fonts()

styles = {
    "eyebrow": ParagraphStyle(
        "eyebrow",
        fontName="LiansSans-Bold",
        fontSize=8,
        leading=10,
        textColor=BLUE,
        spaceAfter=8,
        tracking=1.2,
    ),
    "title": ParagraphStyle(
        "title",
        fontName="LiansSans-Bold",
        fontSize=24,
        leading=28,
        textColor=INK,
        spaceAfter=8,
    ),
    "subtitle": ParagraphStyle(
        "subtitle",
        fontName="LiansSans",
        fontSize=10.5,
        leading=15,
        textColor=MUTED,
        spaceAfter=14,
    ),
    "section": ParagraphStyle(
        "section",
        fontName="LiansSans-Bold",
        fontSize=10,
        leading=12,
        textColor=INK,
        spaceBefore=4,
        spaceAfter=6,
    ),
    "body": ParagraphStyle(
        "body",
        fontName="LiansSans",
        fontSize=8.7,
        leading=12.5,
        textColor=INK,
        alignment=TA_LEFT,
    ),
    "bullet": ParagraphStyle(
        "bullet",
        fontName="LiansSans",
        fontSize=8.5,
        leading=12,
        textColor=INK,
        leftIndent=10,
        firstLineIndent=-7,
        bulletIndent=0,
        spaceAfter=3,
    ),
    "price": ParagraphStyle(
        "price",
        fontName="LiansSans-Bold",
        fontSize=20,
        leading=22,
        textColor=BLUE,
    ),
    "price_label": ParagraphStyle(
        "price_label",
        fontName="LiansSans-Bold",
        fontSize=8.5,
        leading=11,
        textColor=INK,
    ),
    "small": ParagraphStyle(
        "small",
        fontName="LiansSans",
        fontSize=7.5,
        leading=10,
        textColor=MUTED,
    ),
    "callout": ParagraphStyle(
        "callout",
        fontName="LiansSans-Bold",
        fontSize=9,
        leading=13,
        textColor=INK,
    ),
    "section_white": ParagraphStyle(
        "section_white",
        fontName="LiansSans-Bold",
        fontSize=10,
        leading=12,
        textColor=WHITE,
    ),
    "callout_white": ParagraphStyle(
        "callout_white",
        fontName="LiansSans-Bold",
        fontSize=9,
        leading=13,
        textColor=WHITE,
    ),
}


def p(text, style="body"):
    return Paragraph(text, styles[style])


def bullet(text):
    return Paragraph(f"&bull; {text}", styles["bullet"])


def draw_page(canvas, doc):
    width, height = letter
    canvas.saveState()
    canvas.setFillColor(INK)
    canvas.rect(0, height - 0.23 * inch, width, 0.23 * inch, stroke=0, fill=1)
    canvas.setFillColor(BLUE)
    canvas.rect(0, height - 0.27 * inch, width, 0.04 * inch, stroke=0, fill=1)
    canvas.setStrokeColor(LINE)
    canvas.line(0.58 * inch, 0.55 * inch, width - 0.58 * inch, 0.55 * inch)
    canvas.setFont("LiansSans-Bold", 8)
    canvas.setFillColor(INK)
    canvas.drawString(0.58 * inch, 0.35 * inch, "LIANS AI, CORP.")
    canvas.setFont("LiansSans", 8)
    canvas.setFillColor(MUTED)
    canvas.drawRightString(
        width - 0.58 * inch, 0.35 * inch, "lians.ai  |  info@lians.ai"
    )
    canvas.restoreState()


doc = BaseDocTemplate(
    str(OUTPUT),
    pagesize=letter,
    leftMargin=0.58 * inch,
    rightMargin=0.58 * inch,
    topMargin=0.55 * inch,
    bottomMargin=0.72 * inch,
    title="Lians Paid Design Partnership",
    author="Lians AI, Corp.",
)
frame = Frame(
    doc.leftMargin,
    doc.bottomMargin,
    doc.width,
    doc.height,
    leftPadding=0,
    rightPadding=0,
    topPadding=0,
    bottomPadding=0,
)
doc.addPageTemplates(PageTemplate(id="one-page", frames=[frame], onPage=draw_page))

story = [
    p("PAID DESIGN PARTNERSHIP", "eyebrow"),
    p("AI Evidence Readiness Sprint", "title"),
    p(
        "Make one consequential AI workflow reconstructable, reviewable, and safer to change - "
        "without exposing production data.",
        "subtitle",
    ),
]

price_box = Table(
    [
        [
            p("$4,500", "price"),
            p("<b>Two weeks</b><br/>One sanitized workflow", "price_label"),
            p(
                "<b>$2,250 before kickoff</b><br/>$2,250 due within five business days of delivery",
                "price_label",
            ),
        ]
    ],
    colWidths=[1.35 * inch, 1.8 * inch, 3.5 * inch],
)
price_box.setStyle(
    TableStyle(
        [
            ("BACKGROUND", (0, 0), (-1, -1), PALE),
            ("BOX", (0, 0), (-1, -1), 0.8, BLUE),
            ("INNERGRID", (0, 0), (-1, -1), 0.4, LINE),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("LEFTPADDING", (0, 0), (-1, -1), 12),
            ("RIGHTPADDING", (0, 0), (-1, -1), 12),
            ("TOPPADDING", (0, 0), (-1, -1), 9),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 9),
        ]
    )
)
story.extend([price_box, Spacer(1, 14)])

left = [
    p("The problem", "section"),
    p(
        "AI actions depend on users, documents, permissions, policies, models, memory, and tool calls. "
        "Those inputs change. Ordinary logs show events, but often cannot prove the exact state that "
        "authorized a past action or what would break if evidence is corrected or deleted."
    ),
    Spacer(1, 8),
    p("What Lians adds", "section"),
    bullet("Decision-time evidence linking the actor, source, policy, model, memory, tool calls, approvals, and outcome."),
    bullet("Point-in-time reconstruction after sources, permissions, or policies change."),
    bullet("Downstream impact mapping before a memory or evidence record is changed or deleted."),
    bullet("Quality checks for stale, conflicting, duplicated, unsourced, and broken memory."),
]

right = [
    p("What the sprint delivers", "section"),
    bullet("One agreed, synthetic or sanitized workflow."),
    bullet("A source-system or OpenTelemetry-to-Lians evidence path."),
    bullet("A reconstructed decision and verifiable evidence receipt."),
    bullet("Memory-quality and broken-reference findings."),
    bullet("A downstream-impact demonstration."),
    bullet("An implementation gap report and executive walkthrough."),
    Spacer(1, 8),
    p("A successful sprint proves", "section"),
    p(
        "Your team can answer: <b>What did the system know? Why did it act? Who or what authorized it? "
        "Which version applied? What changes downstream if the evidence changes?</b>"
    ),
]

columns = Table(
    [[left, right]],
    colWidths=[3.25 * inch, 3.5 * inch],
    hAlign="LEFT",
)
columns.setStyle(
    TableStyle(
        [
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("LEFTPADDING", (0, 0), (0, 0), 0),
            ("RIGHTPADDING", (0, 0), (0, 0), 15),
            ("LEFTPADDING", (1, 0), (1, 0), 15),
            ("RIGHTPADDING", (1, 0), (1, 0), 0),
            ("LINEBEFORE", (1, 0), (1, 0), 0.6, LINE),
        ]
    )
)
story.extend([columns, Spacer(1, 12)])

guardrail = Table(
    [
        [
            p(
                "Commercial boundary",
                "section_white",
            ),
            p(
                "This is a paid design partnership - not a free pilot, unpaid proof of concept, or open-ended implementation. "
                "Work begins after a signed order form and cleared kickoff payment.",
                "callout_white",
            ),
        ]
    ],
    colWidths=[1.3 * inch, 5.45 * inch],
)
guardrail.setStyle(
    TableStyle(
        [
            ("BACKGROUND", (0, 0), (-1, -1), INK),
            ("TEXTCOLOR", (0, 0), (-1, -1), WHITE),
            ("BOX", (0, 0), (-1, -1), 0.8, INK),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("LEFTPADDING", (0, 0), (-1, -1), 11),
            ("RIGHTPADDING", (0, 0), (-1, -1), 11),
            ("TOPPADDING", (0, 0), (-1, -1), 8),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
        ]
    )
)
story.extend(
    [
        guardrail,
        Spacer(1, 10),
        KeepTogether(
            [
                p("Next step", "section"),
                p(
                    "Name the workflow, executive budget owner, and success criterion. Lians will issue the scoped order form "
                    "and kickoff invoice. <b>Contact: info@lians.ai</b>"
                ),
            ]
        ),
    ]
)

doc.build(story)
print(OUTPUT)
