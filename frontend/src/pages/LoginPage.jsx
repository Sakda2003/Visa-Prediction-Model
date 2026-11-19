import { useState } from "react";
import { useNavigate } from "react-router-dom";
import i18n from "i18next";
import { useTranslation } from "react-i18next";
import {
  Activity,
  Apple,
  ArrowRight,
  Eye,
  EyeOff,
  Globe2,
  LockKeyhole,
  Mail,
  Plane,
  ShieldCheck,
  Zap,
} from "lucide-react";
import { api } from "./pageUtils";

function LoginPage({ onLogin }) {
  const { t } = useTranslation();
  const [isRegister, setIsRegister] = useState(false);
  const [formData, setFormData] = useState({
    name: "",
    email: "",
    password: "",
  });
  const [error, setError] = useState("");
  const [info, setInfo] = useState("");
  const [rememberMe, setRememberMe] = useState(true);
  const [showPassword, setShowPassword] = useState(false);
  const navigate = useNavigate();

  const onSubmit = async (event) => {
    event.preventDefault();
    setError("");
    setInfo("");
    try {
      const endpoint = isRegister ? "/auth/register" : "/auth/login";
      const payload = isRegister
        ? formData
        : { email: formData.email, password: formData.password };
      const response = await api.post(endpoint, payload);
      const token = response?.data?.access_token || response?.data?.token;
      if (!token) {
        throw new Error("Authentication token missing from server response.");
      }
      localStorage.removeItem("visadoc_token");
      localStorage.removeItem("access_token");
      sessionStorage.removeItem("visadoc_token");
      sessionStorage.removeItem("access_token");
      const tokenStorage = rememberMe ? localStorage : sessionStorage;
      tokenStorage.setItem("visadoc_token", token);
      tokenStorage.setItem("access_token", token);
      localStorage.setItem("lang", i18n.language || "en");
      onLogin(response?.data?.user || {});
      navigate("/");
    } catch (err) {
      setError(err?.response?.data?.message || t("loginFailed"));
    }
  };
  const toggleLanguage = () => {
    const nextLanguage = i18n.language === "kh" ? "en" : "kh";
    i18n.changeLanguage(nextLanguage);
    localStorage.setItem("lang", nextLanguage);
  };

  return (
    <div className="workspace-ui theme-light workspace-login">
      <div className="workspace-login-frame">
        <section className="relative flex items-center justify-center bg-white px-6 py-8 sm:px-12 lg:px-14">
          <div className="absolute right-6 top-6">
            <button
              type="button"
              onClick={toggleLanguage}
              aria-label={t("language")}
              className="rounded-full border border-slate-200 bg-white px-3.5 py-1.5 text-xs font-semibold text-slate-600 shadow-sm transition hover:border-blue-300 hover:text-blue-700"
            >
              {i18n.language === "kh" ? "English" : "ខ្មែរ"}
            </button>
          </div>
          <div className="w-full max-w-[420px]">
            <div className="mb-7 text-center">
              <img
                src="/global-eagle-logo.jpg"
                alt="Global Eagle"
                className="mx-auto h-[76px] w-[76px] rounded-2xl bg-black object-contain shadow-[0_10px_25px_rgba(15,23,42,.16)]"
              />
              <h1 className="mt-5 text-3xl font-extrabold tracking-tight text-[#0b1c42]">
                {isRegister ? t("register") : t("login")}
              </h1>
              <p className="mt-2 text-sm text-slate-500">
                {t("signInToContinue")}
              </p>
            </div>

            <form onSubmit={onSubmit} className="space-y-4">
              {isRegister && (
                <label className="block text-sm font-medium text-slate-700">
                  <span className="mb-1.5 block">{t("fullName")}</span>
                  <input
                    required
                    autoComplete="name"
                    className="w-full rounded-xl border border-slate-200 bg-white px-4 py-3 text-slate-900 outline-none transition placeholder:text-slate-400 focus:border-blue-500 focus:ring-4 focus:ring-blue-100"
                    value={formData.name}
                    onChange={(event) =>
                      setFormData((prev) => ({
                        ...prev,
                        name: event.target.value,
                      }))
                    }
                  />
                </label>
              )}
              <label className="block text-sm font-medium text-slate-700">
                <span className="mb-1.5 block">{t("email")}</span>
                <span className="relative block">
                  <input
                    required
                    type="email"
                    autoComplete="email"
                    placeholder={
                      i18n.language === "kh"
                        ? "អាសយដ្ឋានអ៊ីមែល"
                        : "Email address"
                    }
                    className="w-full rounded-xl border border-slate-200 bg-white px-4 py-3 text-slate-900 outline-none transition placeholder:text-slate-400 focus:border-blue-500 focus:ring-4 focus:ring-blue-100"
                    value={formData.email}
                    onChange={(event) =>
                      setFormData((prev) => ({
                        ...prev,
                        email: event.target.value,
                      }))
                    }
                  />
                </span>
              </label>
              <label className="block text-sm font-medium text-slate-700">
                <span className="mb-1.5 block">{t("password")}</span>
                <span className="relative block">
                  <input
                    required
                    type={showPassword ? "text" : "password"}
                    autoComplete={
                      isRegister ? "new-password" : "current-password"
                    }
                    placeholder={t("password")}
                    className="w-full rounded-xl border border-slate-200 bg-white py-3 pl-11 pr-12 text-slate-900 outline-none transition placeholder:text-slate-400 focus:border-blue-500 focus:ring-4 focus:ring-blue-100"
                    value={formData.password}
                    onChange={(event) =>
                      setFormData((prev) => ({
                        ...prev,
                        password: event.target.value,
                      }))
                    }
                  />
                  <button
                    type="button"
                    onClick={() => setShowPassword((visible) => !visible)}
                    aria-label={
                      showPassword ? t("hidePassword") : t("showPassword")
                    }
                    className="absolute right-3 top-1/2 -translate-y-1/2 rounded-md p-1.5 text-slate-400 hover:text-slate-700"
                  >
                    {showPassword ? (
                      <EyeOff className="h-4 w-4" />
                    ) : (
                      <Eye className="h-4 w-4" />
                    )}
                  </button>
                </span>
              </label>

              {!isRegister && (
                <div className="flex flex-wrap items-center justify-between gap-3 text-xs">
                  <label className="inline-flex cursor-pointer items-center gap-2 text-slate-600">
                    <input
                      type="checkbox"
                      checked={rememberMe}
                      onChange={(event) => setRememberMe(event.target.checked)}
                      className="h-4 w-4 rounded border-slate-300 text-blue-700 focus:ring-blue-500"
                    />
                    {t("rememberMe")}
                  </label>
                  <button
                    type="button"
                    onClick={() => setInfo(t("passwordResetUnavailable"))}
                    className="font-semibold text-blue-700 hover:text-blue-900"
                  >
                    {t("forgotPassword")}
                  </button>
                </div>
              )}

              {error && (
                <div
                  role="alert"
                  className="rounded-xl border border-red-200 bg-red-50 px-3 py-2.5 text-sm text-red-700"
                >
                  {error}
                </div>
              )}
              {info && (
                <div
                  role="status"
                  className="rounded-xl border border-blue-200 bg-blue-50 px-3 py-2.5 text-xs text-blue-800"
                >
                  {info}
                </div>
              )}

              <button
                type="submit"
                className="group flex w-full items-center justify-center gap-2 rounded-xl bg-gradient-to-r from-blue-700 to-blue-500 px-4 py-3.5 font-semibold text-white shadow-lg shadow-blue-700/20 transition hover:from-blue-800 hover:to-blue-600 focus:outline-none focus:ring-4 focus:ring-blue-200"
              >
                {isRegister ? t("register") : t("login")}
                <ArrowRight
                  className="h-4 w-4 transition-transform group-hover:translate-x-1"
                  aria-hidden="true"
                />
              </button>
            </form>


            <p className="mt-6 text-center text-sm text-slate-500">
              {isRegister ? t("alreadyHaveAccount") : t("needAccount")}{" "}
              <button
                type="button"
                className="font-semibold text-blue-700 hover:text-blue-900"
                onClick={() => {
                  setError("");
                  setInfo("");
                  setIsRegister((prev) => !prev);
                }}
              >
                {isRegister ? t("login") : t("register")}
              </button>
            </p>
          </div>
        </section>
      </div>
    </div>
  );
}

export default LoginPage;
