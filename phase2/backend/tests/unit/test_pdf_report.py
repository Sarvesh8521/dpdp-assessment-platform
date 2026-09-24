import pytest
import os
import json
from app.services.pdf_renderer import PDFReportRenderer
from pathlib import Path

def test_pdf_generation(tmp_path):
    renderer = PDFReportRenderer()
    
    mock_report = {
        "assessment_id": "1234-abcd",
        "client_id": "client-1",
        "generated_at": "2026-09-19T10:00:00Z",
        "summary": {
            "assessment_outcome": "Assessment outcome cannot be fully determined.",
            "verdicts": {
                "observed": 1,
                "not_observed": 1,
                "cannot_determine": 1
            },
            "total_findings": 3,
            "requires_human_review": 0
        },
        "combined_report": {
            "integration_reports": [
                {
                    "integration_name": "Google Tag Manager",
                    "connection_metadata": {
                        "connected_at": "2026-09-19T09:00:00Z",
                        "provider_account_id": "acc-1",
                        "resource_name": "My GTM Container",
                        "resource_id": "GTM-XXXX",
                        "resource_selected": True
                    },
                    "issues": [
                        {
                            "obligation_code": "POL-1",
                            "obligation_title": "Privacy Policy Required",
                            "verdict": "Observed",
                            "confidence": "High",
                            "severity": "High",
                            "detail": "Missing privacy policy link",
                            "evidence_details": [
                                {
                                    "integration": "gtm",
                                    "operation": "get_tags",
                                    "verdict": "Observed",
                                    "confidence": "High",
                                    "resource_type": "Tag",
                                    "resource_name": "Custom HTML Tag",
                                    "resource_id": "tag-123",
                                    "location": "Tag Configuration",
                                    "detail": "Tag contains raw HTML",
                                    "reason": "Direct HTML insertion identified."
                                }
                            ]
                        }
                    ],
                    "evaluated_obligations": [
                        {
                            "obligation_code": "TRK-1",
                            "obligation_title": "Restrict Tracking",
                            "verdict": "Cannot Determine",
                            "confidence": "Low",
                            "severity": "Medium",
                            "detail": "Unable to verify tracking configuration."
                        }
                    ]
                }
            ],
            "cross_source_reconciliation": [
                {
                    "rule_id": "RECON-1",
                    "source_a": "gtm",
                    "source_b": "cmp",
                    "outcome": "Contradiction",
                    "confidence": "High",
                    "detail": "GTM tags fire before CMP consent is granted."
                }
            ]
        }
    }
    
    output_pdf = tmp_path / "test_report.pdf"
    
    renderer.generate(mock_report, str(output_pdf))
    
    assert output_pdf.exists()
    
    try:
        import pypdf
        reader = pypdf.PdfReader(str(output_pdf))
        text = ""
        for page in reader.pages:
            text += page.extract_text()
            
        assert "Google Tag Manager" in text
        assert "POL-1: Privacy Policy Required" in text
        assert "Tag Configuration" in text
        assert "Missing privacy policy link" in text
        assert "TRK-1: Restrict Tracking" in text
        assert "N/A" not in text  # Ensure placeholders were removed
        assert "No issues were directly observed" not in text
        
        # Check finding count logic roughly by asserting title presence
        assert text.count("Privacy Policy Required") == 2
        assert text.count("Restrict Tracking") == 2
        
    except ImportError:
        pytest.skip("pypdf not installed, cannot verify text content")
