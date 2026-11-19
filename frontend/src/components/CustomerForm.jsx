import { useState } from "react";
import { useTranslation } from "react-i18next";

const emptyCustomer = {
  fullName: "",
  email: "",
  phone: "",
  nationality: "",
  address: "",
  passportNumber: "",
  dateOfBirth: "",
};

/** Reusable customer picker and creator. onCustomerCreated receives the saved customer. */
export default function CustomerForm({
  customers = [],
  value,
  onSelect,
  onCustomerCreated,
  api,
  disabled,
}) {
  const { t } = useTranslation();
  const [mode, setMode] = useState("existing");
  const [form, setForm] = useState(emptyCustomer);
  const [error, setError] = useState("");
  const [saving, setSaving] = useState(false);
  const createCustomer = async (event) => {
    event.preventDefault();
    setSaving(true);
    setError("");
    try {
      const response = await api.post("/customers", form);
      onCustomerCreated?.(response.data.customer);
      setForm(emptyCustomer);
      setMode("existing");
    } catch (requestError) {
      setError(
        requestError?.response?.data?.message || t("unableCreateCustomer"),
      );
    } finally {
      setSaving(false);
    }
  };
  return (
    <section className="rounded-xl border border-slate-200 bg-slate-50 p-4">
      <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
        <h3 className="font-semibold text-slate-900">{t("newCustomer")}</h3>
        <div className="flex rounded-lg border border-slate-200 bg-white p-1 text-sm">
          <button
            type="button"
            onClick={() => setMode("existing")}
            className={`rounded px-3 py-1 ${mode === "existing" ? "bg-blue-600 text-white" : "text-slate-600"}`}
          >
            {t("existingCustomer")}
          </button>
          <button
            type="button"
            onClick={() => setMode("new")}
            className={`rounded px-3 py-1 ${mode === "new" ? "bg-blue-600 text-white" : "text-slate-600"}`}
          >
            {t("createNewCustomer")}
          </button>
        </div>
      </div>
      {mode === "existing" ? (
        <select
          required
          disabled={disabled}
          aria-label={t("customer")}
          className="w-full rounded-xl border border-slate-200 bg-white px-3 py-2"
          value={value}
          onChange={(event) => onSelect(event.target.value)}
        >
          <option value="">{t("selectCustomer")}</option>
          {customers.map((customer) => (
            <option key={customer._id} value={customer._id}>
              {customer.fullName} — {customer.passportNumber || customer.email}
            </option>
          ))}
        </select>
      ) : (
        <form onSubmit={createCustomer} className="grid gap-3 sm:grid-cols-2">
          {[
            ["fullName", "fullNameTitle", true],
            ["email", "emailTitle"],
            ["phone", "phoneTitle"],
            ["nationality", "nationalityTitle"],
            ["passportNumber", "passportNumberTitle", true],
            ["dateOfBirth", "dateOfBirthTitle"],
          ].map(([key, label, required]) => (
            <label key={key} className="text-sm text-slate-600">
              <span className="mb-1 block">{t(label)}</span>
              <input
                required={required}
                type={
                  key === "dateOfBirth"
                    ? "date"
                    : key === "email"
                      ? "email"
                      : "text"
                }
                className="w-full rounded-xl border border-slate-200 bg-white px-3 py-2"
                value={form[key]}
                onChange={(event) =>
                  setForm((current) => ({
                    ...current,
                    [key]: event.target.value,
                  }))
                }
              />
            </label>
          ))}
          <label className="sm:col-span-2 text-sm text-slate-600">
            <span className="mb-1 block">{t("addressTitle")}</span>
            <input
              className="w-full rounded-xl border border-slate-200 bg-white px-3 py-2"
              value={form.address}
              onChange={(event) =>
                setForm((current) => ({
                  ...current,
                  address: event.target.value,
                }))
              }
            />
          </label>
          <div className="sm:col-span-2">
            <button
              disabled={saving || disabled}
              className="rounded-xl bg-blue-600 px-4 py-2 text-sm font-medium text-white disabled:opacity-50"
            >
              {saving ? t("creating") : t("createSelectCustomer")}
            </button>
            {error && (
              <span role="alert" className="ml-3 text-sm text-red-600">
                {error}
              </span>
            )}
          </div>
        </form>
      )}
    </section>
  );
}
