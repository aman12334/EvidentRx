/**
 * useUpload — React hook encapsulating the file upload state machine.
 *
 * Manages the full upload lifecycle: idle → dragging → uploading →
 * done | error. Exposes handlers for drag events and file selection,
 * a fake progress counter (animates while the API call is in-flight),
 * and the final UploadResult.
 */
"use client";

import { useCallback, useRef, useState } from "react";
import {
  uploadClaimsFile,
  validateUploadFile,
  type UploadResult,
} from "@/lib/api/upload";

export type UploadState = "idle" | "dragging" | "uploading" | "done" | "error";

export interface UseUploadReturn {
  state:       UploadState;
  progress:    number;           // 0–100
  result:      UploadResult | null;
  errorMsg:    string;
  filename:    string;
  onDragOver:  (e: React.DragEvent) => void;
  onDragLeave: () => void;
  onDrop:      (e: React.DragEvent) => void;
  onFileChange:(e: React.ChangeEvent<HTMLInputElement>) => void;
  reset:       () => void;
}

export function useUpload(coveredEntityId?: string): UseUploadReturn {
  const [state,    setState]    = useState<UploadState>("idle");
  const [progress, setProgress] = useState(0);
  const [result,   setResult]   = useState<UploadResult | null>(null);
  const [errorMsg, setErrorMsg] = useState("");
  const [filename, setFilename] = useState("");

  const progressRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const startProgress = () => {
    setProgress(0);
    progressRef.current = setInterval(() => {
      setProgress(p => {
        if (p >= 88) { clearInterval(progressRef.current!); return 88; }
        return p < 40 ? p + 6 : p < 70 ? p + 3 : p + 1;
      });
    }, 200);
  };

  const stopProgress = () => {
    clearInterval(progressRef.current!);
    setProgress(100);
  };

  const processFile = useCallback(async (file: File) => {
    const err = validateUploadFile(file);
    if (err) { setErrorMsg(err); setState("error"); return; }

    setFilename(file.name);
    setState("uploading");
    startProgress();

    try {
      const data = await uploadClaimsFile(file, coveredEntityId);
      stopProgress();
      setResult(data);
      setState("done");
    } catch (e: unknown) {
      stopProgress();
      setErrorMsg(e instanceof Error ? e.message : "Upload failed. Try again.");
      setState("error");
    }
  }, [coveredEntityId]);

  const onDragOver  = (e: React.DragEvent) => { e.preventDefault(); setState("dragging"); };
  const onDragLeave = () => setState("idle");
  const onDrop      = (e: React.DragEvent) => {
    e.preventDefault(); setState("idle");
    const file = e.dataTransfer.files?.[0];
    if (file) processFile(file);
  };
  const onFileChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (file) processFile(file);
  };
  const reset = () => {
    setState("idle"); setResult(null);
    setErrorMsg(""); setFilename(""); setProgress(0);
  };

  return { state, progress, result, errorMsg, filename, onDragOver, onDragLeave, onDrop, onFileChange, reset };
}
