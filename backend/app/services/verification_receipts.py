from __future__ import annotations

from decimal import Decimal, InvalidOperation
from html import escape
from io import BytesIO
from pathlib import Path
from typing import Any

import pymupdf as fitz
import qrcode
from PIL import Image as PILImage
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import inch
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import Image, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle


_PRIVATE_DIR = Path(__file__).resolve().parents[2] / "storage" / "private"
_LOGO_PATH = Path(__file__).resolve().parents[3] / "frontend" / "public" / "global-eagle-logo.jpg"
_BLUE = colors.HexColor("#1d4ed8")
_PALE_BLUE = colors.HexColor("#eff6ff")
_BORDER = colors.HexColor("#dbe3ee")
_FONT_CANDIDATES = (
    (Path("C:/Windows/Fonts/KhmerUI.ttf"), Path("C:/Windows/Fonts/KhmerUIb.ttf")),
    (Path("C:/Windows/Fonts/segoeui.ttf"), Path("C:/Windows/Fonts/segoeuib.ttf")),
    (
        Path("/usr/share/fonts/truetype/noto/NotoSansKhmer-Regular.ttf"),
        Path("/usr/share/fonts/truetype/noto/NotoSansKhmer-Bold.ttf"),
    ),
)
_RECEIPT_FONT: str | None = None


def _register_receipt_fonts() -> str:
    global _RECEIPT_FONT
    if _RECEIPT_FONT:
        return _RECEIPT_FONT
    for regular_path, bold_path in _FONT_CANDIDATES:
        if regular_path.is_file() and bold_path.is_file():
            pdfmetrics.registerFont(TTFont("VisaDocUnicode", str(regular_path)))
            pdfmetrics.registerFont(TTFont("VisaDocUnicode-Bold", str(bold_path)))
            pdfmetrics.registerFontFamily(
                "VisaDocUnicode",
                normal="VisaDocUnicode",
                bold="VisaDocUnicode-Bold",
                italic="VisaDocUnicode",
                boldItalic="VisaDocUnicode-Bold",
            )
            _RECEIPT_FONT = "VisaDocUnicode"
            return _RECEIPT_FONT
    _RECEIPT_FONT = "Helvetica"
    return _RECEIPT_FONT


def create_qr_png(url: str) -> bytes:
    image = qrcode.make(url)
    output = BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()


def create_receipt_pdf(
    report: dict[str, Any],
    documents: list[dict[str, Any]],
    verification_url: str,
) -> bytes:
    font_name = _register_receipt_fonts()
    output = BytesIO()
    styles = getSampleStyleSheet()
    for name in ("Normal", "BodyText", "Title", "Heading1", "Heading2", "Heading3"):
        styles[name].fontName = font_name
    title_style = ParagraphStyle(
        "ReceiptTitle",
        parent=styles["Title"],
        textColor=_BLUE,
        fontSize=20,
        leading=24,
        alignment=TA_CENTER,
        spaceAfter=5,
    )
    section_style = ParagraphStyle(
        "ReceiptSection",
        parent=styles["Heading2"],
        textColor=_BLUE,
        fontSize=12,
        leading=16,
        keepWithNext=True,
        spaceBefore=10,
        spaceAfter=6,
    )
    small_style = ParagraphStyle(
        "ReceiptSmall",
        parent=styles["BodyText"],
        fontSize=8,
        leading=11,
    )
    doc_style = ParagraphStyle(
        "ReceiptDocument",
        parent=styles["BodyText"],
        fontSize=9,
        leading=12,
    )
    story: list[Any] = []
    summary = report.get("summary") or {}
    policy = report.get("evidencePolicySnapshot") or {}
    canonical = bool(policy) and (
        policy.get("scoringVersion") == "evidence-v3"
        or summary.get("scoringVersion") == "evidence-v3"
    )
    if canonical:
        section_style.spaceBefore = 6
        small_style.leading = 10
    brand = (
        Image(str(_LOGO_PATH), width=0.8 * inch, height=0.55 * inch)
        if _LOGO_PATH.is_file()
        else Paragraph("<b>VisaDoc AI</b>", title_style)
    )
    brand_header = Table(
        [[brand, Paragraph("<b>VisaDoc AI</b><br/>Document Verification Receipt", title_style)]],
        colWidths=[1.0 * inch, 5.7 * inch],
    )
    brand_header.setStyle(
        TableStyle(
            [
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("ALIGN", (1, 0), (1, 0), "CENTER"),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
            ]
        )
    )
    story.extend([brand_header, Spacer(1, 7)])

    qr_image = Image(BytesIO(create_qr_png(verification_url)), width=0.95 * inch, height=0.95 * inch)
    header = Table(
        [
            [
                Paragraph(
                    "<br/>".join(
                        [
                            f"<b>Receipt:</b> {escape(str(report.get('receiptNumber') or ''))}",
                            f"<b>Issued:</b> {escape(str(report.get('receiptGeneratedAt') or ''))}",
                            f"<b>Status:</b> {escape(str(report.get('finalStatus') or ''))}",
                            f"<b>Report type:</b> {escape(str(report.get('reportType') or 'LEGACY'))}",
                            f"<b>Receipt version:</b> {escape(str(report.get('receiptVersion') or 1))}",
                        ]
                    ),
                    styles["BodyText"],
                ),
                qr_image,
            ]
        ],
        colWidths=[5.7 * inch, 1.0 * inch],
    )
    header.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), _PALE_BLUE),
                ("BOX", (0, 0), (-1, -1), 0.6, _BORDER),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("LEFTPADDING", (0, 0), (-1, -1), 9),
                ("RIGHTPADDING", (0, 0), (-1, -1), 9),
                ("TOPPADDING", (0, 0), (-1, -1), 7),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
            ]
        )
    )
    story.append(header)
    if canonical:
        story.append(Paragraph(f"Verification URL: {escape(verification_url)}", small_style))
    story.append(Paragraph("Applicant Information", section_style))
    applicant = report.get("applicant") or {}
    application = report.get("application") or {}
    applicant_rows = [
        ["Full name", applicant.get("fullName") or "—", "Application reference", application.get("applicationNumber") or "—"],
        ["Nationality", applicant.get("nationality") or "—", "Passport", applicant.get("passportNumber") or "—"],
        ["Destination", application.get("destination") or "—", "Visa type", application.get("visaTypeName") or "—"],
    ]
    story.append(_styled_table(applicant_rows, [1.05, 2.25, 1.15, 2.25]))

    story.append(Paragraph("Verification Summary", section_style))
    summary_rows = [
        ["Total documents", summary.get("totalDocuments", 0), "Verified", summary.get("verifiedDocuments", 0)],
        ["Rejected", summary.get("rejectedDocuments", 0), "Missing required", len(summary.get("missingRequiredDocuments") or [])],
    ]
    if canonical:
        summary_rows.extend([
            ["Evidence strength", f"{summary.get('exactPoints', summary.get('confirmedReadinessPoints', 'Unavailable'))} / 100", "Assessment status", summary.get("status") or "Unavailable"],
            ["Verification percentage", f"{summary.get('verificationPercentage', 0)}%", "Evidence scoring version", summary.get("scoringVersion") or policy.get("scoringVersion")],
        ])
    else:
        summary_rows.extend([
            ["Verification percentage", f"{summary.get('verificationPercentage', 0)}%", "Document readiness", f"{summary.get('documentReadiness', 0)}%"],
            ["Points earned", summary.get("verifiedPoints", 0), "Points available", summary.get("totalAvailablePoints", 0)],
        ])
    story.append(_styled_table(summary_rows, [1.35, 1.9, 1.35, 2.1]))
    if canonical:
        _append_evidence_ledger(story, report, section_style, small_style)
        _append_aba_evidence(story, report, section_style, small_style)

    bank_documents = [
        item
        for item in report.get("documents") or []
        if isinstance(item.get("bankStatementAnalysis"), dict)
        and not (canonical and isinstance(item.get("abaStatementAnalysis"), dict))
    ]
    if bank_documents:
        story.append(Paragraph("Bank Statement Financial Checks", section_style))
        for item in bank_documents:
            analysis = item["bankStatementAnalysis"]
            score = analysis.get("bankStatementScore") or {}
            story.append(
                Paragraph(
                    (
                        f"<b>{escape(str(item.get('fileName') or 'Bank statement'))}</b> - "
                        f"Score: {escape(str(score.get('points') if score.get('available') else 'Unavailable'))}/"
                        f"{escape(str(score.get('maximum', 50)))} - "
                        f"Three-month requirement: "
                        f"{escape(str(analysis.get('financialRequirementStatus') or 'INSUFFICIENT_DATA'))} - "
                        f"Authenticity: not confirmed"
                    ),
                    styles["BodyText"],
                )
            )
            monthly_rows = [
                [
                    "Month",
                    "Opening",
                    "Closing",
                    "Minimum",
                    "Difference",
                    "Status",
                ]
            ]
            monthly_history = {
                str(entry.get("month") or ""): entry
                for entry in analysis.get("monthlyBalanceHistory") or []
                if isinstance(entry, dict)
            }
            for check in analysis.get("monthlyBalanceChecks") or []:
                history = monthly_history.get(str(check.get("month") or ""), {})
                monthly_rows.append(
                    [
                        check.get("month") or "—",
                        (
                            f"{history.get('openingBalance')} {history.get('currency') or ''}"
                            if history.get("openingBalance") is not None
                            else "—"
                        ),
                        (
                            f"{check.get('closingBalance')} {check.get('currency') or ''}"
                            if check.get("closingBalance") is not None
                            else "—"
                        ),
                        (
                            f"{check.get('minimumRequired')} USD"
                            if check.get("minimumRequired") is not None
                            else "—"
                        ),
                        (
                            f"{check.get('differenceFromMinimum')} USD"
                            if check.get("differenceFromMinimum") is not None
                            else "—"
                        ),
                        check.get("status") or "INSUFFICIENT_DATA",
                    ]
                )
            if len(monthly_rows) > 1:
                story.append(
                    _styled_table(
                        monthly_rows,
                        [0.85, 1.0, 1.1, 1.0, 1.0, 1.1],
                    )
                )
            else:
                story.append(
                    Paragraph(
                        "Three complete months of explicit USD closing-balance evidence were not available.",
                        small_style,
                    )
                )
            flags = analysis.get("riskFlags") or []
            if flags:
                story.append(
                    Paragraph(
                        "<br/>".join(
                            f"- {escape(str(flag.get('reason') or 'Manual review required'))}"
                            for flag in flags[:12]
                        ),
                        small_style,
                    )
                )
            story.append(
                Paragraph(
                    escape(str(analysis.get("disclaimer") or "")),
                    small_style,
                )
            )

    prediction = report.get("prediction") or {}
    story.append(Paragraph("Machine Learning Estimate", section_style))
    if prediction.get("status") == "COMPLETED":
        story.append(
            _styled_table(
                [
                    ["Approval probability", f"{prediction.get('approvalProbability')}%", "Rejection probability", f"{prediction.get('rejectionProbability')}%"],
                    ["Risk level", prediction.get("riskLevel") or "Unavailable", "Model version", prediction.get("modelVersion") or "—"],
                    ["Prediction date", prediction.get("timestamp") or "—", "", ""],
                ],
                [1.35, 1.9, 1.35, 2.1],
            )
        )
        for heading, key in (
            ("Positive factors", "positiveFactors"),
            ("Risk factors", "riskFactors"),
            ("Recommendations", "recommendations"),
        ):
            story.append(Paragraph(heading, styles["Heading3"]))
            values = prediction.get(key) or []
            story.append(
                Paragraph(
                    "<br/>".join(f"- {escape(str(value))}" for value in values)
                    if values
                    else "No validated factors available.",
                    small_style,
                )
            )
    else:
        story.append(
            Paragraph(
                "Prediction unavailable. No approval or rejection probability was generated.",
                styles["BodyText"],
            )
        )
    if canonical:
        story.append(_styled_table([
            ["Prediction model version", prediction.get("modelVersion") or "Unavailable", "Feature schema version", prediction.get("featureSchemaVersion") or "Unavailable"],
        ], [1.35, 1.9, 1.35, 2.1]))

    story.append(Paragraph("Uploaded Documents", section_style))
    raw_documents = {str(item.get("_id") or ""): item for item in documents}
    awarded_documents: set[str] = set()
    for item in report.get("documents") or []:
        stored = raw_documents.get(str(item.get("documentId") or ""), {})
        preview = _document_thumbnail(stored)
        thumbnail = Image(BytesIO(preview), width=1.35 * inch, height=1.0 * inch) if preview else Paragraph("Preview unavailable", small_style)
        fields = item.get("extractedFields") or {}
        extracted = "; ".join(
            f"{escape(str(key))}: {escape(str(value))}"
            for key, value in list(fields.items())[:8]
        ) or "No extracted fields"
        points = _canonical_document_points(item, summary, awarded_documents) if canonical else f"Points: {item.get('awardedPoints', 0)} / {item.get('assignedPoints', 0)}"
        detail = "<br/>".join(
            [
                f"<b>{escape(str(item.get('fileName') or 'Document'))}</b>",
                f"Reference: {escape(str(item.get('documentReference') or '—'))}",
                f" OCR: {escape(str(item.get('ocrStatus') or '-'))} - Verification: {escape(str(item.get('status') or '-'))}",
                escape(points),
                f"Comments: {escape(str(item.get('verificationComments') or item.get('rejectionReason') or '—'))}",
                f"Extracted: {extracted}",
            ]
        )
        row = Table([[thumbnail, Paragraph(detail, doc_style)]], colWidths=[1.55 * inch, 5.15 * inch])
        row.setStyle(
            TableStyle(
                [
                    ("BOX", (0, 0), (-1, -1), 0.5, _BORDER),
                    ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                    ("LEFTPADDING", (0, 0), (-1, -1), 7),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 7),
                    ("TOPPADDING", (0, 0), (-1, -1), 7),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
                ]
            )
        )
        story.extend([row, Spacer(1, 5)])

    story.append(Paragraph("Verification", section_style))
    story.append(
        Paragraph(
            "<br/>".join(
                [
                    f"<b>Verified by:</b> {escape(str(report.get('verifiedBy') or '—'))}",
                    f"<b>Verification date:</b> {escape(str(report.get('verifiedAt') or '—'))}",
                    f"<b>Final status:</b> {escape(str(report.get('finalStatus') or '—'))}",
                    f"<b>Administrator comments:</b> {escape(str(report.get('administratorComments') or '—'))}",
                ]
            ),
            styles["BodyText"],
        )
    )
    story.extend(
        [
            Spacer(1, 4 if canonical else 10),
            Paragraph(
                "Official system-generated receipt. Machine-learning predictions are estimates for human review and are not visa decisions or guarantees.",
                small_style,
            ),
            *([] if canonical else [Paragraph(f"Verification URL: {escape(verification_url)}", small_style)]),
        ]
    )
    document = SimpleDocTemplate(
        output,
        pagesize=letter,
        rightMargin=0.45 * inch,
        leftMargin=0.45 * inch,
        topMargin=0.45 * inch,
        bottomMargin=0.55 * inch,
        title=f"VisaDoc AI Receipt {report.get('receiptNumber', '')}",
        author="VisaDoc AI",
    )
    document.build(story, onFirstPage=_draw_footer, onLaterPages=_draw_footer)
    return output.getvalue()


def _display(value: Any, missing: str = "Unavailable") -> str:
    return missing if value is None or value == "" else str(value).replace("—", "-").replace("–", "-").replace("·", "-")


def _minor_amount(value: Any, currency: str) -> str:
    if value is None or isinstance(value, bool):
        return "Unavailable"
    try:
        amount = Decimal(str(value)) / 100
        if not amount.is_finite():
            return "Unavailable"
        return f"{currency} {amount:,.2f}".strip()
    except (InvalidOperation, ValueError):
        return "Unavailable"


def _append_evidence_ledger(story: list[Any], report: dict[str, Any], section_style, small_style) -> None:
    summary = report.get("summary") or {}
    policy = report.get("evidencePolicySnapshot") or {}
    story.append(Paragraph("Evidence Assessment Ledger", section_style))
    story.append(Paragraph(
        "Evidence strength is not visa approval probability or proof of document authenticity. "
        "The saved policy allocates 100 points. Only the selected evidence in each requirement slot earns points; uploads are not added together.",
        small_style,
    ))
    story.append(_styled_table([
        ["Saved policy version", policy.get("version") or summary.get("policyVersion"), "Policy version ID", report.get("policyVersionId") or policy.get("id") or policy.get("_id")],
        ["Policy name", policy.get("name"), "Scoring version", summary.get("scoringVersion") or policy.get("scoringVersion")],
        ["Effective from", policy.get("effectiveFrom"), "Timezone", policy.get("timezone")],
        ["Published at", policy.get("publishedAt"), "Published by", policy.get("publishedBy")],
    ], [1.15, 2.2, 1.15, 2.2]))
    if policy.get("contentHash"):
        story.append(Paragraph(f"Saved policy content hash: {escape(_display(policy['contentHash']))}", small_style))
    if report.get("supersedesReceiptNumber"):
        story.append(Paragraph(
            f"Supersedes receipt: {escape(_display(report['supersedesReceiptNumber']))}<br/>"
            f"Revision reason: {escape(_display(report.get('revisionReason')))}", small_style,
        ))
    rows = [["Requirement", "Exact points / maximum", "Quality (0-1)", "Status", "Selected document"]]
    ledger = [slot for slot in summary.get("requirementScores") or [] if isinstance(slot, dict)]
    for slot in ledger:
        rows.append([
            slot.get("name") or slot.get("requirementId"),
            f"{_display(slot.get('exactEarnedPoints', slot.get('earnedPoints')))} / {_display(slot.get('maximumEarnedPoints', slot.get('configuredPoints')))}",
            _display(slot.get("quality")), slot.get("status"), _display(slot.get("selectedDocumentId"), "Not selected"),
        ])
    rows.append(["Total evidence strength", f"{_display(summary.get('exactPoints', summary.get('confirmedReadinessPoints')))} / 100", "", summary.get("status"), ""])
    story.append(_styled_table(rows, [1.75, 1.0, 0.75, 1.2, 2.0]))
    policy_requirements = {str(item.get("requirementId") or item.get("id") or ""): item for item in policy.get("requirements") or [] if isinstance(item, dict)}
    for slot in ledger:
        requirement = policy_requirements.get(str(slot.get("requirementId") or ""), {})
        provenance = slot.get("provenance") or {}
        versions = []
        for label, value in (
            ("Scorer", requirement.get("qualityScorer")),
            ("Scorer/model version", provenance.get("bankModelVersion") or requirement.get("qualityScorerVersion")),
            ("Mapping version", provenance.get("mappingVersion") or requirement.get("mappingVersion")),
            ("Analysis revision", provenance.get("bankAnalysisRevision")),
            ("Rubric version", provenance.get("rubricVersion")),
        ):
            if value is not None:
                versions.append(f"{label}: {escape(_display(value))}")
        if versions:
            story.append(Paragraph(f"<b>{escape(_display(slot.get('name') or slot.get('requirementId')))}</b> - " + "; ".join(versions), small_style))
        if slot.get("reasons"):
            story.append(Paragraph("Review reasons: " + escape("; ".join(str(reason) for reason in slot['reasons'])), small_style))
    for label, value in (
        ("Assessment input fingerprint", summary.get("inputFingerprint")),
        ("Saved assessment context fingerprint", report.get("assessmentContextFingerprint")),
    ):
        if value:
            story.append(Paragraph(f"{label}: {escape(_display(value))}", small_style))


def _append_aba_evidence(story: list[Any], report: dict[str, Any], section_style, small_style) -> None:
    for item in report.get("documents") or []:
        analysis = item.get("abaStatementAnalysis")
        if not isinstance(analysis, dict):
            continue
        source = analysis.get("source") or {}
        statement = analysis.get("statement") or {}
        metrics = analysis.get("metrics") or {}
        currency = str(statement.get("currency") or source.get("currency") or "")
        story.append(Paragraph("ABA Statement Evidence", section_style))
        source_heading = Paragraph(f"<b>{escape(_display(item.get('fileName'), 'Bank statement'))}</b> - Document: {escape(_display(item.get('documentId')))}", small_style)
        source_heading.keepWithNext = True
        story.append(source_heading)
        strength = analysis.get("statementStrengthScore")
        story.append(_styled_table([
            ["Statement strength", f"{_display(strength)} / 10" if strength is not None else "Unavailable", "Normalized quality (0-1)", _display(analysis.get("normalizedQuality"))],
            ["Review status", analysis.get("reviewStatus"), "Reconciliation", analysis.get("reconciliationStatus")],
            ["Owner relationship", analysis.get("ownerRelationship"), "Ownership confirmed", "Yes" if analysis.get("ownerRelationshipConfirmed") else "No"],
            ["Period start", statement.get("start"), "Period end", statement.get("end")],
            ["Average daily posted balance", _minor_amount(metrics.get("average"), currency), "Ending posted balance", _minor_amount(metrics.get("ending", statement.get("ending")), currency)],
            ["Money in", _minor_amount(metrics.get("money_in", metrics.get("total_in")), currency), "Money out", _minor_amount(metrics.get("money_out", metrics.get("total_out")), currency)],
            ["Scorer/model version", analysis.get("modelVersion"), "Mapping version", analysis.get("mappingVersion")],
            ["Schema version", analysis.get("schemaVersion"), "Source units", source.get("units") or "minor"],
        ], [1.35, 1.9, 1.35, 2.1]))
        story.append(Paragraph(
            "Financial source amounts use minor currency units and are displayed above in major currency units. "
            "For bank-linear-1-to-10-v1: quality = (final capped/rounded score - 1) / 9; "
            "slot points = minimum + (maximum - minimum) x quality. "
            "Unavailable or unverified evidence earns zero confirmed points.", small_style,
        ))
        for label, value in (
            ("Analysis revision", analysis.get("analysisRevision")),
            ("Document revision", analysis.get("documentRevision")),
            ("Source SHA-256", source.get("sha256")),
            ("Source file", source.get("filename")),
            ("Source pages", source.get("pages")),
            ("Calculated at", analysis.get("calculatedAt")),
        ):
            if value is not None:
                story.append(Paragraph(f"{label}: {escape(_display(value))}", small_style))
        for event in analysis.get("audit") or []:
            if not isinstance(event, dict):
                continue
            references = [f"{label}: {_display(event.get(key))}" for label, key in (
                ("Type", "type"), ("At", "at"), ("Actor", "actorId"), ("Status", "status"),
                ("Row", "row"), ("Field", "field"), ("Original (source units)", "original"),
                ("Corrected (source units)", "corrected"), ("Reason", "reason"),
                ("Source resolution confirmed", "sourceResolutionConfirmed"),
            ) if event.get(key) is not None]
            story.append(Paragraph("<b>Source/review audit:</b> " + escape("; ".join(references)), small_style))
        story.append(Paragraph(
            "Authenticity is not verified. " + escape(_display(analysis.get("availableBalance"), "Posted balances may exclude holds and pending withdrawals; spendable balance is unknown.")),
            small_style,
        ))


def _canonical_document_points(item: dict[str, Any], summary: dict[str, Any], awarded_documents: set[str]) -> str:
    document_id = str(item.get("documentId") or "")
    slots = [slot for slot in summary.get("requirementScores") or [] if isinstance(slot, dict)]
    selected = next((slot for slot in slots if str(slot.get("selectedDocumentId") or "") == document_id and document_id), None)
    related = selected or next((slot for slot in slots if document_id in [str(value) for value in slot.get("documentIds") or []] or str(item.get("requirementId") or "") in [str(slot.get("requirementId") or ""), *[str(value) for value in slot.get("alternativeRequirementIds") or []]]), {})
    maximum = _display(related.get("maximumEarnedPoints", related.get("configuredPoints")))
    if not selected or document_id in awarded_documents:
        return f"Points: 0 / {maximum} - Not selected; no additional award"
    awarded_documents.add(document_id)
    return f"Points: {_display(selected.get('exactEarnedPoints', selected.get('earnedPoints')))} / {maximum} - Canonical ledger contribution"


def _styled_table(rows: list[list[Any]], widths: list[float]) -> Table:
    cell_style = ParagraphStyle("ReceiptCell", fontName=_register_receipt_fonts(), fontSize=8, leading=10, splitLongWords=True)
    wrapped = [[Paragraph(escape(_display(value, "-")), cell_style) for value in row] for row in rows]
    table = Table(wrapped, colWidths=[width * inch for width in widths])
    table.setStyle(
        TableStyle(
            [
                ("GRID", (0, 0), (-1, -1), 0.4, _BORDER),
                ("BACKGROUND", (0, 0), (0, -1), _PALE_BLUE),
                ("BACKGROUND", (2, 0), (2, -1), _PALE_BLUE),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("FONTSIZE", (0, 0), (-1, -1), 8),
                ("FONTNAME", (0, 0), (-1, -1), _register_receipt_fonts()),
                ("LEFTPADDING", (0, 0), (-1, -1), 6),
                ("RIGHTPADDING", (0, 0), (-1, -1), 6),
                ("TOPPADDING", (0, 0), (-1, -1), 4),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ]
        )
    )
    return table


def _document_thumbnail(document: dict[str, Any]) -> bytes | None:
    storage_path = Path(str(document.get("storagePath") or "")).resolve()
    try:
        storage_path.relative_to(_PRIVATE_DIR.resolve())
    except ValueError:
        return None
    if not storage_path.is_file():
        return None
    content = storage_path.read_bytes()
    if document.get("mimeType") == "application/pdf":
        with fitz.open(stream=content, filetype="pdf") as source:
            if not source.page_count:
                return None
            page = source.load_page(0)
            pixmap = page.get_pixmap(matrix=fitz.Matrix(0.45, 0.45), alpha=False)
            return pixmap.tobytes("png")
    with PILImage.open(BytesIO(content)) as source:
        source.thumbnail((360, 270))
        converted = source.convert("RGB")
        output = BytesIO()
        converted.save(output, format="JPEG", quality=78)
        return output.getvalue()


def _draw_footer(canvas, _document) -> None:
    canvas.saveState()
    canvas.setStrokeColor(_BORDER)
    canvas.line(0.45 * inch, 0.4 * inch, 7.95 * inch, 0.4 * inch)
    canvas.setFont("Helvetica", 7)
    canvas.setFillColor(colors.HexColor("#64748b"))
    canvas.drawString(0.45 * inch, 0.25 * inch, "VisaDoc AI | Official system-generated receipt")
    canvas.drawRightString(7.95 * inch, 0.25 * inch, f"Page {canvas.getPageNumber()}")
    canvas.restoreState()
