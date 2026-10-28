from fpdf import FPDF
import os, json

pdf = FPDF()
pdf.set_left_margin(15)
pdf.set_right_margin(15)
pdf.add_page()
pdf.add_font("H", "", "C:/Windows/Fonts/simhei.ttf")
pdf.add_font("HB", "", "C:/Windows/Fonts/simhei.ttf")
pdf.add_font("S", "", "C:/Windows/Fonts/simsun.ttc")
pdf.set_auto_page_break(True, 15)

def title(t):
    pdf.set_fill_color(235, 235, 240)
    pdf.set_font("HB", "", 12)
    pdf.cell(0, 8, " " + t, fill=True, new_x="LMARGIN", new_y="NEXT")
    pdf.ln(4)

def body(t):
    pdf.set_font("S", "", 10)
    pdf.multi_cell(0, 5.5, t)
    pdf.ln(1.5)

def dot(t):
    pdf.set_font("S", "", 10)
    pdf.cell(4, 5.5, "")
    pdf.cell(3, 5.5, "-")
    pdf.multi_cell(0, 5.5, t)

BASE = os.path.dirname(os.path.abspath(__file__))
with open(os.path.join(BASE, "pdf_content.json"), "r", encoding="utf-8") as f:
    content = json.load(f)

pdf.set_font("HB", "", 16)
pdf.cell(0, 12, content["title"], align="C", new_x="LMARGIN", new_y="NEXT")
pdf.set_font("H", "", 11)
pdf.cell(0, 7, content["subtitle"], align="C", new_x="LMARGIN", new_y="NEXT")
pdf.ln(6)

for sec in content["sections"]:
    title(sec["heading"])
    for item in sec["items"]:
        if item["type"] == "body":
            body(item["text"])
        elif item["type"] == "dot":
            dot(item["text"])

    if "table" in sec:
        pdf.set_font("HB", "", 9)
        for c, w in zip(["指标", "缩量连板", "ETF轮动", "合并模式", ""], [42, 28, 28, 28, 64]):
            pdf.cell(w, 7, c, border=1, align="C")
        pdf.ln()
        pdf.set_font("S", "", 9)
        for r in sec["table"]:
            for c, w in zip(r, [42, 28, 28, 28, 64]):
                pdf.cell(w, 6.5, c, border=1, align="C")
            pdf.ln()
        pdf.ln(4)

    if "flow" in sec:
        pdf.set_font("S", "", 9)
        for step in sec["flow"]:
            if step == "arrow":
                pdf.cell(0, 6, "    |", align="C", new_x="LMARGIN", new_y="NEXT")
                pdf.cell(0, 3, "    v", align="C", new_x="LMARGIN", new_y="NEXT")
            elif step.startswith("box:"):
                pdf.set_fill_color(245, 248, 252)
                pdf.cell(170, 7, step[4:], border=1, fill=True, align="C", new_x="LMARGIN", new_y="NEXT")
            elif step.startswith("green:"):
                pdf.set_fill_color(230, 255, 240)
                pdf.cell(170, 7, step[6:], border=1, fill=True, align="C", new_x="LMARGIN", new_y="NEXT")
            elif step.startswith("blue:"):
                pdf.set_fill_color(220, 240, 255)
                pdf.cell(170, 7, step[5:], border=1, fill=True, align="C", new_x="LMARGIN", new_y="NEXT")
            elif step.startswith("dual:"):
                pdf.set_fill_color(245, 248, 252)
                pdf.cell(170, 14, step[5:], border=1, fill=True, align="C", new_x="LMARGIN", new_y="NEXT")
        pdf.ln(4)

path = os.path.join(BASE, "作品设计书_QuantOrion缩量连板量化选股系统.pdf")
pdf.output(path)
print("Done: " + path)
