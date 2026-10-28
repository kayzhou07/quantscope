from pathlib import Path
from xml.sax.saxutils import escape
import re, os
from reportlab.platypus import (
    SimpleDocTemplate,
    Paragraph,
    Spacer,
    PageBreak,
    Image,
    Table,
    TableStyle,
)
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib import colors
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.lib.pagesizes import A4
from PIL import Image as PILImage
from pypdf import PdfReader

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent if (HERE.parent / "src/quantscope").exists() else HERE / "quantscope"
D = ROOT / "docs/business-plan"
FONT = os.environ.get(
    "QUANTSCOPE_SONGTI_FONT", "/System/Library/Fonts/Supplemental/Songti.ttc"
)
pdfmetrics.registerFont(TTFont("Song", FONT, subfontIndex=6))
pdfmetrics.registerFont(TTFont("SongBold", FONT, subfontIndex=1))
pdfmetrics.registerFontFamily(
    "Song", normal="Song", bold="SongBold", italic="Song", boldItalic="SongBold"
)
styles = {
    "title": ParagraphStyle(
        "title",
        fontName="SongBold",
        fontSize=20,
        leading=25,
        textColor=colors.HexColor("#142B3B"),
        spaceAfter=16,
    ),
    "body": ParagraphStyle(
        "body", fontName="Song", fontSize=10.5, leading=15.5, spaceAfter=10
    ),
    "code": ParagraphStyle(
        "code",
        fontName="Song",
        fontSize=7.8,
        leading=11.2,
        leftIndent=9,
        rightIndent=9,
        backColor=colors.HexColor("#F1F5F7"),
        spaceAfter=10,
    ),
    "caption": ParagraphStyle(
        "caption",
        fontName="Song",
        fontSize=8.3,
        leading=11.5,
        textColor=colors.HexColor("#5F6D75"),
        spaceAfter=12,
    ),
    "cell": ParagraphStyle("cell", fontName="Song", fontSize=8.7, leading=12),
    "head": ParagraphStyle(
        "head", fontName="SongBold", fontSize=8.7, leading=12, textColor=colors.white
    ),
}
md = (D / "business-plan.en.md").read_text()
sections = re.split(r"^## ", md, flags=re.M)[1:]
assert len(sections) == 15
story = []
for index, section in enumerate(sections):
    title, content = section.split("\n", 1)
    if index:
        story.append(PageBreak())
    story.append(Paragraph(escape(title), styles["title"]))
    lines = content.strip().splitlines()
    i = 0
    while i < len(lines):
        line = lines[i].strip()
        if not line:
            i += 1
            continue
        if line.startswith("```"):
            code = []
            i += 1
            while not lines[i].startswith("```"):
                code.append(lines[i])
                i += 1
            story.append(
                Paragraph(
                    "<br/>".join(escape(x).replace(" ", "&#160;") for x in code),
                    styles["code"],
                )
            )
            i += 1
            continue
        if line.startswith("!["):
            path = re.search(r"\]\(([^)]+)\)", line).group(1)
            img = PILImage.open(D / path)
            width = 320 if index in (7,) else 420
            height = width * img.height / img.width
            maxh = 195 if index in (3, 5, 9) else 275
            if height > maxh:
                height = maxh
                width = height * img.width / img.height
            image = Image(str(D / path), width=width, height=height)
            image.hAlign = "CENTER"
            story.append(image)
            story.append(Spacer(1, 6))
            i += 1
            continue
        if line.startswith("|"):
            rows = []
            while i < len(lines) and lines[i].strip().startswith("|"):
                cells = [x.strip() for x in lines[i].strip().strip("|").split("|")]
                if not all(re.fullmatch(r"[-: ]+", c) for c in cells):
                    rows.append(cells)
                i += 1
            data = [
                [
                    Paragraph(escape(c), styles["head"] if j == 0 else styles["cell"])
                    for c in row
                ]
                for j, row in enumerate(rows)
            ]
            t = Table(data, colWidths=[128, 289, 82])
            t.setStyle(
                TableStyle(
                    [
                        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#142B3B")),
                        (
                            "ROWBACKGROUNDS",
                            (0, 1),
                            (-1, -1),
                            [colors.white, colors.HexColor("#F1F5F7")],
                        ),
                        ("VALIGN", (0, 0), (-1, -1), "TOP"),
                        ("TOPPADDING", (0, 0), (-1, -1), 7),
                        ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
                    ]
                )
            )
            story.append(t)
            story.append(Spacer(1, 12))
            continue
        paragraph = [line]
        i += 1
        while (
            i < len(lines)
            and lines[i].strip()
            and not lines[i].startswith(("```", "![", "|"))
        ):
            paragraph.append(lines[i].strip())
            i += 1
        body = " ".join(paragraph)
        iscap = body.startswith(
            (
                "Historical QuantConnect",
                "A separate historical",
                "Historical system",
                "Historical exhibition",
                "Research workspace",
                "Earlier capital",
                "Historical SuperMind",
                "Historical platform",
            )
        )
        story.append(
            Paragraph(escape(body), styles["caption"] if iscap else styles["body"])
        )


def footer(c, doc):
    c.setFillColor(colors.HexColor("#5F6D75"))
    c.setFont("Song", 8)
    c.drawString(48, A4[1] - 28, "QuantScope · 量见")
    c.drawRightString(A4[0] - 48, A4[1] - 28, "Company Overview and Business Plan")
    c.drawString(48, 25, "English edition")
    c.drawRightString(A4[0] - 48, 25, f"{doc.page:02d} / 15")


pdf = D / "QuantScope-Business-Plan-EN.pdf"
doc = SimpleDocTemplate(
    str(pdf),
    pagesize=A4,
    rightMargin=48,
    leftMargin=48,
    topMargin=64,
    bottomMargin=48,
    title="QuantScope — Company Overview and Business Plan",
    author="QuantScope",
)
doc.build(story, onFirstPage=footer, onLaterPages=footer)
reader = PdfReader(pdf)
print("English plan pages:", len(reader.pages))
assert len(reader.pages) == 15
for text in [
    "QuantVela",
    "QuantOrion",
    "QuantLibra",
    "QuantPyxis",
    "48,468",
    "Formerly AlphaBoard",
    "Formerly QuantBalance",
]:
    assert text in "\n".join(p.extract_text() for p in reader.pages), text
