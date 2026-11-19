import { useState } from "react";

export function Metric({ label, value }) {
  return (
    <div className="rounded-xl bg-slate-50 p-4">
      <p className="text-sm text-slate-500">{label}</p>
      <p className="mt-1 text-2xl font-bold text-slate-900">{value}</p>
    </div>
  );
}

export function AutomatedVerificationReport({ verification, isPhoto = false }) {
  const checks = verification.checks || [];
  return (
    <section className="space-y-3 rounded-xl border border-slate-200 bg-slate-50 p-4">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div>
          <h4 className="font-semibold text-slate-900">
            {isPhoto ? "Applicant photo validation" : "Automated verification"}:{" "}
            {verification.status || "NOT AVAILABLE"}
          </h4>
          <p className="mt-1 text-sm text-slate-600">
            Document score: {verification.documentScore ?? "—"}/100 ·{" "}
            {isPhoto
              ? "OCR confidence is not used for photo checks"
              : `OCR confidence: ${
                  verification.ocrConfidencePercent == null
                    ? "Unavailable"
                    : `${verification.ocrConfidencePercent}%`
                }`}
          </p>
        </div>
        <p className="text-sm font-semibold text-slate-700">
          Risk: {verification.riskLevel || "UNAVAILABLE"}
          {verification.riskScore == null
            ? ""
            : ` (${verification.riskScore}/100)`}
        </p>
      </div>
      <p className="text-xs text-slate-600">
        Checked{" "}
        {verification.verificationTimestamp
          ? new Date(verification.verificationTimestamp).toLocaleString()
          : "time unavailable"}
        . Authenticity was not established.
      </p>
      {checks.length > 0 && (
        <div className="grid gap-3 md:grid-cols-2">
          {[
            [
              "Passed checks",
              checks.filter((check) => check.status === "PASSED"),
            ],
            [
              "Unresolved checks",
              checks.filter((check) => check.status !== "PASSED"),
            ],
          ].map(([title, items]) => (
            <div key={title} className="rounded-lg bg-white p-3">
              <h5 className="text-sm font-semibold text-slate-800">{title}</h5>
              {items.length ? (
                <ul className="mt-2 space-y-2 text-xs text-slate-700">
                  {items.map((check) => (
                    <li key={check.code}>
                      <span
                        className={`font-semibold ${
                          check.status === "PASSED"
                            ? "text-emerald-700"
                            : check.status === "FAILED"
                              ? "text-red-700"
                              : "text-amber-800"
                        }`}
                      >
                        {check.label} · {check.status}
                      </span>
                      <p>{check.reason}</p>
                    </li>
                  ))}
                </ul>
              ) : (
                <p className="mt-2 text-xs text-slate-500">None</p>
              )}
            </div>
          ))}
        </div>
      )}
      {verification.warnings?.length > 0 && (
        <ul className="list-disc space-y-1 pl-5 text-xs text-amber-800">
          {verification.warnings.map((warning) => (
            <li key={warning}>{warning}</li>
          ))}
        </ul>
      )}
    </section>
  );
}

export function BankStatementAnalysis({ analysis, requirementScore }) {
  const [showDetails, setShowDetails] = useState(false);
  const [query, setQuery] = useState("");
  const [fromDate, setFromDate] = useState("");
  const [toDate, setToDate] = useState("");
  const [direction, setDirection] = useState("all");
  const [page, setPage] = useState(0);
  const transactions = analysis.transactions || [];
  const invalid = analysis.invalidTransactions || [];
  const monthly = analysis.transactionSummary?.monthly || [];
  const checks = analysis.monthlyBalanceChecks || [];
  const pageSize = 10;
  const money = (value, currency = "USD") => {
    if (value == null) return "—";
    const code = String(currency || "").toUpperCase();
    if (["USD", "KHR", "EUR", "THB", "GBP", "CAD", "AUD"].includes(code)) {
      return new Intl.NumberFormat(undefined, {
        style: "currency",
        currency: code,
        maximumFractionDigits: 2,
      }).format(Number(value));
    }
    return `${Number(value).toLocaleString(undefined, { maximumFractionDigits: 2 })} ${currency || ""}`.trim();
  };
  const filtered = transactions.filter((item) => {
    const text = [
      item.date,
      item.description,
      item.amount,
      item.direction,
      item.currency,
      item.page,
      item.evidence,
    ]
      .filter((value) => value != null)
      .join(" ")
      .toLowerCase();
    return (
      text.includes(query.trim().toLowerCase()) &&
      (!fromDate || (item.date && item.date >= fromDate)) &&
      (!toDate || (item.date && item.date <= toDate)) &&
      (direction === "all" || item.direction === direction)
    );
  });
  const pages = Math.ceil(filtered.length / pageSize);
  const currentPage = Math.min(page, Math.max(0, pages - 1));
  const visible = filtered.slice(
    currentPage * pageSize,
    (currentPage + 1) * pageSize,
  );

  return (
    <section className="rounded-xl border border-blue-100 bg-blue-50/50 p-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h4 className="font-semibold text-slate-900">
            Bank statement financial checks
          </h4>
          <p className="mt-1 text-xs text-slate-600">
            {analysis.financialRequirementStatus || "INSUFFICIENT_DATA"} ·
            authenticity is not confirmed
          </p>
        </div>
        <div className="flex items-center gap-2">
          <span className="rounded-lg bg-white px-3 py-2 text-sm font-bold text-blue-900">
            Bank score:{" "}
            {analysis.bankStatementScore?.available
              ? `${analysis.bankStatementScore.points} / ${analysis.bankStatementScore.maximum ?? 50} (${analysis.bankStatementScore.percentage ?? "—"}%)`
              : "Unavailable"}
          </span>
          {requirementScore && (
            <span className="rounded-lg bg-white px-3 py-2 text-sm font-bold text-emerald-800">
              Requirement points: {requirementScore.bankRequirementPointsEarned ?? 0} / {requirementScore.maximumConfiguredPoints ?? 0}
            </span>
          )}
          <button
            type="button"
            aria-expanded={showDetails}
            onClick={() => setShowDetails((value) => !value)}
            className="rounded-lg border border-blue-300 bg-white px-3 py-2 text-sm font-semibold text-blue-800 hover:bg-blue-50"
          >
            {showDetails ? "Hide details" : "View detailed analysis"}
          </button>
        </div>
      </div>
      {showDetails && (
        <div className="mt-4 space-y-4">
          {analysis.bankStatementScore && (
            <div className="rounded-lg bg-white p-3 text-sm">
              <div className="flex flex-wrap items-center justify-between gap-2">
                <h5 className="font-semibold text-slate-800">Bank assessment</h5>
                <span className="rounded-md bg-slate-100 px-2 py-1 text-xs font-bold text-slate-700">
                  {analysis.bankStatementScore.assessment || "INSUFFICIENT_DATA"}
                </span>
              </div>
              <p className="mt-1 text-xs text-slate-600">
                Evidence coverage: {analysis.bankStatementScore.evidenceCoveragePercentage ?? 0}% ·
                {` ${analysis.bankStatementScore.assessmentNote || "Human review is required."}`}
              </p>
              <p className="mt-1 text-xs text-slate-500">
                The percentage is a document-quality score, not a visa approval probability or proof of authenticity.
              </p>
            </div>
          )}
          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
            {[
              ["Bank", analysis.accountInformation?.bankName],
              [
                "Account holder",
                analysis.accountInformation?.accountHolderName,
              ],
              ["Account number", analysis.accountInformation?.accountNumber],
              ["Currency", analysis.accountInformation?.currency],
            ].map(([label, value]) => (
              <div key={label} className="rounded-lg bg-white p-3">
                <p className="text-xs text-slate-500">{label}</p>
                <p className="mt-1 break-words text-sm font-semibold text-slate-800">
                  {value || "NOT_AVAILABLE"}
                </p>
              </div>
            ))}
          </div>
          <div className="rounded-lg bg-white p-3 text-sm">
            <h5 className="font-semibold text-slate-800">
              OCR and statement period
            </h5>
            <p className="mt-1 text-xs text-slate-600">
              {analysis.ocrProcessingSummary?.status || "NOT_AVAILABLE"} ·{" "}
              {analysis.ocrProcessingSummary?.pagesProcessed ?? 0}/
              {analysis.ocrProcessingSummary?.totalPages ??
                analysis.documentInformation?.ocrPageCount ??
                0}{" "}
              OCR pages · {transactions.length} normalized transactions ·{" "}
              {invalid.length} requiring review
            </p>
            <p className="mt-1 text-xs text-slate-600">
              {analysis.statementPeriod?.startDate || "Start unavailable"} to{" "}
              {analysis.statementPeriod?.endDate || "end unavailable"} ·{" "}
              {analysis.statementPeriod?.status || "INSUFFICIENT_DATA"}
            </p>
          </div>
          <div className="overflow-x-auto rounded-lg bg-white p-3">
            <h5 className="text-sm font-semibold text-slate-800">
              Monthly financial summary
            </h5>
            <table className="mt-2 w-full min-w-[650px] text-left text-xs">
              <thead className="text-slate-500">
                <tr>
                  <th className="py-1 pr-3">Month</th>
                  <th className="py-1 pr-3">Credits</th>
                  <th className="py-1 pr-3">Debits</th>
                  <th className="py-1 pr-3">Transactions</th>
                  <th className="py-1 pr-3">Closing balance</th>
                  <th className="py-1">Result</th>
                </tr>
              </thead>
              <tbody>
                {monthly.length ? (
                  monthly.map((item) => (
                    <tr key={item.month} className="border-t border-slate-100">
                      <td className="py-1 pr-3">{item.month}</td>
                      <td className="py-1 pr-3">
                        {money(item.totalDeposits, item.currency)}
                      </td>
                      <td className="py-1 pr-3">
                        {money(item.totalWithdrawals, item.currency)}
                      </td>
                      <td className="py-1 pr-3">
                        {item.transactionCount ??
                          (item.incomingCount || 0) + (item.outgoingCount || 0)}
                      </td>
                      <td className="py-1 pr-3">
                        {money(item.closingBalance, item.currency)}
                      </td>
                      <td className="py-1">
                        {checks.find((check) => check.month === item.month)
                          ?.status || "NOT_AVAILABLE"}
                      </td>
                    </tr>
                  ))
                ) : (
                  <tr>
                    <td colSpan="6" className="py-2 text-slate-500">
                      No monthly evidence is available.
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>
          <div className="grid gap-3 sm:grid-cols-2">
            <div className="rounded-lg bg-white p-3 text-xs">
              <h5 className="font-semibold">Balance reconciliation</h5>
              <p className="mt-1">
                {analysis.reconciliation?.status || "NOT_AVAILABLE"}
              </p>
              <p>
                Expected:{" "}
                {money(analysis.reconciliation?.expected_closing_balance)} ·
                Extracted:{" "}
                {money(analysis.reconciliation?.extracted_closing_balance)}
              </p>
            </div>
            <div className="rounded-lg bg-white p-3 text-xs">
              <h5 className="font-semibold">Account holder</h5>
              <p className="mt-1">
                {analysis.accountHolderCheck?.status || "INSUFFICIENT_DATA"} ·{" "}
                {analysis.accountHolderCheck?.reason ||
                  "Name evidence unavailable."}
              </p>
            </div>
          </div>
          {analysis.sourceOfFundsAnalysis && (
            <div className="rounded-lg bg-white p-3 text-xs">
              <h5 className="font-semibold">Source of funds</h5>
              <p className="mt-1">
                {analysis.sourceOfFundsAnalysis.status || "INSUFFICIENT_DATA"}
              </p>
              <p>{analysis.sourceOfFundsAnalysis.note}</p>
            </div>
          )}
          {analysis.reviewFlags?.length > 0 && (
            <div className="rounded-lg border border-amber-200 bg-amber-50 p-3 text-xs">
              <h5 className="font-semibold text-amber-900">Review flags</h5>
              <ul className="mt-1 list-disc pl-5">
                {analysis.reviewFlags.map((flag, index) => (
                  <li key={`${flag.code}-${index}`}>{flag.reason}</li>
                ))}
              </ul>
            </div>
          )}
          {(transactions.length > 0 || invalid.length > 0) && (
            <details className="rounded-lg bg-white p-3">
              <summary className="cursor-pointer text-sm font-semibold">
                Transaction history ({filtered.length} filtered ·{" "}
                {transactions.length} total · {invalid.length} invalid)
              </summary>
              <div className="mt-3 grid gap-2 sm:grid-cols-3">
                <input
                  type="search"
                  value={query}
                  onChange={(event) => {
                    setQuery(event.target.value);
                    setPage(0);
                  }}
                  placeholder="Search transactions"
                  className="rounded-lg border px-3 py-2 text-sm"
                />
                <input
                  type="date"
                  value={fromDate}
                  onChange={(event) => {
                    setFromDate(event.target.value);
                    setPage(0);
                  }}
                  aria-label="From date"
                  className="rounded-lg border px-3 py-2 text-sm"
                />
                <input
                  type="date"
                  value={toDate}
                  onChange={(event) => {
                    setToDate(event.target.value);
                    setPage(0);
                  }}
                  aria-label="To date"
                  className="rounded-lg border px-3 py-2 text-sm"
                />
                <select
                  value={direction}
                  onChange={(event) => {
                    setDirection(event.target.value);
                    setPage(0);
                  }}
                  className="rounded-lg border px-3 py-2 text-sm"
                >
                  <option value="all">All directions</option>
                  <option value="debit">Debit</option>
                  <option value="credit">Credit</option>
                </select>
              </div>
              <div className="mt-3 max-h-72 overflow-auto">
                <table className="w-full min-w-[850px] text-left text-xs">
                  <thead className="text-slate-500">
                    <tr>
                      <th className="py-1 pr-2">Date</th>
                      <th className="py-1 pr-2">Description</th>
                      <th className="py-1 pr-2">Debit</th>
                      <th className="py-1 pr-2">Credit</th>
                      <th className="py-1 pr-2">Balance</th>
                      <th className="py-1 pr-2">Currency</th>
                      <th className="py-1 pr-2">Page</th>
                      <th className="py-1 pr-2">Status</th>
                      <th className="py-1">Evidence</th>
                    </tr>
                  </thead>
                  <tbody>
                    {visible.map((item, index) => (
                      <tr
                        key={`${item.date}-${item.page}-${currentPage}-${index}`}
                        className="border-t"
                      >
                        <td className="py-1 pr-2">{item.date || "—"}</td>
                        <td className="py-1 pr-2">{item.description || "—"}</td>
                        <td className="py-1 pr-2">
                          {item.direction === "debit"
                            ? money(item.amount, item.currency)
                            : "—"}
                        </td>
                        <td className="py-1 pr-2">
                          {item.direction === "credit"
                            ? money(item.amount, item.currency)
                            : "—"}
                        </td>
                        <td className="py-1 pr-2">
                          {money(item.running_balance, item.currency)}
                        </td>
                        <td className="py-1 pr-2">{item.currency || "—"}</td>
                        <td className="py-1 pr-2">{item.page ?? "—"}</td>
                        <td className="py-1 pr-2">
                          {item.extraction_status || "NEEDS_REVIEW"}
                        </td>
                        <td className="py-1">{item.evidence || "—"}</td>
                      </tr>
                    ))}
                    {!visible.length && (
                      <tr>
                        <td colSpan="9" className="py-3 text-slate-500">
                          No matching transactions.
                        </td>
                      </tr>
                    )}
                  </tbody>
                </table>
              </div>
              <div className="mt-3 flex items-center justify-between text-xs">
                <span>
                  {filtered.length
                    ? `Transaction page ${currentPage + 1} of ${pages}`
                    : "No results"}
                </span>
                <div className="flex gap-2">
                  <button
                    type="button"
                    disabled={!currentPage}
                    onClick={() => setPage(currentPage - 1)}
                    className="rounded border px-3 py-1 disabled:opacity-40"
                  >
                    Previous
                  </button>
                  <button
                    type="button"
                    disabled={!pages || currentPage >= pages - 1}
                    onClick={() => setPage(currentPage + 1)}
                    className="rounded border px-3 py-1 disabled:opacity-40"
                  >
                    Next
                  </button>
                </div>
              </div>
            </details>
          )}
        </div>
      )}
      <p className="text-xs text-slate-500">{analysis.disclaimer}</p>
    </section>
  );
}
