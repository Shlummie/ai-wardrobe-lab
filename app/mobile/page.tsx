"use client";

import { ChangeEvent, FormEvent, useEffect, useMemo, useState } from "react";

type MobileSession = {
  active: boolean;
  expires_at?: string;
  seconds_remaining?: number;
  uploads?: number;
  reference_uploaded?: boolean;
};

type UploadedItem = {
  id: string;
  name: string;
  dominant_color: string;
  category: string;
};

type UploadRecord = {
  id: string;
  file: File;
  preview: string;
  category: string;
  state: "uploading" | "complete" | "failed";
  item?: UploadedItem;
  error?: string;
};

const categories = [
  ["top", "Tops"],
  ["bottom", "Bottoms"],
  ["one_piece", "One pieces"],
  ["shoes", "Shoes"],
  ["outerwear", "Outerwear"],
  ["accessory", "Accessories"],
] as const;

function apiBase() {
  if (typeof window === "undefined") return "";
  return `${window.location.protocol}//${window.location.hostname}:8765`;
}

async function responseError(response: Response) {
  try {
    const body = (await response.json()) as { detail?: string };
    return body.detail || `Request failed (${response.status})`;
  } catch {
    return `Request failed (${response.status})`;
  }
}

function createPreview(file: File) {
  try {
    return URL.createObjectURL(file);
  } catch {
    return "";
  }
}

function createUploadId() {
  return globalThis.crypto?.randomUUID?.() ??
    `${Date.now()}-${Math.random().toString(36).slice(2)}`;
}

export default function MobileIntake() {
  const [joinToken, setJoinToken] = useState("");
  const [accessToken, setAccessToken] = useState("");
  const [pin, setPin] = useState("");
  const [category, setCategory] = useState("top");
  const [phase, setPhase] = useState<"loading" | "pin" | "ready" | "done">("loading");
  const [session, setSession] = useState<MobileSession | null>(null);
  const [uploads, setUploads] = useState<UploadRecord[]>([]);
  const [busy, setBusy] = useState(false);
  const [referenceBusy, setReferenceBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const request = window.setTimeout(() => {
      const hash = new URLSearchParams(window.location.hash.replace(/^#/, ""));
      const incomingJoin = hash.get("join") || sessionStorage.getItem("aiwl-mobile-join") || "";
      const savedAccess = sessionStorage.getItem("aiwl-mobile-access") || "";
      const savedCategory = localStorage.getItem("aiwl-mobile-category") || "top";
      if (incomingJoin) sessionStorage.setItem("aiwl-mobile-join", incomingJoin);
      if (window.location.hash) history.replaceState(null, "", window.location.pathname);
      setJoinToken(incomingJoin);
      setCategory(savedCategory);
      if (!savedAccess) {
        setPhase("pin");
        return;
      }
      fetch(`${apiBase()}/api/mobile/status`, {
        headers: { Authorization: `Bearer ${savedAccess}` },
      })
        .then(async (response) => {
          if (!response.ok) throw new Error(await responseError(response));
          return response.json() as Promise<MobileSession>;
        })
        .then((nextSession) => {
          setAccessToken(savedAccess);
          setSession(nextSession);
          setPhase("ready");
        })
        .catch(() => {
          sessionStorage.removeItem("aiwl-mobile-access");
          setPhase("pin");
        });
    }, 0);
    return () => window.clearTimeout(request);
  }, []);

  const completed = uploads.filter((upload) => upload.state === "complete").length;
  const failed = uploads.filter((upload) => upload.state === "failed").length;
  const totalSaved = Math.max(session?.uploads ?? 0, completed);
  const progress = Math.min(100, (totalSaved / 20) * 100);
  const categoryLabel = useMemo(
    () => categories.find(([value]) => value === category)?.[1] ?? "Garments",
    [category],
  );

  function chooseCategory(next: string) {
    setCategory(next);
    localStorage.setItem("aiwl-mobile-category", next);
  }

  async function authenticate(event: FormEvent) {
    event.preventDefault();
    if (!joinToken) {
      setError("This QR link is incomplete. Start a new mobile session on the PC.");
      return;
    }
    setBusy(true);
    setError(null);
    try {
      const response = await fetch(`${apiBase()}/api/mobile/auth`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ join_token: joinToken, pin }),
      });
      if (!response.ok) throw new Error(await responseError(response));
      const body = (await response.json()) as {
        access_token: string;
        session: MobileSession;
      };
      sessionStorage.setItem("aiwl-mobile-access", body.access_token);
      sessionStorage.removeItem("aiwl-mobile-join");
      setAccessToken(body.access_token);
      setSession(body.session);
      setPin("");
      setPhase("ready");
      navigator.vibrate?.(35);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Could not join the session.");
    } finally {
      setBusy(false);
    }
  }

  async function uploadOne(file: File, existingId?: string, uploadCategory = category) {
    const id = existingId || createUploadId();
    if (!existingId) {
      setUploads((current) => [
        { id, file, preview: createPreview(file), category: uploadCategory, state: "uploading" },
        ...current,
      ]);
    } else {
      setUploads((current) =>
        current.map((upload) =>
          upload.id === id ? { ...upload, state: "uploading", error: undefined } : upload,
        ),
      );
    }
    const form = new FormData();
    form.set("file", file);
    form.set("category", uploadCategory);
    form.set("name", "");
    try {
      const response = await fetch(`${apiBase()}/api/mobile/items`, {
        method: "POST",
        headers: { Authorization: `Bearer ${accessToken}` },
        body: form,
      });
      if (!response.ok) throw new Error(await responseError(response));
      const body = (await response.json()) as {
        item: UploadedItem;
        session: MobileSession;
      };
      setSession(body.session);
      setUploads((current) =>
        current.map((upload) =>
          upload.id === id ? { ...upload, state: "complete", item: body.item } : upload,
        ),
      );
      navigator.vibrate?.(25);
    } catch (caught) {
      setUploads((current) =>
        current.map((upload) =>
          upload.id === id
            ? {
                ...upload,
                state: "failed",
                error: caught instanceof Error ? caught.message : "Upload failed.",
              }
            : upload,
        ),
      );
    }
  }

  async function handleGarments(event: ChangeEvent<HTMLInputElement>) {
    const files = Array.from(event.target.files || []);
    event.target.value = "";
    if (!files.length) return;
    setBusy(true);
    setError(null);
    for (const file of files) await uploadOne(file);
    setBusy(false);
  }

  async function retryUpload(upload: UploadRecord) {
    setBusy(true);
    await uploadOne(upload.file, upload.id, upload.category);
    setBusy(false);
  }

  async function uploadReference(event: ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0];
    event.target.value = "";
    if (!file) return;
    setReferenceBusy(true);
    setError(null);
    const form = new FormData();
    form.set("file", file);
    try {
      const response = await fetch(`${apiBase()}/api/mobile/reference`, {
        method: "POST",
        headers: { Authorization: `Bearer ${accessToken}` },
        body: form,
      });
      if (!response.ok) throw new Error(await responseError(response));
      const body = (await response.json()) as { session: MobileSession };
      setSession(body.session);
      navigator.vibrate?.([30, 30, 30]);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Reference upload failed.");
    } finally {
      setReferenceBusy(false);
    }
  }

  async function finish() {
    if (!accessToken) return;
    setBusy(true);
    try {
      await fetch(`${apiBase()}/api/mobile/finish`, {
        method: "POST",
        headers: { Authorization: `Bearer ${accessToken}` },
      });
    } finally {
      sessionStorage.removeItem("aiwl-mobile-access");
      sessionStorage.removeItem("aiwl-mobile-join");
      setPhase("done");
      setBusy(false);
    }
  }

  if (phase === "loading") {
    return <main className="mobile-intake mobile-centered"><div className="mobile-loader" /><p>Opening private intake...</p></main>;
  }

  if (phase === "done") {
    return (
      <main className="mobile-intake mobile-centered">
        <span className="mobile-complete-mark">✓</span>
        <p className="mobile-eyebrow">Session closed</p>
        <h1>Your closet is on the PC.</h1>
        <p className="mobile-muted">You can close this page and continue in AI Wardrobe Lab.</p>
      </main>
    );
  }

  if (phase === "pin") {
    return (
      <main className="mobile-intake mobile-auth">
        <header><span className="mobile-logo">AWL</span><span>Private closet intake</span></header>
        <section>
          <p className="mobile-eyebrow">Same Wi-Fi / local only</p>
          <h1>Enter the PIN shown on your PC.</h1>
          <p className="mobile-muted">Your photos upload directly to that computer. No cloud or OpenAI call is involved.</p>
          <form onSubmit={authenticate}>
            <input value={pin} onChange={(event) => setPin(event.target.value.replace(/\D/g, "").slice(0, 6))} inputMode="numeric" autoComplete="one-time-code" placeholder="000000" aria-label="Six-digit mobile intake PIN" />
            <button disabled={busy || pin.length !== 6}>{busy ? "Checking..." : "Open intake"}</button>
          </form>
          {error && <p className="mobile-error" role="alert">{error}</p>}
        </section>
      </main>
    );
  }

  return (
    <main className="mobile-intake">
      <header className="mobile-header">
        <div><span className="mobile-logo">AWL</span><span><strong>Closet intake</strong><small>Connected locally</small></span></div>
        <span className="mobile-count">{totalSaved}<small>/20+</small></span>
      </header>

      <section className="mobile-progress" aria-label={`${totalSaved} garments uploaded toward twenty`}>
        <span style={{ width: `${progress}%` }} />
      </section>

      <section className="mobile-category-section">
        <p className="mobile-eyebrow">1 / Keep one category selected</p>
        <div className="mobile-category-scroll">
          {categories.map(([value, label]) => (
            <button key={value} className={category === value ? "selected" : ""} onClick={() => chooseCategory(value)} disabled={busy}>{label}</button>
          ))}
        </div>
      </section>

      <section className="mobile-capture-section">
        <p className="mobile-eyebrow">2 / Add {categoryLabel.toLowerCase()}</p>
        <div className="mobile-capture-actions">
          <label className="mobile-camera-button">
            <span className="mobile-camera-icon" />
            <strong>{busy ? "Uploading..." : "Take next photo"}</strong>
            <small>Uploads immediately, then you can shoot another</small>
            <input type="file" accept="image/*,.heic,.heif" capture="environment" onChange={handleGarments} disabled={busy} />
          </label>
          <label className="mobile-library-button">
            <strong>Choose several from Photos</strong>
            <small>All selected photos use {categoryLabel}</small>
            <input type="file" accept="image/*,.heic,.heif" multiple onChange={handleGarments} disabled={busy} />
          </label>
        </div>
      </section>

      {(uploads.length > 0 || error) && (
        <section className="mobile-queue">
          <div className="mobile-section-heading"><strong>Today&apos;s queue</strong><span>{completed} saved{failed ? ` / ${failed} retry` : ""}</span></div>
          {error && <p className="mobile-error" role="alert">{error}</p>}
          <div className="mobile-upload-list">
            {uploads.slice(0, 12).map((upload) => (
              <article key={upload.id} className={upload.state}>
                <div>{upload.preview ? <img src={upload.preview} alt="" /> : <span />}</div>
                <span><strong>{upload.item?.name || upload.file.name}</strong><small>{upload.state === "complete" ? `${upload.item?.dominant_color} / ${upload.item?.category.replace("_", " ")}` : upload.error || "Uploading locally..."}</small></span>
                {upload.state === "failed" ? <button onClick={() => void retryUpload(upload)} disabled={busy}>Retry</button> : <i>{upload.state === "complete" ? "✓" : ""}</i>}
              </article>
            ))}
          </div>
        </section>
      )}

      <section className="mobile-reference-section">
        <div><p className="mobile-eyebrow">Optional / Body reference</p><h2>{session?.reference_uploaded ? "Reference saved" : "Add one full-body photo"}</h2><p>The original stays on your PC. A face-blurred copy is created automatically.</p></div>
        <label className={session?.reference_uploaded ? "complete" : ""}>
          {referenceBusy ? "Processing..." : session?.reference_uploaded ? "Replace" : "Take photo"}
          <input type="file" accept="image/*,.heic,.heif" capture="environment" onChange={uploadReference} disabled={referenceBusy || busy} />
        </label>
      </section>

      <section className="mobile-finish-section">
        <button onClick={() => void finish()} disabled={busy}>Finish and close intake</button>
        <p>The private session closes automatically after 15 minutes without an upload.</p>
      </section>
    </main>
  );
}
