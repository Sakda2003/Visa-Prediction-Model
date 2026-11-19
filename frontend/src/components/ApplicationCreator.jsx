import { useEffect, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import { AlertCircle, CheckCircle2, CloudUpload, FileText } from "lucide-react";
import { waitForDocumentOcr } from "../pages/pageUtils";

const EMPTY_APPLICANT = {
  fullName: "",
  email: "",
  phone: "",
  nationality: "",
  passportNumber: "",
  dateOfBirth: "",
  address: "",
  employmentStatus: "",
  monthlyIncomeUsd: "",
  monthlyExpensesUsd: "",
  travelHistoryCount: "",
  previousVisaDenials: "",
  hasOverstayHistory: "",
  accountHistoryMonths: "",
  proofOfTiesToCambodia: "",
};

// These fields identify the applicant and are required before an application
// can advance. The remaining fields are useful, but deliberately optional.
const REQUIRED_APPLICANT_FIELDS = [
  "fullName",
  "nationality",
  "passportNumber",
  "dateOfBirth",
];
const APPLICANT_FIELDS = Object.keys(EMPTY_APPLICANT);
const PREDICTION_FIELDS = [
  ["employmentStatus", "employmentStatusTitle", "text"],
  ["monthlyIncomeUsd", "monthlyIncomeTitle", "number"],
  ["monthlyExpensesUsd", "monthlyExpensesTitle", "number"],
  ["travelHistoryCount", "travelHistoryTitle", "number"],
  ["previousVisaDenials", "previousVisaDenialsTitle", "number"],
  ["accountHistoryMonths", "accountHistoryTitle", "number"],
  ["hasOverstayHistory", "overstayHistoryTitle", "boolean"],
  ["proofOfTiesToCambodia", "tiesToCambodiaTitle", "boolean"],
];

const MAX_FILE_SIZE = 10 * 1024 * 1024;
const MAX_DATE = new Date().toISOString().slice(0, 10);
const BOOLEAN_APPLICANT_FIELDS = [
  "hasOverstayHistory",
  "proofOfTiesToCambodia",
];
const normalizeOptionalBoolean = (value) => {
  if (value === true || value === false || value === "") return value;
  if (typeof value === "string") {
    const normalized = value.trim().toLowerCase();
    if (normalized === "true") return true;
    if (normalized === "false") return false;
  }
  return "";
};
const humanSize = (bytes) => `${(bytes / 1024 / 1024).toFixed(1)} MB`;
const formatPoints = (points) => Number(points.toFixed(1));

function VerificationResultPanel({
  verifying,
  verificationResult,
  verificationExplanation,
}) {
  const { t } = useTranslation();
  const readinessScore =
    verificationResult?.completenessScore ??
    verificationResult?.documentAssessmentScore ??
    verificationResult?.documentReadiness ??
    0;
  const earnedPoints = verificationResult?.earnedPoints;
  const maximumPoints =
    verificationResult?.maximumPoints ?? verificationResult?.totalPoints ?? 0;
  const pointsSummary = verificationResult
    ? t("pointsToPercentage", {
        earned: formatPoints(Number(earnedPoints) || 0),
        total: formatPoints(Number(maximumPoints) || 0),
        percentage: readinessScore || 0,
      })
    : "";
  if (!verifying && !verificationResult) return null;

  return (
    <section
      aria-live="polite"
      className="space-y-4 rounded-xl border border-slate-200 bg-slate-50 p-4 sm:p-5"
    >
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h4 className="font-semibold text-slate-900">
            {t("verificationOutcome")}
          </h4>
          <p className="mt-1 text-sm text-slate-600">
            {t("noApprovalPrediction")}
          </p>
        </div>
        {verificationResult && (
          <span
            className={`rounded-full px-3 py-1 text-sm font-semibold ${
              verificationResult.status === "VERIFIED"
                ? "bg-emerald-100 text-emerald-800"
                : verificationResult.status === "REJECTED"
                  ? "bg-red-100 text-red-800"
                  : "bg-amber-100 text-amber-800"
            }`}
          >
            {verificationResult.status}
          </span>
        )}
      </div>
      {verifying ? (
        <p role="status" className="text-sm font-medium text-blue-700">
          {t("verificationRunning")}
        </p>
      ) : verificationResult ? (
        <div className="grid gap-4 md:grid-cols-[minmax(0,220px)_1fr]">
          <div className="rounded-xl border border-slate-200 bg-white p-4">
            <p className="text-sm font-medium text-slate-600">
              {t("readinessScore")}
            </p>
            <p className="mt-1 text-3xl font-bold text-slate-900">
              {readinessScore}%
            </p>
            <p className="mt-1 text-sm font-medium text-slate-600">
              {pointsSummary}
            </p>
            <div
              className="mt-3 h-2 overflow-hidden rounded-full bg-slate-100"
              role="progressbar"
              aria-valuenow={readinessScore}
              aria-valuemin={0}
              aria-valuemax={100}
              aria-label={t("readinessScore")}
            >
              <div
                className={`h-full rounded-full transition-all ${
                  verificationResult.status === "VERIFIED"
                    ? "bg-emerald-500"
                    : verificationResult.status === "REJECTED"
                      ? "bg-red-500"
                      : "bg-amber-500"
                }`}
                style={{
                  width: `${Math.min(
                    100,
                    Math.max(0, readinessScore || 0),
                  )}%`,
                }}
              />
            </div>
          </div>
          <div className="space-y-2">
            <p className="text-sm font-semibold text-slate-700">
              {verificationResult.results?.some(
                (item) => !["PASS", "NOT_APPLICABLE"].includes(item.status),
              )
                ? t("checksToReview")
                : t("allChecksPassed")}
            </p>
            {verificationResult.results
              ?.filter(
                (item) => !["PASS", "NOT_APPLICABLE"].includes(item.status),
              )
              .map((item) => (
                <div
                  key={item.requirementId}
                  className={`rounded-xl border p-3 ${
                    ["FAIL", "MISSING"].includes(item.status)
                      ? "border-red-100 bg-red-50"
                      : "border-amber-100 bg-amber-50"
                  }`}
                >
                  <div className="flex flex-wrap items-center justify-between gap-2">
                    <span className="font-medium text-slate-900">
                      {item.requirementName}
                    </span>
                    <span className="text-xs font-semibold text-slate-600">
                      {t(
                        {
                          NOT_APPLICABLE: "statusNotApplicable",
                        }[item.status] || item.status,
                      )}
                    </span>
                  </div>
                  <p className="mt-1 text-xs font-medium text-slate-500">
                    {formatPoints(Number(item.earnedPoints) || 0)} /{" "}
                    {formatPoints(Number(item.configuredPoints) || 0)} points
                    {" · "}
                    {Number(item.scoreWeightPercent || 0).toFixed(2)}% of
                    total score
                  </p>
                  <p className="mt-1 text-sm text-slate-700">
                    {verificationExplanation(item)}
                  </p>
                </div>
              ))}
            {verificationResult.results
              ?.flatMap((item) => item.bankStatements || [])
              .map(({ documentId, documentReference, analysis }) => (
                <div
                  key={documentId}
                  className="rounded-xl border border-blue-100 bg-blue-50 p-3"
                >
                  <div className="flex flex-wrap items-center justify-between gap-2">
                    <span className="font-medium text-slate-900">
                      Bank statement · {documentReference}
                    </span>
                    <span className="text-sm font-semibold text-blue-900">
                      {analysis.bankStatementScore?.available
                        ? t("bankScore", {
                            points: analysis.bankStatementScore.points,
                            maximum: analysis.bankStatementScore.maximum,
                          })
                        : t("bankScoreUnavailable", {
                            maximum:
                              analysis.bankStatementScore?.maximum ?? 50,
                          })}
                    </span>
                  </div>
                  <p className="mt-1 text-xs text-slate-700">
                    Three-month balance check:{" "}
                    {analysis.financialRequirementStatus || "INSUFFICIENT_DATA"} ·{" "}
                    {analysis.status || "NEEDS_MANUAL_REVIEW"}
                  </p>
                  {analysis.monthlyBalanceChecks?.length ? (
                    <ul className="mt-2 grid gap-1 text-xs text-slate-600 sm:grid-cols-3">
                      {analysis.monthlyBalanceChecks.map((month) => (
                        <li key={month.month}>
                          {month.month}: {month.currency || ""}{" "}
                          {month.closingBalance ?? "—"} · {month.status}
                        </li>
                      ))}
                    </ul>
                  ) : (
                    <p className="mt-2 text-xs text-amber-800">
                      Three complete months of USD closing-balance evidence
                      were not available.
                    </p>
                  )}
                  {analysis.riskFlags?.length > 0 && (
                    <ul className="mt-2 list-disc space-y-1 pl-5 text-xs text-amber-900">
                      {analysis.riskFlags.slice(0, 5).map((flag, index) => (
                        <li key={`${flag.code}-${index}`}>{flag.reason}</li>
                      ))}
                    </ul>
                  )}
                  <p className="mt-2 text-[11px] text-slate-500">
                    OCR and consistency analysis only; authenticity is not confirmed.
                  </p>
                </div>
              ))}
          </div>
        </div>
      ) : null}
    </section>
  );
}

export default function ApplicationCreator({
  customers,
  visaTypes,
  api,
  onApplicationCreated,
  onApplicationReady,
  onDocumentUploaded,
  verificationResult,
  verifying,
  verificationExplanation,
}) {
  const { t, i18n } = useTranslation();
  const [photoFile, setPhotoFile] = useState(null);
  const [photoPreview, setPhotoPreview] = useState("");
  const [customerId, setCustomerId] = useState("");
  useEffect(() => {
    let active = true; let url = "";
    setPhotoPreview("");
    if (photoFile) { url = URL.createObjectURL(photoFile); setPhotoPreview(url); }
    else if (customerId) api.get(`/customers/${customerId}/photo`, {responseType:"blob"}).then(r => { url = URL.createObjectURL(r.data); if(active)setPhotoPreview(url); else URL.revokeObjectURL(url); }).catch(() => {});
    return () => { active = false; if(url)URL.revokeObjectURL(url); };
  }, [photoFile, customerId]);
  const [applicant, setApplicant] = useState(EMPTY_APPLICANT);
  const [visaTypeId, setVisaTypeId] = useState("");
  const [travelStartDate, setTravelStartDate] = useState("");
  const [returnDate, setReturnDate] = useState("");
  const [requirements, setRequirements] = useState([]);
  const [requirementsLoading, setRequirementsLoading] = useState(false);
  const [requirementsError, setRequirementsError] = useState("");
  const [files, setFiles] = useState({});
  const [documentAvailable, setDocumentAvailable] = useState({});
  const [application, setApplication] = useState(null);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [activeStep, setActiveStep] = useState(0);

  const selectedVisaType = visaTypes.find((item) => item._id === visaTypeId);
  const requiredRequirements = useMemo(
    () => requirements.filter((item) => item.required),
    [requirements],
  );
  const hasRequirementFile = (requirement) =>
    files[requirement._id]?.status === "Uploaded" ||
    Boolean(files[requirement._id]?.file);
  const missingRequirements = requiredRequirements.filter(
    (item) => !hasRequirementFile(item),
  );
  const attachedCount = requirements.filter(
    (item) => Boolean(files[item._id]?.file),
  ).length;
  const readyCount = requirements.filter(hasRequirementFile).length;
  const completion = requirements.length
    ? Math.round((readyCount / requirements.length) * 100)
    : 0;
  const personalCompletedCount = APPLICANT_FIELDS.filter((field) =>
    String(applicant[field] || "").trim(),
  ).length;
  const personalCompletion = Math.round(
    (personalCompletedCount / APPLICANT_FIELDS.length) * 100,
  );
  const missingApplicantFields = REQUIRED_APPLICANT_FIELDS.filter(
    (field) => !String(applicant[field] || "").trim(),
  );
  const visaFields = [visaTypeId, travelStartDate, returnDate];
  const visaCompletion = Math.round(
    (visaFields.filter(Boolean).length / visaFields.length) * 100,
  );
  const documentCountLabel = t("documentsReadyCount", {
    count: readyCount,
    total: requirements.length,
  });
  const verifiedPointsPercentage = verificationResult?.completenessScore;
  const optionalUploaded = requirements.filter(
    (item) =>
      !item.required &&
      hasRequirementFile(item),
  ).length;
  const uploadedCount = requirements.filter(
    (item) => files[item._id]?.status === "Uploaded",
  ).length;

  useEffect(() => {
    let active = true;
    if (!visaTypeId) return () => { active = false; };

    api.get(`/policy-profiles/effective/visa-type/${visaTypeId}`)
      .then(async response => {
        const pinned=response.data.profile?.requirements;
        if (pinned) { if(active)setRequirements(pinned.map(r=>({...r,_id:r.requirementId||r._id}))); }
        else { const legacy=await api.get("/requirements",{params:{visaTypeId}}); if(active)setRequirements(legacy.data.requirements||[]); }
      })
      .catch((requestError) => {
        if (active)
          setRequirementsError(
            requestError?.response?.data?.message ||
              t("unableLoadDocumentRequirements"),
          );
      })
      .finally(() => {
        if (active) setRequirementsLoading(false);
      });
    return () => {
      active = false;
    };
  }, [api, t, visaTypeId]);

  const selectCustomer = (id) => {
    setPhotoFile(null);
    setCustomerId(id);
    const customer = customers.find((item) => item._id === id);
    setApplicant(
      customer
        ? Object.fromEntries(
            Object.keys(EMPTY_APPLICANT).map((field) => {
              const value = customer[field] ?? "";
              return [
                field,
                BOOLEAN_APPLICANT_FIELDS.includes(field)
                  ? normalizeOptionalBoolean(value)
                  : value,
              ];
            }),
          )
        : EMPTY_APPLICANT,
    );
  };

  const selectVisaType = (id) => {
    setRequirements([]);
    setRequirementsError("");
    setFiles({});
    setDocumentAvailable({});
    setRequirementsLoading(Boolean(id));
    setVisaTypeId(id);
  };

  const selectFile = (requirement, file) => {
    const formats = requirement.acceptedFormats || [
      "pdf",
      "jpg",
      "jpeg",
      "png",
    ];
    const extension = file?.name.split(".").pop()?.toLowerCase();
    const maxFileSize = Math.min(
      Number(requirement.maxFileSize) || MAX_FILE_SIZE,
      MAX_FILE_SIZE,
    );
    if (
      !file ||
      (!formats.includes(extension) &&
        !(extension === "jpg" && formats.includes("jpeg")) &&
        !(extension === "jpeg" && formats.includes("jpg")))
    ) {
      setFiles((current) => ({
        ...current,
        [requirement._id]: {
          ...current[requirement._id],
          file: null,
          error: `${t("useAcceptedFile")} (${formats.join(", ").toUpperCase()}).`,
        },
      }));
      return;
    }
    if (file.size > maxFileSize) {
      setFiles((current) => ({
        ...current,
        [requirement._id]: {
          file: null,
          error: `${t("fileExceeds")} ${humanSize(maxFileSize)}.`,
        },
      }));
      return;
    }
    setFiles((current) => ({
      ...current,
      [requirement._id]: {
        file,
        status: "Ready to upload",
        error: "",
      },
    }));
  };

  const updateDocumentOcrState = (requirementId, document, progress) => {
    const updatedDocument = {
      ...document,
      workflowStatus: progress.workflowStatus,
      ocrStatus: progress.ocrStatus,
      ocrPagesProcessed: progress.pagesProcessed,
      ocrTotalPages: progress.totalPages,
      ocrProgressPercent: progress.progressPercent,
      ocrMessage: progress.message,
      ocrError: progress.error,
    };
    onDocumentUploaded?.(updatedDocument);
    setFiles((items) => ({
      ...items,
      [requirementId]: {
        ...items[requirementId],
        status: "Uploaded",
        file: null,
        documentId: document._id,
        fileName: document.fileName,
        workflowStatus: progress.workflowStatus,
        ocrStatus: progress.ocrStatus,
        pagesProcessed: progress.pagesProcessed,
        totalPages: progress.totalPages,
        progressPercent: progress.progressPercent,
        error:
          ["OCR_FAILED", "OCR_PARTIAL"].includes(progress.ocrStatus)
            ? progress.error ||
              progress.message ||
              t("documentReadingFailed")
            : "",
      },
    }));
  };

  const retryDocumentOcr = async (requirementId) => {
    const current = files[requirementId];
    if (
      !current?.documentId ||
      !["OCR_FAILED", "OCR_PARTIAL"].includes(current.ocrStatus) ||
      current.retryingOcr
    ) {
      return;
    }
    setFiles((items) => ({
      ...items,
      [requirementId]: {
        ...items[requirementId],
        error: "",
        retryingOcr: true,
      },
    }));
    try {
      const response = await api.post(
        `/documents/${current.documentId}/ocr/retry`,
        {},
      );
      const document = response.data.document;
      onDocumentUploaded?.(document);
      setFiles((items) => ({
        ...items,
        [requirementId]: {
          ...items[requirementId],
          status: "Uploaded",
          ocrStatus: "OCR_PENDING",
          workflowStatus: "OCR_PENDING",
          pagesProcessed: 0,
          totalPages: 0,
          progressPercent: 0,
        },
      }));
      await waitForDocumentOcr(
        api,
        current.documentId,
        (progress) =>
          updateDocumentOcrState(requirementId, document, progress),
      );
    } catch (requestError) {
      setFiles((items) => ({
        ...items,
        [requirementId]: {
          ...items[requirementId],
          error:
            requestError?.response?.data?.message ||
            requestError?.message ||
            t("documentReadingFailed"),
        },
      }));
    } finally {
      setFiles((items) => ({
        ...items,
        [requirementId]: {
          ...items[requirementId],
          retryingOcr: false,
        },
      }));
    }
  };

  const uploadPendingFiles = async (targetApplication) => {
    let failures = 0;
    for (const requirement of requirements) {
      const current = files[requirement._id];
      if (
        !documentAvailable[requirement._id] ||
        !current?.file ||
        current.status === "Uploaded"
      ) continue;
      setFiles((items) => ({
        ...items,
        [requirement._id]: { ...items[requirement._id], status: "Uploading" },
      }));
      const payload = new FormData();
      payload.append("file", current.file);
      payload.append("applicationId", targetApplication._id);
      payload.append("requirementId", requirement._id);
      let uploadedDocument = null;
      try {
        const response = await api.post("/documents/upload", payload);
        uploadedDocument = response.data.document;
        const initialProgress = {
          workflowStatus: uploadedDocument.workflowStatus,
          ocrStatus: uploadedDocument.ocrStatus,
          pagesProcessed: uploadedDocument.ocrPagesProcessed || 0,
          totalPages: uploadedDocument.ocrTotalPages || 0,
          progressPercent: uploadedDocument.ocrProgressPercent || 0,
          message: uploadedDocument.ocrMessage || t("readingDocument"),
        };
        updateDocumentOcrState(
          requirement._id,
          uploadedDocument,
          initialProgress,
        );
        const finalProgress = await waitForDocumentOcr(
          api,
          uploadedDocument._id,
          (progress) =>
            updateDocumentOcrState(
              requirement._id,
              uploadedDocument,
              progress,
            ),
        );
        setFiles((items) => ({
          ...items,
          [requirement._id]: {
            ...items[requirement._id],
            status: "Uploaded",
            fileName: uploadedDocument.fileName,
            documentId: uploadedDocument._id,
            ocrStatus: finalProgress.ocrStatus,
            workflowStatus: finalProgress.workflowStatus,
            pagesProcessed: finalProgress.pagesProcessed,
            totalPages: finalProgress.totalPages,
            progressPercent: finalProgress.progressPercent,
            error: "",
          },
        }));
      } catch (requestError) {
        failures += 1;
        const message =
          requestError?.response?.data?.message ||
          requestError?.message ||
          t("uploadFailed");
        setFiles((items) => ({
          ...items,
          [requirement._id]: {
            ...items[requirement._id],
            status: uploadedDocument ? "Uploaded" : "Upload failed",
            file: uploadedDocument ? null : items[requirement._id].file,
            documentId: uploadedDocument?._id,
            ocrStatus: uploadedDocument
              ? items[requirement._id].ocrStatus
              : "OCR_FAILED",
            error: message,
          },
        }));
      }
    }
    if (failures) {
      setError(t("someUploadsFailed"));
      return false;
    }
    setError("");
    setNotice(
      targetApplication
        ? t("applicationCreatedFilesUploaded")
        : t("documentsUploaded"),
    );
    return true;
  };

  const validateApplicant = () => {
    if (missingApplicantFields.length) {
      setError(
        "Complete the required applicant fields (full name, nationality, passport number, and date of birth) before continuing.",
      );
      return false;
    }
    if (
      applicant.email.trim() &&
      !/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(applicant.email.trim())
    ) {
      setError("Enter a valid email address before continuing.");
      return false;
    }
    if (applicant.dateOfBirth && applicant.dateOfBirth > MAX_DATE) {
      setError("Date of birth cannot be in the future.");
      return false;
    }
    return true;
  };

  const validateVisaDetails = () => {
    if (!visaTypeId) {
      setError(t("selectVisaCode"));
      return false;
    }
    if (!travelStartDate || !returnDate) {
      setError("Select both travel start and return dates before continuing.");
      return false;
    }
    if (travelStartDate && returnDate && returnDate <= travelStartDate) {
      setError(t("returnDateAfterDeparture"));
      return false;
    }
    return true;
  };

  const validateDocuments = () => {
    if (requirementsLoading || requirementsError) {
      setError(
        requirementsError || t("loadingRequirements"),
      );
      return false;
    }
    if (!requirements.length) {
      setError(t("adminMustConfigure"));
      return false;
    }
    // Incomplete evidence may be saved as a draft; final assessment enforces its gates.
    return true;
  };

  const goToStep = (nextStep) => {
    setError("");
    if (nextStep > 0 && !validateApplicant()) {
      setActiveStep(0);
      return;
    }
    if (nextStep > 1 && !validateVisaDetails()) {
      setActiveStep(1);
      return;
    }
    if (nextStep > 2 && !validateDocuments()) {
      setActiveStep(2);
      return;
    }
    setActiveStep(nextStep);
  };

  const submitApplication = async (event) => {
    event.preventDefault();
    setError("");
    setNotice("");
    if (!validateApplicant()) {
      setActiveStep(0);
      return;
    }
    if (!validateVisaDetails()) {
      setActiveStep(1);
      return;
    }
    if (!validateDocuments()) {
      setActiveStep(2);
      return;
    }

    setSubmitting(true);
    try {
      let targetApplication = application;
      let targetCustomer = customers.find((item) => item._id === customerId);
      if (!targetApplication) {
        const applicantPayload = { ...applicant };
        for (const field of BOOLEAN_APPLICANT_FIELDS) {
          applicantPayload[field] = normalizeOptionalBoolean(
            applicantPayload[field],
          );
        }
        const customerResponse = customerId
          ? await api.put(`/customers/${customerId}`, applicantPayload)
          : await api.post("/customers", {
              ...applicantPayload,
              allowIncomplete: true,
            });
        const savedCustomer = customerResponse.data.customer;
        targetCustomer = savedCustomer;
        if (!customerId) setCustomerId(savedCustomer._id);
        if (photoFile) {
          const photoPayload = new FormData(); photoPayload.append("file", photoFile);
          await api.post(`/customers/${savedCustomer._id}/photo`, photoPayload);
          setPhotoFile(null);
        }
        const applicationResponse = await api.post("/visa-types/applications", {
          customer: savedCustomer._id,
          visaType: visaTypeId,
          travelStartDate,
          returnDate,
        });
        targetApplication = applicationResponse.data.application;
        setApplication(targetApplication);
        await onApplicationCreated?.(targetApplication, savedCustomer);
      }
      const uploadsComplete = await uploadPendingFiles(targetApplication);
      if (uploadsComplete) {
        await onApplicationReady?.(targetApplication, targetCustomer);
        setActiveStep(3);
      }
    } catch (requestError) {
      setError(
        requestError?.response?.data?.message ||
          t("unableCreateVisaApplication"),
      );
    } finally {
      setSubmitting(false);
    }
  };

  const reset = () => {
    setCustomerId("");
    setPhotoFile(null);
    setApplicant(EMPTY_APPLICANT);
    setVisaTypeId("");
    setTravelStartDate("");
    setReturnDate("");
    setRequirements([]);
    setFiles({});
    setDocumentAvailable({});
    setApplication(null);
    setError("");
    setNotice("");
    setActiveStep(0);
  };

  const steps = [
    { key: "personal", label: "Personal Info", completion: personalCompletion },
    { key: "visa", label: "Visa & Travel", completion: visaCompletion },
    { key: "documents", label: "Documents", completion },
    { key: "review", label: "Review & Submit", completion: 0 },
  ];

  return (
    <form
      noValidate
      onSubmit={submitApplication}
      className="application-creator overflow-hidden rounded-xl border border-slate-200 bg-white"
    >
      <header className="application-form-heading"><h2>{t("newApplication")}</h2></header>

      <div className="px-4 pt-5 sm:px-8 sm:pt-7">
        <nav
          aria-label="Application steps"
          className="grid grid-cols-2 gap-2 sm:grid-cols-4 sm:gap-0"
        >
          {steps.map((step, index) => {
            const isActive = activeStep === index;
            const isComplete = activeStep > index || Boolean(application);
            return (
              <button
                key={step.key}
                type="button"
                aria-current={isActive ? "step" : undefined}
                onClick={() => goToStep(index)}
                className="group relative flex min-h-14 items-center gap-2 rounded-xl px-2 py-2 text-left sm:rounded-none sm:px-3"
              >
                {index > 0 && (
                  <span className="absolute -left-1 top-1/2 hidden h-px w-3 bg-slate-200 sm:block" />
                )}
                <span
                  className={`z-10 flex h-8 w-8 shrink-0 items-center justify-center rounded-full text-sm font-bold ${
                    isActive
                      ? "bg-blue-600 text-white shadow-md shadow-blue-200"
                      : isComplete
                        ? "bg-emerald-500 text-white"
                        : "bg-slate-200 text-slate-600"
                  }`}
                >
                  {index + 1}
                </span>
                <span
                  className={`text-xs font-semibold sm:text-sm ${
                    isActive ? "text-blue-800" : "text-slate-500"
                  }`}
                >
                  {step.label} {index < 3 && `(${step.completion}%)`}
                </span>
                {index < steps.length - 1 && (
                  <span className="ml-auto hidden text-xl text-slate-300 sm:block">
                    ›
                  </span>
                )}
              </button>
            );
          })}
        </nav>
      </div>

      <div className="grid gap-5 px-4 pb-5 pt-4 sm:px-8 sm:pb-8 lg:grid-cols-[minmax(0,1fr)_280px]">
        <div className="min-w-0">
          {activeStep === 0 && (
      <section className="space-y-5 overflow-hidden rounded-xl border border-slate-200 bg-white shadow-sm">
        <div className="application-section-heading">
          
          <h3 className="font-bold">1. {t("applicantInformation")}</h3>
        </div>
        <div className="space-y-5 p-4 sm:p-5">
        
        <label className="block text-sm font-medium text-slate-700">
          {t("useExistingCustomer")}
          <select
            aria-label={t("existingCustomer")}
            className="mt-1.5 w-full rounded-xl border border-slate-300 bg-white px-3 py-2.5 outline-none transition focus:border-blue-500 focus:ring-4 focus:ring-blue-100"
            value={customerId}
            disabled={Boolean(application)}
            onChange={(event) => selectCustomer(event.target.value)}
          >
            <option value="">{t("enterNewApplicant")}</option>
            {customers.map((customer) => (
              <option key={customer._id} value={customer._id}>
                {customer.fullName} · {customer.passportNumber || customer.email}
              </option>
            ))}
          </select>
        </label>
        <div className="flex flex-wrap items-start gap-4 rounded-lg border border-slate-200 p-4">
          {photoPreview && <img src={photoPreview} alt="Applicant photo preview" className="h-28 w-24 rounded-lg object-cover" />}
          <label className="block min-w-0 text-sm font-medium text-slate-700">Applicant photo (optional)
            <input type="file" accept="image/jpeg,image/png,image/webp" disabled={submitting || Boolean(application)} className="mt-2 block w-full text-sm" onChange={event => {
              const file=event.target.files?.[0]; event.target.value=""; if(!file)return;
              if(!["image/jpeg","image/png","image/webp"].includes(file.type) || file.size>5*1024*1024){setError("Choose a JPG, PNG or WebP photo up to 5 MB.");return;}
              setError(""); setPhotoFile(file);
            }}/>
            <span className="mt-2 block text-xs text-slate-500">JPG, PNG or WebP · Up to 5 MB.</span>
            {photoFile && <span className="mt-1 block text-xs">Selected: {photoFile.name}</span>}
          </label>
        </div>
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
          {[
            ["fullName", "fullNameTitle", "text"],
            ["email", "emailTitle", "email"],
            ["phone", "phoneTitle", "tel"],
            ["nationality", "nationalityTitle", "text"],
            ["passportNumber", "passportNumberTitle", "text"],
            ["dateOfBirth", "dateOfBirthTitle", "date"],
          ].map(([field, label, type]) => (
            <label key={field} className="text-sm font-medium text-slate-700">
              {t(label)}
              <input
                type={type}
                autoComplete={field === "fullName" ? "name" : undefined}
                max={type === "date" ? MAX_DATE : undefined}
                className="mt-1.5 w-full rounded-xl border border-slate-300 bg-white px-3 py-2.5 text-slate-900 outline-none transition placeholder:text-slate-400 focus:border-blue-500 focus:ring-4 focus:ring-blue-100 disabled:bg-slate-50"
                value={applicant[field]}
                disabled={Boolean(application)}
                onChange={(event) =>
                  setApplicant((current) => ({
                    ...current,
                    [field]: event.target.value,
                  }))
                }
              />
            </label>
          ))}
          <label className="text-sm font-medium text-slate-700 sm:col-span-2 lg:col-span-3">
            {t("addressTitle")}
            <input
              autoComplete="street-address"
              className="mt-1.5 w-full rounded-xl border border-slate-300 bg-white px-3 py-2.5 text-slate-900 outline-none transition focus:border-blue-500 focus:ring-4 focus:ring-blue-100 disabled:bg-slate-50"
              value={applicant.address}
              disabled={Boolean(application)}
              onChange={(event) =>
                setApplicant((current) => ({
                  ...current,
                  address: event.target.value,
                }))
              }
            />
          </label>
          <div className="sm:col-span-2 lg:col-span-3">
            <h4 className="font-semibold text-slate-800">
              {t("predictionInputsTitle")}
            </h4>
            <p className="mt-1 text-xs text-slate-500">
              {t("predictionInputsNote")}
            </p>
          </div>
          {PREDICTION_FIELDS.map(([field, label, type]) => (
            <label key={field} className="text-sm font-medium text-slate-700">
              {t(label)}
              {type === "boolean" ? (
                <select
                  className="mt-1.5 w-full rounded-xl border border-slate-300 bg-white px-3 py-2.5"
                  value={
                    applicant[field] === true
                      ? "true"
                      : applicant[field] === false
                        ? "false"
                        : ""
                  }
                  disabled={Boolean(application)}
                  onChange={(event) =>
                    setApplicant((current) => ({
                      ...current,
                      [field]:
                        event.target.value === ""
                          ? ""
                          : event.target.value === "true",
                    }))
                  }
                >
                  <option value="">Not provided</option>
                  <option value="true">Yes</option>
                  <option value="false">No</option>
                </select>
              ) : (
                <input
                  type={type}
                  min={type === "number" ? 0 : undefined}
                  step={
                    type === "number" && field.includes("Usd") ? "0.01" : "1"
                  }
                  className="mt-1.5 w-full rounded-xl border border-slate-300 bg-white px-3 py-2.5 disabled:bg-slate-50"
                  value={applicant[field]}
                  disabled={Boolean(application)}
                  onChange={(event) =>
                    setApplicant((current) => ({
                      ...current,
                      [field]: event.target.value,
                    }))
                  }
                />
              )}
            </label>
          ))}
        </div>
        </div>
      </section>
          )}

          {activeStep === 1 && (
        <section className="space-y-5 overflow-hidden rounded-xl border border-slate-200 bg-white shadow-sm">
          <div className="application-section-heading">
            <h3 className="font-bold">2. Visa &amp; Travel Details</h3>
          </div>
          <div className="space-y-5 p-4 sm:p-5">
          <div>
            <label className="block text-sm font-semibold text-slate-700">
              {t("selectVisaCode")}
            </label>
            <select
              required
              aria-label={t("selectVisaCode")}
              className="mt-2 w-full rounded-lg border border-slate-300 bg-white px-3 py-3 text-slate-900 shadow-sm outline-none transition focus:border-blue-500 focus:ring-4 focus:ring-blue-100"
              value={visaTypeId}
              disabled={Boolean(application)}
              onChange={(event) => selectVisaType(event.target.value)}
            >
              <option value="">{t("selectVisaCode")}</option>
              {visaTypes.map((visaType) => (
                <option key={visaType._id} value={visaType._id}>
                  {visaType.visaTypeCode} · {visaType.name}
                </option>
              ))}
            </select>
            {selectedVisaType && (
              <p className="mt-2 inline-flex rounded-full bg-blue-50 px-3 py-1 text-sm font-medium text-blue-800">
                {selectedVisaType.destination} · {selectedVisaType.category}
              </p>
            )}
          </div>
          {visaTypeId && (
            <section className="grid gap-4 sm:grid-cols-2">
              <label className="text-sm font-medium text-slate-700">
                {t("travelStartDate")}
                <input
                  type="date"
                  disabled={Boolean(application)}
                  className="mt-1.5 w-full rounded-lg border border-slate-300 bg-white px-3 py-3 text-slate-900 outline-none transition focus:border-blue-500 focus:ring-4 focus:ring-blue-100 disabled:bg-slate-50"
                  value={travelStartDate}
                  onChange={(event) => setTravelStartDate(event.target.value)}
                />
              </label>
              <label className="text-sm font-medium text-slate-700">
                {t("returnDate")}
                <input
                  type="date"
                  min={travelStartDate || undefined}
                  disabled={Boolean(application)}
                  className="mt-1.5 w-full rounded-lg border border-slate-300 bg-white px-3 py-3 text-slate-900 outline-none transition focus:border-blue-500 focus:ring-4 focus:ring-blue-100 disabled:bg-slate-50"
                  value={returnDate}
                  onChange={(event) => setReturnDate(event.target.value)}
                />
              </label>
            </section>
          )}
          </div>
        </section>
          )}

          {activeStep === 2 && (
        <section className="space-y-4 overflow-hidden rounded-xl border border-slate-200 bg-white shadow-sm">
          <div className="application-section-heading flex flex-wrap items-center justify-between gap-3">
            <h3 className="font-bold text-teal-950">3. {t("visaDocumentChecklist")}</h3>
            <span className="text-sm font-semibold text-blue-700">
              {documentCountLabel}
              {Number.isFinite(verifiedPointsPercentage) &&
                ` · ${verifiedPointsPercentage}%`}
            </span>
          </div>
          <div className="space-y-4 p-4 sm:p-5">
          <div className="flex flex-wrap items-end justify-between gap-2">
            <div className="text-sm text-slate-500">
              {selectedVisaType && (
                <p>{selectedVisaType.visaTypeCode} · {selectedVisaType.destination}</p>
              )}
            </div>
          </div>
          <div
            className="h-2 overflow-hidden rounded-full bg-slate-100"
            role="progressbar"
            aria-valuenow={completion}
            aria-valuemin={0}
            aria-valuemax={100}
            aria-label={t("requiredDocumentCompletion")}
          >
            <div
              className="h-full bg-blue-600 transition-all"
              style={{ width: `${completion}%` }}
            />
          </div>
          {requirementsLoading && (
            <p className="text-sm text-slate-500">{t("loadingRequirements")}</p>
          )}
          {requirementsError && (
            <p role="alert" className="rounded-xl bg-red-50 p-3 text-sm text-red-700">
              {requirementsError}
            </p>
          )}
          {!requirementsLoading &&
            !requirementsError &&
            requirements.length === 0 && (
              <p className="rounded-xl bg-amber-50 p-3 text-sm text-amber-800">
                {t("noVisaRequirements")}
              </p>
            )}
          <div className="divide-y divide-slate-200 overflow-hidden rounded-xl border border-slate-200 bg-white">
            {requirements.map((requirement) => {
              const fileState = files[requirement._id] || {};
              const formats = requirement.acceptedFormats || [
                "pdf",
                "jpg",
                "jpeg",
                "png",
              ];
              const isUploaded = fileState.status === "Uploaded";
              const isUploading = fileState.status === "Uploading";
              const isReady = hasRequirementFile(requirement);
              const isAvailable = Boolean(documentAvailable[requirement._id]) || isReady;
              const isPhotoRequirement = /photo|photograph/i.test(
                `${requirement.code || ""} ${requirement.name || ""}`,
              );
              const isOcrFailed = !isPhotoRequirement && ["OCR_FAILED", "OCR_PARTIAL"].includes(
                fileState.ocrStatus,
              );
              const isOcrProcessing = ["OCR_PENDING", "OCR_PROCESSING"].includes(
                fileState.ocrStatus,
              );
              const statusLabel = isPhotoRequirement && isUploaded
                ? t("photoOcrNotRequired")
                : isOcrFailed
                ? t("documentReadingFailed")
                : isOcrProcessing
                  ? t("readingDocument")
                  : fileState.ocrStatus === "OCR_COMPLETED"
                    ? t("ocrCompletedReady")
                    : isUploaded
                      ? t("uploaded")
                      : isUploading
                  ? t("uploading")
                  : fileState.status === "Upload failed"
                    ? t("uploadFailedStatus")
                    : isReady
                      ? t("readyToUpload")
                      : t("notUploaded");
              return (
                <article
                  key={requirement._id}
                  className="grid gap-4 p-4 transition hover:bg-slate-50/80 sm:grid-cols-[minmax(0,1fr)_210px_130px] sm:items-center"
                >
                  <div className="flex min-w-0 items-start gap-3">
                    <span className={`flex h-11 w-11 shrink-0 items-center justify-center rounded-xl ${
                      isUploaded ? "bg-emerald-50 text-emerald-700" : "bg-blue-50 text-blue-700"
                    }`}>
                      <FileText className="h-5 w-5" aria-hidden="true" />
                    </span>
                    <div className="min-w-0">
                      <h4 className="font-semibold text-slate-900">
                        {requirement.name}
                        <span
                          className={`ml-2 rounded-full px-2 py-0.5 text-xs ${requirement.required ? "bg-red-50 text-red-700" : "bg-slate-100 text-slate-700"}`}
                        >
                          {requirement.required
                            ? t("required")
                            : requirement.conditional
                              ? t("conditionalRequirement")
                              : t("optional")}
                        </span>
                      </h4>
                      <p className="mt-1 text-xs text-slate-600">
                        {requirement.description &&
                          (typeof requirement.description === "string"
                            ? requirement.description
                            : requirement.description[i18n.language] || requirement.description.en)}
                      </p>
                      <p className="mt-1 text-xs text-slate-400">
                        {formats.join(", ").toUpperCase()} · {t("maximum")} {humanSize(requirement.maxFileSize || MAX_FILE_SIZE)}
                      </p>
                      <label className="mt-3 inline-flex cursor-pointer items-center gap-2 text-sm font-medium text-slate-700">
                        <input
                          type="checkbox"
                          checked={isAvailable}
                          disabled={Boolean(application) || isUploaded}
                          className="h-4 w-4 rounded border-slate-300 accent-blue-600 focus:ring-blue-500 disabled:cursor-not-allowed"
                          onChange={(event) => {
                            const available = event.target.checked;
                            setDocumentAvailable((current) => ({
                              ...current,
                              [requirement._id]: available,
                            }));
                            if (!available) {
                              setFiles((current) => {
                                const next = { ...current };
                                delete next[requirement._id];
                                return next;
                              });
                            }
                          }}
                        />
                        {t("iHaveThisDocument")}
                      </label>
                    </div>
                  </div>
                  {(isAvailable || isUploaded) && <label className={`relative flex min-h-12 cursor-pointer items-center justify-center gap-2 rounded-lg border border-dashed px-3 py-2 text-center text-sm font-semibold transition ${
                    isUploaded
                      ? "border-emerald-200 bg-emerald-50 text-emerald-700"
                      : "border-blue-300 bg-blue-50/70 text-blue-700 hover:border-blue-500 hover:bg-blue-50"
                  } ${submitting || isUploaded || !isAvailable ? "cursor-not-allowed opacity-60" : ""}`}>
                    {!isUploaded && (
                      <>
                        <CloudUpload className="h-5 w-5 shrink-0" aria-hidden="true" />
                        <span>{fileState.file ? t("replace") : t("uploadDocument")}</span>
                        <input
                          type="file"
                          aria-label={`${t("uploadDocument")}: ${requirement.name}`}
                          accept={formats.map((format) => `.${format}`).join(",")}
                        disabled={submitting || !isAvailable}
                          className="absolute inset-0 h-full w-full cursor-pointer opacity-0 disabled:cursor-not-allowed"
                          onChange={(event) => {
                            const file = event.target.files?.[0];
                            event.target.value = "";
                            if (file) selectFile(requirement, file);
                          }}
                        />
                      </>
                    )}
                    {isUploaded && <CheckCircle2 className="h-5 w-5" aria-hidden="true" />}
                    {isUploaded && <span>{t("uploaded")}</span>}
                  </label>}
                  <div className="flex min-w-0 items-center gap-2 text-sm sm:justify-end">
                    {isUploaded ? (
                      <CheckCircle2 className="h-4 w-4 shrink-0 text-emerald-600" aria-hidden="true" />
                    ) : isReady ? (
                      <CheckCircle2 className="h-4 w-4 shrink-0 text-blue-600" aria-hidden="true" />
                    ) : fileState.status === "Upload failed" ? (
                      <AlertCircle className="h-4 w-4 shrink-0 text-red-600" aria-hidden="true" />
                    ) : (
                      <AlertCircle className="h-4 w-4 shrink-0 text-slate-400" aria-hidden="true" />
                    )}
                    <span className={`truncate ${
                      isUploaded
                        ? "text-emerald-700"
                        : isReady
                          ? "text-blue-700"
                          : fileState.status === "Upload failed"
                            ? "text-red-700"
                            : "text-slate-500"
                    }`}>
                      <span className="block">{statusLabel}</span>
                      {(fileState.fileName || fileState.file?.name) && (
                        <span className="block max-w-full truncate text-xs font-normal text-slate-500">
                          {fileState.fileName || fileState.file.name}
                        </span>
                      )}
                      {fileState.totalPages > 0 && (
                        <span className="mt-1 block text-xs font-normal text-slate-600">
                          {t("ocrPagesProgress", {
                            processed: fileState.pagesProcessed || 0,
                            total: fileState.totalPages,
                          })}
                          {" · "}
                          {t("ocrProgressPercent", {
                            percent: fileState.progressPercent || 0,
                          })}
                        </span>
                      )}
                    </span>
                  </div>
                  {fileState.error && (
                    <p role="alert" className="text-sm text-red-600 sm:col-span-3">
                      {fileState.error}
                    </p>
                  )}
                  {isOcrFailed && fileState.documentId && (
                    <button
                      type="button"
                      disabled={fileState.retryingOcr}
                      onClick={() => retryDocumentOcr(requirement._id)}
                      className="text-left text-sm font-semibold text-blue-700 underline hover:text-blue-900 disabled:opacity-50 sm:col-span-3"
                    >
                      {fileState.retryingOcr
                        ? t("readingDocument")
                        : t("retryOcr")}
                    </button>
                  )}
                </article>
              );
            })}
          </div>
          <div className="flex gap-3 rounded-xl border border-blue-100 bg-blue-50 p-4 text-sm text-blue-900">
            <AlertCircle className="mt-0.5 h-5 w-5 shrink-0 text-blue-600" aria-hidden="true" />
            <div>
              <p className="font-semibold">{t("documentChecklist")}</p>
              <p className="mt-1 text-blue-800">{t("documentAvailabilityNote")}</p>
            </div>
          </div>
          </div>
        </section>
      )}

      {visaTypeId && requirements.length > 0 && (
        <section className="space-y-4 rounded-2xl border border-slate-200 bg-slate-50 p-4 sm:p-5">
          <div className="flex items-center gap-2">
            <span className="flex h-6 w-6 items-center justify-center rounded-full bg-blue-600 text-xs font-bold text-white">
              3
            </span>
            <h3 className="font-semibold text-slate-900">
              {t("documentUploadSummary")}
            </h3>
          </div>
          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-5">
            {[
              [t("totalRequired"), requiredRequirements.length],
              [
                application ? t("uploadedDocuments") : t("filesAttached"),
                application ? uploadedCount : attachedCount,
              ],
              [t("missingRequired"), missingRequirements.length],
              [
                application
                  ? t("optionalDocumentsUploaded")
                  : t("optionalFilesAttached"),
                optionalUploaded,
              ],
              ...(!verificationResult
                ? [[t("completion"), `${completion}%`]]
                : []),
            ].map(([label, value]) => (
              <div key={label} className="rounded-xl border border-slate-100 bg-white p-3.5 shadow-sm">
                <p className="text-xs text-slate-500">{label}</p>
                <p className="mt-1 text-lg font-semibold text-slate-900">
                  {value}
                </p>
              </div>
            ))}
            </div>
        </section>
            )}

          {activeStep === 3 && (
            <section className="space-y-5 rounded-xl border border-slate-200 bg-white p-4 shadow-sm sm:p-5">
              <div>
                <h3 className="text-xl font-bold text-slate-900">4. Review &amp; Submit</h3>
                <p className="mt-1 text-sm text-slate-500">
                  Check the application details and document checklist before submitting.
                </p>
              </div>
              <div className="grid gap-4 sm:grid-cols-2">
                <div className="rounded-xl border border-blue-100 bg-blue-50/70 p-4">
                  <h4 className="mb-2 font-semibold text-blue-900">{t("applicantInformation")}</h4>
                  <dl className="space-y-1 text-sm text-slate-700">
                    <div><dt className="inline font-medium">{t("fullNameTitle")}: </dt><dd className="inline">{applicant.fullName || "—"}</dd></div>
                    <div><dt className="inline font-medium">{t("emailTitle")}: </dt><dd className="inline break-all">{applicant.email || "—"}</dd></div>
                    <div><dt className="inline font-medium">{t("phoneTitle")}: </dt><dd className="inline">{applicant.phone || "—"}</dd></div>
                    <div><dt className="inline font-medium">{t("nationalityTitle")}: </dt><dd className="inline">{applicant.nationality || "—"}</dd></div>
                    <div><dt className="inline font-medium">{t("passportNumberTitle")}: </dt><dd className="inline">{applicant.passportNumber || "—"}</dd></div>
                  </dl>
                  <button type="button" onClick={() => goToStep(0)} className="mt-3 text-sm font-semibold text-blue-700 hover:text-blue-900">Edit applicant</button>
                </div>
                <div className="rounded-xl border border-emerald-100 bg-emerald-50/70 p-4">
                  <h4 className="mb-2 font-semibold text-emerald-900">Visa &amp; Travel</h4>
                  <dl className="space-y-1 text-sm text-slate-700">
                    <div><dt className="inline font-medium">{t("visaType")}: </dt><dd className="inline">{selectedVisaType ? `${selectedVisaType.visaTypeCode} · ${selectedVisaType.name}` : "—"}</dd></div>
                    <div><dt className="inline font-medium">{t("country")}: </dt><dd className="inline">{selectedVisaType?.destination || "—"}</dd></div>
                    <div><dt className="inline font-medium">{t("travelStartDate")}: </dt><dd className="inline">{travelStartDate || "—"}</dd></div>
                    <div><dt className="inline font-medium">{t("returnDate")}: </dt><dd className="inline">{returnDate || "—"}</dd></div>
                  </dl>
                  <button type="button" onClick={() => goToStep(1)} className="mt-3 text-sm font-semibold text-emerald-800 hover:text-emerald-950">Edit travel details</button>
                </div>
              </div>
              <div className="rounded-xl border border-teal-100 bg-teal-50/70 p-4">
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <h4 className="font-semibold text-teal-950">{t("visaDocumentChecklist")}</h4>
                  <span className="text-sm font-semibold text-teal-900">{missingRequirements.length} {t("missingRequired")}</span>
                </div>
                <p className="mt-1 text-sm text-slate-600">
                  {documentCountLabel}
                </p>
                {missingRequirements.length > 0 && (
                  <ul className="mt-2 list-inside list-disc text-sm text-amber-800">
                    {missingRequirements.map((requirement) => (
                      <li key={requirement._id}>{requirement.name}</li>
                    ))}
                  </ul>
                )}
                <button type="button" onClick={() => goToStep(2)} className="mt-3 text-sm font-semibold text-teal-800 hover:text-teal-950">Review documents</button>
              </div>
              <VerificationResultPanel
                verifying={verifying}
                verificationResult={verificationResult}
                verificationExplanation={verificationExplanation}
              />
            </section>
          )}

      {error && (
        <p role="alert" className="mt-4 rounded-xl border border-red-200 bg-red-50 p-3 text-sm text-red-700">
          {error}
        </p>
      )}
      {notice && (
        <p role="status" className="mt-4 rounded-xl border border-emerald-200 bg-emerald-50 p-3 text-sm text-emerald-800">
          {notice}
        </p>
      )}
      <div className="mt-5 flex flex-wrap items-center justify-between gap-3 border-t border-slate-200 pt-4">
        <button
          type="button"
          onClick={() => goToStep(Math.max(0, activeStep - 1))}
          disabled={activeStep === 0}
          className="rounded-lg border border-slate-300 bg-white px-4 py-2.5 text-sm font-semibold text-slate-700 transition hover:bg-slate-50 disabled:invisible"
        >
          Back
        </button>
        <div className="flex flex-wrap items-center justify-end gap-3">
        {application && (
          <button
            type="button"
            onClick={reset}
            className="rounded-xl border border-slate-200 px-4 py-2 text-sm font-medium text-slate-700"
          >
            {t("startAnotherApplication")}
          </button>
        )}
        {activeStep < steps.length - 1 && !application && (
          <button
            type="button"
            onClick={() => goToStep(activeStep + 1)}
            className="rounded-lg bg-blue-700 px-5 py-2.5 font-semibold text-white shadow-sm transition hover:bg-blue-800 focus:outline-none focus:ring-4 focus:ring-blue-200"
          >
            Continue <span aria-hidden="true">→</span>
          </button>
        )}
        {activeStep === steps.length - 1 && !application && (
          <button
            type="submit"
            disabled={submitting || requirementsLoading || Boolean(requirementsError)}
            className="rounded-lg bg-emerald-600 px-5 py-2.5 font-semibold text-white shadow-sm transition hover:bg-emerald-700 focus:outline-none focus:ring-4 focus:ring-emerald-200 disabled:cursor-not-allowed disabled:opacity-50"
          >
            {submitting ? t("creatingApplication") : t("createAndVerifyApplication")}
          </button>
        )}
        {application && (
          <button
            type="submit"
            disabled={
              submitting ||
              !Object.values(files).some(
                (item) => item.file && item.status !== "Uploaded",
              )
            }
            className="rounded-xl bg-blue-600 px-5 py-2.5 font-semibold text-white shadow-sm transition hover:bg-blue-700 focus:outline-none focus:ring-4 focus:ring-blue-200 disabled:cursor-not-allowed disabled:opacity-50"
          >
            {submitting ? t("uploading") : t("retryFailedUploads")}
          </button>
        )}
        </div>
      </div>

        </div>
        <aside data-application-summary className="h-fit space-y-4 rounded-xl border border-slate-200 bg-white p-4 shadow-sm lg:sticky lg:top-24">
          <h3 className="text-lg font-bold text-slate-900">{t("applicationReadinessSummary")}</h3>
          <section className="rounded-xl bg-blue-50 p-3">
            <h4 className="font-semibold text-blue-800">{t("applicantInformation")}</h4>
            <p className="mt-2 text-sm text-slate-700">{applicant.fullName || "Applicant name"}</p>
            <p className="text-sm text-slate-600">{applicant.nationality || "Nationality"}{applicant.passportNumber ? ` · ${applicant.passportNumber}` : ""}</p>
          </section>
          <section className="rounded-xl bg-emerald-50 p-3">
            <h4 className="font-semibold text-emerald-800">Visa &amp; Travel</h4>
            <p className="mt-2 text-sm text-slate-700">{selectedVisaType?.destination || "Destination not selected"}</p>
            <p className="text-sm text-slate-600">{selectedVisaType?.name || "Visa type not selected"}</p>
            {travelStartDate && <p className="mt-1 text-xs text-slate-600">{travelStartDate}{returnDate ? ` – ${returnDate}` : ""}</p>}
          </section>
          <section className="rounded-xl bg-teal-50 p-3">
            <div className="flex items-center justify-between gap-2">
              <h4 className="font-semibold text-teal-800">{t("visaDocumentChecklist")}</h4>
              {!verificationResult && (
                <span className="text-sm font-bold text-teal-900">{completion}%</span>
              )}
            </div>
            <p className="mt-2 text-sm text-slate-700">{documentCountLabel}</p>
            <div className="mt-2 h-2 overflow-hidden rounded-full bg-white">
              <div className="h-full rounded-full bg-teal-600 transition-all" style={{ width: `${completion}%` }} />
            </div>
          </section>
          <p className="text-xs leading-relaxed text-slate-500">{t("requirementsFromAdmin")}</p>
        </aside>
      </div>
    </form>
  );
}
