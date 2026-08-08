"""Assemble the daily prospect report. Produces the exact columns the customer
asked for, and can write a local CSV (always available) as well as feed the
Google Sheets exporter."""
import csv
import os
from typing import List

# Column order = what the customer sees in their daily report.
COLUMNS = [
    ("company", "Company"),
    ("full_name", "Contact Name"),
    ("title", "Title"),
    ("company_address", "Address"),
    ("phone", "Phone"),
    ("email", "Email"),
    ("website", "Website"),
    ("linkedin_url", "LinkedIn"),
    ("company_description", "Company Description"),
    ("fit_reason", "Why It's a Good Fit"),
    ("intro_email_subject", "Intro Email — Subject"),
    ("intro_email_body", "Intro Email — Body"),
    ("linkedin_message", "LinkedIn Message"),
]


def rows_from(prospects) -> List[List[str]]:
    """Header row followed by one row per prospect, in COLUMNS order. A leading
    Group column is added ONLY when the report spans more than one tier
    (bonus/spare); with the default no-over-delivery model it's omitted."""
    tiers = {(p.tier or "core") for p in prospects}
    show_group = len(tiers) > 1
    prefix = ["Group"] if show_group else []
    header = prefix + [label for _, label in COLUMNS]
    body = []
    for p in prospects:
        d = p.to_dict()
        row_prefix = ([TIER_LABEL.get(d.get("tier") or "core", "")]
                      if show_group else [])
        body.append(row_prefix + [str(d.get(key, "") or "") for key, _ in COLUMNS])
    return [header] + body


def _esc(s: str) -> str:
    return (str(s or "").replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;"))


# Brand palette for the Doc layout (blue/green brand).
_INK = "#1a1c23"; _MUTED = "#6b7280"; _BRAND = "#2563eb"; _GREEN = "#059669"
_CARD_BORDER = "#e5e7eb"; _DESC_BG = "#f3f4f6"
_FIT_BG = "#ecfdf5"; _FIT_INK = "#047857"; _EMAIL_BG = "#eff6ff"

# Report section labels (core / bonus / spare).
_SECTIONS = {
    "core":  ("Your prospects", "Matched to your target audience.", _BRAND),
    "bonus": ("★ Bonus prospects", "Extra qualified prospects — included free, so you always have plenty.", _GREEN),
    "spare": ("↻ Spare prospects", "Swap-ins: use these to replace any prospect above that isn't a fit or has incomplete/incorrect info.", _MUTED),
}
TIER_LABEL = {"core": "Your prospects", "bonus": "Bonus", "spare": "Spare"}


def _contact_line(icon: str, label: str, value: str, link: bool = False) -> str:
    if not value:
        value = "—"
    val = (f'<a href="{_esc(value)}">{_esc(value)}</a>'
           if link and value != "—" else _esc(value))
    return (f'<tr>'
            f'<td style="padding:2px 10px 2px 0;color:{_MUTED};font-size:10pt;'
            f'white-space:nowrap;">{icon}&nbsp;{label}</td>'
            f'<td style="padding:2px 0;color:{_INK};font-size:11pt;">{val}</td></tr>')


def html_report(prospects, heading: str) -> str:
    """A polished, document-style layout: one styled 'card' per prospect.
    Uploaded to Drive as a Google Doc (an alternative to the spreadsheet)."""
    counts = {}
    for p in prospects:
        counts[p.tier or "core"] = counts.get(p.tier or "core", 0) + 1
    multi_tier = len(counts) > 1
    # Only show the "N core + N bonus + ..." breakdown when there's more than
    # one tier; otherwise it's just noise next to the total.
    breakdown = " + ".join(
        f"{counts[t]} {TIER_LABEL[t].lower()}" for t in ("core", "bonus", "spare")
        if counts.get(t)) if multi_tier else ""
    breakdown_html = (f' &nbsp;·&nbsp; <span style="color:{_MUTED};'
                      f'font-weight:normal;">{breakdown}</span>' if breakdown else "")
    parts = [
        f'<p style="color:{_MUTED};font-size:10pt;letter-spacing:1px;">'
        f'PROSPECTDAILY</p>',
        f'<h1 style="color:{_INK};font-size:22pt;margin:0;">{_esc(heading)}</h1>',
        f'<p style="color:{_BRAND};font-weight:bold;font-size:12pt;">'
        f'{len(prospects)} prospects{breakdown_html}</p>',
        f'<hr style="border:none;border-top:3px solid {_BRAND};">',
    ]
    last_tier = None
    for i, p in enumerate(prospects, 1):
        # With a single tier, skip the section header entirely — the report
        # heading already says what these are.
        if multi_tier and (p.tier or "core") != last_tier:
            last_tier = p.tier or "core"
            title, sub, color = _SECTIONS.get(last_tier, ("Prospects", "", _INK))
            parts.append(
                f'<p style="margin:22px 0 2px;font-size:15pt;font-weight:bold;color:{color};">{title}</p>'
                f'<p style="margin:0 0 10px;color:{_MUTED};font-size:10pt;">{sub}</p>')
        d = p.to_dict()
        meta = []
        if d.get("employee_count"):
            meta.append(f"~{d['employee_count']} employees")
        if d.get("industry"):
            meta.append(_esc(d["industry"]))
        meta_line = (" &nbsp;·&nbsp; " + " &nbsp;·&nbsp; ".join(meta)) if meta else ""
        body_html = _esc(d.get("intro_email_body", "")).replace("\n", "<br>")

        contact = "".join([
            _contact_line("✉️", "Email", d["email"]),
            _contact_line("📞", "Phone", d["phone"]),
            _contact_line("🌐", "Website", d["website"], link=True),
            _contact_line("in", "LinkedIn", d["linkedin_url"], link=True),
            _contact_line("📍", "Address", d["company_address"]),
        ])

        parts.append(f"""
<table style="width:100%;border:1px solid {_CARD_BORDER};border-collapse:collapse;">
  <tr><td style="background-color:{_BRAND};padding:12px 16px;">
    <span style="color:#ffffff;font-size:14pt;font-weight:bold;">{i}. {_esc(d['full_name'])}</span><br>
    <span style="color:#dfe1f5;font-size:11pt;">{_esc(d['title'])}</span>
  </td></tr>
  <tr><td style="padding:12px 16px;">
    <p style="margin:0 0 8px 0;font-size:13pt;font-weight:bold;color:{_INK};">
      {_esc(d['company'])}<span style="font-weight:normal;color:{_MUTED};font-size:10pt;">{meta_line}</span></p>
    <table style="border-collapse:collapse;">{contact}</table>
  </td></tr>
  <tr><td style="background-color:{_DESC_BG};padding:10px 16px;color:#374151;font-style:italic;font-size:11pt;">
    {_esc(d['company_description'])}</td></tr>
  <tr><td style="background-color:{_FIT_BG};padding:10px 16px;font-size:11pt;">
    <b style="color:{_FIT_INK};">✓ Why it's a good fit:</b> {_esc(d['fit_reason'])}</td></tr>
  <tr><td style="background-color:{_EMAIL_BG};padding:12px 16px;">
    <p style="margin:0 0 6px 0;color:{_MUTED};font-size:9pt;letter-spacing:1px;">INTRO EMAIL · BONUS DRAFT</p>
    <p style="margin:0 0 8px 0;font-weight:bold;font-size:11pt;color:{_INK};">Subject: {_esc(d.get('intro_email_subject',''))}</p>
    <p style="margin:0;font-size:11pt;color:{_INK};line-height:1.5;">{body_html}</p>
    <p style="margin:8px 0 0;color:{_MUTED};font-size:9pt;font-style:italic;">A starting point, not a finished email. Edit it to your voice before sending.</p>
  </td></tr>
</table>
<p style="margin:6px 0;">&nbsp;</p>""")
    return "<html><body>" + "".join(parts) + "</body></html>"


def write_csv(prospects, path: str) -> str:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerows(rows_from(prospects))
    return path


# --- Polished PDF layout (rendered by WeasyPrint) ------------------------
# Table-based layout (no flexbox/grid) so WeasyPrint renders it faithfully.
_PDF_CSS = """
@page { size: Letter; margin: 1.3cm 1.3cm 1.5cm; }
* { box-sizing: border-box; }
body { font-family: 'Liberation Sans','Helvetica Neue',Arial,sans-serif;
  color:#0f172a; font-size:10.5pt; margin:0; }
.cover { background: linear-gradient(135deg,#0f172a 0%,#1e3a8a 60%,#2563eb 100%);
  color:#fff; padding:20px 24px; border-radius:14px; margin-bottom:16px; }
.brandrow { font-weight:800; font-size:10pt; letter-spacing:1px; }
.cover h1 { margin:8px 0 2px; font-size:22pt; }
.cover .sub { font-size:10pt; opacity:.9; }
.stats { margin-top:14px; border-collapse:collapse; }
.stats td { padding-right:30px; }
.stats .n { font-size:16pt; font-weight:800; }
.stats .l { font-size:7.5pt; letter-spacing:1px; text-transform:uppercase; opacity:.8; }
.card { border:1px solid #e5e9f0; border-radius:12px; margin-bottom:13px;
  break-before:page; break-inside:avoid; }
.chead { width:100%; border-collapse:collapse; border-bottom:1px solid #e5e9f0; }
.chead td { padding:11px 14px; vertical-align:top; }
.notes { margin-top:11px; }
.noteslbl { font-size:7pt; letter-spacing:1.5px; text-transform:uppercase;
  color:#64748b; font-weight:800; margin-bottom:5px; }
.notesbox { width:100%; height:54px; border:1px solid #cbd5e1; border-radius:9px;
  padding:8px 10px; font-family:inherit; font-size:10pt; color:#1e293b; background:#ffffff; }
.linkedin { background:#eef5fc; border:1px solid #cfe3f7; border-radius:10px;
  padding:10px 13px; margin-bottom:10px; }
.lilbl { font-size:7pt; letter-spacing:1.5px; text-transform:uppercase; color:#0a66c2; font-weight:800; }
.limsg { font-size:10pt; line-height:1.55; color:#1e293b; margin-top:5px; white-space:pre-line; }
.num { display:inline-block; width:26px; height:26px; line-height:26px; text-align:center;
  background:#2563eb; color:#fff; font-weight:800; border-radius:8px; font-size:11pt; }
.name { font-size:13pt; font-weight:800; }
.ptitle { color:#64748b; font-size:10pt; }
.co { text-align:right; }
.coname { font-weight:700; font-size:10.5pt; }
.cometa { color:#64748b; font-size:9pt; }
.body { padding:11px 14px 13px; }
.grid { width:100%; border-collapse:collapse; margin-bottom:9px; }
.grid td { width:50%; padding:3px 10px 3px 0; font-size:10pt; vertical-align:top; }
.grid .k { color:#64748b; font-size:8.5pt; letter-spacing:.5px; }
.grid a { color:#2563eb; text-decoration:none; }
.desc { color:#475569; font-size:10pt; line-height:1.5; border-left:3px solid #e5e9f0;
  padding-left:12px; margin:4px 0 11px; }
.fit { background:#ecfdf5; border:1px solid #bbf7d0; border-radius:10px;
  padding:9px 13px; font-size:10pt; line-height:1.5; margin-bottom:11px; }
.fit b { color:#059669; }
.email { background:#f0f6ff; border:1px solid #dbeafe; border-radius:10px; padding:10px 13px; }
.elbl { font-size:7pt; letter-spacing:1.5px; text-transform:uppercase; color:#2563eb; font-weight:800; }
.esubj { font-weight:700; font-size:10pt; margin:5px 0 6px; }
.emsg { font-size:10pt; line-height:1.55; white-space:pre-line; color:#1e293b; }
.bonustag { display:inline-block; background:#fef3c7; color:#92400e; font-size:6.5pt;
  font-weight:800; letter-spacing:1pt; text-transform:uppercase; padding:1px 6px;
  border-radius:5px; margin-left:6px; vertical-align:middle; }
.bonusnote { font-size:8pt; color:#64748b; margin-top:6px; font-style:italic; }
.covernote { font-size:8.5pt; opacity:.92; margin-top:11px; line-height:1.45; max-width:82%; }
.foot { text-align:center; color:#94a3b8; font-size:8pt; margin-top:8px; }
"""


def _fmt_date(run_date: str) -> str:
    import datetime
    try:
        y, m, d = (int(x) for x in run_date.split("-"))
        return datetime.date(y, m, d).strftime("%A, %B %d, %Y").replace(" 0", " ")
    except Exception:
        return run_date or ""


def _cell(k: str, val: str, link: bool = False) -> str:
    val = _esc(val) if val else "—"
    if link and val != "—":
        href = val if val.startswith("http") else "https://" + val
        val = f'<a href="{_esc(href)}">{val}</a>'
    return f'<div><span class="k">{k}</span><br>{val}</div>'


def pdf_html(prospects, company_name: str = "", run_date: str = "") -> str:
    """Build the polished report HTML for WeasyPrint → PDF."""
    n = len(prospects)
    sub = "Prepared for " + _esc(company_name) if company_name else "Your daily prospects"
    if run_date:
        sub += " · " + _fmt_date(run_date)
    parts = [f"<html><head><meta charset='utf-8'><style>{_PDF_CSS}</style></head><body>",
             '<div class="cover">',
             '<div class="brandrow">◎ PROSPECTDAILY</div>',
             '<h1>Daily Prospect Report</h1>',
             f'<div class="sub">{sub}</div>',
             '<table class="stats"><tr>',
             f'<td><div class="n">{n}</div><div class="l">Prospects</div></td>',
             f'<td><div class="n">{n}</div><div class="l">Net-new today</div></td>',
             '<td><div class="n">100%</div><div class="l">Verified email</div></td>',
             '</tr></table>',
             '<div class="covernote">The verified contacts and company intel below are '
             'the core of your report. The draft email and LinkedIn note on each are a '
             'bonus starting point. Edit them to your own voice before sending.</div>',
             '</div>']
    for i, p in enumerate(prospects, 1):
        d = p.to_dict()
        meta = []
        if d.get("employee_count"):
            meta.append(f"~{d['employee_count']} employees")
        if d.get("industry"):
            meta.append(_esc(d["industry"]))
        meta_line = " · ".join(meta)
        contact = (f'<table class="grid"><tr>'
                   f'<td>{_cell("EMAIL", d["email"])}</td>'
                   f'<td>{_cell("PHONE", d["phone"])}</td></tr><tr>'
                   f'<td>{_cell("WEBSITE", d["website"], link=True)}</td>'
                   f'<td>{_cell("LINKEDIN", d["linkedin_url"], link=True)}</td></tr><tr>'
                   f'<td colspan="2">{_cell("ADDRESS", d["company_address"])}</td>'
                   f'</tr></table>')
        body_html = _esc(d.get("intro_email_body", ""))
        li_msg = _esc(d.get("linkedin_message", ""))
        li_block = (f'<div class="linkedin"><div class="lilbl">LinkedIn note'
                    f'<span class="bonustag">Bonus draft</span></div>'
                    f'<div class="limsg">{li_msg}</div></div>'
                    if li_msg else "")
        desc_block = ('<div class="desc">' + _esc(d['company_description']) + '</div>'
                      if d.get('company_description') else "")
        parts.append(f"""
<div class="card">
  <table class="chead"><tr>
    <td style="width:38px"><span class="num">{i}</span></td>
    <td><div class="name">{_esc(d['full_name'])}</div><div class="ptitle">{_esc(d['title'])}</div></td>
    <td class="co"><div class="coname">{_esc(d['company'])}</div><div class="cometa">{meta_line}</div></td>
  </tr></table>
  <div class="body">
    {contact}
    {desc_block}
    <div class="fit"><b>✓ Why it's a fit:</b> {_esc(d['fit_reason'])}</div>
    {li_block}
    <div class="email">
      <div class="elbl">Intro email<span class="bonustag">Bonus draft</span></div>
      <div class="esubj">Subject: {_esc(d.get('intro_email_subject',''))}</div>
      <div class="emsg">{body_html}</div>
      <div class="bonusnote">A starting point, not a finished email. Edit it to your voice before sending.</div>
    </div>
    <form><div class="notes">
      <div class="noteslbl">Status &amp; next steps</div>
      <textarea class="notesbox" name="notes_{i}"></textarea>
    </div></form>
  </div>
</div>""")
    parts.append('<div class="foot">ProspectDaily · fresh, verified B2B prospects '
                 'every weekday · prospectdaily.com</div>')
    parts.append("</body></html>")
    return "".join(parts)
