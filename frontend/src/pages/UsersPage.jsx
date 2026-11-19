import { useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import { api, useSecureData } from "./pageUtils";

const ROLES = ["ADMIN", "STAFF", "CUSTOMER"];
const STATUSES = ["ACTIVE", "INACTIVE", "SUSPENDED"];

function getUserStatus(user) {
  return String(
    user.status || (user.isActive === false ? "INACTIVE" : "ACTIVE"),
  ).toUpperCase();
}

function roleLabel(role, t) {
  const translationKey = {
    ADMIN: "adminRole",
    STAFF: "staffRole",
    CUSTOMER: "customerRole",
  }[role];
  return translationKey ? t(translationKey) : role;
}

export default function UsersPage({ profile = {} }) {
  const { t, i18n } = useTranslation();
  const {
    data: loadedUsers,
    loading,
    error: loadError,
  } = useSecureData(async () => {
    const response = await api.get("/users");
    return Array.isArray(response.data.users) ? response.data.users : [];
  }, []);
  const [usersOverride, setUsersOverride] = useState(null);
  const users = usersOverride ?? loadedUsers;
  const [edits, setEdits] = useState({});
  const [search, setSearch] = useState("");
  const [roleFilter, setRoleFilter] = useState("ALL");
  const [savingUserId, setSavingUserId] = useState("");
  const [actionError, setActionError] = useState("");
  const [notice, setNotice] = useState("");
  const error = actionError || loadError;

  const counts = useMemo(
    () =>
      ROLES.reduce(
        (result, role) => {
          result[role] = users.filter(
            (user) => String(user.role || "CUSTOMER").toUpperCase() === role,
          ).length;
          return result;
        },
        { ADMIN: 0, STAFF: 0, CUSTOMER: 0 },
      ),
    [users],
  );

  const filteredUsers = useMemo(() => {
    const query = search.trim().toLowerCase();
    return users.filter((user) => {
      const role = String(user.role || "CUSTOMER").toUpperCase();
      const matchesRole = roleFilter === "ALL" || role === roleFilter;
      const matchesSearch =
        !query ||
        [user.name, user.fullName, user.email, user.phone, role]
          .filter(Boolean)
          .some((value) => String(value).toLowerCase().includes(query));
      return matchesRole && matchesSearch;
    });
  }, [users, search, roleFilter]);

  const updateDraft = (user, field, value) => {
    const id = String(user._id || user.email);
    setEdits((current) => ({
      ...current,
      [id]: {
        role: String(user.role || "CUSTOMER").toUpperCase(),
        status: getUserStatus(user),
        ...current[id],
        [field]: value,
      },
    }));
    setActionError("");
    setNotice("");
  };

  const saveUser = async (user) => {
    const id = String(user._id || user.email);
    const draft = edits[id];
    if (!draft || savingUserId) return;
    setSavingUserId(id);
    setActionError("");
    setNotice("");
    try {
      const response = await api.put(`/users/${encodeURIComponent(id)}`, draft);
      const updatedUser = response.data.user;
      setUsersOverride((current) =>
        (current ?? loadedUsers).map((item) =>
          String(item._id || item.email) === id ? updatedUser : item,
        ),
      );
      setEdits((current) => {
        const next = { ...current };
        delete next[id];
        return next;
      });
      setNotice(t("userUpdated"));
    } catch (requestError) {
      setActionError(
        requestError?.response?.data?.message ||
          requestError?.message ||
          t("userUpdateFailed"),
      );
    } finally {
      setSavingUserId("");
    }
  };

  const locale = i18n.language === "kh" ? "km-KH" : "en-US";

  return (
    <div className="space-y-6">
      <header className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="mt-2 text-3xl font-bold text-slate-900">
            {t("users")}
          </h1>
          <p className="mt-1 text-sm text-slate-600">
            {t("userManagementDescription")}
          </p>
        </div>
      </header>

      <section className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
        <article className="rounded-xl border border-slate-200 bg-white p-4">
          <p className="text-xs text-slate-500">{t("allUsers")}</p>
          <p className="mt-1 text-2xl font-bold text-slate-900">
            {users.length}
          </p>
        </article>
        {ROLES.map((role) => (
          <article
            key={role}
            className="rounded-xl border border-slate-200 bg-white p-4"
          >
            <p className="text-xs text-slate-500">{roleLabel(role, t)}</p>
            <p className="mt-1 text-2xl font-bold text-slate-900">
              {counts[role]}
            </p>
          </article>
        ))}
      </section>

      {error && (
        <p
          role="alert"
          className="rounded-lg border border-rose-200 bg-rose-50 p-3 text-sm text-rose-700"
        >
          {error}
        </p>
      )}
      {notice && (
        <p
          role="status"
          className="rounded-lg border border-emerald-200 bg-emerald-50 p-3 text-sm text-emerald-700"
        >
          {notice}
        </p>
      )}

      <section className="overflow-hidden rounded-xl border border-slate-200 bg-white">
        <div className="grid gap-2 border-b border-slate-200 bg-slate-50 p-3 sm:grid-cols-[minmax(0,1fr)_180px]">
          <input
            type="search"
            value={search}
            onChange={(event) => setSearch(event.target.value)}
            placeholder={t("searchUsers")}
            aria-label={t("searchUsers")}
            className="min-w-0 rounded-lg border border-slate-200 bg-white px-3 py-2 text-sm"
          />
          <select
            value={roleFilter}
            onChange={(event) => setRoleFilter(event.target.value)}
            aria-label={t("filterByRole")}
            className="rounded-lg border border-slate-200 bg-white px-3 py-2 text-sm"
          >
            <option value="ALL">{t("allRoles")}</option>
            {ROLES.map((role) => (
              <option key={role} value={role}>
                {roleLabel(role, t)}
              </option>
            ))}
          </select>
        </div>
        <div className="overflow-x-auto">
          <table className="w-full min-w-[760px] text-left text-sm">
            <thead className="bg-white text-xs uppercase tracking-wide text-slate-500">
              <tr>
                <th className="px-4 py-3">{t("name")}</th>
                <th className="px-4 py-3">{t("role")}</th>
                <th className="px-4 py-3">{t("accountStatus")}</th>
                <th className="px-4 py-3">{t("created")}</th>
                <th className="px-4 py-3 text-right">{t("actions")}</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100">
              {filteredUsers.map((user) => {
                const id = String(user._id || user.email);
                const draft = edits[id] || {
                  role: String(user.role || "CUSTOMER").toUpperCase(),
                  status: getUserStatus(user),
                };
                const isSelf = id === String(profile._id || profile.id || "");
                const hasChanges =
                  draft.role !== String(user.role || "CUSTOMER").toUpperCase() ||
                  draft.status !== getUserStatus(user);
                return (
                  <tr key={id} className="hover:bg-slate-50">
                    <td className="px-4 py-3">
                      <p className="font-semibold text-slate-900">
                        {user.fullName || user.name || user.email || "—"}
                      </p>
                      <p className="mt-0.5 text-xs text-slate-500">
                        {user.email || "—"}
                      </p>
                    </td>
                    <td className="px-4 py-3">
                      <select
                        value={draft.role}
                        onChange={(event) =>
                          updateDraft(user, "role", event.target.value)
                        }
                        disabled={isSelf || savingUserId === id}
                        aria-label={`${t("role")}: ${user.email || id}`}
                        className="rounded-lg border border-slate-200 bg-white px-2 py-1.5 text-sm disabled:bg-slate-100"
                      >
                        {ROLES.map((role) => (
                          <option key={role} value={role}>
                            {roleLabel(role, t)}
                          </option>
                        ))}
                      </select>
                    </td>
                    <td className="px-4 py-3">
                      <select
                        value={draft.status}
                        onChange={(event) =>
                          updateDraft(user, "status", event.target.value)
                        }
                        disabled={isSelf || savingUserId === id}
                        aria-label={`${t("accountStatus")}: ${user.email || id}`}
                        className="rounded-lg border border-slate-200 bg-white px-2 py-1.5 text-sm disabled:bg-slate-100"
                      >
                        {STATUSES.map((status) => (
                          <option key={status} value={status}>
                            {t(status.toLowerCase())}
                          </option>
                        ))}
                      </select>
                    </td>
                    <td className="px-4 py-3 text-xs text-slate-600">
                      {user.createdAt
                        ? new Date(user.createdAt).toLocaleDateString(locale)
                        : "—"}
                    </td>
                    <td className="px-4 py-3 text-right">
                      <button
                        type="button"
                        disabled={!hasChanges || isSelf || savingUserId === id}
                        onClick={() => void saveUser(user)}
                        className="rounded-lg bg-teal-700 px-3 py-1.5 text-xs font-semibold text-white hover:bg-teal-800 disabled:cursor-not-allowed disabled:bg-slate-300"
                      >
                        {savingUserId === id ? t("saving") : t("saveChanges")}
                      </button>
                    </td>
                  </tr>
                );
              })}
              {!loading && filteredUsers.length === 0 && (
                <tr>
                  <td
                    colSpan={5}
                    className="px-4 py-10 text-center text-sm text-slate-500"
                  >
                    {users.length ? t("noMatchingUsers") : t("noUsers")}
                  </td>
                </tr>
              )}
              {loading && (
                <tr>
                  <td
                    colSpan={5}
                    className="px-4 py-10 text-center text-sm text-slate-500"
                  >
                    {t("loading")}
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
        <div className="border-t border-slate-100 px-4 py-3 text-xs text-slate-500">
          {t("usersShown", { shown: filteredUsers.length, total: users.length })}
        </div>
      </section>
    </div>
  );
}
