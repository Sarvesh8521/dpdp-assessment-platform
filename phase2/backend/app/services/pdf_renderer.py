import json
from pathlib import Path
from typing import Any

from reportlab.lib.pagesizes import letter
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, KeepTogether
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib import colors

class PDFReportRenderer:
    def __init__(self):
        self.styles = getSampleStyleSheet()
        self.styles.add(ParagraphStyle(
            name="Warning",
            parent=self.styles["Normal"],
            textColor=colors.purple,
            fontSize=10,
            leading=12,
            spaceBefore=6,
            spaceAfter=6,
        ))
        self.styles.add(ParagraphStyle(
            name="FindingTitle",
            parent=self.styles["Heading3"],
            textColor=colors.HexColor("#1f2937"),
            spaceBefore=12,
            spaceAfter=6,
        ))
        self.styles.add(ParagraphStyle(
            name="DetailText",
            parent=self.styles["Normal"],
            fontSize=9,
            leading=11,
            spaceAfter=6,
        ))
        self.styles.add(ParagraphStyle(
            name="EvidenceTitle",
            parent=self.styles["Heading4"],
            fontSize=10,
            spaceBefore=6,
            spaceAfter=4,
        ))

    def generate(self, report_data: dict[str, Any], output_path: str) -> str:
        doc = SimpleDocTemplate(
            output_path,
            pagesize=letter,
            rightMargin=36,
            leftMargin=36,
            topMargin=36,
            bottomMargin=36
        )

        story = []

        # Title
        story.append(Paragraph("DPDP Compliance Assessment Report", self.styles["Title"]))
        story.append(Spacer(1, 12))

        # Assessment Info
        story.append(Paragraph(f"<b>Assessment ID:</b> {report_data.get('assessment_id', 'Unknown')}", self.styles["Normal"]))
        story.append(Paragraph(f"<b>Generated At:</b> {report_data.get('generated_at', 'Unknown')}", self.styles["Normal"]))
        story.append(Spacer(1, 12))

        # Assessment Outcome Summary
        summary = report_data.get("summary", {})
        outcome_text = summary.get("assessment_outcome", "")
        if outcome_text:
            verdicts = summary.get("verdicts", {})
            story.append(Paragraph("<b>Assessment Outcome</b>", self.styles["Heading2"]))
            story.append(Paragraph(outcome_text, self.styles["Normal"]))
            story.append(Spacer(1, 6))
            verdict_table_data = [
                ["Verdict", "Count"],
                ["Observed (issues identified)", str(verdicts.get("observed", 0))],
                ["Not observed (confirmed clean)", str(verdicts.get("not_observed", 0))],
                ["Cannot determine (evidence incomplete)", str(verdicts.get("cannot_determine", 0))],
            ]
            vt = Table(verdict_table_data, colWidths=[320, 80])
            vt.setStyle(TableStyle([
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#374151")),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("FONTSIZE", (0, 0), (-1, -1), 9),
                ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
                ("ALIGN", (1, 0), (1, -1), "CENTER"),
            ]))
            story.append(vt)
            story.append(Spacer(1, 6))
            story.append(Paragraph(
                f"<b>Total findings:</b> {summary.get('total_findings', 0)} | "
                f"<b>Human review required:</b> {summary.get('requires_human_review', 0)}",
                self.styles["Normal"]
            ))
            story.append(Spacer(1, 12))

        def render_finding(f, is_issue=True):
            finding_story = []
            title = f"{f.get('obligation_code', 'UNK')}: {f.get('obligation_title', '')}"
            finding_story.append(Paragraph(title, self.styles["FindingTitle"]))
            
            # Attributes
            verdict = f.get('verdict', '')
            conf = f.get('confidence', '')
            sev = f.get('severity', '')
            
            # Required fields
            finding_story.append(Paragraph(f"<b>Verdict:</b> {verdict} | <b>Confidence:</b> {conf} | <b>Severity:</b> {sev}", self.styles["Normal"]))
            
            if verdict.lower() == "cannot determine":
                finding_story.append(Paragraph("<b>Technical Observation:</b>", self.styles["Normal"]))
                finding_story.append(Paragraph(f.get("detail", ""), self.styles["DetailText"]))
                
                finding_story.append(Paragraph("<b>System Reasoning:</b>", self.styles["Normal"]))
                reason_html = str(f.get("reason", "")).replace("\n", "<br/>")
                finding_story.append(Paragraph(reason_html, self.styles["DetailText"]))
                
                finding_story.append(Paragraph("<b>Required Evidence / Resolution:</b>", self.styles["Normal"]))
                finding_story.append(Paragraph(f.get("recommended_action") or "Provide the necessary client authorization and/or resource selection.", self.styles["DetailText"]))
            else:
                ev_items = f.get('evidence_details', [])
                exact_resource = "N/A"
                exact_location = "N/A"
                if ev_items:
                    primary_ev = ev_items[0]
                    # Format as 'type - name (ID)' if all are available, else fallback
                    if primary_ev.get('resource_type') and primary_ev.get('resource_type') != "Unknown":
                        exact_resource = f"{primary_ev.get('resource_type')} - {primary_ev.get('resource_name', 'Unknown')} (ID: {primary_ev.get('resource_id', 'Unknown')})"
                    elif primary_ev.get('resource_name'):
                        exact_resource = primary_ev.get('resource_name')
                        
                    if primary_ev.get('location') and primary_ev.get('location') != 'Not available from provider API':
                        exact_location = primary_ev.get('location')

                finding_story.append(Paragraph("<b>Exact Resource:</b>", self.styles["Normal"]))
                finding_story.append(Paragraph(exact_resource, self.styles["DetailText"]))
                
                if exact_location != "N/A":
                    finding_story.append(Paragraph("<b>Exact Location:</b>", self.styles["Normal"]))
                    finding_story.append(Paragraph(exact_location, self.styles["DetailText"]))
                
                finding_story.append(Paragraph("<b>Technical Observation:</b>", self.styles["Normal"]))
                finding_story.append(Paragraph(f.get("detail", ""), self.styles["DetailText"]))
                
                # Evidence Traceability
                finding_story.append(Paragraph("<b>Evidence Traceability:</b>", self.styles["Normal"]))
                if not ev_items:
                    finding_story.append(Paragraph("No evidence details provided.", self.styles["DetailText"]))
                for idx, ev in enumerate(ev_items, 1):
                    finding_story.append(Paragraph(f"<b>--- Evidence #{idx} ---</b>", self.styles["DetailText"]))
                    finding_story.append(Paragraph(f"<b>Integration:</b> {ev.get('integration', 'Unknown')} | <b>Operation:</b> {ev.get('operation', 'Unknown')}", self.styles["DetailText"]))
                    finding_story.append(Paragraph(f"<b>Verdict:</b> {ev.get('verdict', 'Unknown')} | <b>Confidence:</b> {ev.get('confidence', 'Unknown')}", self.styles["DetailText"]))
                    finding_story.append(Paragraph(f"<b>Resource:</b> {ev.get('resource_type', 'Unknown')} - {ev.get('resource_name', 'Unknown')} (ID: {ev.get('resource_id', 'Unknown')})", self.styles["DetailText"]))
                    finding_story.append(Paragraph(f"<b>Location:</b> {ev.get('location', 'Not available from provider API')}", self.styles["DetailText"]))
                    finding_story.append(Paragraph(f"<b>Observed Detail:</b> {ev.get('detail', 'Unknown')}", self.styles["DetailText"]))
                    
                    if ev.get("expected_state") and ev.get("expected_state") != "Unknown":
                        finding_story.append(Paragraph(f"<b>Expected State:</b> {ev.get('expected_state')}", self.styles["DetailText"]))
                    if ev.get("observed_state") and ev.get("observed_state") != "Unknown":
                        finding_story.append(Paragraph(f"<b>Observed State:</b> {ev.get('observed_state')}", self.styles["DetailText"]))
                    
                    finding_story.append(Paragraph(f"<b>Reason:</b> {ev.get('reason', 'Unknown')}", self.styles["DetailText"]))
                    if ev.get('recommended_action'):
                        finding_story.append(Paragraph(f"<b>Recommended Action:</b> {ev.get('recommended_action')}", self.styles["DetailText"]))
                    finding_story.append(Spacer(1, 6))
                if f.get("reason"):
                    finding_story.append(Paragraph("<b>System Reasoning:</b>", self.styles["Normal"]))
                    reason_html = str(f.get("reason", "")).replace("\n", "<br/>")
                    finding_story.append(Paragraph(reason_html, self.styles["DetailText"]))
                
                finding_story.append(Paragraph("<b>Compliance Implication:</b>", self.styles["Normal"]))
                finding_story.append(Paragraph(f.get("compliance_implication", "Pending legal review."), self.styles["DetailText"]))
                
                finding_story.append(Paragraph("<b>Recommended Action:</b>", self.styles["Normal"]))
                rec_action = f.get("recommended_action") or "Please consult your legal team to ensure compliance."
                finding_story.append(Paragraph(rec_action, self.styles["DetailText"]))
                
                finding_story.append(Paragraph("<b>Source/API Operation:</b>", self.styles["Normal"]))
                srcs = ", ".join(f.get("sources", []))
                finding_story.append(Paragraph(srcs, self.styles["DetailText"]))
                
                finding_story.append(Paragraph("<b>Observed At:</b>", self.styles["Normal"]))
                finding_story.append(Paragraph(report_data.get('generated_at', 'Unknown'), self.styles["DetailText"]))
                
                finding_story.append(Paragraph("<b>Human Review:</b>", self.styles["Normal"]))
                finding_story.append(Paragraph("Required" if f.get("human_review_required") else "Not Required", self.styles["DetailText"]))
            
            finding_story.append(Spacer(1, 18))
            story.append(KeepTogether(finding_story))

        # Integrations
        combined = report_data.get("combined_report", {})
        integration_reports = combined.get("integration_reports", [])
        
        for int_rep in integration_reports:
            int_name = int_rep.get("integration_name", "Unknown Integration")
            story.append(Paragraph(f"{int_name}", self.styles["Heading2"]))
            
            conn_meta = int_rep.get("connection_metadata", {})
            conn_status = "Connected" if conn_meta.get("connected_at") else "Not connected"
            if conn_status == "Connected" and not conn_meta.get("resource_selected"):
                conn_status = "Resource not selected"
                
            story.append(Paragraph(f"<b>Connection Status:</b> {conn_status}", self.styles["Normal"]))
            story.append(Paragraph(f"<b>Account:</b> {conn_meta.get('provider_account_id') or 'N/A'}", self.styles["Normal"]))
            story.append(Paragraph(f"<b>Selected Resource:</b> {conn_meta.get('resource_name') or 'N/A'} ({conn_meta.get('resource_id') or 'N/A'})", self.styles["Normal"]))
            story.append(Spacer(1, 12))
            
            issues = int_rep.get("issues", [])
            evaluated = int_rep.get("evaluated_obligations", [])
            
            story.append(Paragraph("Issues Found", self.styles["Heading3"]))
            story.append(Spacer(1, 6))
            
            if issues:
                for idx, issue in enumerate(issues):
                    story.append(Paragraph(f"<b>{idx+1}. {issue.get('obligation_title', 'Finding')}</b>", self.styles["Heading4"]))
                    render_finding(issue, is_issue=True)
            elif not evaluated:
                story.append(Paragraph("Assessment confirmed no observed issues for assessed obligations.", self.styles["Normal"]))
                story.append(Spacer(1, 12))
                
            # Render evaluated obligations regardless of whether issues were found
            if evaluated:
                cannot_determine_items = [e for e in evaluated if str(e.get('verdict', '')).lower() in ('cannot determine', 'cannot_determine')]
                not_observed_items = [e for e in evaluated if str(e.get('verdict', '')).lower() in ('not observed', 'not_observed')]
                
                if cannot_determine_items:
                    story.append(Paragraph(
                        f"Assessment outcome cannot be fully determined. Evidence was incomplete for "
                        f"{len(cannot_determine_items)} obligation(s) listed below. "
                        f"These are NOT confirmed clean \u2014 they require further investigation.",
                        self.styles["Warning"]
                    ))
                    story.append(Spacer(1, 6))
                    story.append(Paragraph("Unresolved Evidence", self.styles["Heading3"]))
                    for idx, ev in enumerate(cannot_determine_items):
                        story.append(Paragraph(f"<b>{idx+1}. {ev.get('obligation_title', 'Evaluated')}</b>", self.styles["Heading4"]))
                        render_finding(ev, is_issue=False)
                
                if not_observed_items:
                    story.append(Paragraph("Confirmed Non-Observations", self.styles["Heading3"]))
                    for idx, ev in enumerate(not_observed_items):
                        story.append(Paragraph(f"<b>{idx+1}. {ev.get('obligation_title', 'Evaluated')}</b>", self.styles["Heading4"]))
                        render_finding(ev, is_issue=False)

        # Cross source reconciliation
        recon_data = combined.get("cross_source_reconciliation", [])
        if recon_data:
            story.append(Paragraph("Cross-Source Reconciliations", self.styles["Heading2"]))
            for r in recon_data:
                story.append(Paragraph(f"<b>{r.get('rule_id', 'Reconciliation')}</b>", self.styles["Heading4"]))
                story.append(Paragraph(f"<b>Sources:</b> {r.get('source_a', 'UNK')} vs {r.get('source_b', 'UNK')}", self.styles["Normal"]))
                
                outcome_text = f"{r.get('outcome', 'UNK')} (Confidence: {r.get('confidence', 'UNK').title()})"
                if r.get("human_review_required"):
                    outcome_text += " \u2014 <font color='red'>Human Review Required</font>"
                story.append(Paragraph(f"<b>Outcome:</b> {outcome_text}", self.styles["Normal"]))
                
                story.append(Paragraph(f"<b>Detail:</b> {r.get('detail', '')}", self.styles["DetailText"]))
                
                ev_a = r.get("evidence_a_detail")
                ev_b = r.get("evidence_b_detail")
                if ev_a or ev_b:
                    story.append(Spacer(1, 4))
                    if ev_a:
                        story.append(Paragraph(f"<i>Source A Evidence:</i> {ev_a}", self.styles["DetailText"]))
                    if ev_b:
                        story.append(Paragraph(f"<i>Source B Evidence:</i> {ev_b}", self.styles["DetailText"]))
                
                story.append(Spacer(1, 12))

        doc.build(story)
        return output_path
