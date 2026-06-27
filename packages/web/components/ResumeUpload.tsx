"use client";

import { useState } from "react";

export default function ResumeUpload({
  onSubmit,
  disabled,
}: {
  onSubmit: (file: File) => void;
  disabled: boolean;
}) {
  const [file, setFile] = useState<File | null>(null);

  return (
    <div className="rounded-xl border border-gray-200 bg-white p-6 shadow-sm">
      <h2 className="text-base font-semibold text-gray-900">Upload your resume</h2>
      <p className="mt-1 text-sm text-gray-500">
        PDF, DOCX, or TXT. We&apos;ll match you against live job postings and show
        only the ones you qualify for, with cited evidence.
      </p>

      <div className="mt-4 flex flex-col gap-3 sm:flex-row sm:items-center">
        <input
          type="file"
          accept=".pdf,.docx,.txt"
          disabled={disabled}
          onChange={(e) => setFile(e.target.files?.[0] ?? null)}
          className="block w-full text-sm text-gray-700 file:mr-4 file:rounded-md file:border-0 file:bg-gray-900 file:px-4 file:py-2 file:text-sm file:font-medium file:text-white hover:file:bg-gray-700 disabled:opacity-50 file:disabled:bg-gray-400"
        />
        <button
          type="button"
          disabled={disabled || !file}
          onClick={() => {
            if (file) onSubmit(file);
          }}
          className="shrink-0 rounded-md bg-blue-600 px-4 py-2 text-sm font-medium text-white shadow-sm transition hover:bg-blue-500 disabled:cursor-not-allowed disabled:bg-gray-300"
        >
          {disabled ? "Matching…" : "Find matching jobs"}
        </button>
      </div>

      {file && (
        <p className="mt-3 text-sm text-gray-600">
          Selected: <span className="font-medium text-gray-900">{file.name}</span>
        </p>
      )}
    </div>
  );
}
