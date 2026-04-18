import json
import datetime
import logging
from pathlib import Path
from api.app.config import settings

logger = logging.getLogger(__name__)


def escape_html(text: str) -> str:
    text = str(text)
    return (
        text.replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
            .replace('"', "&quot;")
    )


def load_failures(json_path) -> list:
    with open(json_path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def group_by_owner(failures: list) -> dict:
    groups: dict = {}
    for f in failures:
        groups.setdefault(f["owner"], []).append(f)
    return groups


def confidence_color(value: float) -> str:
    if value >= 0.80:
        return "#3B6D11"
    elif value >= 0.50:
        return "#854F0B"
    else:
        return "#A32D2D"


def confidence_bg(value: float) -> str:
    if value >= 0.80:
        return "#EAF3DE"
    elif value >= 0.50:
        return "#FAEEDA"
    else:
        return "#FCEBEB"


def confidence_label(value: float) -> str:
    if value >= 0.80:
        return "High confidence"
    elif value >= 0.50:
        return "Medium confidence"
    else:
        return "Low confidence"


def confidence_bar(value: float) -> str:
    pct = int(value * 100)
    color = confidence_color(value)
    return (
        f'<div class="conf-row">'
        f'<div class="conf-bar-wrap">'
        f'<div class="conf-bar" style="width:{pct}%;background:{color}"></div>'
        f'</div>'
        f'<span class="conf-label" style="color:{color}">{pct}% &bull; {confidence_label(value)}</span>'
        f'</div>'
    )


def owner_badge(owner: str) -> str:
    if owner == "DEVELOPERS":
        return '<span class="badge badge-dev">Developer</span>'
    return '<span class="badge badge-devops">DevOps</span>'


def severity_badge(severity: str) -> str:
    cfg = {
        "CRITICAL": ("#FCEBEB", "#791F1F", "#F7C1C1"),
        "HIGH":     ("#FAECE7", "#712B13", "#F5C4B3"),
        "MEDIUM":   ("#FAEEDA", "#633806", "#FAC775"),
        "LOW":      ("#EAF3DE", "#27500A", "#C0DD97"),
    }
    bg, fg, border = cfg.get(severity, ("#F1EFE8", "#444441", "#D3D1C7"))
    return (
        f'<span class="sev-badge" style="background:{bg};color:{fg};border:0.5px solid {border}">'
        f'{escape_html(severity)}</span>'
    )


def recurrence_badge(count: int) -> str:
    if count == 0:
        return ""
    c      = "#A32D2D" if count >= 5 else "#854F0B" if count >= 2 else "#5F5E5A"
    bg     = "#FCEBEB" if count >= 5 else "#FAEEDA" if count >= 2 else "#F1EFE8"
    border = "#F7C1C1" if count >= 5 else "#FAC775" if count >= 2 else "#D3D1C7"
    return (
        f'<span class="recur-badge" style="background:{bg};color:{c};border:0.5px solid {border}">'
        f'{count}x recurrence</span>'
    )


def similarity_badge(score: float | None) -> str:
    if score is None:
        return ""
    pct = int(score * 100)
    return (
        f'<span class="sim-badge" style="background:#EEEDFE;color:#3C3489;border:0.5px solid #AFA9EC">'
        f'{pct}% KB match</span>'
    )



def render_patch(patch: str) -> str:
    lines_html = ""
    for line in patch.splitlines():
        escaped = escape_html(line)
        if line.startswith("+++") or line.startswith("---"):
            cls = "patch-file"
        elif line.startswith("+"):
            cls = "patch-add"
        elif line.startswith("-"):
            cls = "patch-del"
        elif line.startswith("@@"):
            cls = "patch-hunk"
        else:
            cls = "patch-ctx"
        lines_html += f'<div class="patch-line {cls}">{escaped}</div>'
    return (
        f'<div class="patch-block">'
        f'<div class="patch-header">Suggested patch</div>'
        f'<div class="patch-body">{lines_html}</div>'
        f'</div>'
    )


def render_fix_commands(commands: list[str], copy_id: str) -> str:
    """Dark terminal block with a Copy button. Skips comment lines when copying."""
    if not commands:
        return '<div class="empty-field">no fix commands provided</div>'
    lines = "".join(
        f'<div class="cmd-line"><span class="cmd-prompt">$</span>{escape_html(cmd)}</div>'
        for cmd in commands
    )
    return (
        f'<div class="cmd-block">'
        f'<div class="cmd-header">'
        f'<span>Fix commands</span>'
        f'<button class="copy-btn" onclick="copyCommands(\'{copy_id}\')" title="Copy executable lines">Copy</button>'
        f'</div>'
        f'<div class="cmd-body" id="{copy_id}">{lines}</div>'
        f'</div>'
    )


def render_field_grid(fields: list[tuple[str, str]], full_width_keys: set[str] | None = None) -> str:
    """Two-column grid; items whose label is in full_width_keys span both columns."""
    full_width_keys = full_width_keys or set()
    items = ""
    for label, value_html in fields:
        span = ' style="grid-column:1/-1"' if label in full_width_keys else ""
        items += (
            f'<div class="field-item"{span}>'
            f'<div class="field-label">{escape_html(label)}</div>'
            f'<div class="field-value">{value_html}</div>'
            f'</div>'
        )
    return f'<div class="field-grid">{items}</div>'


def field_val(v, type_: str = "text") -> str:
    """Render a typed value, or italic 'not provided' when empty."""
    if v is None or v == "" or v == []:
        return '<span class="empty-val">not provided</span>'
    if type_ == "bool":
        color = "#A32D2D" if v else "#3B6D11"
        label = "Yes" if v else "No"
        return f'<span style="color:{color};font-weight:500">{label}</span>'
    if type_ == "pct":
        c = confidence_color(float(v))
        return f'<span style="color:{c};font-weight:500">{int(float(v) * 100)}%</span>'
    if type_ == "priority":
        pc = {"CRITICAL": "#A32D2D", "HIGH": "#712B13", "MEDIUM": "#854F0B", "LOW": "#3B6D11"}
        c = pc.get(str(v), "#444")
        return f'<span style="color:{c};font-weight:500">{escape_html(str(v))}</span>'
    return f'<span>{escape_html(str(v))}</span>'


def render_related_files(files: list[str] | None) -> str:
    if not files:
        return '<span class="empty-val">not provided</span>'
    tags = "".join(
        f'<span class="rel-file">{escape_html(f)}</span>'
        for f in files
    )
    return f'<div class="rel-files">{tags}</div>'


def render_inv_links(links: list[str] | None) -> str:
    if not links:
        return '<span class="empty-val">not provided</span>'
    return "".join(
        f'<a href="{escape_html(l)}" class="inv-link" target="_blank">Link</a>'
        for l in links
    )


def render_notes(notes: str | None, fg: str) -> str:
    """Render the free-text notes field if present."""
    if not notes:
        return ""
    return (
        f'<div class="notes-box" style="border-left-color:{fg}">'
        f'<div class="field-label">Notes</div>'
        f'<div class="notes-val">{escape_html(notes)}</div>'
        f'</div>'
    )


_REM_COUNTER = 0


def _next_copy_id() -> str:
    global _REM_COUNTER
    _REM_COUNTER += 1
    return f"cmd-{_REM_COUNTER}"


def _render_fix_dev(rem: dict) -> str:
    strategy   = rem.get("strategy", "")
    patch      = rem.get("suggested_patch")
    patch_conf = rem.get("patch_confidence")
    related    = rem.get("related_files") or []
    fix_cmds   = rem.get("fix_commands") or []

    html = render_fix_commands(fix_cmds, _next_copy_id())

    fields: list[tuple[str, str]] = [
        ("Related files", render_related_files(related)),
    ]
    if strategy == "GENERATE_CODE_PATCH":
        fields.append(("Patch confidence", field_val(patch_conf, "pct")))

    html += render_field_grid(fields, full_width_keys={"Related files"})

    if patch:
        html += render_patch(patch)
        if patch_conf is not None:
            pc_color = confidence_color(patch_conf)
            html += (
                f'<div class="patch-conf" style="color:{pc_color}">'
                f'Patch confidence: {int(patch_conf * 100)}%</div>'
            )
    return html


def _render_fix_test(rem: dict) -> str:
    strategy    = rem.get("strategy", "")
    retry_count = rem.get("retry_count")
    skip_reason = rem.get("skip_reason")
    related     = rem.get("related_files") or []
    fix_cmds    = rem.get("fix_commands") or []

    html = render_fix_commands(fix_cmds, _next_copy_id())

    fields: list[tuple[str, str]] = [
        ("Related files", render_related_files(related)),
    ]
    if strategy == "MARK_FLAKY_RETRY":
        fields.append(("Retry count", field_val(retry_count)))
    if strategy == "SKIP_TEST_TEMPORARILY":
        fields.append(("Skip reason", field_val(skip_reason)))

    html += render_field_grid(fields, full_width_keys={"Related files", "Skip reason"})
    return html


def _render_fix_ci_infra(rem: dict) -> str:
    est_time     = rem.get("estimated_recovery_time_seconds")
    req_approval = rem.get("requires_human_approval")
    related      = rem.get("related_files") or []
    fix_cmds     = rem.get("fix_commands") or []

    html = render_fix_commands(fix_cmds, _next_copy_id())

    fields: list[tuple[str, str]] = [
        ("Est. recovery",     field_val(f"{est_time}s" if est_time is not None else None)),
        ("Requires approval", field_val(req_approval, "bool")),
        ("Related files",     render_related_files(related)),
    ]
    html += render_field_grid(fields, full_width_keys={"Related files"})
    return html


def _render_manual(rem: dict) -> str:
    next_step  = rem.get("suggested_next_step")
    escalation = rem.get("escalation_team")
    priority   = rem.get("priority")
    inv_links  = rem.get("investigation_links") or []
    related    = rem.get("related_files") or []

    fields: list[tuple[str, str]] = [
        ("Priority",       field_val(priority, "priority")),
        ("Escalation",     field_val(escalation)),
        ("Related files",  render_related_files(related)),
        ("Investigation links", render_inv_links(inv_links)),
    ]
    html = render_field_grid(
        fields,
        full_width_keys={"Related files", "Investigation links"},
    )

    if next_step:
        html += (
            f'<div class="next-step">'
            f'<div class="field-label">Suggested next step</div>'
            f'<div class="next-step-val">{escape_html(next_step)}</div>'
            f'</div>'
        )
    return html


_ACTION_RENDERERS = {
    "FIX_DEV":              _render_fix_dev,
    "FIX_TEST":             _render_fix_test,
    "FIX_CI_INFRA":         _render_fix_ci_infra,
    "MANUAL_INVESTIGATION": _render_manual,
}

_ACTION_COLORS: dict[str, tuple[str, str, str]] = {
    "FIX_DEV":              ("#FAEEDA", "#633806", "#FAC775"),
    "FIX_TEST":             ("#EAF3DE", "#27500A", "#C0DD97"),
    "FIX_CI_INFRA":         ("#E6F1FB", "#0C447C", "#B5D4F4"),
    "MANUAL_INVESTIGATION": ("#FCEBEB", "#791F1F", "#F7C1C1"),
}


def render_single_remediation(rem: dict, is_secondary: bool = False) -> str:
    action   = rem.get("action", "")
    strategy = rem.get("strategy", "")
    target   = rem.get("target") or rem.get("reason") or ""
    notes    = rem.get("notes")

    bg, fg, border = _ACTION_COLORS.get(action, ("#F1EFE8", "#444441", "#D3D1C7"))
    label     = "Secondary remediation" if is_secondary else "Remediation"
    sec_style = "opacity:0.88;" if is_secondary else ""

    strategy_html = (
        f'<span class="strategy-tag" style="background:{bg};color:{fg};border-color:{border}">'
        f'{escape_html(strategy)}</span>'
    ) if strategy else ""

    body_html = _ACTION_RENDERERS.get(action, lambda _: "")(rem)
    notes_html = render_notes(notes, fg)

    return (
        f'<div class="action-box" style="border-left-color:{fg};{sec_style}">'
        f'<div class="action-label" style="color:{fg}">{escape_html(label)}</div>'
        f'<div class="action-body">'
        f'<div class="action-title">'
        f'<strong style="color:{fg}">{escape_html(action)}</strong>{strategy_html}'
        f'</div>'
        f'<p class="action-target">{escape_html(target)}</p>'
        f'{body_html}'
        f'{notes_html}'
        f'</div>'
        f'</div>'
    )


def render_remediation(f: dict) -> str:
    html = render_single_remediation(f["remediation"])
    for sec in f.get("secondary_remediations") or []:
        html += render_single_remediation(sec, is_secondary=True)
    return html


def render_failure(f: dict, index: int) -> str:
    category       = escape_html(f["validated_category"])
    cause          = escape_html(f["root_cause"])
    error          = escape_html(f["error_line"])
    fp             = f.get("fingerprint", "")[:16] + "..."
    confidence_val = f["rca_confidence"]
    conf_color     = confidence_color(confidence_val)
    analyzed_at    = f.get("analyzed_at", "")
    evidence_url   = f.get("evidence_url")

    if evidence_url:
        evidence_html = (
            f'<a href="{escape_html(evidence_url)}" class="evidence-link" target="_blank">'
            f'View CI job</a>'
        )
    else:
        evidence_html = '<span class="no-evidence">no evidence url</span>'

    return f"""
    <div class="failure-card" id="failure-{index}" style="border-left-color:{conf_color}">
      <div class="failure-header">
        <div class="failure-meta">
          <span class="failure-index" style="background:{conf_color};">#{index + 1}</span>
          <h3>{category}</h3>
          {severity_badge(f.get("severity", "MEDIUM"))}
          {recurrence_badge(f.get("recurrence_count", 0))}
          {similarity_badge(f.get("similarity_score"))}
        </div>
        {owner_badge(f["owner"])}
      </div>
      <p class="cause-text">{cause}</p>
      <div class="error-block"><span class="error-prefix">ERROR</span>{error}</div>
      <div class="rem-section">{render_remediation(f)}</div>
      <div class="failure-footer">
        {confidence_bar(confidence_val)}
        <div class="fp-meta">
          <span class="analyzed-at">{escape_html(analyzed_at)}</span>
          {evidence_html}
          <span class="fp-label">FP: <code>{escape_html(fp)}</code></span>
        </div>
      </div>
    </div>
"""


def build_sections(failures: list) -> str:
    groups = group_by_owner(failures)
    section_order = ["DEVOPS_ENGINEERS", "DEVELOPERS"]
    section_labels = {
        "DEVOPS_ENGINEERS": ("Assigned to — DevOps engineers", "#E6F1FB", "#0C447C"),
        "DEVELOPERS":       ("Assigned to — Developers",       "#FAEEDA", "#633806"),
    }
    html = ""
    global_index = 0
    for owner in section_order:
        items = groups.get(owner, [])
        if not items:
            continue
        label, bg, fg = section_labels[owner]
        html += (
            f'<div class="section-heading" style="color:{fg};border-bottom:0.5px solid {fg}50">'
            f'{label} '
            f'<span class="section-count" style="background:{bg};color:{fg};border:0.5px solid {fg}50">'
            f'{len(items)}</span>'
            f'</div>\n'
        )
        for f in items:
            html += render_failure(f, global_index)
            global_index += 1
    return html


HTML_TEMPLATE = """\
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>Build Failure Report</title>
  <style>
    *, *::before, *::after {{ box-sizing: border-box; margin: 0; padding: 0; }}
    body {{
      font-family: 'Segoe UI', system-ui, sans-serif;
      font-size: 14px; line-height: 1.6;
      background: #f8f9fc; color: #1a1a2e;
    }}
    .page {{ max-width: 860px; margin: 0 auto; padding: 1.5rem 1rem 4rem; }}

    /* ── Header ── */
    .report-header {{
      background: #ffffff; border: 0.5px solid #e2e8f0;
      border-radius: 12px; padding: 1.5rem 1.75rem 1.25rem; margin-bottom: 8px;
    }}
    .report-header h1 {{ font-size: 18px; font-weight: 500; color: #0f172a; margin-bottom: 3px; }}
    .report-header .subtitle {{ color: #64748b; font-size: 12px; margin-bottom: 14px; }}
    .build-meta {{ display: flex; flex-wrap: wrap; gap: 8px; }}
    .build-tag {{
      display: inline-flex; flex-direction: column; gap: 1px;
      background: #f8fafc; border: 0.5px solid #e2e8f0; border-radius: 8px; padding: 6px 14px;
    }}
    .build-tag .tag-label {{
      color: #94a3b8; font-size: 10px; font-weight: 500;
      text-transform: uppercase; letter-spacing: .07em;
    }}
    .build-tag .tag-value {{ font-size: 13px; font-weight: 500; color: #0f172a; }}

    /* ── Summary bar ── */
    .summary-bar {{
      display: flex; background: #ffffff;
      border: 0.5px solid #e2e8f0; border-radius: 12px; margin-bottom: 20px; overflow: hidden;
    }}
    .summary-item {{ flex: 1; padding: .85rem 1rem; border-right: 0.5px solid #e2e8f0; text-align: center; }}
    .summary-item:last-child {{ border-right: none; }}
    .summary-item .num {{ font-size: 22px; font-weight: 500; display: block; }}
    .summary-item .lbl {{ font-size: 10px; color: #94a3b8; text-transform: uppercase; letter-spacing: .06em; }}

    /* ── Section headings ── */
    .section-heading {{
      font-size: 11px; font-weight: 500; text-transform: uppercase; letter-spacing: .08em;
      padding: .75rem 0 .5rem; display: flex; align-items: center; gap: 8px; margin-bottom: 4px;
    }}
    .section-count {{ font-size: 11px; font-weight: 500; padding: 2px 10px; border-radius: 20px; margin-left: auto; }}

    /* ── Failure card ── */
    .failure-card {{
      background: #ffffff; border: 0.5px solid #e2e8f0; border-radius: 12px;
      padding: 1.25rem 1.5rem; margin-bottom: .75rem;
      border-left-width: 3px; border-left-style: solid;
    }}
    .failure-header {{
      display: flex; align-items: flex-start; justify-content: space-between;
      gap: 10px; margin-bottom: 10px;
    }}
    .failure-meta {{ display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }}
    .failure-index {{ font-size: 11px; font-weight: 500; color: #fff; border-radius: 6px; padding: 2px 9px; flex-shrink: 0; }}
    .failure-header h3 {{ font-size: 14px; font-weight: 500; color: #0f172a; }}

    /* ── Badges ── */
    .badge {{ font-size: 11px; font-weight: 500; padding: 3px 12px; border-radius: 20px; flex-shrink: 0; border: 0.5px solid; }}
    .badge-devops {{ background: #E6F1FB; color: #0C447C; border-color: #B5D4F4; }}
    .badge-dev    {{ background: #FAEEDA; color: #633806; border-color: #FAC775; }}
    .sev-badge, .recur-badge, .sim-badge {{
      font-size: 10px; font-weight: 500; padding: 2px 9px; border-radius: 20px; letter-spacing: .03em;
    }}

    /* ── Cause & error ── */
    .cause-text {{ font-size: 13px; color: #374151; margin-bottom: 10px; line-height: 1.5; }}
    .error-block {{
      font-family: 'Courier New', Courier, monospace; font-size: 11.5px;
      background: #0f172a; border: 0.5px solid #334155; border-left: 3px solid #E24B4A;
      border-radius: 8px; padding: 10px 14px; color: #fca5a5;
      word-break: break-all; margin-bottom: 12px;
    }}
    .error-prefix {{
      font-size: 10px; font-weight: 500; background: #E24B4A; color: #fff;
      border-radius: 4px; padding: 1px 6px; margin-right: 8px;
    }}

    /* ── Remediation ── */
    .rem-section {{ display: flex; flex-direction: column; gap: 8px; margin-bottom: 10px; }}
    .action-box {{
      background: #f8fafc; border: 0.5px solid #e2e8f0; border-radius: 10px; padding: 12px 14px;
      border-left-width: 3px; border-left-style: solid;
    }}
    .action-label {{ font-size: 10px; font-weight: 500; text-transform: uppercase; letter-spacing: .07em; margin-bottom: 6px; }}
    .action-title {{ display: flex; align-items: center; gap: 4px; margin-bottom: 4px; }}
    .action-title strong {{ font-size: 13px; font-weight: 500; }}
    .action-target {{ font-size: 12.5px; color: #4b5563; margin-bottom: 8px; }}
    .strategy-tag {{ font-size: 11px; font-weight: 500; border-radius: 4px; padding: 2px 8px; vertical-align: middle; border: 0.5px solid; }}

    /* ── Fix commands ── */
    .cmd-block {{
      border: 0.5px solid #334155; border-radius: 8px; overflow: hidden;
      font-family: 'Courier New', Courier, monospace; font-size: 12px; margin-bottom: 8px;
    }}
    .cmd-header {{
      background: #1e293b; color: #94a3b8; font-size: 10px; font-weight: 500;
      text-transform: uppercase; letter-spacing: .07em; padding: 5px 14px;
      display: flex; justify-content: space-between; align-items: center;
    }}
    .copy-btn {{
      font-size: 10px; color: #94a3b8; background: transparent;
      border: 0.5px solid #475569; border-radius: 4px; padding: 2px 8px;
      cursor: pointer; font-family: inherit;
    }}
    .copy-btn:hover {{ background: #334155; color: #e2e8f0; }}
    .cmd-body {{ background: #0f172a; padding: 6px 0; }}
    .cmd-line {{
      padding: 2px 14px; color: #e2e8f0; white-space: pre; overflow-x: auto;
      display: flex; align-items: baseline; gap: 8px;
    }}
    .cmd-line:hover {{ background: #1e293b; }}
    .cmd-prompt {{ color: #22c55e; font-weight: 500; flex-shrink: 0; }}
    .empty-field {{ font-style: italic; color: #94a3b8; font-size: 11.5px; padding: 4px 0; }}

    /* ── Field grid ── */
    .field-grid {{ display: grid; grid-template-columns: 1fr 1fr; gap: 6px; margin-top: 6px; margin-bottom: 6px; }}
    .field-item {{ background: #ffffff; border: 0.5px solid #e2e8f0; border-radius: 8px; padding: 7px 10px; }}
    .field-label {{ font-size: 10px; text-transform: uppercase; letter-spacing: .05em; color: #94a3b8; margin-bottom: 2px; font-weight: 500; }}
    .field-value {{ font-size: 12px; color: #0f172a; font-weight: 500; }}
    .empty-val {{ font-style: italic; color: #94a3b8; font-weight: 400; font-size: 11.5px; }}

    /* ── Related files ── */
    .rel-files {{ display: flex; flex-wrap: wrap; gap: 4px; margin-top: 2px; }}
    .rel-file {{
      font-family: 'Courier New', Courier, monospace; font-size: 11px;
      background: #f1f5f9; border: 0.5px solid #e2e8f0; border-radius: 4px;
      padding: 2px 7px; color: #475569;
    }}

    /* ── Patch ── */
    .patch-block {{ margin-top: 8px; border: 0.5px solid #334155; border-radius: 8px; overflow: hidden; font-family: 'Courier New', Courier, monospace; font-size: 11.5px; }}
    .patch-header {{ background: #1e293b; color: #94a3b8; font-size: 10px; font-weight: 500; text-transform: uppercase; letter-spacing: .07em; padding: 5px 14px; }}
    .patch-body {{ background: #0f172a; }}
    .patch-line {{ padding: 1px 14px; white-space: pre; overflow-x: auto; }}
    .patch-add  {{ background: #14532d40; color: #86efac; }}
    .patch-del  {{ background: #7f1d1d40; color: #fca5a5; }}
    .patch-hunk {{ background: #1e3a5f40; color: #93c5fd; }}
    .patch-file {{ background: #1e293b; color: #e2e8f0; font-weight: 500; }}
    .patch-ctx  {{ color: #64748b; }}
    .patch-conf {{ font-size: 11px; font-weight: 500; margin-top: 4px; text-align: right; padding-right: 4px; }}

    /* ── Notes ── */
    .notes-box {{
      background: #ffffff; border: 0.5px solid #e2e8f0;
      border-left-width: 3px; border-left-style: solid;
      border-radius: 8px; padding: 7px 10px; margin-top: 6px;
    }}
    .notes-val {{ font-size: 12.5px; color: #374151; margin-top: 2px; line-height: 1.5; }}

    /* ── Next step ── */
    .next-step {{ background: #ffffff; border: 0.5px solid #e2e8f0; border-radius: 8px; padding: 7px 10px; margin-top: 6px; }}
    .next-step-val {{ font-size: 12.5px; color: #374151; margin-top: 2px; }}

    /* ── Links ── */
    .inv-link {{
      font-size: 11px; color: #3C3489; text-decoration: none; background: #EEEDFE;
      padding: 1px 7px; border-radius: 4px; margin-right: 4px; border: 0.5px solid #AFA9EC;
    }}
    .inv-link:hover {{ text-decoration: underline; }}

    /* ── Card footer ── */
    .failure-footer {{
      display: flex; align-items: center; justify-content: space-between;
      gap: 12px; margin-top: 10px; padding-top: 8px; border-top: 0.5px solid #f1f5f9;
    }}
    .conf-row {{ display: flex; align-items: center; gap: 8px; }}
    .conf-bar-wrap {{ width: 110px; height: 5px; background: #e5e7eb; border-radius: 99px; overflow: hidden; border: 0.5px solid #e2e8f0; }}
    .conf-bar {{ height: 100%; border-radius: 99px; }}
    .conf-label {{ font-size: 11px; font-weight: 500; white-space: nowrap; }}
    .fp-meta {{ display: flex; align-items: center; gap: 10px; flex-wrap: wrap; justify-content: flex-end; }}
    .fp-label {{ font-size: 11px; color: #94a3b8; }}
    .fp-label code {{ font-family: 'Courier New', monospace; font-size: 10px; }}
    .analyzed-at {{ font-size: 10.5px; color: #94a3b8; }}
    .evidence-link {{
      font-size: 11px; color: #0C447C; text-decoration: none; background: #E6F1FB;
      padding: 2px 8px; border-radius: 6px; border: 0.5px solid #B5D4F4;
    }}
    .evidence-link:hover {{ text-decoration: underline; }}
    .no-evidence {{ font-size: 11px; color: #94a3b8; font-style: italic; }}

    /* ── Report footer ── */
    .report-footer {{
      text-align: center; font-size: 11.5px; color: #94a3b8;
      margin-top: 2.5rem; padding-top: 1rem; border-top: 0.5px solid #e2e8f0;
    }}

    @media print {{
      body {{ background: #fff; }}
      .page {{ padding: 0; max-width: 100%; }}
      .failure-card {{ break-inside: avoid; }}
    }}
  </style>
</head>
<body>
<div class="page">

  <div class="report-header">
    <h1>Build failure report</h1>
    <div class="subtitle">Generated on {generated_on} &middot; {total} failure{plural} detected across CI pipeline</div>
    <div class="build-meta">
      <div class="build-tag"><span class="tag-label">Branch</span><span class="tag-value">{branch_name}</span></div>
      <div class="build-tag"><span class="tag-label">Job</span><span class="tag-value">{job_name}</span></div>
      <div class="build-tag"><span class="tag-label">Build</span><span class="tag-value">#{build_number}</span></div>
    </div>
  </div>

  <div class="summary-bar">
    <div class="summary-item"><span class="num" style="color:#0f172a">{total}</span><span class="lbl">Total failures</span></div>
    <div class="summary-item"><span class="num" style="color:#185FA5">{devops_count}</span><span class="lbl">DevOps</span></div>
    <div class="summary-item"><span class="num" style="color:#633806">{dev_count}</span><span class="lbl">Developers</span></div>
    <div class="summary-item"><span class="num" style="color:{avg_color}">{avg_conf}%</span><span class="lbl">Avg confidence</span></div>
    <div class="summary-item"><span class="num" style="color:{crit_color}">{critical_count}</span><span class="lbl">Critical</span></div>
  </div>

  {sections_html}

  <div class="report-footer">
    Auto-generated from CI pipeline failure logs &middot; {total} unique fingerprint{plural} &middot; {generated_on}
  </div>
</div>

<script>
function copyCommands(id) {{
  const el = document.getElementById(id);
  if (!el) return;
  const lines = Array.from(el.querySelectorAll('.cmd-line'))
    .map(l => {{
      const t = l.textContent;
      const m = t.match(/^\s*\$\s*(.*)/s);
      return m ? m[1].trim() : t.trim();
    }})
    .filter(l => !l.startsWith('#') && l.length > 0);
  navigator.clipboard.writeText(lines.join('\\n')).then(() => {{
    const btn = el.parentElement.querySelector('.copy-btn');
    if (btn) {{ btn.textContent = 'Copied!'; setTimeout(() => btn.textContent = 'Copy', 2000); }}
  }});
}}
</script>
</body>
</html>
"""


def generate_report(failure_id: str, branch_name: str, job_name: str, build_number: str) -> str:
    global _REM_COUNTER
    _REM_COUNTER = 0 

    branch_name  = str(branch_name)
    job_name     = str(job_name)
    build_number = str(build_number)

    json_path   = Path(settings.LOG_PATH) / failure_id / "root_cause.json"
    output_path = Path(settings.LOG_PATH) / failure_id / "rca_report.html"

    failures = load_failures(json_path)
    groups   = group_by_owner(failures)
    total    = len(failures)

    devops_c     = len(groups.get("DEVOPS_ENGINEERS", []))
    dev_c        = len(groups.get("DEVELOPERS", []))
    critical_c   = sum(1 for f in failures if f.get("severity") == "CRITICAL")
    avg_conf_val = sum(f["rca_confidence"] for f in failures) / total
    avg_conf     = round(avg_conf_val * 100)
    avg_color    = confidence_color(avg_conf_val)
    crit_color   = "#A32D2D" if critical_c > 0 else "#3B6D11"
    generated    = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    plural       = "s" if total != 1 else ""

    html = HTML_TEMPLATE.format(
        generated_on=generated,
        total=total,
        plural=plural,
        devops_count=devops_c,
        dev_count=dev_c,
        avg_conf=avg_conf,
        avg_color=avg_color,
        critical_count=critical_c,
        crit_color=crit_color,
        branch_name=escape_html(branch_name),
        job_name=escape_html(job_name),
        build_number=escape_html(build_number),
        sections_html=build_sections(failures),
    )

    with open(output_path, "w", encoding="utf-8") as fh:
        fh.write(html)

    logger.info("Report saved: %s (%d failure%s, %s bytes)", output_path, total, plural, f"{len(html):,}")
    return str(output_path)