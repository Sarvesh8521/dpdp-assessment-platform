import json
from pathlib import Path
from typing import Any

from reportlab.lib.pagesizes import letter
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
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

        def render_finding(f, is_issue=True):
            title = f"{f.get('obligation_code', 'UNK')}: {f.get('obligation_title', '')}"
            story.append(Paragraph(title, self.styles["FindingTitle"]))
            
            # Attributes
            verdict = f.get('verdict', '')
            conf = f.get('confidence', '')
            sev = f.get('severity', '')
            
            # Required fields
            story.append(Paragraph(f"<b>Verdict:</b> {verdict} | <b>Confidence:</b> {conf} | <b>Severity:</b> {sev}", self.styles["Normal"]))
            
            if verdict.lower() == "cannot determine":
                story.append(Paragraph("<b>Technical Observation:</b>", self.styles["Normal"]))
                story.append(Paragraph(f.get("detail", ""), self.styles["DetailText"]))
                
                story.append(Paragraph("<b>System Reasoning:</b>", self.styles["Normal"]))
                reason_html = str(f.get("reason", "")).replace("\n", "<br/>")
                story.append(Paragraph(reason_html, self.styles["DetailText"]))
                
                story.append(Paragraph("<b>Required Evidence / Resolution:</b>", self.styles["Normal"]))
                story.append(Paragraph("Required provider evidence unavailable. Connect the integration or verify resource access to resolve this.", self.styles["DetailText"]))
            else:
                story.append(Paragraph("<b>Exact Resource:</b>", self.styles["Normal"]))
                story.append(Paragraph(f.get("resource_name", f.get("resource_id", "N/A")), self.styles["DetailText"]))
                
                story.append(Paragraph("<b>Exact Location:</b>", self.styles["Normal"]))
                story.append(Paragraph("N/A", self.styles["DetailText"]))
                
                story.append(Paragraph("<b>Technical Observation:</b>", self.styles["Normal"]))
                story.append(Paragraph(f.get("detail", ""), self.styles["DetailText"]))
                
                # Evidence summary
                story.append(Paragraph("<b>Evidence:</b>", self.styles["Normal"]))
                story.append(Paragraph(f"{len(f.get('evidence_details', []))} evidence item(s)", self.styles["DetailText"]))
                
                if f.get("reason"):
                    story.append(Paragraph("<b>System Reasoning:</b>", self.styles["Normal"]))
                    reason_html = str(f.get("reason", "")).replace("\n", "<br/>")
                    story.append(Paragraph(reason_html, self.styles["DetailText"]))
                
                story.append(Paragraph("<b>Compliance Implication:</b>", self.styles["Normal"]))
                story.append(Paragraph(f.get("compliance_implication", "Pending legal review."), self.styles["DetailText"]))
                
                story.append(Paragraph("<b>Recommended Action:</b>", self.styles["Normal"]))
                rec_action = f.get("recommended_action") or "Please consult your legal team to ensure compliance."
                story.append(Paragraph(rec_action, self.styles["DetailText"]))
                
                story.append(Paragraph("<b>Source/API Operation:</b>", self.styles["Normal"]))
                srcs = ", ".join(f.get("sources", []))
                story.append(Paragraph(srcs, self.styles["DetailText"]))
                
                story.append(Paragraph("<b>Observed At:</b>", self.styles["Normal"]))
                story.append(Paragraph(report_data.get('generated_at', 'Unknown'), self.styles["DetailText"]))
                
                story.append(Paragraph("<b>Human Review:</b>", self.styles["Normal"]))
                story.append(Paragraph("Required" if f.get("human_review_required") else "Not Required", self.styles["DetailText"]))
            
            story.append(Spacer(1, 18))

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
            story.append(Paragraph(f"<b>Account:</b> {conn_meta.get('provider_account_id', 'N/A')}", self.styles["Normal"]))
            story.append(Paragraph(f"<b>Selected Resource:</b> {conn_meta.get('resource_name', 'N/A')} ({conn_meta.get('resource_id', 'N/A')})", self.styles["Normal"]))
            story.append(Spacer(1, 12))
            
            issues = int_rep.get("issues", [])
            evaluated = int_rep.get("evaluated_obligations", [])
            
            story.append(Paragraph("Issues Found", self.styles["Heading3"]))
            story.append(Spacer(1, 6))
            
            if issues:
                for idx, issue in enumerate(issues):
                    story.append(Paragraph(f"<b>{idx+1}. {issue.get('obligation_title', 'Finding')}</b>", self.styles["Heading4"]))
                    render_finding(issue, is_issue=True)
            else:
                story.append(Paragraph("No direct issues found. Evaluated obligations listed below:", self.styles["Normal"]))
                story.append(Spacer(1, 12))
                for idx, ev in enumerate(evaluated):
                    story.append(Paragraph(f"<b>{idx+1}. {ev.get('obligation_title', 'Evaluated')}</b>", self.styles["Heading4"]))
                    render_finding(ev, is_issue=False)

        # Cross source reconciliation
        recon_data = combined.get("cross_source_reconciliation", [])
        if recon_data:
            story.append(Paragraph("Cross-Source Reconciliations", self.styles["Heading2"]))
            for r in recon_data:
                story.append(Paragraph(f"<b>{r.get('rule_id', 'Reconciliation')}</b>", self.styles["Heading4"]))
                story.append(Paragraph(f"<b>Sources:</b> {r.get('source_a', 'UNK')} vs {r.get('source_b', 'UNK')}", self.styles["Normal"]))
                story.append(Paragraph(f"<b>Outcome:</b> {r.get('outcome', 'UNK')}", self.styles["Normal"]))
                story.append(Paragraph(f"<b>Detail:</b> {r.get('detail', '')}", self.styles["DetailText"]))
                story.append(Spacer(1, 12))

        doc.build(story)
        return output_path
