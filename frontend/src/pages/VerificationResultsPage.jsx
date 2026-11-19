import { useEffect, useState } from "react";
import {
  ArrowLeft,
  CalendarDays,
  CheckCircle2,
  CircleCheck,
  CircleX,
  Clock3,
  Download,
  FileCheck2,
  FileText,
  FolderOpen,
  Grid2X2,
  Hourglass,
  Percent,
  Printer,
  ShieldAlert,
  Eye,
} from "lucide-react";
import { Link, useParams } from "react-router-dom";
import { api } from "./pageUtils";
import AbaBankPanel from "../components/AbaBankPanel";
import EvidenceAssessmentPanel from "../components/EvidenceAssessmentPanel";
import {
  AutomatedVerificationReport,
  BankStatementAnalysis,
  Metric,
} from "../components/VerificationReportDetails";

function receiptTime(value) {
  if (!value) return "Issue time unavailable";
  const timestamp = String(value);
  const date = new Date(/(?:Z|[+-]\d{2}:\d{2})$/i.test(timestamp) ? timestamp : `${timestamp}Z`);
  return Number.isNaN(date.getTime()) ? "Issue time unavailable" : `${date.toLocaleString("en-GB", { timeZone: "Asia/Phnom_Penh" })} (Asia/Phnom_Penh)`;
}

function isPhotoDocument(document) {
  return String(document.requirementCode || "").toLowerCase().includes("photo");
}

export default function VerificationResultsPage({ profile = {} }) {
  const { applicationId } = useParams();
  const [results, setResults] = useState(null);
  const [previews, setPreviews] = useState({});
  const [previewErrors, setPreviewErrors] = useState({});
  const [selectedPreviewId, setSelectedPreviewId] = useState("");
  const [qrCode, setQrCode] = useState("");
  const [comments, setComments] = useState("");
  const [finalStatus, setFinalStatus] = useState("VERIFIED");
  const [reportType, setReportType] = useState("EVIDENCE_ASSESSMENT");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [reviewForms, setReviewForms] = useState({});
  const canManage = ["ADMIN", "STAFF"].includes(profile.role);

  const loadResults = async () => {
    const response = await api.get(`/verification/results/${applicationId}`);
    setResults(response.data);
  };

  useEffect(() => {
    let active = true;
    api
      .get(`/verification/results/${applicationId}`)
      .then((response) => {
        if (active) setResults(response.data);
      })
      .catch((requestError) => {
        if (active) {
          setError(
            requestError?.response?.data?.message ||
              "Unable to load verification results.",
          );
        }
      });
    return () => {
      active = false;
    };
  }, [applicationId]);

  useEffect(() => {
    const needsRefresh = results?.documents?.some((item) => {
      const status = String(item.ocrStatus || "").toUpperCase();
      return (
        ["OCR_PENDING", "OCR_PROCESSING"].includes(status) ||
        (status === "OCR_COMPLETED" && !item.automatedVerification)
      );
    });
    if (!needsRefresh) return undefined;
    const timer = window.setInterval(() => {
      api
        .get(`/verification/results/${applicationId}`)
        .then((response) => setResults(response.data))
        .catch((requestError) =>
          setError(
            requestError?.response?.data?.message ||
              "Unable to refresh automated verification results.",
          ),
        );
    }, 1500);
    return () => window.clearInterval(timer);
  }, [applicationId, results]);

  useEffect(() => {
    if (!results?.documents?.length) return undefined;
    let active = true;
    const objectUrls = [];
    Promise.all(
      results.documents.map(async (document) => {
        try {
          const response = await api.get(document.contentUrl, {
            responseType: "blob",
          });
          const url = URL.createObjectURL(response.data);
          objectUrls.push(url);
          return [document.documentId, url];
        } catch {
          return [document.documentId, ""];
        }
      }),
    ).then((items) => {
      if (!active) return;
      setPreviews(Object.fromEntries(items));
      setPreviewErrors(
        Object.fromEntries(
          items
            .filter(([, url]) => !url)
            .map(([id]) => [
              id,
              "Preview unavailable. Use the authorized download link.",
            ]),
        ),
      );
    });
    return () => {
      active = false;
      objectUrls.forEach((url) => URL.revokeObjectURL(url));
    };
  }, [results]);

  useEffect(() => {
    let active = true;
    if (!results?.receiptNumber) {
      return () => {
        active = false;
      };
    }
    api
      .get(`/verification/receipts/${results.receiptNumber}`)
      .then((response) => {
        if (active) setQrCode(response.data.qrCodeDataUrl || "");
      })
      .catch(() => {
        if (active) setQrCode("");
      });
    return () => {
      active = false;
    };
  }, [results?.receiptNumber]);

  const runPrediction = async () => {
    setBusy(true);
    setError("");
    setNotice("");
    try {
      await api.post(`/verification/${applicationId}/predict`);
      await loadResults();
      setNotice(
        "Prediction updated. Any estimate shown is not a visa decision.",
      );
    } catch (requestError) {
      setError(
        requestError?.response?.data?.message || "Unable to run prediction.",
      );
    } finally {
      setBusy(false);
    }
  };

  const finalize = async (event) => {
    event.preventDefault();
    setBusy(true);
    setError("");
    setNotice("");
    try {
      await api.post(`/verification/${applicationId}/finalize`, {
        status: finalStatus,
        reportType,
        comments,
      });
      await loadResults();
      setNotice("Verification completed and receipt generated.");
    } catch (requestError) {
      setError(
        requestError?.response?.data?.message ||
          "Unable to finalize verification.",
      );
    } finally {
      setBusy(false);
    }
  };

  const downloadReceiptNumber = async (number) => {
    if (typeof number !== "string" || !number.trim()) return;
    setError("");
    try {
      const response = await api.get(
        `/verification/receipts/${encodeURIComponent(number)}/pdf`,
        { responseType: "blob" },
      );
      const url = URL.createObjectURL(response.data);
      const link = document.createElement("a");
      link.href = url;
      link.download = `${number}.pdf`;
      link.click();
      window.setTimeout(() => URL.revokeObjectURL(url), 1000);
    } catch (requestError) {
      setError(
        requestError?.response?.data?.message ||
          "Unable to download the receipt.",
      );
    }
  };

  // Keep DOM click events out of the receipt-number argument. Both current
  // and historical downloads use the same authenticated blob request.
  const downloadReceipt = () => downloadReceiptNumber(results?.receiptNumber);

  const rerunDocumentVerification = async (documentId) => {
    setBusy(true);
    setError("");
    try {
      await api.post(`/documents/${documentId}/verification/retry`);
      await loadResults();
      setNotice("Document verification was rerun using the saved OCR evidence.");
    } catch (requestError) {
      setError(
        requestError?.response?.data?.message ||
          "Unable to rerun document verification.",
      );
    } finally {
      setBusy(false);
    }
  };

  const reviewDocument = async (item) => {
    const form = reviewForms[item.documentId] || {};
    setBusy(true);
    setError("");
    setNotice("");
    try {
      await api.patch(`/documents/${item.documentId}/review`, {
        status: form.status || "NEEDS_REVIEW",
        reason: form.reason || "",
      });
      await loadResults();
      setReviewForms((current) => ({
        ...current,
        [item.documentId]: { ...current[item.documentId], reason: "" },
      }));
      setNotice("Document review override saved with its audit reason.");
    } catch (requestError) {
      setError(
        requestError?.response?.data?.message ||
          "Unable to save the document review.",
      );
    } finally {
      setBusy(false);
    }
  };

  if (!results) {
    return (
      <main className="space-y-4">
        <h1 className="text-2xl font-bold text-slate-900">
          Verification Report
        </h1>
        {error ? (
          <p role="alert" className="rounded-xl bg-red-50 p-4 text-red-700">
            {error}
          </p>
        ) : (
          <p role="status" className="text-slate-600">
            Loading verification report…
          </p>
        )}
      </main>
    );
  }

  const application = results.application || {};
  const applicant = results.applicant || {};
  const summary = results.summary || {};
  const readiness = summary.readinessScore;
  const bankRequirementScores = Object.fromEntries(
    (summary.bankStatementScore || []).map((item) => [item.documentId, item]),
  );
  const prediction = results.prediction || { status: "UNAVAILABLE" };
  const documents = results.documents || [];
  const latestVerification = results.latestVerification;
  const report = results.verificationReport || {};
  const receiptNumber = results.receiptNumber;
  const finalized = Boolean(receiptNumber) && results.receiptIsCurrent !== false;
  const needsRefinalization = Boolean(results.assessmentNeedsRefinalization);
  const reportStatus = finalized
    ? report.finalStatus || application.status || "RECORDED"
    : summary.automatedVerificationStatus || "INSUFFICIENT_DATA";
  const statusStyle =
    reportStatus === "VERIFIED"
      ? "border-emerald-200 bg-emerald-50 text-emerald-800"
      : reportStatus === "REJECTED"
        ? "border-red-200 bg-red-50 text-red-800"
        : "border-amber-200 bg-amber-50 text-amber-900";
  const selectedDocument =
    documents.find((item) => item.documentId === selectedPreviewId) ||
    documents[0];
  const allChecks = documents.flatMap((item) =>
    (item.automatedVerification?.checks || []).map((check) => ({
      ...check,
      fileName: item.fileName,
      documentStatus: item.status,
    })),
  );
  const passedChecks = allChecks.filter((check) => check.status === "PASSED");
  const unresolvedChecks = allChecks.filter(
    (check) => check.status !== "PASSED" && String(check.documentStatus || "").toUpperCase() !== "VERIFIED",
  );
  const terminalDocumentStates = [
    "VERIFIED",
    "REJECTED",
    "SUSPICIOUS",
    "INSUFFICIENT_DATA",
  ];
  const pendingDocuments = documents.filter(
    (item) =>
      !terminalDocumentStates.includes(String(item.status || "").toUpperCase()),
  ).length;
  const totalPages = summary.ocrProgress?.totalPages ?? 0;
  const processedPages = summary.ocrProgress?.pagesProcessed ?? 0;
  const reportDate = latestVerification?.createdAt || application.createdAt;
  const pdfAction = finalized ? downloadReceipt : () => window.print();
  const bankDocuments = documents.filter((item) => item.bankStatementAnalysis);
  const requirementScores = summary.requirementScores || [];
  const requirementById = Object.fromEntries(
    requirementScores.map((item) => [String(item.requirementId), item]),
  );
  const documentName = (item) =>
    requirementById[String(item.requirementId)]?.name ||
    item.requirementCode ||
    item.fileName ||
    "Document";
  const statusMeta = (status) => {
    const normalized = String(status || "PROCESSING").toUpperCase();
    if (normalized === "VERIFIED" || normalized === "PASS") {
      return {
        label: "Verified",
        icon: CheckCircle2,
        badge: "bg-emerald-50 text-emerald-700",
        border: "border-emerald-200",
      };
    }
    if (normalized === "REJECTED" || normalized === "FAIL") {
      return {
        label: "Rejected",
        icon: CircleX,
        badge: "bg-red-50 text-red-700",
        border: "border-red-200",
      };
    }
    if (normalized === "NOT_UPLOADED" || normalized === "MISSING") {
      return {
        label: "Not submitted",
        icon: FileText,
        badge: "bg-slate-100 text-slate-600",
        border: "border-slate-200",
      };
    }
    return {
      label: normalized === "NEEDS_REVIEW" ? "Pending review" : "Processing",
      icon: Hourglass,
      badge: "bg-amber-50 text-amber-700",
      border: "border-amber-200",
    };
  };
  const allChecksCompleted =
    requirementScores.length > 0 &&
    requirementScores.every((item) => item.status === "VERIFIED");

  return (
    <main className="verification-report mx-auto max-w-[1500px] space-y-5">
      <header className="verification-hero flex flex-wrap items-center justify-between gap-3">
        <div className="flex items-center gap-3">
          <div>
            <p className="verification-breadcrumb text-xs font-semibold text-blue-700">
              Visa Applications <span aria-hidden="true">›</span> Verification Results
            </p>
            <h1 className="text-2xl font-bold text-slate-900">
              Application assessment
            </h1>
            <p className="text-sm text-slate-600">
              {applicant.fullName || "Applicant"} ·{" "}
              {application.visaTypeName ||
                application.destination ||
                "Visa type unavailable"}
            </p>
          </div>
        </div>
        <div className="flex flex-wrap gap-2">
          <Link
            to="/applications"
            className="inline-flex items-center gap-2 rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm font-semibold text-slate-700 hover:bg-slate-50"
          >
            <ArrowLeft className="h-4 w-4" aria-hidden="true" /> Back to
            applications
          </Link>
          <button
            type="button"
            onClick={pdfAction}
            className="inline-flex items-center gap-2 rounded-lg bg-blue-700 px-3 py-2 text-sm font-semibold text-white hover:bg-blue-800"
          >
            <Download className="h-4 w-4" aria-hidden="true" />{" "}
            {finalized ? "Download report PDF" : "Print / Save PDF"}
          </button>
          <button
            type="button"
            onClick={() => window.print()}
            className="inline-flex items-center gap-2 rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm font-semibold text-slate-700 hover:bg-slate-50"
          >
            <Printer className="h-4 w-4" aria-hidden="true" /> Print
          </button>
        </div>
      </header>

      {error && (
        <p
          role="alert"
          className="rounded-xl bg-red-50 p-3 text-sm text-red-700"
        >
          {error}
        </p>
      )}
      {notice && (
        <p
          role="status"
          className="rounded-xl bg-emerald-50 p-3 text-sm text-emerald-800"
        >
          {notice}
        </p>
      )}

      <EvidenceAssessmentPanel results={results} applicationId={applicationId} canManage={canManage} onChange={loadResults} />

      <section className="grid gap-3 sm:grid-cols-2 xl:grid-cols-5">
        {[
          {
            label: "Total documents",
            value: summary.totalDocuments ?? documents.length,
            detail: `${summary.totalDocuments ?? documents.length} documents uploaded`,
            Icon: FolderOpen,
            color: "bg-blue-50 text-blue-700",
          },
          {
            label: "Verified documents",
            value: summary.verifiedDocuments ?? 0,
            detail: `${summary.totalDocuments ? Math.round(((summary.verifiedDocuments || 0) / summary.totalDocuments) * 100) : 0}% complete`,
            Icon: CheckCircle2,
            color: "bg-emerald-50 text-emerald-600",
          },
          {
            label: "Pending review",
            value: pendingDocuments,
            detail: `${pendingDocuments ? "Needs attention" : "0%"}`,
            Icon: Hourglass,
            color: "bg-amber-50 text-amber-600",
          },
          {
            label: "Rejected documents",
            value: summary.rejectedDocuments ?? 0,
            detail: "0%",
            Icon: CircleX,
            color: "bg-red-50 text-red-600",
          },
          {
            label: "Submission completeness",
            value: `${summary.submissionCompleteness ?? 0}%`,
            detail: `${summary.submittedRequiredDocuments ?? 0}/${summary.totalRequiredDocuments ?? 0} required submitted`,
            Icon: Percent,
            color: "bg-violet-50 text-violet-600",
          },
        ].map(({ label, value, detail, Icon, color }) => (
          <article
            key={label}
            className="verification-metric rounded-2xl border border-slate-200 bg-white p-4 shadow-sm"
          >
            <div className="flex items-start justify-between gap-3">
              <div>
                <p className="text-xs font-semibold text-slate-500">{label}</p>
                <p className="mt-2 text-2xl font-extrabold text-slate-900">{value}</p>
                <p className="mt-1 text-[11px] font-medium text-slate-500">{detail}</p>
              </div>
            </div>
            <div className="mt-3 h-1.5 overflow-hidden rounded-full bg-slate-100">
              <div
                className={`h-full rounded-full ${label === "Rejected documents" ? "bg-red-500" : label === "Pending review" ? "bg-amber-500" : "bg-emerald-500"}`}
                style={{
                  width: `${label === "Submission completeness" ? summary.submissionCompleteness || 0 : label === "Verified documents" ? (summary.totalDocuments ? ((summary.verifiedDocuments || 0) / summary.totalDocuments) * 100 : 0) : label === "Total documents" ? 100 : label === "Rejected documents" ? (summary.totalDocuments ? ((summary.rejectedDocuments || 0) / summary.totalDocuments) * 100 : 0) : (pendingDocuments ? 45 : 0)}%`,
                }}
              />
            </div>
          </article>
        ))}
      </section>

      <section className="grid gap-4 xl:grid-cols-[1.05fr_1fr]">
        <article className="verification-panel overflow-hidden rounded-2xl border border-slate-200 bg-white shadow-sm">
          <div className="flex items-center justify-between border-b border-slate-100 px-5 py-4">
            <div>
              <h2 className="flex items-center gap-2 text-base font-bold text-slate-900">
                <FolderOpen className="h-5 w-5 text-blue-700" /> Document list
              </h2>
              <p className="mt-1 text-xs text-slate-500">Uploaded files and their current verification state</p>
            </div>
            <div className="flex items-center gap-1 rounded-lg bg-slate-100 p-1 text-xs font-semibold text-slate-600">
              <span className="rounded-md bg-white px-2 py-1 text-blue-700 shadow-sm"><FileText className="mr-1 inline h-3.5 w-3.5" />List</span>
              <span className="px-2 py-1"><Grid2X2 className="mr-1 inline h-3.5 w-3.5" />Grid</span>
            </div>
          </div>
          <div className="divide-y divide-slate-100">
            {documents.length ? documents.map((item) => {
              const meta = statusMeta(item.status);
              const StatusIcon = meta.icon;
              const selected = selectedDocument?.documentId === item.documentId;
              return (
                <button
                  type="button"
                  key={item.documentId}
                  onClick={() => setSelectedPreviewId(item.documentId)}
                  className={`verification-document-row flex w-full items-center gap-3 p-4 text-left transition hover:bg-blue-50/50 ${selected ? "bg-blue-50/50" : ""} ${meta.label === "Verified" ? "is-verified" : meta.label === "Rejected" ? "is-rejected" : "is-pending"}`}
                >
                  {previews[item.documentId] && item.mimeType?.startsWith("image/") ? (
                    <img src={previews[item.documentId]} alt="" className="h-14 w-16 rounded-lg border border-slate-200 object-cover" />
                  ) : (
                    <span className="flex h-14 w-16 shrink-0 items-center justify-center rounded-lg bg-blue-50 text-blue-700"><FileText className="h-7 w-7" /></span>
                  )}
                  <span className="min-w-0 flex-1">
                    <span className="block truncate text-sm font-bold text-slate-900">{documentName(item)}</span>
                    <span className="mt-1 block truncate text-xs text-slate-500">{item.fileName}</span>
                    <span className="mt-1 block text-[11px] text-slate-400">Uploaded {item.uploadDate ? new Date(item.uploadDate).toLocaleString() : "—"}</span>
                  </span>
                  <span className={`inline-flex shrink-0 items-center gap-1 rounded-full px-2.5 py-1 text-xs font-bold ${meta.badge}`}>
                    <StatusIcon className="h-3.5 w-3.5" /> {meta.label}
                  </span>
                  <Eye className="h-4 w-4 shrink-0 text-blue-600" />
                </button>
              );
            }) : (
              <p className="p-6 text-sm text-slate-500">No documents uploaded.</p>
            )}
          </div>
        </article>

        <article className="verification-panel overflow-hidden rounded-2xl border border-slate-200 bg-white shadow-sm">
          <div className="flex items-center justify-between border-b border-slate-100 px-5 py-4">
            <div>
              <h2 className="flex items-center gap-2 text-base font-bold text-slate-900">
                <CheckCircle2 className="h-5 w-5 text-blue-700" /> Verification checklist
              </h2>
              <p className="mt-1 text-xs text-slate-500">Configured checks for this visa type</p>
            </div>
            <span className={`rounded-full px-3 py-1 text-xs font-bold ${allChecksCompleted ? "bg-emerald-50 text-emerald-700" : "bg-amber-50 text-amber-700"}`}>
              {allChecksCompleted ? "All checks completed" : "Review required"}
            </span>
          </div>
          <div className="divide-y divide-slate-100">
            {requirementScores.length ? requirementScores.map((item) => {
              const meta = statusMeta(item.status);
              const StatusIcon = meta.icon;
              return (
                <div key={item.requirementId} className="verification-check-row flex items-center gap-3 px-5 py-3">
                  <span className={`flex h-8 w-8 items-center justify-center rounded-full ${meta.badge}`}><StatusIcon className="h-4 w-4" /></span>
                  <div className="min-w-0 flex-1">
                    <p className="truncate text-sm font-bold text-slate-900">{item.name}</p>
                    <p className="mt-0.5 text-xs text-slate-500">{item.required ? "Required" : "Optional"} · {item.earnedPoints ?? 0}/{item.configuredPoints ?? 0} points</p>
                  </div>
                  <span className={`rounded-full px-2.5 py-1 text-xs font-bold ${meta.badge}`}>{meta.label}</span>
                </div>
              );
            }) : (
              <p className="p-6 text-sm text-slate-500">No configured requirement results are available.</p>
            )}
          </div>
        </article>
      </section>

      <section className="hidden grid gap-4 xl:grid-cols-[1.65fr_1fr]">
        <article className="grid gap-4 rounded-2xl border border-slate-200 bg-white p-5 shadow-sm md:grid-cols-2">
          <div>
            <h2 className="mb-3 text-sm font-bold text-slate-900">
              Applicant information
            </h2>
            <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-2 text-sm">
              <dt className="text-slate-500">Name</dt>
              <dd className="font-medium text-slate-900">
                {applicant.fullName || "—"}
              </dd>
              <dt className="text-slate-500">Nationality</dt>
              <dd className="font-medium text-slate-900">
                {applicant.nationality || "—"}
              </dd>
              <dt className="text-slate-500">Passport</dt>
              <dd className="font-medium text-slate-900">
                {applicant.passportNumber || "—"}
              </dd>
              <dt className="text-slate-500">Application ID</dt>
              <dd className="break-all font-medium text-slate-900">
                {application.applicationNumber || application.id || "—"}
              </dd>
              <dt className="text-slate-500">Destination</dt>
              <dd className="font-medium text-slate-900">
                {application.destination || "—"}
              </dd>
              <dt className="text-slate-500">Visa type</dt>
              <dd className="font-medium text-slate-900">
                {application.visaTypeName || "—"}
              </dd>
            </dl>
          </div>
          <div className="border-t border-slate-100 pt-4 md:border-l md:border-t-0 md:pl-5 md:pt-0">
            <h2 className="mb-3 text-sm font-bold text-slate-900">
              Report details
            </h2>
            <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-2 text-sm">
              <dt className="flex items-center gap-1 text-slate-500">
                <CalendarDays className="h-4 w-4" />
                Date
              </dt>
              <dd className="font-medium text-slate-900">
                {reportDate ? new Date(reportDate).toLocaleString() : "—"}
              </dd>
              <dt className="text-slate-500">Report ID</dt>
              <dd className="break-all font-medium text-slate-900">
                {latestVerification?._id || "NOT AVAILABLE"}
              </dd>
              <dt className="text-slate-500">Reviewed by</dt>
              <dd className="font-medium text-slate-900">
                {report.verifiedBy || "Not finalized"}
              </dd>
              <dt className="text-slate-500">Receipt</dt>
              <dd className="font-medium text-slate-900">
                {receiptNumber || "Not issued"}
              </dd>
            </dl>
          </div>
        </article>
        <article
          className={`flex min-h-40 flex-col items-center justify-center rounded-2xl border p-5 text-center shadow-sm ${statusStyle}`}
        >
          {reportStatus === "VERIFIED" ? (
            <CircleCheck className="h-12 w-12" aria-hidden="true" />
          ) : reportStatus === "REJECTED" ? (
            <CircleX className="h-12 w-12" aria-hidden="true" />
          ) : (
            <Clock3 className="h-12 w-12" aria-hidden="true" />
          )}
          <h2 className="mt-2 text-lg font-bold">Verification status</h2>
          <p className="text-2xl font-extrabold">{reportStatus}</p>
          <p className="mt-1 text-xs">
            {finalized
              ? "Staff-finalized document review"
              : "Automated checks only · authenticity not confirmed"}
          </p>
        </article>
      </section>

      <section className="hidden grid gap-3 sm:grid-cols-2 xl:grid-cols-5">
        <Metric
          label="Total documents"
          value={summary.totalDocuments ?? documents.length}
        />
        <Metric
          label="Verified documents"
          value={summary.verifiedDocuments ?? 0}
        />
        <Metric label="Pending review" value={pendingDocuments} />
        <Metric
          label="Rejected documents"
          value={summary.rejectedDocuments ?? 0}
        />
        <Metric
          label="Submitted required documents"
          value={`${summary.submissionCompleteness ?? 0}%`}
        />
      </section>

      <section className="hidden rounded-2xl border border-blue-200 bg-blue-50 p-5 shadow-sm">
        <h2 className="font-bold text-blue-950">Document-based assessment score</h2>
        <p className="mt-2 text-3xl font-extrabold text-blue-900">
          {summary.documentAssessmentScore ?? summary.documentReadiness ?? 0}%
        </p>
        <p className="mt-1 text-sm text-blue-900">
          Earned {summary.earnedPoints ?? summary.verifiedPoints ?? 0} / {summary.maximumPoints ?? summary.totalAvailablePoints ?? 0} configured points
        </p>
        <p className="mt-1 text-xs text-blue-800">
          Required completed: {summary.verifiedRequiredDocuments ?? 0} / {summary.totalRequiredDocuments ?? 0} · Missing: {summary.missingRequiredDocuments?.length ?? 0} · Processing: {summary.processingDocuments ?? 0} · Rejected: {summary.rejectedDocuments ?? 0} · Needs review: {summary.needsReviewDocuments ?? 0}
        </p>
        <p className="mt-1 text-xs text-blue-800">
          Last calculated: {summary.lastCalculationAt ? new Date(summary.lastCalculationAt).toLocaleString() : "Not calculated"}
        </p>
      </section>

      <p className="hidden flex items-start gap-2 rounded-xl border border-blue-200 bg-blue-50 p-3 text-sm text-blue-950">
        <ShieldAlert className="mt-0.5 h-4 w-4 shrink-0" aria-hidden="true" />
        OCR and automated consistency checks do not prove document authenticity
        or determine visa approval. Only a recorded staff decision is shown as
        finalized.
      </p>

      <section className="grid gap-4 xl:grid-cols-[1fr_1.2fr]">
        <article className="verification-panel overflow-hidden rounded-2xl border border-slate-200 bg-white shadow-sm">
          <div className="flex items-center gap-2 border-b border-slate-100 bg-white px-4 py-3 text-sm font-bold text-slate-900">
            <Eye className="h-4 w-4 text-blue-700" />
            Document preview
          </div>
          {documents.length ? (
            <>
              <div className="flex gap-1 overflow-x-auto border-b border-slate-200 px-3 pt-2">
                {documents.map((item) => (
                  <button
                    key={item.documentId}
                    type="button"
                    onClick={() => setSelectedPreviewId(item.documentId)}
                    className={`shrink-0 border-b-2 px-3 py-2 text-xs font-semibold ${selectedDocument?.documentId === item.documentId ? "border-blue-700 text-blue-800" : "border-transparent text-slate-500 hover:text-slate-800"}`}
                  >
                    {item.requirementCode || item.fileName}
                  </button>
                ))}
              </div>
              <div className="flex min-h-[300px] items-center justify-center bg-slate-50 p-3">
                {selectedDocument && previews[selectedDocument.documentId] ? (
                  selectedDocument.mimeType === "application/pdf" ? (
                    <iframe
                      title={selectedDocument.fileName}
                      src={previews[selectedDocument.documentId]}
                      className="h-[340px] w-full rounded-lg bg-white"
                    />
                  ) : selectedDocument.mimeType?.startsWith("image/") ? (
                    <img
                      src={previews[selectedDocument.documentId]}
                      alt={selectedDocument.fileName}
                      className="max-h-[340px] max-w-full object-contain"
                    />
                  ) : (
                    <p className="text-sm text-slate-500">
                      Preview is not available for this file type.
                    </p>
                  )
                ) : (
                  <p className="text-sm text-slate-500">
                    {selectedDocument &&
                    previewErrors[selectedDocument.documentId]
                      ? previewErrors[selectedDocument.documentId]
                      : "Loading document preview…"}
                  </p>
                )}
              </div>
              {selectedDocument && (
                <div className="flex flex-wrap items-center justify-between gap-2 border-t border-slate-100 p-3 text-xs">
                  <span className="font-medium text-slate-700">
                    {selectedDocument.fileName} · {selectedDocument.ocrStatus} ·{" "}
                    {selectedDocument.pagesProcessed}/
                    {selectedDocument.totalPages} pages
                  </span>
                  {previews[selectedDocument.documentId] && (
                    <a
                      href={previews[selectedDocument.documentId]}
                      download={selectedDocument.fileName}
                      className="font-semibold text-blue-700 underline"
                    >
                      Download original
                    </a>
                  )}
                </div>
              )}
            </>
          ) : (
            <p className="p-8 text-center text-sm text-slate-500">
              No documents uploaded.
            </p>
          )}
        </article>

        <article className="verification-panel overflow-hidden rounded-2xl border border-slate-200 bg-white shadow-sm">
          <div className="flex items-center justify-between border-b border-slate-100 bg-white px-4 py-3 text-sm font-bold text-slate-900">
            <span className="flex items-center gap-2"><CheckCircle2 className="h-4 w-4 text-blue-700" />
            Automated verification results
            </span>
            <span className="text-xs font-semibold text-blue-700">View detailed report →</span>
          </div>
          <div className="grid divide-y divide-slate-200 md:grid-cols-3 md:divide-x md:divide-y-0">
            <div className="p-4">
              <h3 className="text-sm font-bold text-slate-900">OCR results</h3>
              <p className="mt-3 text-xs text-slate-600">
                {summary.ocrCompletedDocuments ?? 0} of{" "}
                {summary.totalDocuments ?? documents.length} documents processed
              </p>
              <p className="mt-1 text-xs text-slate-600">
                Pages: {processedPages} / {totalPages}
              </p>
              <p className="mt-1 text-xs text-slate-600">
                Processing:{" "}
                {summary.documentProcessingStatus || "NOT AVAILABLE"}
              </p>
            </div>
            <div className="p-4">
              <h3 className="text-sm font-bold text-slate-900">Field checks</h3>
              <p className="mt-3 text-xs text-slate-600">
                Passed: {passedChecks.length} / {allChecks.length} available
                checks
              </p>
              <p className="mt-1 text-xs text-slate-600">
                Automated status:{" "}
                {summary.automatedVerificationStatus || "INSUFFICIENT_DATA"}
              </p>
              <p className="mt-1 text-xs text-slate-500">
                Checks compare extracted fields and application data only.
              </p>
            </div>
            <div className="p-4">
              <h3 className="text-sm font-bold text-slate-900">Consistency</h3>
              {bankDocuments.length ? (
                bankDocuments.map((item) => (
                  <div
                    key={item.documentId}
                    className="mt-3 text-xs text-slate-600"
                  >
                    <p>{item.fileName}</p>
                    <p className="mt-1">
                      {item.abaStatementAnalysis ? "ABA review: " : "Legacy three-month diagnostic: "}
                      {item.abaStatementAnalysis?.reviewStatus || item.bankStatementAnalysis.financialRequirementStatus || "INSUFFICIENT_DATA"}
                    </p>
                    <p>
                      Reconciliation:{" "}
                      {item.abaStatementAnalysis?.reconciliationStatus || item.bankStatementAnalysis.reconciliation?.status || "NOT_AVAILABLE"}
                    </p>
                  </div>
                ))
              ) : (
                <p className="mt-3 text-xs text-slate-500">
                  No bank statement consistency result available.
                </p>
              )}
            </div>
          </div>
          {unresolvedChecks.length > 0 && (
            <div className="border-t border-amber-200 bg-amber-50 p-4">
              <h3 className="text-sm font-bold text-amber-900">
                Checks requiring review
              </h3>
              <ul className="mt-2 grid gap-2 text-xs text-amber-900 sm:grid-cols-2">
                {unresolvedChecks.slice(0, 6).map((check, index) => (
                  <li key={`${check.fileName}-${check.code}-${index}`}>
                    {check.fileName}: {check.label} · {check.status}
                  </li>
                ))}
              </ul>
            </div>
          )}
        </article>
      </section>

      <section className="grid gap-4 lg:grid-cols-[1fr_1.3fr_1fr]">
        <article className="rounded-2xl border border-slate-200 bg-white p-5 shadow-sm">
          <h2 className="flex items-center gap-2 font-bold text-slate-900">
            <ShieldAlert className="h-5 w-5 text-amber-600" />
            Model assessment
          </h2>
          {prediction.status === "COMPLETED" ? (
            <>
              <p className="mt-3 text-2xl font-extrabold text-slate-900">
                {prediction.riskLevel || "NOT AVAILABLE"}
              </p>
              <p className="mt-1 text-sm text-slate-600">
                Model estimate: {prediction.approvalProbability}% approval ·{" "}
                {prediction.rejectionProbability}% rejection
              </p>
              <p className="mt-2 text-xs text-slate-500">
                {prediction.disclaimer}
              </p>
            </>
          ) : (
            <p className="mt-3 text-sm text-slate-600">
              {prediction.message || "No validated model result is available."}
            </p>
          )}
          {canManage && !finalized && prediction.status !== "COMPLETED" && (
            <button
              type="button"
              onClick={runPrediction}
              disabled={busy}
              className="mt-4 rounded-lg bg-blue-700 px-3 py-2 text-sm font-semibold text-white hover:bg-blue-800 disabled:opacity-50"
            >
              {busy ? "Working…" : "Check model availability"}
            </button>
          )}
        </article>
        <article className="rounded-2xl border border-slate-200 bg-white p-5 shadow-sm">
          <h2 className="font-bold text-slate-900">Verification summary</h2>
          <div className="mt-4 grid gap-3 sm:grid-cols-2">
            <Metric
              label="Required documents verified"
              value={`${summary.verificationPercentage ?? 0}%`}
            />
            <Metric
              label="Document readiness"
              value={`${summary.documentReadiness ?? 0}%`}
            />
            <Metric
              label="Configured points"
              value={`${summary.earnedPoints ?? 0} / ${summary.maximumPoints ?? 0}`}
            />
            <Metric
              label="OCR page progress"
              value={`${summary.ocrProgress?.percentage ?? 0}%`}
            />
            <Metric
              label="Missing required documents"
              value={summary.missingRequiredDocuments?.length ?? 0}
            />
          </div>
          <p className="mt-3 text-xs text-slate-500">
            These are document workflow measures, not visa approval probability.
          </p>
        </article>
        {readiness && summary.scoringVersion !== "evidence-v3" && (
          <article className="rounded-2xl border border-indigo-200 bg-white p-5 shadow-sm sm:col-span-2">
            <div className="flex flex-wrap items-start justify-between gap-4">
              <div>
                <p className="text-sm font-semibold text-indigo-700">ពិន្ទុត្រៀមខ្លួន · Readiness Score</p>
                <p className="mt-1 text-xs text-slate-500">Weighted document and financial evidence assessment</p>
              </div>
              <div className="text-right">
                <p className="text-4xl font-extrabold text-slate-900">{readiness.score}%</p>
                <p className="text-xs font-semibold text-slate-500">{readiness.riskLevel} RISK</p>
              </div>
            </div>
            <div className="mt-3 h-3 overflow-hidden rounded-full bg-slate-100">
              <div className="h-full rounded-full bg-indigo-600" style={{ width: `${Math.max(0, Math.min(100, readiness.score || 0))}%` }} />
            </div>
            {readiness.manualReviewRequired && (
              <div className="mt-3 rounded-lg border border-amber-200 bg-amber-50 p-3 text-sm text-amber-900">
                <strong>Manual review required:</strong> Some passport or bank evidence is incomplete or has low OCR confidence. Do not rely on the automated score until checked.
                {readiness.reviewFlags?.length > 0 && <p className="mt-1">{readiness.reviewFlags.join(" · ")}</p>}
              </div>
            )}
            {readiness.gateApplied && (
              <div className="mt-3 rounded-lg border border-amber-200 bg-amber-50 p-3 text-sm text-amber-900">
                <strong>Gate Rule applied:</strong> Score capped at 40% because a required document is missing or passport validity is under six months.
                {readiness.missingRequiredDocuments?.length > 0 && <p className="mt-1">Missing: {readiness.missingRequiredDocuments.join(", ")}</p>}
              </div>
            )}
            <div className="mt-4 grid gap-2 sm:grid-cols-2 lg:grid-cols-3">
              {Object.entries(readiness.components || {}).map(([key, item]) => (
                <div key={key} className="rounded-lg bg-slate-50 p-3">
                  <p className="text-xs font-medium capitalize text-slate-500">{key.replaceAll("_", " ")}</p>
                  <p className="mt-1 text-lg font-bold text-slate-900">{item.points ?? 0} / {item.maximum}</p>
                  {key === "bank_statement" && item.features?.length > 0 && (
                    <>
                      <ul className="mt-2 space-y-1 text-xs text-slate-600">
                        {item.features.map((feature) => <li key={feature.name}>{feature.name.replaceAll("_", " ")}: {feature.points == null ? "Needs evidence" : `${feature.points}/${feature.maximum}`}</li>)}
                      </ul>
                      <p className="mt-2 text-xs text-slate-500">OCR confidence: {item.ocrConfidence == null ? "Unavailable" : `${Math.round(item.ocrConfidence * 100)}%`} · Balance reconciliation: {item.balanceReconciliationStatus || "NOT_AVAILABLE"}</p>
                      {Object.keys(item.fieldConfidence || {}).length > 0 && (
                        <ul className="mt-1 space-y-1 text-xs text-slate-500">
                          {Object.entries(item.fieldConfidence).map(([field, evidence]) => <li key={field}>{field}: {evidence.confidence == null ? "confidence unavailable" : `${Math.round(evidence.confidence * 100)}%`}</li>)}
                        </ul>
                      )}
                    </>
                  )}
                </div>
              ))}
            </div>
            {readiness.recommendations?.length > 0 && (
              <div className="mt-4">
                <h3 className="text-sm font-semibold text-slate-800">Recommended actions</h3>
                <ul className="mt-2 list-disc space-y-1 pl-5 text-sm text-slate-600">
                  {readiness.recommendations.map((tip) => <li key={tip}>{tip}</li>)}
                </ul>
              </div>
            )}
            <p className="mt-4 text-xs text-slate-500">{readiness.disclaimer}</p>
          </article>
        )}
        <article
          className={`rounded-2xl border p-5 shadow-sm ${finalized ? statusStyle : "border-slate-200 bg-white text-slate-800"}`}
        >
          <h2 className="font-bold">Final staff decision</h2>
          <p className="mt-3 text-xl font-extrabold">
            {finalized
              ? report.finalStatus || "RECORDED"
              : "PENDING STAFF REVIEW"}
          </p>
          {finalized ? (
            <>
              <p className="mt-1 text-sm">
                Reviewed by {report.verifiedBy || "—"}
              </p>
              {report.administratorComments && (
                <p className="mt-2 text-sm">{report.administratorComments}</p>
              )}
              <p className="mt-2 text-xs">Receipt: {receiptNumber}</p>
              <button
                type="button"
                onClick={downloadReceipt}
                className="mt-4 inline-flex items-center gap-2 rounded-lg bg-emerald-700 px-3 py-2 text-sm font-semibold text-white hover:bg-emerald-800"
              >
                <Download className="h-4 w-4" />
                Download receipt PDF
              </button>
            </>
          ) : (
            <p className="mt-2 text-sm">
              Automated checks do not finalize or approve a visa application.
            </p>
          )}
          {needsRefinalization && (
            <p className="mt-3 text-sm text-amber-900">
              The evidence or policy changed after the previous receipt. Refresh verification and record a new assessment with a revision reason.
            </p>
          )}
          {results.historicalReceipts?.length > 0 && (
            <details className="mt-4 text-sm">
              <summary className="cursor-pointer font-semibold">Previous assessment receipts</summary>
              <ul className="mt-2 space-y-2">
                {results.historicalReceipts.map((receipt) => (
                  <li key={receipt.receiptNumber}>
                    <div className="flex flex-wrap items-start justify-between gap-2">
                      <div className="min-w-0">
                        <p className="break-words font-medium">{receipt.receiptNumber} · {receipt.finalStatus || "Recorded"}</p>
                        <p className="mt-1 text-xs text-slate-600">{receipt.reportType || "Historical assessment"} · {receiptTime(receipt.issuedAt)}</p>
                        <p className="mt-1 text-xs text-slate-600">Historical snapshot only; it does not confirm the current assessment.</p>
                      </div>
                      <button type="button" onClick={() => downloadReceiptNumber(receipt.receiptNumber)} className="inline-flex min-h-10 items-center gap-2 rounded-lg border border-slate-300 px-3 py-2 text-xs font-semibold text-slate-800 hover:bg-slate-50" aria-label={`Download historical receipt ${receipt.receiptNumber} PDF`}><Download className="h-4 w-4" />Download historical PDF</button>
                    </div>
                  </li>
                ))}
              </ul>
            </details>
          )}
          {qrCode && (
            <img
              src={qrCode}
              alt={`QR code for receipt ${receiptNumber}`}
              className="mt-3 h-20 w-20 rounded bg-white p-1"
            />
          )}
        </article>
      </section>

      <section className="overflow-hidden rounded-2xl border border-slate-200 bg-white shadow-sm">
        <div className="flex items-center gap-2 bg-slate-900 px-4 py-3 text-sm font-bold text-white">
          <FileText className="h-4 w-4" />
          Document verification details
        </div>
        {documents.length ? (
          <div className="divide-y divide-slate-100">
            {documents.map((item) => (
              <details key={item.documentId} className="group p-4">
                <summary className="flex cursor-pointer flex-wrap items-center justify-between gap-2 text-sm">
                  <span className="font-semibold text-slate-900">
                    {item.fileName} · {item.requirementCode}
                  </span>
                  <span className="text-xs text-slate-600">
                    {item.status} · OCR {item.ocrStatus} · {item.pagesProcessed}
                    /{item.totalPages} pages
                  </span>
                </summary>
                <div className="mt-4 space-y-4">
                  <p className="text-sm font-semibold text-slate-800">
                    Status: {String(item.status || "PROCESSING").toUpperCase()}
                    {" · "}Score:{" "}
                    {bankRequirementScores[item.documentId]?.score?.available
                      ? `${bankRequirementScores[item.documentId].score.points} / ${bankRequirementScores[item.documentId].score.maximum} points`
                      : item.bankStatementAnalysis?.bankStatementScore?.available
                        ? `${item.bankStatementAnalysis.bankStatementScore.points} / ${item.bankStatementAnalysis.bankStatementScore.maximum} points`
                        : `${item.awardedPoints ?? 0} / ${item.assignedPoints ?? 0} points`}
                  </p>
                  <p className="text-xs text-slate-600">
                    Document reference: {item.documentReference}
                    {summary.scoringVersion === "evidence-v3"
                      ? " · Confirmed points are recorded in the requirement ledger above."
                      : ` · Points: ${item.awardedPoints}/${item.assignedPoints}`}
                  </p>
                  {item.automatedVerification?.crossDocumentChecks?.filter(
                    (check) => check.status !== "PASSED",
                  ).map((check) => (
                    <p
                      key={check.code}
                      className="rounded-lg border border-amber-200 bg-amber-50 p-3 text-xs text-amber-900"
                    >
                      {check.label}: {check.reason}
                    </p>
                  ))}
                  <div className="grid gap-2 rounded-lg border border-blue-100 bg-blue-50/60 p-3 text-xs sm:grid-cols-3">
                    <div>
                      <p className="font-semibold text-slate-700">OCR status</p>
                      <p className="mt-1 text-slate-600">{item.ocrStatus || "NOT_AVAILABLE"}</p>
                    </div>
                    <div>
                      <p className="font-semibold text-slate-700">OCR confidence</p>
                      <p className="mt-1 text-slate-600">
                        {item.ocrConfidence == null ? "Unavailable" : `${Math.round(Number(item.ocrConfidence) * 100)}%`}
                      </p>
                    </div>
                    <div>
                      <p className="font-semibold text-slate-700">Missing fields</p>
                      <p className="mt-1 text-slate-600">{(item.missingFields || []).length ? item.missingFields.join(", ") : "None"}</p>
                    </div>
                  </div>
                  {item.automatedVerification && !item.abaStatementAnalysis && (
                    <AutomatedVerificationReport
                      verification={item.automatedVerification}
                      isPhoto={isPhotoDocument(item)}
                    />
                  )}
                  {item.abaStatementAnalysis && canManage && (
                    <AbaBankPanel documentId={item.documentId} applicationId={applicationId} canManage={canManage} onChange={loadResults} requirementScore={(summary.requirementScores || []).find(r => r.documentIds?.includes(item.documentId))} />
                  )}
                  {item.bankStatementAnalysis && !item.abaStatementAnalysis && (
                      <BankStatementAnalysis
                        analysis={item.bankStatementAnalysis}
                        requirementScore={{
                          ...(bankRequirementScores[item.documentId] || {}),
                          maximumConfiguredPoints:
                            bankRequirementScores[item.documentId]?.bankDiagnosticScore
                              ? (summary.requirementScores || []).find(
                                  (requirement) =>
                                    String(requirement.requirementId || requirement._id) === String(item.requirementId),
                                )?.configuredPoints || item.assignedPoints || 0
                              : item.assignedPoints || 0,
                        }}
                      />
                  )}
                  {(item.verificationComments || item.rejectionReason) && (
                    <p className="text-sm text-slate-600">
                      {item.verificationComments || item.rejectionReason}
                    </p>
                  )}
                  {canManage &&
                    !finalized &&
                    !item.abaStatementAnalysis &&
                    (isPhotoDocument(item) ||
                      item.ocrStatus === "OCR_COMPLETED") && (
                    <div className="space-y-2 rounded-lg border border-amber-200 bg-amber-50 p-3">
                      <div className="flex flex-wrap items-center justify-between gap-2">
                        <p className="text-sm font-semibold text-amber-900">
                          Staff review / override
                        </p>
                        <button
                          type="button"
                          disabled={busy}
                          onClick={() =>
                            rerunDocumentVerification(item.documentId)
                          }
                          className="rounded-lg border border-blue-300 bg-white px-3 py-1.5 text-xs font-semibold text-blue-800 disabled:opacity-50"
                        >
                          Rerun automated checks
                        </button>
                      </div>
                      <div className="grid gap-2 sm:grid-cols-[180px_1fr_auto]">
                        <select
                          aria-label={`Review status for ${item.fileName}`}
                          value={
                            reviewForms[item.documentId]?.status ||
                            "NEEDS_REVIEW"
                          }
                          onChange={(event) =>
                            setReviewForms((current) => ({
                              ...current,
                              [item.documentId]: {
                                ...current[item.documentId],
                                status: event.target.value,
                              },
                            }))
                          }
                          className="rounded-lg border border-amber-300 bg-white px-3 py-2 text-sm"
                        >
                          <option value="NEEDS_REVIEW">Needs review</option>
                          <option value="VERIFIED">Verified</option>
                          <option value="REJECTED">Rejected</option>
                          <option value="INSUFFICIENT_DATA">
                            Insufficient data
                          </option>
                        </select>
                        <input
                          aria-label={`Review reason for ${item.fileName}`}
                          value={reviewForms[item.documentId]?.reason || ""}
                          onChange={(event) =>
                            setReviewForms((current) => ({
                              ...current,
                              [item.documentId]: {
                                ...current[item.documentId],
                                reason: event.target.value,
                              },
                            }))
                          }
                          placeholder="Required reason for this review or override"
                          className="min-w-0 rounded-lg border border-amber-300 bg-white px-3 py-2 text-sm"
                        />
                        <button
                          type="button"
                          disabled={busy}
                          onClick={() => reviewDocument(item)}
                          className="rounded-lg bg-amber-800 px-3 py-2 text-sm font-semibold text-white disabled:opacity-50"
                        >
                          Save review
                        </button>
                      </div>
                      {item.manualReviewHistory?.length > 0 && (
                        <ul className="space-y-1 text-xs text-amber-950">
                          {item.manualReviewHistory.map((review, index) => (
                            <li key={`${review.reviewedAt}-${index}`}>
                              {review.status} · {review.reason} ·{" "}
                              {review.reviewedAt
                                ? new Date(review.reviewedAt).toLocaleString()
                                : "time unavailable"}
                            </li>
                          ))}
                        </ul>
                      )}
                    </div>
                  )}
                  {Object.keys(item.extractedFields || {}).length > 0 && (
                    <details className="rounded-lg bg-slate-50 p-3">
                      <summary className="cursor-pointer text-sm font-semibold text-slate-800">
                        Extracted fields
                      </summary>
                      <dl className="mt-3 grid gap-2 text-xs sm:grid-cols-2">
                        {Object.entries(item.extractedFields).map(
                          ([key, value]) => (
                            <div key={key} className="min-w-0">
                              <dt className="font-medium text-slate-500">
                                {key}
                              </dt>
                              <dd className="break-words text-slate-800">
                                {Array.isArray(value)
                                  ? `${value.length} entries`
                                  : value && typeof value === "object"
                                    ? JSON.stringify(value)
                                    : String(value ?? "—")}
                              </dd>
                            </div>
                          ),
                        )}
                      </dl>
                    </details>
                  )}
                  {Object.keys(item.fieldEvidence || {}).length > 0 && (
                    <details className="rounded-lg border border-blue-100 bg-blue-50/50 p-3">
                      <summary className="cursor-pointer text-sm font-semibold text-slate-800">
                        Document extraction evidence
                      </summary>
                      <dl className="mt-3 grid gap-2 text-xs sm:grid-cols-2">
                        {Object.entries(item.fieldEvidence).map(([key, evidence]) => (
                          <div key={key} className="min-w-0">
                            <dt className="font-medium text-slate-500">{key}</dt>
                            <dd className="break-words text-slate-800">
                              {String(evidence?.value ?? "—")} · {evidence?.source || "OCR"} ·{" "}
                              {evidence?.page == null ? "page unavailable" : `page ${evidence.page}`} ·{" "}
                              {evidence?.confidence == null ? "confidence unavailable" : `${Math.round(Number(evidence.confidence) * 100)}%`}
                              {evidence?.evidenceText && (
                                <span className="mt-1 block text-slate-500">
                                  Source: {evidence.evidenceText}
                                </span>
                              )}
                            </dd>
                          </div>
                        ))}
                      </dl>
                    </details>
                  )}
                </div>
              </details>
            ))}
          </div>
        ) : (
          <p className="p-5 text-sm text-slate-500">No documents uploaded.</p>
        )}
      </section>

      {canManage && !finalized && (
        <form
          onSubmit={finalize}
          className="space-y-4 rounded-2xl border border-slate-200 bg-white p-5 shadow-sm"
        >
          <h2 className="text-xl font-bold text-slate-900">
            Complete staff verification
          </h2>
          <p className="text-sm text-slate-600">
            Evidence-only reports require current document and staff verification. Prediction reports additionally require a current validated model estimate.
          </p>
          <div className="grid gap-3 sm:grid-cols-2">
            <label className="text-sm font-medium text-slate-700">
              Report type
              <select value={reportType} onChange={e => setReportType(e.target.value)} className="mt-1 block w-full rounded-lg border border-slate-300 px-3 py-2">
                <option value="EVIDENCE_ASSESSMENT">Evidence assessment only</option>
                <option value="PREDICTION">Evidence and approval prediction</option>
              </select>
            </label>
            <label className="text-sm font-medium text-slate-700">
              Final verification status
              <select
                value={finalStatus}
                onChange={(event) => setFinalStatus(event.target.value)}
                className="mt-1 block w-full rounded-lg border border-slate-300 px-3 py-2"
              >
                <option value="VERIFIED">Verified</option>
                <option value="REJECTED">Rejected</option>
              </select>
            </label>
            <label className="text-sm font-medium text-slate-700">
              {needsRefinalization ? "Revision reason (at least 8 characters)" : "Administrator comments"}
              <input
                required={needsRefinalization || finalStatus === "REJECTED"}
                minLength={needsRefinalization ? 8 : undefined}
                maxLength={2000}
                value={comments}
                onChange={(event) => setComments(event.target.value)}
                className="mt-1 block w-full rounded-lg border border-slate-300 px-3 py-2"
              />
            </label>
          </div>
          <button
            type="submit"
            disabled={busy}
            className="rounded-xl bg-blue-700 px-5 py-2.5 text-sm font-semibold text-white hover:bg-blue-800 disabled:opacity-50"
          >
            {busy ? "Working…" : needsRefinalization ? "Finalize revised assessment" : "Finalize and generate receipt"}
          </button>
        </form>
      )}
    </main>
  );
}
