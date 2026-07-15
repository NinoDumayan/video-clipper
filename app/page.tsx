"use client";

import { useState, useCallback, useRef } from "react";

type Clip = {
  index: number;
  title: string;
  start_time: number;
  end_time: number;
  virality_score: number;
  reason: string;
};

type JobStatusData = {
  job_id: string;
  status: "uploading" | "transcribing" | "analyzing" | "rendering" | "complete" | "error";
  progress: number;
  message: string;
  clips: Clip[];
  error: string | null;
  original_filename: string;
  duration: number;
};

type InputMode = "file" | "youtube";

const YOUTUBE_REGEX = /^(https?:\/\/)?(www\.)?(youtube\.com|youtu\.be)\//;

const STATUS_STEPS = ["uploading", "transcribing", "analyzing", "rendering"] as const;
const STATUS_LABELS: Record<string, string> = {
  uploading: "Uploading",
  transcribing: "Transcribing",
  analyzing: "Analyzing",
  rendering: "Rendering",
};

export default function HomePage() {
  const [token, setToken] = useState<string | null>(() => {
    if (typeof window !== "undefined") return localStorage.getItem("token");
    return null;
  });

  if (!token) return <LoginScreen onLogin={(t) => { localStorage.setItem("token", t); setToken(t); }} />;
  return <MainApp token={token} onLogout={() => { localStorage.removeItem("token"); setToken(null); }} />;
}


function LoginScreen({ onLogin }: { onLogin: (token: string) => void }) {
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);

  const handleLogin = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const res = await fetch("/api/login", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ password }),
      });
      if (!res.ok) throw new Error("Invalid password");
      const data = await res.json();
      onLogin(data.token);
    } catch {
      setError("Invalid password");
    } finally {
      setLoading(false);
    }
  }, [password, onLogin]);

  return (
    <main className="mx-auto flex min-h-screen max-w-sm flex-col items-center justify-center px-4">
      <div className="w-full rounded-lg border border-zinc-800 bg-zinc-900/60 p-6">
        <h1 className="mb-1 text-center text-xl font-bold text-zinc-100">Clipper AI</h1>
        <p className="mb-6 text-center text-sm text-zinc-500">Enter password to continue</p>
        <input
          type="password"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && handleLogin()}
          placeholder="Password"
          className="mb-3 w-full rounded-lg border border-zinc-700 bg-zinc-800 px-3.5 py-2.5 text-sm text-zinc-200 placeholder-zinc-600 outline-none focus:border-violet-500 focus:ring-2 focus:ring-violet-500/40"
        />
        {error && <p className="mb-3 text-sm text-red-400">{error}</p>}
        <button
          onClick={handleLogin}
          disabled={loading}
          className="w-full rounded-lg bg-violet-600 px-4 py-2 text-sm font-medium text-white transition-colors hover:bg-violet-500 disabled:opacity-50"
        >
          {loading ? "Signing in..." : "Sign In"}
        </button>
      </div>
    </main>
  );
}


function MainApp({ token, onLogout }: { token: string; onLogout: () => void }) {
  const [mode, setMode] = useState<InputMode>("file");
  const [file, setFile] = useState<File | null>(null);
  const [youtubeUrl, setYoutubeUrl] = useState("");
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [jobId, setJobId] = useState<string | null>(null);
  const [status, setStatus] = useState<JobStatusData["status"] | "idle">("idle");
  const [message, setMessage] = useState("Upload a video or paste a YouTube link to get started");
  const [clips, setClips] = useState<Clip[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [renderState, setRenderState] = useState<Record<number, "idle" | "rendering" | "error" | "cancelled">>({});
  const [renderIds, setRenderIds] = useState<Record<number, string>>({});
  const [clipAspectRatio, setClipAspectRatio] = useState<Record<number, string>>({});
  const [clipAutoReframe, setClipAutoReframe] = useState<Record<number, boolean>>({});
  const [isDragOver, setIsDragOver] = useState(false);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  const urlRef = useRef<HTMLInputElement>(null);

  const authFetch = useCallback(
    (url: string, opts: RequestInit = {}) => fetch(url, {
      ...opts,
      headers: { ...opts.headers, "Authorization": `Bearer ${token}` },
    }),
    [token]
  );

  const stopPolling = useCallback(() => {
    if (pollRef.current) {
      clearInterval(pollRef.current);
      pollRef.current = null;
    }
  }, []);

  const pollJob = useCallback(
    (id: string) => {
      stopPolling();
      pollRef.current = setInterval(async () => {
        try {
          const res = await authFetch(`/api/jobs/${id}`);
          if (!res.ok) throw new Error(await res.text());
          const data: JobStatusData = await res.json();
          setStatus(data.status);
          setMessage(data.message);
          setClips(data.clips);
          if (data.error) {
            setError(data.error);
            stopPolling();
          }
          if (data.status === "complete" || data.status === "error") stopPolling();
        } catch (err) {
          stopPolling();
          setError(err instanceof Error ? err.message : "Status check failed");
          setStatus("error");
        }
      }, 1000);
    },
    [stopPolling, authFetch]
  );

  const reset = useCallback(() => {
    stopPolling();
    setFile(null);
    setJobId(null);
    setStatus("idle");
    setMessage("Upload a video or paste a YouTube link to get started");
    setClips([]);
    setError(null);
    setRenderState({});
    setRenderIds({});
  }, [stopPolling]);

  const handleUpload = useCallback(
    async (videoFile: File) => {
      if (!videoFile.name.toLowerCase().endsWith(".mp4")) {
        setError("Only .mp4 files are supported");
        return;
      }
      setFile(videoFile);
      setError(null);
      setClips([]);
      setStatus("uploading");
      setMessage("Uploading...");
      const formData = new FormData();
      formData.append("file", videoFile);
      try {
        const res = await authFetch("/api/upload-video", { method: "POST", body: formData });
        if (!res.ok) throw new Error(await res.text());
        const data = await res.json();
        setJobId(data.job_id);
        pollJob(data.job_id);
      } catch (err) {
        setError(err instanceof Error ? err.message : "Upload failed");
        setStatus("error");
      }
    },
    [pollJob, authFetch]
  );

  const handleYoutubeSubmit = useCallback(async () => {
    const url = youtubeUrl.trim();
    if (!url || isSubmitting) return;
    setIsSubmitting(true);
    setError(null);
    setClips([]);
    setStatus("uploading");
    setMessage("Queued...");
    try {
      const res = await authFetch("/api/process-youtube", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ url }),
      });
      if (!res.ok) throw new Error(await res.text());
      const data = await res.json();
      setJobId(data.job_id);
      pollJob(data.job_id);
    } catch (err) {
      setError(err instanceof Error ? err.message : "YouTube processing failed");
      setStatus("error");
    } finally {
      setIsSubmitting(false);
    }
  }, [youtubeUrl, pollJob, isSubmitting, authFetch]);

  const cancelRender = useCallback(
    async (clipIdx: number) => {
      const rid = renderIds[clipIdx];
      if (!rid) return;
      try {
        await authFetch(`/api/render-cancel/${rid}`, { method: "POST" });
      } catch {}
      setRenderState((prev) => ({ ...prev, [clipIdx]: "cancelled" }));
      setMessage("Render cancelled");
    },
    [renderIds, authFetch]
  );

  const handleRender = useCallback(
    async (clip: Clip) => {
      if (!jobId) return;
      renderIds[clip.index] = "";
      setRenderState((prev) => ({ ...prev, [clip.index]: "rendering" }));
      setError(null);
      setMessage(`Rendering ${clip.title}...`);
      try {
        const res = await authFetch("/api/render-clip", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            job_id: jobId,
            clip_index: clip.index,
            include_subtitles: true,
            aspect_ratio: clipAspectRatio[clip.index] || "original",
            auto_reframe: clipAutoReframe[clip.index] || false,
          }),
        });
        if (!res.ok) throw new Error(await res.text());
        const { render_id } = await res.json();
        setRenderIds((prev) => ({ ...prev, [clip.index]: render_id }));

        const poll = setInterval(async () => {
          try {
            const r = await authFetch(`/api/render-status/${render_id}`);
            if (!r.ok) throw new Error(await r.text());
            const rs = await r.json();
            if (rs.status === "complete") {
              clearInterval(poll);
              const link = document.createElement("a");
              link.href = rs.download_url;
              link.download = `${clip.title.replace(/\s+/g, "_")}.mp4`;
              document.body.appendChild(link);
              link.click();
              document.body.removeChild(link);
              setMessage(`Rendered: ${clip.title}`);
              setRenderState((prev) => ({ ...prev, [clip.index]: "idle" }));
            } else if (rs.status === "error" || rs.status === "cancelled") {
              clearInterval(poll);
              setRenderState((prev) => ({ ...prev, [clip.index]: rs.status }));
              if (rs.status === "error") setError(rs.error || "Render failed");
              else setMessage("Render cancelled");
            } else {
              setMessage(rs.message);
            }
          } catch {
            clearInterval(poll);
            setRenderState((prev) => ({ ...prev, [clip.index]: "error" }));
          }
        }, 1000);
      } catch (err) {
        setRenderState((prev) => ({ ...prev, [clip.index]: "error" }));
        setError(err instanceof Error ? err.message : "Render failed");
      }
    },
    [jobId, clipAspectRatio, clipAutoReframe, authFetch]
  );

  const handleDrop = useCallback(
    (e: React.DragEvent<HTMLDivElement>) => {
      e.preventDefault();
      setIsDragOver(false);
      const f = e.dataTransfer.files[0];
      if (f) {
        setMode("file");
        handleUpload(f);
      }
    },
    [handleUpload]
  );

  const handlePaste = useCallback((e: React.ClipboardEvent) => {
    const text = e.clipboardData.getData("text");
    if (YOUTUBE_REGEX.test(text.trim())) {
      setMode("youtube");
      setYoutubeUrl(text.trim());
      setTimeout(() => urlRef.current?.focus(), 0);
    }
  }, []);

  const showInput = status === "idle";
  const urlValid = youtubeUrl.trim().length === 0 || YOUTUBE_REGEX.test(youtubeUrl.trim());
  const currentStepIdx = STATUS_STEPS.indexOf(status as (typeof STATUS_STEPS)[number]);

  return (
    <main className="mx-auto min-h-screen max-w-xl px-4 py-12" onPaste={handlePaste}>
      <div className="mb-8 text-center">
        <div className="flex items-center justify-center gap-3">
          <h1 className="text-2xl font-bold tracking-tight text-zinc-100">
            Clipper AI
          </h1>
          <button
            onClick={onLogout}
            className="rounded-md border border-zinc-700 px-2.5 py-1 text-xs text-zinc-500 transition-colors hover:border-zinc-600 hover:text-zinc-400"
            title="Logout"
          >
            Logout
          </button>
        </div>
        <p className="mt-1.5 text-sm text-zinc-500">
          AI-powered video clipping &mdash; upload or paste a YouTube link
        </p>
      </div>

      <div className="rounded-lg border border-zinc-800 bg-zinc-900/60 p-5">
        {showInput && (
          <div className="mb-5 flex gap-1 rounded-lg bg-zinc-800/50 p-0.5">
            <button
              onClick={() => setMode("file")}
              className={`flex-1 rounded-md px-3 py-1.5 text-sm font-medium transition-colors ${
                mode === "file"
                  ? "bg-zinc-900 text-zinc-100 shadow-sm"
                  : "text-zinc-500 hover:text-zinc-300"
              }`}
            >
              Upload File
            </button>
            <button
              onClick={() => {
                setMode("youtube");
                setTimeout(() => urlRef.current?.focus(), 50);
              }}
              className={`flex-1 rounded-md px-3 py-1.5 text-sm font-medium transition-colors ${
                mode === "youtube"
                  ? "bg-zinc-900 text-zinc-100 shadow-sm"
                  : "text-zinc-500 hover:text-zinc-300"
              }`}
            >
              YouTube Link
            </button>
          </div>
        )}

        {showInput && mode === "file" ? (
          <div
            key="file-upload"
            onDrop={handleDrop}
            onDragOver={(e) => { e.preventDefault(); setIsDragOver(true); }}
            onDragLeave={() => setIsDragOver(false)}
            onClick={() => inputRef.current?.click()}
            className={`flex cursor-pointer flex-col items-center justify-center rounded-lg border-2 border-dashed px-6 py-12 transition-colors ${
              isDragOver
                ? "border-violet-500 bg-violet-500/10"
                : "border-zinc-700 hover:border-zinc-600"
            }`}
          >
            <input
              ref={inputRef}
              type="file"
              accept=".mp4"
              className="hidden"
              onChange={(e) => {
                const f = e.target.files?.[0];
                if (f) handleUpload(f);
              }}
            />
            {file ? (
              <div className="text-center">
                <p className="text-sm font-medium text-zinc-200">{file.name}</p>
                <p className="mt-1 text-xs text-zinc-600">
                  {(file.size / 1_000_000).toFixed(1)} MB
                </p>
              </div>
            ) : (
              <>
                <svg className="mb-4 h-8 w-8 text-zinc-600" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M7 16a4 4 0 01-.88-7.903A5 5 0 1115.9 6L16 6a5 5 0 011 9.9M15 13l-3-3m0 0l-3 3m3-3v12" />
                </svg>
                <p className="text-sm text-zinc-500">Drop an .mp4 here, or click to browse</p>
                <p className="mt-2 text-xs text-zinc-700">Only .mp4 files supported</p>
              </>
            )}
          </div>
        ) : showInput && mode === "youtube" ? (
          <div key="youtube-url" className="space-y-3">
            <input
              ref={urlRef}
              type="url"
              value={youtubeUrl}
              onChange={(e) => setYoutubeUrl(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && handleYoutubeSubmit()}
              placeholder="https://youtube.com/watch?v=..."
              className={`w-full rounded-lg border bg-zinc-800 px-3.5 py-2.5 text-sm text-zinc-200 placeholder-zinc-600 outline-none transition-colors focus:ring-2 focus:ring-violet-500/40 ${
                youtubeUrl.trim() && !urlValid
                  ? "border-red-500/50"
                  : "border-zinc-700 focus:border-violet-500"
              }`}
            />
            <div className="flex items-center gap-3">
              <button
                onClick={handleYoutubeSubmit}
                disabled={!youtubeUrl.trim() || !urlValid || isSubmitting}
                className="rounded-lg bg-violet-600 px-4 py-2 text-sm font-medium text-white transition-colors hover:bg-violet-500 disabled:cursor-not-allowed disabled:opacity-40"
              >
                {isSubmitting ? "Queuing..." : "Process"}
              </button>
              <span className="text-sm text-zinc-600">
                Downloads, transcribes, and finds clips
              </span>
            </div>
            {youtubeUrl.trim() && !urlValid && (
              <p className="text-sm text-red-400">
                Please enter a valid YouTube URL (youtube.com or youtu.be)
              </p>
            )}
          </div>
        ) : null}

        {(status !== "idle" || error) && (
          <div className="space-y-5">
            <div className="rounded-lg border border-zinc-800 bg-zinc-900/80 p-4">
              {status !== "error" && (
                <div className="mb-4 flex items-center gap-2">
                  {STATUS_STEPS.map((step, i) => {
                    const done = status === "complete" || currentStepIdx > i;
                    const active = currentStepIdx === i;
                    return (
                      <div key={step} className="flex items-center gap-2">
                        <div
                          className={`flex h-6 w-6 items-center justify-center rounded-full text-xs font-medium ${
                            done
                              ? "bg-emerald-500/20 text-emerald-400"
                              : active
                                ? "bg-violet-500/20 text-violet-400"
                                : "bg-zinc-800 text-zinc-600"
                          }`}
                        >
                          {done ? "✓" : active ? "●" : "○"}
                        </div>
                        <span
                          className={`text-xs font-medium ${
                            done ? "text-emerald-400" : active ? "text-violet-400" : "text-zinc-600"
                          }`}
                        >
                          {STATUS_LABELS[step]}
                        </span>
                        {i < STATUS_STEPS.length - 1 && (
                          <div className={`h-px w-6 ${done || (active && i === currentStepIdx - 1) ? "bg-emerald-500/30" : "bg-zinc-800"}`} />
                        )}
                      </div>
                    );
                  })}
                </div>
              )}
              <div className="flex items-center justify-between gap-3">
                <p className="text-sm text-zinc-400">{message}</p>
                {(status === "uploading" || status === "transcribing" || status === "analyzing") && jobId && (
                  <button
                    onClick={async () => {
                      try { await authFetch(`/api/jobs/${jobId}/cancel`, { method: "POST" }); } catch {}
                      setStatus("error");
                      setMessage("Cancelled");
                    }}
                    className="shrink-0 rounded-md border border-red-500/40 px-2.5 py-1 text-xs font-medium text-red-400 transition-colors hover:bg-red-500/10"
                  >
                    Cancel
                  </button>
                )}
              </div>
              {error && (
                <div className="mt-3 rounded-md bg-red-500/10 px-3 py-2 text-sm text-red-400">
                  {error}
                </div>
              )}
              {status === "error" && (
                <button
                  onClick={reset}
                  className="mt-3 text-sm font-medium text-violet-400 hover:text-violet-300"
                >
                  Try again
                </button>
              )}
            </div>

            {clips.length > 0 && (
              <div className="space-y-3">
                <div className="flex items-center justify-between">
                  <h2 className="text-sm font-medium text-zinc-400">
                    Clips found &mdash; {clips.length}
                  </h2>
                  <button
                    onClick={reset}
                    className="text-sm text-zinc-600 transition-colors hover:text-zinc-400"
                  >
                    New video &rarr;
                  </button>
                </div>
                {clips.map((clip) => (
                  <div
                    key={clip.index}
                    className="rounded-lg border border-zinc-800 bg-zinc-900/40 px-4 py-3 transition-colors hover:border-zinc-700"
                  >
                    <div className="flex items-start justify-between gap-4">
                      <div className="min-w-0 flex-1">
                        <h3 className="truncate text-sm font-medium text-zinc-200">
                          {clip.title}
                        </h3>
                        <p className="mt-0.5 line-clamp-2 text-sm text-zinc-500">
                          {clip.reason}
                        </p>
                        <div className="mt-2 flex items-center gap-2 text-sm text-zinc-600">
                          <span>[{fmt(clip.start_time)} &ndash; {fmt(clip.end_time)}]</span>
                          <span className="text-zinc-700">|</span>
                          <span>{Math.round(clip.end_time - clip.start_time)}s</span>
                        </div>
                      </div>
                      <div className="flex shrink-0 flex-col items-end gap-2">
                        <span
                          className={`inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium ${
                            clip.virality_score >= 70
                              ? "bg-emerald-500/15 text-emerald-400"
                              : clip.virality_score >= 40
                                ? "bg-amber-500/15 text-amber-400"
                                : "bg-zinc-700/50 text-zinc-400"
                          }`}
                        >
                          {clip.virality_score}%
                        </span>
                        <select
                          value={clipAspectRatio[clip.index] || "original"}
                          onChange={(e) =>
                            setClipAspectRatio((prev) => ({
                              ...prev,
                              [clip.index]: e.target.value,
                            }))
                          }
                          className="w-full rounded border border-zinc-700 bg-zinc-800 px-2 py-1 text-xs text-zinc-300 outline-none focus:ring-1 focus:ring-violet-500"
                        >
                          <option value="original">Original</option>
                          <option value="9:16">9:16 TikTok/Reels</option>
                          <option value="16:9">16:9 Landscape</option>
                          <option value="1:1">1:1 Square</option>
                          <option value="4:5">4:5 Portrait</option>
                        </select>
                        <label className="flex items-center gap-1.5 text-xs text-zinc-500">
                          <input
                            type="checkbox"
                            checked={clipAutoReframe[clip.index] || false}
                            onChange={(e) =>
                              setClipAutoReframe((prev) => ({
                                ...prev,
                                [clip.index]: e.target.checked,
                              }))
                            }
                            className="accent-violet-500"
                          />
                          Auto-track
                        </label>
                        <div className="flex items-center gap-1.5">
                          {renderState[clip.index] === "rendering" && (
                            <button
                              onClick={() => cancelRender(clip.index)}
                              className="rounded-md border border-red-500/40 px-2.5 py-1.5 text-xs font-medium text-red-400 transition-colors hover:bg-red-500/10"
                            >
                              Cancel
                            </button>
                          )}
                          {renderState[clip.index] === "error" || renderState[clip.index] === "cancelled" ? (
                            <button
                              onClick={() => handleRender(clip)}
                              className="rounded-md bg-violet-600 px-3 py-1.5 text-xs font-medium text-white transition-colors hover:bg-violet-500"
                            >
                              Retry
                            </button>
                          ) : (
                            <button
                              disabled={renderState[clip.index] === "rendering"}
                              onClick={() => handleRender(clip)}
                              className="rounded-md bg-violet-600 px-3 py-1.5 text-xs font-medium text-white transition-colors hover:bg-violet-500 disabled:cursor-not-allowed disabled:opacity-50"
                            >
                              {renderState[clip.index] === "rendering" ? "Rendering..." : "Render"}
                            </button>
                          )}
                        </div>
                      </div>
                    </div>
                  </div>
                ))}
              </div>
            )}
          </div>
        )}
      </div>
    </main>
  );
}

function fmt(s: number): string {
  const m = Math.floor(s / 60);
  const sec = Math.floor(s % 60);
  return `${m}:${sec.toString().padStart(2, "0")}`;
}
