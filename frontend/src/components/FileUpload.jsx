import { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";

/** A requirement-aware uploader; upload receives the chosen File. */
export default function FileUpload({
  acceptedFormats = ["pdf", "jpg", "jpeg", "png"],
  onUpload,
  disabled,
  label = "addFile",
  registerTrigger,
  maxFileSize = 10 * 1024 * 1024,
  capture,
}) {
  const { t } = useTranslation();
  const input = useRef(null);
  const [progress, setProgress] = useState(0);
  const [error, setError] = useState("");
  const selectFile = async (event) => {
    const file = event.target.files?.[0];
    if (!file) return;
    const extension = file.name.split(".").pop()?.toLowerCase();
    if (
      (!acceptedFormats.includes(extension) &&
        !(extension === "jpg" && acceptedFormats.includes("jpeg")) &&
        !(extension === "jpeg" && acceptedFormats.includes("jpg"))) ||
      file.size > maxFileSize
    ) {
      setError(
        `${t("useAcceptedFile")} (${acceptedFormats.join(", ").toUpperCase()}) ${t("upTo")} ${(maxFileSize / 1024 / 1024).toFixed(1)} ${t("mb")}.`,
      );
      return;
    }
    setError("");
    setProgress(25);
    try {
      await onUpload(file, setProgress);
      setProgress(100);
    } catch (uploadError) {
      setError(uploadError?.message || t("uploadFailed"));
    } finally {
      event.target.value = "";
      setTimeout(() => setProgress(0), 800);
    }
  };
  useEffect(() => {
    if (!registerTrigger) return undefined;
    registerTrigger(() => input.current?.click());
    return () => registerTrigger(null);
  }, [registerTrigger]);
  return (
    <div>
      <input
        ref={input}
        className="hidden"
        type="file"
        accept={acceptedFormats.map((format) => `.${format}`).join(",")}
        capture={capture}
        onChange={selectFile}
      />
      <button
        type="button"
        disabled={disabled}
        onClick={() => input.current?.click()}
        className="min-h-11 rounded-lg border border-blue-200 px-3 py-2 text-sm font-medium text-blue-700 hover:bg-blue-50 disabled:cursor-not-allowed disabled:opacity-50"
      >
        {progress > 0 && progress < 100 ? `${t("uploading")} ${progress}%` : t(label)}
      </button>
      {progress > 0 && (
        <div className="mt-2 h-1.5 w-32 overflow-hidden rounded bg-slate-200" role="progressbar" aria-valuenow={progress} aria-valuemin="0" aria-valuemax="100" aria-label={t("uploading")}>
          <div
            className="h-full bg-blue-600"
            style={{ width: `${progress}%` }}
          />
        </div>
      )}
      {error && (
        <p role="alert" className="mt-1 text-xs text-red-600">
          {error}
        </p>
      )}
    </div>
  );
}
