import { useMemo, useState } from "react";
import {
  ArrowDownToLine,
  ArrowRight,
  CheckCircle2,
  Clock3,
  FileCheck2,
  Files,
  Search,
  ShieldAlert,
  TrendingUp,
  XCircle,
} from "lucide-react";
import {
  CartesianGrid,
  Cell,
  Line,
  LineChart,
  Pie,
  PieChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { Link } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { api, useSecureData } from "./pageUtils";

const EMPTY_REPORT_DATA = { applications: [], documents: [] };
const STATUS_COLORS = {
  verified: "#10b981",
  rejected: "#ef4444",
  pending: "#f59e0b",
};

function getId(record) {
  return String(record?._id || record?.id || "");
}

function getApplicationId(document) {
  return String(document?.applicationId || document?.application_id || "");
}

function getDate(value) {
  if (!value) return null;
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? null : date;
}

function getRecordDate(record, application) {
  const fields = [
    "automatedVerificationAt",
    "verifiedAt",
    "reviewedAt",
    "updatedAt",
    "createdAt",
    "uploadedAt",
  ];
  const dates = fields.map((field) => getDate(record?.[field])).filter(Boolean);
  if (dates.length) return dates.sort((left, right) => right - left)[0];
  return getDate(application?.updatedAt) || getDate(application?.createdAt);
}

function normalizeDocumentStatus(document) {
  const status = String(
    document?.status ||
      document?.automatedVerificationStatus ||
      document?.automatedVerification?.status ||
      document?.verificationStatus ||
      document?.verification?.status ||
      "",
  ).toUpperCase();
  if (["VERIFIED", "APPROVED", "PASSED", "PASS"].includes(status)) {
    return "verified";
  }
  if (["REJECTED", "FAILED", "FAIL"].includes(status)) return "rejected";
  return "pending";
}

function getApplicationStatus(application, documents) {
  const status = String(application?.status || "").toUpperCase();
  if (
    ["REJECTED", "FINAL_FAIL", "FAILED"].includes(status) ||
    documents.some((document) => normalizeDocumentStatus(document) === "rejected")
  ) {
    return "rejected";
  }

  const verificationPercentage = Number(application?.verificationPercentage);
  if (
    ["FINAL_PASS", "APPROVED", "VERIFIED"].includes(status) ||
    (application?.hasVerificationResult === true &&
      Number.isFinite(verificationPercentage) &&
      verificationPercentage >= 100 &&
      documents.length > 0 &&
      documents.every(
        (document) => normalizeDocumentStatus(document) === "verified",
      ))
  ) {
    return "verified";
  }
  return "pending";
}

function getApplicationScore(application) {
  const readiness = application?.documentReadiness;
  const score =
    readiness && typeof readiness === "object"
      ? readiness.score
      : readiness;
  const value =
    score ??
    application?.completenessScore ??
    application?.documentAssessmentScore;
  if (value === null || value === undefined || value === "") return null;
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : null;
}

function getDocumentType(document) {
  const value =
    document?.requirementName ||
    document?.documentType ||
    document?.document_type ||
    document?.requirementCode ||
    document?.documentName;
  if (typeof value === "string" && value.trim()) return value.trim();
  return "Other";
}

function formatPercent(value) {
  return `${Number(value || 0).toFixed(1)}%`;
}

function formatDate(value, locale) {
  const date = getDate(value);
  return date
    ? new Intl.DateTimeFormat(locale, {
        year: "numeric",
        month: "short",
        day: "numeric",
      }).format(date)
    : "—";
}

function exportCsv(rows, labels) {
  const csvEscape = (value) => {
    const text = String(value ?? "");
    const safeText = /^[=+\-@]/.test(text) ? `'${text}` : text;
    return `"${safeText.replace(/"/g, '""')}"`;
  };
  const lines = [
    labels,
    ...rows.map((row) => [
      row.applicationNumber,
      row.applicant,
      row.visaType,
      row.documentCount,
      row.score === null ? "" : `${row.score}%`,
      row.status,
      row.date ? row.date.toISOString() : "",
    ]),
  ];
  const blob = new Blob(
    [`\uFEFF${lines.map((line) => line.map(csvEscape).join(",")).join("\r\n")}`],
    { type: "text/csv;charset=utf-8" },
  );
  const url = URL.createObjectURL(blob);
  const link = window.document.createElement("a");
  link.href = url;
  link.download = "verification-reports.csv";
  link.click();
  window.setTimeout(() => URL.revokeObjectURL(url), 0);
}

export default function ReportsPage() {
  const { t, i18n } = useTranslation();
  const [periodDays, setPeriodDays] = useState(30);
  const [query, setQuery] = useState("");
  const [statusFilter, setStatusFilter] = useState("all");
  const [now] = useState(() => new Date());
  const { data, loading, error } = useSecureData(async () => {
    const [applicationsResponse, documentsResponse] = await Promise.all([
      api.get("/visa-types/applications"),
      api.get("/documents"),
    ]);
    return {
      applications: Array.isArray(applicationsResponse.data)
        ? applicationsResponse.data
        : applicationsResponse.data?.applications || [],
      documents: Array.isArray(documentsResponse.data)
        ? documentsResponse.data
        : documentsResponse.data?.documents || [],
    };
  }, EMPTY_REPORT_DATA);

  const locale = i18n.language?.startsWith("km") ? "km-KH" : "en-US";
  const applications = useMemo(
    () => (Array.isArray(data?.applications) ? data.applications : []),
    [data],
  );
  const allDocuments = useMemo(
    () =>
      (Array.isArray(data?.documents) ? data.documents : []).filter(
        (document) =>
          !["DELETED", "REPLACED"].includes(
            String(document?.status || "").toUpperCase(),
          ),
      ),
    [data],
  );
  const applicationById = useMemo(
    () => new Map(applications.map((application) => [getId(application), application])),
    [applications],
  );

  const currentStart = useMemo(() => {
    const start = new Date(now);
    start.setHours(0, 0, 0, 0);
    start.setDate(start.getDate() - periodDays + 1);
    return start;
  }, [now, periodDays]);
  const previousStart = useMemo(() => {
    const start = new Date(currentStart);
    start.setDate(start.getDate() - periodDays);
    return start;
  }, [currentStart, periodDays]);

  const documentsByApplication = useMemo(() => {
    const grouped = new Map();
    for (const document of allDocuments) {
      const applicationId = getApplicationId(document);
      if (!applicationId) continue;
      const group = grouped.get(applicationId) || [];
      group.push(document);
      grouped.set(applicationId, group);
    }
    return grouped;
  }, [allDocuments]);

  const datedDocuments = useMemo(
    () =>
      allDocuments
        .map((document) => {
          const application = applicationById.get(getApplicationId(document));
          return {
            document,
            date: getRecordDate(document, application),
            status: normalizeDocumentStatus(document),
          };
        })
        .filter((entry) => entry.date),
    [allDocuments, applicationById],
  );
  const currentDocuments = useMemo(
    () => datedDocuments.filter(({ date }) => date >= currentStart && date <= now),
    [datedDocuments, currentStart, now],
  );
  const previousDocuments = useMemo(
    () => datedDocuments.filter(({ date }) => date >= previousStart && date < currentStart),
    [datedDocuments, previousStart, currentStart],
  );

  const countStatuses = (entries) => ({
    total: entries.length,
    verified: entries.filter((entry) => entry.status === "verified").length,
    rejected: entries.filter((entry) => entry.status === "rejected").length,
    pending: entries.filter((entry) => entry.status === "pending").length,
  });
  const totals = countStatuses(currentDocuments);
  const previousTotals = countStatuses(previousDocuments);
  const statusChange = (status) => {
    const previous = previousTotals[status];
    if (!previous) return null;
    return Math.round(((totals[status] - previous) / previous) * 100);
  };

  const trendCounts = new Map();
  for (const entry of currentDocuments) {
    const key = entry.date.toDateString();
    const counts = trendCounts.get(key) || {
      verified: 0,
      rejected: 0,
      pending: 0,
    };
    counts[entry.status] += 1;
    trendCounts.set(key, counts);
  }
  const trendData = [];
  for (let offset = 0; offset < periodDays; offset += 1) {
    const day = new Date(currentStart);
    day.setDate(day.getDate() + offset);
    trendData.push({
      date: day,
      label: new Intl.DateTimeFormat(locale, {
        month: "short",
        day: "numeric",
      }).format(day),
      ...(trendCounts.get(day.toDateString()) || {
        verified: 0,
        rejected: 0,
        pending: 0,
      }),
    });
  }

  const statusData = [
    { name: t("reportVerified"), value: totals.verified, key: "verified" },
    { name: t("reportRejected"), value: totals.rejected, key: "rejected" },
    { name: t("reportPending"), value: totals.pending, key: "pending" },
  ];
  const completionRate = totals.total
    ? (totals.verified / totals.total) * 100
    : 0;

  const typeCounts = new Map();
  for (const { document } of currentDocuments) {
    const type = getDocumentType(document);
    typeCounts.set(type, (typeCounts.get(type) || 0) + 1);
  }
  const typeData = [...typeCounts.entries()]
    .map(([name, count]) => ({ name, count }))
    .sort((left, right) => right.count - left.count || left.name.localeCompare(right.name))
    .slice(0, 6);

  const reportRows = useMemo(() => {
    return applications
      .map((application) => {
        const applicationId = getId(application);
        const documents = documentsByApplication.get(applicationId) || [];
        const date =
          documents
            .map((document) => getRecordDate(document, application))
            .filter(Boolean)
            .sort((left, right) => right - left)[0] ||
          getDate(application.updatedAt) ||
          getDate(application.createdAt);
        const applicant =
          application.applicantName ||
          application.fullName ||
          (typeof application.applicant === "string"
            ? application.applicant
            : application.applicant?.fullName) ||
          application.customerName ||
          "";
        const visaType =
          application.visaTypeName ||
          application.visaCategory ||
          application.visaType?.name ||
          application.visaTypeCode ||
          "";
        return {
          id: applicationId,
          applicationNumber:
            application.applicationNumber ||
            application.applicationNo ||
            applicationId,
          applicant,
          visaType,
          documentCount: documents.length,
          score: getApplicationScore(application),
          status: getApplicationStatus(application, documents),
          date,
        };
      })
      .filter(
        (row) =>
          row.date &&
          row.date >= currentStart &&
          row.date <= now &&
          (statusFilter === "all" || row.status === statusFilter) &&
          `${row.applicationNumber} ${row.applicant} ${row.visaType}`
            .toLowerCase()
            .includes(query.trim().toLowerCase()),
      )
      .sort((left, right) => right.date - left.date);
  }, [
    applications,
    currentStart,
    documentsByApplication,
    now,
    query,
    statusFilter,
  ]);

  const formatChange = (status) => {
    const change = statusChange(status);
    return change === null
      ? t("reportNoPreviousData")
      : `${change > 0 ? "+" : ""}${change}% ${t("reportVsPreviousPeriod")}`;
  };
  const handleExport = () =>
    exportCsv(reportRows, [
      t("reportApplication"),
      t("applicant"),
      t("visaType"),
      t("reportDocuments"),
      t("reportScore"),
      t("status"),
      t("reportUpdated"),
    ]);

  const statCards = [
    {
      label: t("reportTotalVerifications"),
      value: totals.total,
      icon: Files,
      color: "text-blue-700",
      background: "bg-blue-50",
      change: formatChange("total"),
    },
    {
      label: t("reportVerified"),
      value: totals.verified,
      icon: CheckCircle2,
      color: "text-emerald-700",
      background: "bg-emerald-50",
      change: formatChange("verified"),
    },
    {
      label: t("reportRejected"),
      value: totals.rejected,
      icon: XCircle,
      color: "text-red-700",
      background: "bg-red-50",
      change: formatChange("rejected"),
    },
    {
      label: t("reportPending"),
      value: totals.pending,
      icon: Clock3,
      color: "text-amber-700",
      background: "bg-amber-50",
      change: formatChange("pending"),
    },
  ];

  return (
    <div className="space-y-6 p-4 sm:p-6 lg:p-8">
      <header className="flex flex-col gap-4 sm:flex-row sm:items-end sm:justify-between">
        <div>
          <div className="mb-2 flex items-center gap-2 text-sm font-semibold text-blue-700">
            <FileCheck2 size={18} />
            {t("reports")}
          </div>
          <h1 className="text-2xl font-bold tracking-tight text-slate-900 sm:text-3xl">
            {t("reportTitle")}
          </h1>
          <p className="mt-1 max-w-2xl text-sm text-slate-500">
            {t("reportDescription")}
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <label className="sr-only" htmlFor="report-period">
            {t("reportDateRange")}
          </label>
          <select
            id="report-period"
            className="h-10 rounded-lg border border-slate-200 bg-white px-3 text-sm text-slate-700 shadow-sm outline-none focus:border-blue-500 focus:ring-2 focus:ring-blue-100"
            value={periodDays}
            onChange={(event) => setPeriodDays(Number(event.target.value))}
          >
            <option value={7}>{t("reportLast7Days")}</option>
            <option value={30}>{t("reportLast30Days")}</option>
            <option value={90}>{t("reportLast90Days")}</option>
            <option value={365}>{t("reportLast12Months")}</option>
          </select>
          <button
            type="button"
            onClick={handleExport}
            disabled={loading || reportRows.length === 0}
            className="inline-flex h-10 items-center gap-2 rounded-lg bg-blue-700 px-4 text-sm font-semibold text-white shadow-sm transition hover:bg-blue-800 disabled:cursor-not-allowed disabled:opacity-50"
          >
            <ArrowDownToLine size={16} />
            {t("reportExport")}
          </button>
        </div>
      </header>

      {error && (
        <div
          className="rounded-xl border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-800"
          role="alert"
        >
          {error}
        </div>
      )}

      {loading ? (
        <div className="rounded-2xl border border-slate-200 bg-white p-8 text-center text-sm text-slate-500">
          {t("loading")}
        </div>
      ) : (
        <>
          <section
            className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4"
            aria-label={t("reportSummary")}
          >
            {statCards.map((card) => {
              const Icon = card.icon;
              return (
                <article
                  key={card.label}
                  className="rounded-2xl border border-slate-200 bg-white p-5 shadow-sm"
                >
                  <div className="flex items-start justify-between gap-3">
                    <div>
                      <p className="text-sm font-medium text-slate-500">
                        {card.label}
                      </p>
                      <p className="mt-2 text-3xl font-bold tracking-tight text-slate-900">
                        {card.value.toLocaleString(locale)}
                      </p>
                    </div>
                    <span
                      className={`grid h-11 w-11 place-items-center rounded-xl ${card.background} ${card.color}`}
                    >
                      <Icon size={21} />
                    </span>
                  </div>
                  <div className="mt-4 flex items-center gap-1.5 text-xs text-slate-500">
                    <TrendingUp size={14} className="text-slate-400" />
                    {card.change}
                  </div>
                </article>
              );
            })}
          </section>

          <section className="grid gap-5 xl:grid-cols-3">
            <article className="rounded-2xl border border-slate-200 bg-white p-5 shadow-sm xl:col-span-2">
              <div className="mb-5 flex flex-wrap items-start justify-between gap-3">
                <div>
                  <h2 className="font-semibold text-slate-900">
                    {t("reportVerificationTrend")}
                  </h2>
                  <p className="mt-1 text-sm text-slate-500">
                    {t("reportTrendDescription")}
                  </p>
                </div>
                <span className="inline-flex items-center gap-1.5 rounded-full bg-blue-50 px-3 py-1 text-xs font-medium text-blue-700">
                  <TrendingUp size={14} />
                  {t("reportSelectedPeriod")}
                </span>
              </div>
              {totals.total === 0 ? (
                <div className="grid h-64 place-items-center text-sm text-slate-400">
                  {t("reportNoData")}
                </div>
              ) : (
                <div className="h-64 w-full">
                  <ResponsiveContainer width="100%" height="100%">
                    <LineChart
                      data={trendData}
                      margin={{ top: 8, right: 12, bottom: 0, left: -18 }}
                    >
                      <CartesianGrid stroke="#e2e8f0" strokeDasharray="3 3" />
                      <XAxis
                        dataKey="label"
                        tick={{ fill: "#64748b", fontSize: 11 }}
                        tickLine={false}
                        axisLine={false}
                        interval={Math.max(0, Math.ceil(trendData.length / 8) - 1)}
                      />
                      <YAxis
                        allowDecimals={false}
                        tick={{ fill: "#64748b", fontSize: 11 }}
                        tickLine={false}
                        axisLine={false}
                      />
                      <Tooltip
                        labelFormatter={(_, payload) =>
                          payload?.[0]?.payload?.date
                            ? formatDate(payload[0].payload.date, locale)
                            : ""
                        }
                      />
                      <Line
                        type="monotone"
                        dataKey="verified"
                        name={t("reportVerified")}
                        stroke={STATUS_COLORS.verified}
                        strokeWidth={2.5}
                        dot={false}
                        activeDot={{ r: 4 }}
                      />
                      <Line
                        type="monotone"
                        dataKey="rejected"
                        name={t("reportRejected")}
                        stroke={STATUS_COLORS.rejected}
                        strokeWidth={2.5}
                        dot={false}
                        activeDot={{ r: 4 }}
                      />
                      <Line
                        type="monotone"
                        dataKey="pending"
                        name={t("reportPending")}
                        stroke={STATUS_COLORS.pending}
                        strokeWidth={2.5}
                        dot={false}
                        activeDot={{ r: 4 }}
                      />
                    </LineChart>
                  </ResponsiveContainer>
                </div>
              )}
            </article>

            <article className="rounded-2xl border border-slate-200 bg-white p-5 shadow-sm">
              <div className="mb-2">
                <h2 className="font-semibold text-slate-900">
                  {t("reportStatusBreakdown")}
                </h2>
                <p className="mt-1 text-sm text-slate-500">
                  {t("reportCurrentStatus")}
                </p>
              </div>
              {totals.total === 0 ? (
                <div className="grid h-56 place-items-center text-sm text-slate-400">
                  {t("reportNoData")}
                </div>
              ) : (
                <div className="relative h-56">
                  <ResponsiveContainer width="100%" height="100%">
                    <PieChart>
                      <Pie
                        data={statusData}
                        dataKey="value"
                        nameKey="name"
                        innerRadius={66}
                        outerRadius={88}
                        paddingAngle={3}
                        stroke="none"
                      >
                        {statusData.map((item) => (
                          <Cell
                            key={item.key}
                            fill={STATUS_COLORS[item.key]}
                          />
                        ))}
                      </Pie>
                      <Tooltip />
                    </PieChart>
                  </ResponsiveContainer>
                  <div className="pointer-events-none absolute inset-0 flex flex-col items-center justify-center">
                    <span className="text-2xl font-bold text-slate-900">
                      {formatPercent(completionRate)}
                    </span>
                    <span className="text-xs text-slate-500">
                      {t("reportVerified")}
                    </span>
                  </div>
                </div>
              )}
              <div className="mt-2 grid grid-cols-3 gap-2">
                {statusData.map((item) => (
                  <div key={item.key} className="text-center">
                    <span
                      className="mx-auto mb-1 block h-2 w-2 rounded-full"
                      style={{ backgroundColor: STATUS_COLORS[item.key] }}
                    />
                    <span className="block text-xs text-slate-500">
                      {item.name}
                    </span>
                    <span className="text-sm font-semibold text-slate-800">
                      {item.value.toLocaleString(locale)}
                    </span>
                  </div>
                ))}
              </div>
            </article>
          </section>

          <section className="grid gap-5 xl:grid-cols-3">
            <article className="rounded-2xl border border-slate-200 bg-white p-5 shadow-sm">
              <div className="mb-4 flex items-center justify-between gap-2">
                <div>
                  <h2 className="font-semibold text-slate-900">
                    {t("reportDocumentTypes")}
                  </h2>
                  <p className="mt-1 text-sm text-slate-500">
                    {t("reportDocumentTypesDescription")}
                  </p>
                </div>
                <Files size={18} className="text-slate-400" />
              </div>
              {typeData.length === 0 ? (
                <div className="grid h-40 place-items-center text-sm text-slate-400">
                  {t("reportNoData")}
                </div>
              ) : (
                <div className="space-y-4">
                  {typeData.map((item) => {
                    const width = Math.max(
                      4,
                      (item.count / typeData[0].count) * 100,
                    );
                    return (
                      <div key={item.name}>
                        <div className="mb-1.5 flex items-center justify-between gap-3 text-sm">
                          <span className="truncate text-slate-600">
                            {item.name}
                          </span>
                          <span className="font-semibold text-slate-900">
                            {item.count.toLocaleString(locale)}
                          </span>
                        </div>
                        <div className="h-2 overflow-hidden rounded-full bg-slate-100">
                          <div
                            className="h-full rounded-full bg-blue-600"
                            style={{ width: `${width}%` }}
                          />
                        </div>
                      </div>
                    );
                  })}
                </div>
              )}
            </article>

            <article className="rounded-2xl border border-blue-100 bg-gradient-to-br from-blue-50 to-white p-5 shadow-sm xl:col-span-2">
              <div className="flex h-full flex-col justify-between gap-5 sm:flex-row sm:items-center">
                <div className="max-w-xl">
                  <span className="mb-3 inline-flex h-10 w-10 items-center justify-center rounded-xl bg-blue-100 text-blue-700">
                    <ShieldAlert size={20} />
                  </span>
                  <h2 className="text-lg font-semibold text-slate-900">
                    {t("reportDecisionNoteTitle")}
                  </h2>
                  <p className="mt-2 text-sm leading-6 text-slate-600">
                    {t("reportDecisionNote")}
                  </p>
                </div>
                <Link
                  to="/applications"
                  className="inline-flex shrink-0 items-center justify-center gap-2 rounded-lg border border-blue-200 bg-white px-4 py-2.5 text-sm font-semibold text-blue-700 transition hover:bg-blue-50"
                >
                  {t("reportViewApplications")}
                  <ArrowRight size={16} />
                </Link>
              </div>
            </article>
          </section>

          <section className="overflow-hidden rounded-2xl border border-slate-200 bg-white shadow-sm">
            <div className="flex flex-col gap-4 border-b border-slate-100 p-5 lg:flex-row lg:items-center lg:justify-between">
              <div>
                <h2 className="font-semibold text-slate-900">
                  {t("reportRecentTitle")}
                </h2>
                <p className="mt-1 text-sm text-slate-500">
                  {t("reportRecentDescription")}
                </p>
              </div>
              <div className="flex flex-col gap-2 sm:flex-row">
                <label className="relative block">
                  <span className="sr-only">{t("reportSearch")}</span>
                  <Search
                    size={16}
                    className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-400"
                  />
                  <input
                    value={query}
                    onChange={(event) => setQuery(event.target.value)}
                    placeholder={t("reportSearch")}
                    className="h-10 w-full rounded-lg border border-slate-200 bg-white pl-9 pr-3 text-sm outline-none placeholder:text-slate-400 focus:border-blue-500 focus:ring-2 focus:ring-blue-100 sm:w-64"
                  />
                </label>
                <label>
                  <span className="sr-only">{t("filterByStatus")}</span>
                  <select
                    value={statusFilter}
                    onChange={(event) => setStatusFilter(event.target.value)}
                    className="h-10 w-full rounded-lg border border-slate-200 bg-white px-3 text-sm text-slate-700 outline-none focus:border-blue-500 focus:ring-2 focus:ring-blue-100 sm:w-40"
                  >
                    <option value="all">{t("reportAllStatuses")}</option>
                    <option value="verified">{t("reportVerified")}</option>
                    <option value="rejected">{t("reportRejected")}</option>
                    <option value="pending">{t("reportPending")}</option>
                  </select>
                </label>
              </div>
            </div>
            <div className="overflow-x-auto">
              <table className="min-w-full divide-y divide-slate-100 text-left text-sm">
                <thead className="bg-slate-50 text-xs uppercase tracking-wide text-slate-500">
                  <tr>
                    <th className="px-5 py-3 font-semibold">{t("reportApplication")}</th>
                    <th className="px-5 py-3 font-semibold">{t("applicant")}</th>
                    <th className="px-5 py-3 font-semibold">{t("visaType")}</th>
                    <th className="px-5 py-3 font-semibold">{t("reportDocuments")}</th>
                    <th className="px-5 py-3 font-semibold">{t("reportScore")}</th>
                    <th className="px-5 py-3 font-semibold">{t("status")}</th>
                    <th className="px-5 py-3 font-semibold">{t("reportUpdated")}</th>
                    <th className="px-5 py-3 font-semibold">
                      <span className="sr-only">{t("viewReport")}</span>
                    </th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-slate-100">
                  {reportRows.length === 0 ? (
                    <tr>
                      <td
                        colSpan={8}
                        className="px-5 py-12 text-center text-sm text-slate-500"
                      >
                        {t("reportNoMatchingRows")}
                      </td>
                    </tr>
                  ) : (
                    reportRows.map((row) => (
                      <tr key={row.id} className="transition hover:bg-slate-50/70">
                        <td className="whitespace-nowrap px-5 py-4 font-medium text-slate-900">
                          {row.applicationNumber}
                        </td>
                        <td className="whitespace-nowrap px-5 py-4 text-slate-700">
                          {row.applicant || "—"}
                        </td>
                        <td className="whitespace-nowrap px-5 py-4 text-slate-600">
                          {row.visaType || "—"}
                        </td>
                        <td className="px-5 py-4 text-slate-600">
                          {row.documentCount.toLocaleString(locale)}
                        </td>
                        <td className="whitespace-nowrap px-5 py-4 font-medium text-slate-700">
                          {row.score === null ? "—" : formatPercent(row.score)}
                        </td>
                        <td className="whitespace-nowrap px-5 py-4">
                          <span
                            className={`inline-flex items-center gap-1.5 rounded-full px-2.5 py-1 text-xs font-semibold ${
                              row.status === "verified"
                                ? "bg-emerald-50 text-emerald-700"
                                : row.status === "rejected"
                                  ? "bg-red-50 text-red-700"
                                  : "bg-amber-50 text-amber-800"
                            }`}
                          >
                            <span className="h-1.5 w-1.5 rounded-full bg-current" />
                            {t(
                              row.status === "verified"
                                ? "reportVerified"
                                : row.status === "rejected"
                                  ? "reportRejected"
                                  : "reportPending",
                            )}
                          </span>
                        </td>
                        <td className="whitespace-nowrap px-5 py-4 text-slate-500">
                          {formatDate(row.date, locale)}
                        </td>
                        <td className="whitespace-nowrap px-5 py-4 text-right">
                          <Link
                            to={`/applications/${encodeURIComponent(row.id)}/results`}
                            className="inline-flex items-center gap-1 text-sm font-semibold text-blue-700 hover:text-blue-900"
                          >
                            {t("viewReport")}
                            <ArrowRight size={14} />
                          </Link>
                        </td>
                      </tr>
                    ))
                  )}
                </tbody>
              </table>
            </div>
            <div className="flex items-center justify-between border-t border-slate-100 px-5 py-3 text-xs text-slate-500">
              <span>
                {t("reportShowingRows", { count: reportRows.length })}
              </span>
              <span>
                {t("reportDateRange")}: {formatDate(currentStart, locale)} –{" "}
                {formatDate(now, locale)}
              </span>
            </div>
          </section>
        </>
      )}
    </div>
  );
}
