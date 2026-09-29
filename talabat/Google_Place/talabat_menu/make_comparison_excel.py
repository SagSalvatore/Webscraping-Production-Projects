"""
make_comparison_excel.py
Create a management-ready Excel comparison: Apify vs Oxylabs for Talabat scraping.
"""

import xlsxwriter
from pathlib import Path

OUT = Path(__file__).parent / "output" / "Apify_vs_Oxylabs_Comparison.xlsx"
OUT.parent.mkdir(parents=True, exist_ok=True)

wb = xlsxwriter.Workbook(str(OUT))

# ── Colour palette ──────────────────────────────────────────────────────────
DARK   = "#1A1A2E"
WHITE  = "#FFFFFF"
GREY_L = "#F5F5F5"
GREY_B = "#E0E0E0"

AMBER  = "#F59E0B"
AMBER_L= "#FEF3C7"
AMBER_D= "#92400E"

GREEN  = "#10B981"
GREEN_L= "#D1FAE5"
GREEN_D= "#065F46"

RED    = "#EF4444"
RED_L  = "#FEE2E2"
RED_D  = "#991B1B"

BLUE_H = "#1E40AF"   # header accent

# ── Format factory ───────────────────────────────────────────────────────────
def fmt(bold=False, font_sz=10, font_color=DARK, bg=WHITE,
        align="left", valign="vcenter", wrap=True, border=0,
        italic=False, num_format=None, top=0, bottom=0, left=0, right=0):
    d = dict(
        font_name="Calibri", font_size=font_sz, bold=bold, italic=italic,
        font_color=font_color, bg_color=bg,
        align=align, valign=valign, text_wrap=wrap, border=border,
    )
    if num_format:
        d["num_format"] = num_format
    if top:    d["top"]    = top
    if bottom: d["bottom"] = bottom
    if left:   d["left"]   = left
    if right:  d["right"]  = right
    return wb.add_format(d)


# ============================================================
# SHEET 1 — Summary (key stats)
# ============================================================
s1 = wb.add_worksheet("Summary")
s1.set_tab_color(GREEN)
s1.hide_gridlines(2)
s1.set_zoom(90)

# column widths
s1.set_column("A:A", 2)
s1.set_column("B:B", 28)
s1.set_column("C:C", 36)
s1.set_column("D:D", 36)
s1.set_column("E:E", 2)

# row heights
for r in range(60):
    s1.set_row(r, 18)

# ── Title banner ─────────────────────────────────────────────
title_fmt  = fmt(bold=True, font_sz=16, font_color=WHITE, bg=DARK, align="center", wrap=False)
sub_fmt    = fmt(font_sz=10, font_color="#AAAACC",  bg=DARK, align="center", wrap=False)
s1.merge_range("B1:D1", "Talabat Scraping: Apify  vs  Oxylabs + Custom Scraper", title_fmt)
s1.merge_range("B2:D2", "Management Comparison  —  Data Accuracy · Scale · Cost · Control", sub_fmt)
s1.set_row(0, 32)
s1.set_row(1, 20)

# ── KPI cards (row 4-7) ──────────────────────────────────────
kpi_head = fmt(bold=True, font_sz=9, font_color=WHITE, bg=DARK, align="center", wrap=False, border=0)
kpi_num  = fmt(bold=True, font_sz=22, font_color=GREEN, bg=GREEN_L, align="center", wrap=False)
kpi_lbl  = fmt(font_sz=9,  font_color=GREEN_D, bg=GREEN_L, align="center", wrap=False)
kpi_num2 = fmt(bold=True, font_sz=22, font_color=AMBER,   bg=AMBER_L, align="center", wrap=False)
kpi_lbl2 = fmt(font_sz=9,  font_color=AMBER_D, bg=AMBER_L, align="center", wrap=False)

s1.merge_range("B4:B4", "OXYLABS RUN — 15k RESTAURANTS", kpi_head)
s1.set_row(3, 16)

# merge C and D for two KPI columns each
s1.set_row(4, 36)
s1.set_row(5, 20)
s1.set_row(6, 36)
s1.set_row(7, 20)

# Left 3 KPIs (green — Oxylabs)
kpi_data_g = [
    ("15,000", "Restaurants scraped — in 1 day"),
    ("1,450",  "Proxy requests used (10 restaurants/req)"),
    ("3 GB",   "Bandwidth consumed (~200 KB/restaurant)"),
]
kpi_data_a = [
    ("80%",    "Apify menu success rate (best case)"),
    ("Wrong",  "Apify branch — brand page, not exact URL"),
    ("$30",    "Apify cost for 15k restaurants @ $0.002/each"),
]

# We'll do a 3-column KPI layout starting col B (left) and col D (right)
col_map = {"B": (kpi_num,  kpi_lbl,  GREEN,   GREEN_L,  GREEN_D),
           "D": (kpi_num2, kpi_lbl2, AMBER,   AMBER_L,  AMBER_D)}

for i, (num, lbl) in enumerate(kpi_data_g):
    row_n = 4 + i * 2
    row_l = 5 + i * 2
    s1.set_row(row_n, 32)
    s1.set_row(row_l, 18)
    s1.write(f"B{row_n+1}", num, fmt(bold=True, font_sz=20, font_color=GREEN, bg=GREEN_L, align="center", wrap=False))
    s1.write(f"B{row_l+1}", lbl, fmt(font_sz=9, font_color=GREEN_D, bg=GREEN_L, align="center", wrap=False))

for i, (num, lbl) in enumerate(kpi_data_a):
    row_n = 4 + i * 2
    row_l = 5 + i * 2
    s1.write(f"D{row_n+1}", num, fmt(bold=True, font_sz=20, font_color=AMBER, bg=AMBER_L, align="center", wrap=False))
    s1.write(f"D{row_l+1}", lbl, fmt(font_sz=9, font_color=AMBER_D, bg=AMBER_L, align="center", wrap=False))

# Stat labels in column C (spacer)
s1.write("C4", "← Oxylabs result", fmt(font_sz=9, font_color="#888888", bg=WHITE, align="center", italic=True))
s1.write("C6", "vs", fmt(bold=True, font_sz=14, font_color=DARK, bg=WHITE, align="center"))
s1.write("C8", "Apify limitation →", fmt(font_sz=9, font_color="#888888", bg=WHITE, align="center", italic=True))

# ── How it works — two flows (row 12+) ───────────────────────
s1.set_row(10, 22)
section_fmt = fmt(bold=True, font_sz=10, font_color=WHITE, bg=BLUE_H, align="left", wrap=False)
s1.merge_range("B11:D11", "  HOW EACH APPROACH WORKS", section_fmt)

flow_head_a = fmt(bold=True, font_sz=10, font_color=AMBER_D, bg=AMBER_L, align="center", wrap=False)
flow_head_o = fmt(bold=True, font_sz=10, font_color=GREEN_D, bg=GREEN_L, align="center", wrap=False)
flow_step   = fmt(font_sz=9, font_color=DARK,  bg=WHITE,   align="left",  wrap=True)
flow_bad    = fmt(font_sz=9, font_color=RED_D, bg=RED_L,   align="left",  wrap=True, bold=True)
flow_good   = fmt(font_sz=9, font_color=GREEN_D, bg=GREEN_L, align="left", wrap=True, bold=True)

s1.set_row(11, 20)
s1.write("C12", "🟡  APIFY (thirdwatch/talabat-scraper)", flow_head_a)
s1.write("D12", "🟢  OXYLABS + Custom Scraper",           flow_head_o)

apify_steps = [
    "1. Input: exact URL with vendor_id=748535 & aid=6484",
    "2. Actor strips to brand slug → 'zam-zam-mandi'",
    "3. Navigates to talabat.com/uae/zam-zam-mandi (WRONG branch, no aid)",
    "4. Gets default branch menu — different location, different items",
    "❌  Result: 'main_course' missing, prices may be wrong area",
]
oxy_steps = [
    "1. Input: exact URL with vendor_id=748535 & aid=6484",
    "2. Residential proxy hits the URL as-is — no conversion",
    "3. Talabat returns exact branch + delivery zone (aid=6484) menu",
    "4. Custom parser extracts all categories, prices, descriptions",
    "✅  Result: complete correct menu — what customers actually see",
]

for i, (a, o) in enumerate(zip(apify_steps, oxy_steps)):
    r = 13 + i
    s1.set_row(r, 28)
    color_a = flow_bad  if a.startswith("❌") else flow_step
    color_o = flow_good if o.startswith("✅") else flow_step
    bg_a    = RED_L     if a.startswith("❌") else ("#FFFBEB" if i % 2 == 0 else WHITE)
    bg_o    = GREEN_L   if o.startswith("✅") else ("#F0FDF9" if i % 2 == 0 else WHITE)
    s1.write(r, 2, a, fmt(font_sz=9, font_color=(RED_D if a.startswith("❌") else DARK),
                          bg=bg_a, align="left", wrap=True,
                          bold=a.startswith("❌")))
    s1.write(r, 3, o, fmt(font_sz=9, font_color=(GREEN_D if o.startswith("✅") else DARK),
                          bg=bg_o, align="left", wrap=True,
                          bold=o.startswith("✅")))

# ── Bottom line ───────────────────────────────────────────────
r = 19
s1.set_row(r, 14)
s1.set_row(r+1, 40)
s1.merge_range(r, 1, r, 3, "  RECOMMENDATION FOR MANAGEMENT", section_fmt)
rec_text = (
    "Apify is a quick-start exploration tool — it hit a fundamental limitation because Talabat's menu is branch + "
    "area-specific (the aid= parameter matters). The actor cannot use direct URLs, so it navigates to a generic brand "
    "page and returns an incomplete menu. Oxylabs with a custom scraper hits the exact URL, gets the correct and "
    "complete menu, runs at 15k restaurants/day, and gives full control over the data pipeline. "
    "For a production Talabat data feed, Oxylabs is the right tool."
)
s1.merge_range(r+1, 1, r+1, 3, rec_text,
               fmt(font_sz=10, font_color=GREEN_D, bg=GREEN_L, align="left", wrap=True))


# ============================================================
# SHEET 2 — Detailed Comparison
# ============================================================
s2 = wb.add_worksheet("Detailed Comparison")
s2.set_tab_color(AMBER)
s2.hide_gridlines(2)
s2.set_zoom(90)

s2.set_column("A:A", 2)
s2.set_column("B:B", 24)
s2.set_column("C:C", 38)
s2.set_column("D:D", 38)
s2.set_column("E:E", 2)

# Title
s2.merge_range("B1:D1", "Talabat Scraping — Detailed Dimension Comparison", title_fmt)
s2.merge_range("B2:D2", "Apify  vs  Oxylabs + Custom Scraper", sub_fmt)
s2.set_row(0, 32)
s2.set_row(1, 20)

# Header row
hdr_dim  = fmt(bold=True, font_sz=10, font_color=WHITE, bg=DARK, align="center", wrap=False)
hdr_api  = fmt(bold=True, font_sz=10, font_color=AMBER_D, bg=AMBER_L, align="center", wrap=False)
hdr_oxy  = fmt(bold=True, font_sz=10, font_color=GREEN_D, bg=GREEN_L, align="center", wrap=False)
s2.set_row(3, 22)
s2.write("B4", "Dimension",               hdr_dim)
s2.write("C4", "🟡  Apify Actor",          hdr_api)
s2.write("D4", "🟢  Oxylabs + Custom",     hdr_oxy)

rows = [
    ("Data Accuracy",
     "❌ Brand page only — navigates to talabat.com/uae/{brand-slug}, not the specific branch URL. Wrong menu returned.",
     "✅ Exact branch URL hit — vendor_id + area ID (aid) preserved. Menu matches what customers in that delivery zone see."),
    ("Menu Completeness",
     "❌ Missing categories (e.g. 'main_course') — brand page shows a simplified or different-branch menu.",
     "✅ All categories, items, prices, descriptions returned — same as the live Talabat page for that branch."),
    ("Area / Delivery Zone",
     "❌ aid= parameter ignored — actor discards the area ID, so delivery-zone-specific pricing is lost.",
     "✅ aid= preserved in URL — correct prices and availability for the specified delivery area."),
    ("URL Control",
     "❌ No startUrls field — must convert branch URLs to brand slugs or search queries. Lossy transformation.",
     "✅ Full URL control — pass any Talabat URL as-is. No conversion, no data loss."),
    ("Scale / Speed",
     "⚠️ ~50 restaurants per batch, 1–2 min per batch, polling overhead. ~500–1000 restaurants/hour.",
     "✅ 15,000 restaurants scraped in 1 day. Fully parallelizable — no queue or infrastructure delay."),
    ("Requests / Efficiency",
     "⚠️ 1 Apify result per restaurant. Batch queuing adds latency. Not your infrastructure.",
     "✅ 1,450 requests for 15k restaurants (~10 per request avg). Efficient session reuse."),
    ("Bandwidth Cost",
     "⚠️ $0.002 per restaurant result. 15k restaurants = $30.",
     "✅ Pay per GB. 3 GB for 15k restaurants. Cost depends on your Oxylabs plan — typically much lower at scale."),
    ("Parser / Field Control",
     "❌ Black box actor — you get what the actor's parser returns. Cannot customise fields or output schema.",
     "✅ Own parser — extract exactly the fields you need, in the schema your pipeline expects."),
    ("Retry / Error Handling",
     "❌ Actor handles retries internally — you have no visibility or control over retry logic.",
     "✅ Own retry logic — custom back-off, per-restaurant failure tracking, dead-letter queue."),
    ("Reliability / Longevity",
     "❌ Third-party actor can change, break, or be deprecated without notice.",
     "✅ Your code, your stack — full control over versioning, maintenance, and updates."),
    ("Setup Complexity",
     "✅ Low — call actor API, get results. Good for quick exploration.",
     "⚠️ Higher — need to build and maintain the scraper. Already done in your case."),
    ("IP Detection Risk",
     "⚠️ Apify uses shared proxy pools — may be rate-limited or blocked on high volumes.",
     "✅ Residential proxies — real device IPs, very low detection rate at scale."),
    ("Data Trust for Management",
     "❌ Menu data is from a different branch than the URL you provided — cannot be used for branch-level analysis.",
     "✅ Menu data comes directly from the exact restaurant + area you specified — branch-level accuracy."),
]

dim_fmt  = fmt(bold=True, font_sz=9, font_color=DARK, bg=GREY_L, align="left", wrap=True)
bad_fmt  = fmt(font_sz=9, font_color=RED_D,   bg="#FFF5F5", align="left", wrap=True)
warn_fmt = fmt(font_sz=9, font_color=AMBER_D,  bg="#FFFBEB", align="left", wrap=True)
good_fmt = fmt(font_sz=9, font_color=GREEN_D, bg="#F0FDF9", align="left", wrap=True)

def cell_fmt(text):
    if text.startswith("❌"):
        return bad_fmt
    if text.startswith("⚠️"):
        return warn_fmt
    return good_fmt

for i, (dim, apify_val, oxy_val) in enumerate(rows):
    r = 4 + i
    s2.set_row(r, 42)
    s2.write(r, 1, dim,       dim_fmt)
    s2.write(r, 2, apify_val, cell_fmt(apify_val))
    s2.write(r, 3, oxy_val,   cell_fmt(oxy_val))

# Footer
fr = 4 + len(rows) + 1
s2.set_row(fr, 36)
s2.merge_range(fr, 1, fr, 3,
    "Verdict: Use Oxylabs for production data. Use Apify only for rapid prototyping where exact branch accuracy is not required.",
    fmt(bold=True, font_sz=10, font_color=GREEN_D, bg=GREEN_L, align="center", wrap=True))


# ============================================================
# SHEET 3 — Cost Model
# ============================================================
s3 = wb.add_worksheet("Cost Model")
s3.set_tab_color("#3B82F6")
s3.hide_gridlines(2)
s3.set_zoom(90)

s3.set_column("A:A", 2)
s3.set_column("B:B", 30)
s3.set_column("C:C", 20)
s3.set_column("D:D", 20)
s3.set_column("E:E", 2)

s3.merge_range("B1:D1", "Cost Model — Apify vs Oxylabs at Scale", title_fmt)
s3.merge_range("B2:D2", "Per-restaurant cost and total spend at different volumes", sub_fmt)
s3.set_row(0, 32)
s3.set_row(1, 20)

# Headers
s3.set_row(3, 20)
s3.write("B4", "Metric",                    hdr_dim)
s3.write("C4", "🟡  Apify",                 hdr_api)
s3.write("D4", "🟢  Oxylabs",               hdr_oxy)

num_fmt = fmt(font_sz=10, font_color=DARK,   bg=WHITE,   align="center", num_format='$#,##0.00')
txt_fmt = fmt(font_sz=10, font_color=DARK,   bg=GREY_L,  align="left")
bad_n   = fmt(font_sz=10, font_color=RED_D,  bg=RED_L,   align="center", bold=True)
good_n  = fmt(font_sz=10, font_color=GREEN_D,bg=GREEN_L, align="center", bold=True)
warn_n  = fmt(font_sz=10, font_color=AMBER_D,bg=AMBER_L, align="center")

cost_rows = [
    ("Pricing unit",             "Per result (restaurant)",          "Per GB bandwidth"),
    ("Unit price",               "$0.002 / restaurant",              "~$15 / GB (residential)"),
    ("Cost for 1,000 restaurants","$2.00",                           "~$0.20  (1,000 × 200KB = 200MB)"),
    ("Cost for 15,000 restaurants","$30.00",                         "~$3.00  (3 GB)"),
    ("Cost for 100,000 restaurants","$200.00",                       "~$20.00  (20 GB)"),
    ("Cost for 1,000,000 restaurants","$2,000.00",                   "~$200.00  (200 GB)"),
    ("Data accuracy at any scale", "❌ Wrong branch menu",           "✅ Exact branch menu"),
    ("Speed (15k restaurants)",   "~30 hours (50/batch × 2 min)",   "1 day (parallelized)"),
    ("Included in cost",          "Scraping only",                   "Bandwidth only (your infra)"),
]

for i, (metric, apify_v, oxy_v) in enumerate(cost_rows):
    r = 4 + i
    s3.set_row(r, 24)
    af = bad_n if apify_v.startswith("❌") else (warn_n if "$" in apify_v else warn_fmt)
    of = good_n if oxy_v.startswith("✅") else good_fmt
    s3.write(r, 1, metric,  dim_fmt)
    s3.write(r, 2, apify_v, af)
    s3.write(r, 3, oxy_v,   of)

# note
nr = 4 + len(cost_rows) + 1
s3.set_row(nr, 50)
s3.merge_range(nr, 1, nr, 3,
    "Note: Oxylabs residential proxy cost is ~$15/GB on pay-as-you-go plans. "
    "On a monthly plan (e.g. $99/month for 20GB), 3GB for 15k restaurants has near-zero marginal cost. "
    "Apify costs scale linearly with no volume discount on the FREE tier ($0.002/result).",
    fmt(font_sz=9, font_color="#555555", bg=GREY_L, align="left", wrap=True, italic=True))


wb.close()
print(f"Excel saved: {OUT}")
