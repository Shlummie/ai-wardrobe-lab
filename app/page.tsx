"use client";

import { FormEvent, useCallback, useEffect, useMemo, useRef, useState } from "react";

const API_BASE =
  process.env.NEXT_PUBLIC_AIWL_API_URL ?? "http://127.0.0.1:8765";

type Garment = {
  id: string;
  name: string;
  category: string;
  dominant_color: string;
  color_hex: string;
  media_url: string;
  available: boolean;
  last_worn: string | null;
  notes: string;
};

type ReferencePhoto = {
  id: string;
  media_url: string;
  privacy_media_url: string;
  privacy_method: string;
  faces_detected: number;
  created_at: string;
};

type TryOnResult = {
  status: string;
  media_url: string | null;
  privacy_media_url: string | null;
  reference_id: string | null;
  error: string | null;
};

type Outfit = {
  id: string;
  item_ids: string[];
  item_names: string[];
  local_score: number;
  board_media_url: string;
  tryon?: TryOnResult;
  rank?: number;
  overall?: number;
  fashionable?: number;
  confident?: number;
  approachable?: number;
  audience_fit?: number;
  consensus?: string;
  concerns?: string[];
};

type Evaluation = {
  id: string;
  audience: string;
  impression: string;
  model: string;
  panel_size: number;
  api_calls: number;
  cache_hit: boolean;
  created_at: string;
  outfits: Outfit[];
};

type Tournament = {
  id: string;
  audience: string;
  impression: string;
  model: string;
  mode: "staged" | "full_panel";
  protocol_version: string;
  status: "preparing" | "ready" | "running" | "paused" | "budget_stopped" | "complete";
  stage: "screening" | "semifinal" | "final" | "complete";
  total_outfits: number;
  ranked_outfits: number;
  screened_outfits: number;
  semifinalists: number;
  finalists: number;
  estimated_calls: number;
  completed_calls: number;
  estimated_cost_usd: number;
  budget_usd: number;
  stage_counts: Record<string, { complete: number; total: number }>;
  error: string | null;
  created_at: string;
  updated_at: string;
  completed_at: string | null;
  outfits: Outfit[];
  evaluation: Evaluation | null;
};

type Health = {
  status: string;
  openai_configured: boolean;
  openai_model: string;
  storage: string;
  focus_group: {
    default_panel_size: number;
    max_panel_size: number;
    tournament_budget_usd: number;
    tournament_protocol: string;
  };
  mobile: {
    lan_ip: string | null;
    available: boolean;
    session_active: boolean;
    session_uploads: number;
  };
  tryon: { ready: boolean; verified?: boolean; label: string };
};

type MobileAdminSession = {
  active: boolean;
  id?: string;
  lan_ip?: string;
  mobile_url?: string;
  qr_media_url?: string;
  pin?: string;
  expires_at?: string;
  seconds_remaining?: number;
  uploads?: number;
  reference_uploaded?: boolean;
};

type LocalModelResponse = {
  outfits: Outfit[];
  generated: number;
  cache_hits: number;
  note?: string;
  privacy?: string;
};

const categories = [
  ["top", "Top"],
  ["bottom", "Bottom"],
  ["one_piece", "One piece"],
  ["shoes", "Shoes"],
  ["outerwear", "Outerwear"],
  ["accessory", "Accessory"],
] as const;

function mediaUrl(path?: string | null) {
  if (!path) return "";
  return path.startsWith("http") ? path : `${API_BASE}${path}`;
}

async function api<T>(path: string, options?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, options);
  if (!response.ok) {
    let message = `Request failed (${response.status})`;
    try {
      const body = (await response.json()) as { detail?: string };
      if (body.detail) message = body.detail;
    } catch {
      // Keep the status-based fallback.
    }
    throw new Error(message);
  }
  return (await response.json()) as T;
}

function formatDate(value: string | null) {
  if (!value) return "Never";
  const parsed = new Date(`${value}T12:00:00`);
  return Number.isNaN(parsed.getTime())
    ? value
    : new Intl.DateTimeFormat("en-US", {
        month: "short",
        day: "numeric",
      }).format(parsed);
}

function scoreLabel(value?: number) {
  return typeof value === "number" ? value.toFixed(1) : "-";
}

function tournamentProgress(run: Tournament) {
  if (run.status === "complete") return 100;
  if (run.stage === "screening") {
    const screeningShare = run.mode === "full_panel" ? 100 : 80;
    return run.total_outfits
      ? (run.screened_outfits / run.total_outfits) * screeningShare
      : 0;
  }
  if (run.stage === "semifinal") {
    const counts = run.stage_counts.semifinal;
    return 80 + (counts?.total ? (counts.complete / counts.total) * 15 : 0);
  }
  if (run.stage === "final") {
    const counts = run.stage_counts.final;
    return 95 + (counts?.total ? (counts.complete / counts.total) * 5 : 0);
  }
  return 0;
}

export default function Home() {
  const [health, setHealth] = useState<Health | null>(null);
  const [items, setItems] = useState<Garment[]>([]);
  const [reference, setReference] = useState<ReferencePhoto | null>(null);
  const [outfits, setOutfits] = useState<Outfit[]>([]);
  const [evaluation, setEvaluation] = useState<Evaluation | null>(null);
  const [tournament, setTournament] = useState<Tournament | null>(null);
  const [mobileSession, setMobileSession] = useState<MobileAdminSession>({ active: false });
  const [mobileBusy, setMobileBusy] = useState(false);
  const [target, setTarget] = useState("Women aged 18-25 at a bar");
  const [impression, setImpression] = useState(
    "Fashionable, confident, and approachable",
  );
  const [panelSize, setPanelSize] = useState(6);
  const panelSizeInitialized = useRef(false);
  const briefInitialized = useRef(false);
  const [garmentFile, setGarmentFile] = useState<File | null>(null);
  const [garmentName, setGarmentName] = useState("");
  const [category, setCategory] = useState("top");
  const [editingItemId, setEditingItemId] = useState<string | null>(null);
  const [editingName, setEditingName] = useState("");
  const [busy, setBusy] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const [nextHealth, nextItems, nextReference, latest, nextMobileSession, nextTournament] = await Promise.all([
        api<Health>("/api/health"),
        api<Garment[]>("/api/items"),
        api<ReferencePhoto | null>("/api/reference/current"),
        api<Evaluation | null>("/api/evaluations/latest"),
        api<MobileAdminSession>("/api/mobile/session/current"),
        api<Tournament | null>("/api/tournaments/current"),
      ]);
      setHealth(nextHealth);
      if (!panelSizeInitialized.current) {
        setPanelSize(nextHealth.focus_group.default_panel_size);
        panelSizeInitialized.current = true;
      }
      setItems(nextItems);
      setReference(nextReference);
      setMobileSession(nextMobileSession);
      setTournament(nextTournament);
      const tournamentIsNewest = Boolean(
        nextTournament?.evaluation &&
        (!latest || new Date(nextTournament.updated_at) >= new Date(latest.created_at)),
      );
      const selectedEvaluation = tournamentIsNewest
        ? nextTournament?.evaluation ?? null
        : latest;
      if (!briefInitialized.current) {
        const briefSource =
          nextTournament && nextTournament.status !== "complete"
            ? nextTournament
            : selectedEvaluation ?? nextTournament;
        if (briefSource) {
          setTarget(briefSource.audience);
          setImpression(briefSource.impression);
        }
        briefInitialized.current = true;
      }
      setEvaluation(selectedEvaluation);
      if (selectedEvaluation?.outfits) setOutfits(selectedEvaluation.outfits);
      setError(null);
    } catch (caught) {
      setError(
        caught instanceof Error
          ? caught.message
          : "The local service is not responding.",
      );
    }
  }, []);

  useEffect(() => {
    const request = window.setTimeout(() => void load(), 0);
    return () => window.clearTimeout(request);
  }, [load]);

  useEffect(() => {
    if (!mobileSession.active) return;
    const interval = window.setInterval(() => {
      void Promise.all([
        api<MobileAdminSession>("/api/mobile/session/current"),
        api<Garment[]>("/api/items"),
        api<ReferencePhoto | null>("/api/reference/current"),
      ]).then(([nextSession, nextItems, nextReference]) => {
        setMobileSession(nextSession);
        setItems(nextItems);
        setReference(nextReference);
      }).catch(() => {
        // The main status message handles service failures on the next full load.
      });
    }, 4000);
    return () => window.clearInterval(interval);
  }, [mobileSession.active]);

  useEffect(() => {
    if (!tournament || !["preparing", "ready", "running"].includes(tournament.status)) return;
    const interval = window.setInterval(() => {
      void api<Tournament | null>(`/api/tournaments/${tournament.id}`).then((next) => {
        if (!next) return;
        if (next.status === "complete" && tournament.status !== "complete") {
          setEvaluation(next.evaluation);
          setOutfits(next.outfits);
          setNotice(
            next.mode === "full_panel"
              ? `Full panel complete: all ${next.total_outfits} looks received six lenses in ${next.completed_calls} OpenAI calls.`
              : `Tournament complete: ${next.total_outfits} looks screened in ${next.completed_calls} OpenAI calls.`,
          );
          setError(null);
        } else if (["paused", "budget_stopped"].includes(next.status)) {
          setError(next.error ?? "The tournament paused safely.");
        }
        setTournament(next);
      }).catch((caught) => {
        setError(caught instanceof Error ? caught.message : "Tournament status could not be loaded.");
      });
    }, 4000);
    return () => window.clearInterval(interval);
  }, [tournament]);

  const availableCount = useMemo(
    () => items.filter((item) => item.available).length,
    [items],
  );
  const categoryCount = useMemo(
    () =>
      new Set(
        items.filter((item) => item.available).map((item) => item.category),
      ).size,
    [items],
  );
  const completedTryons = outfits
    .slice(0, 3)
    .filter((outfit) => outfit.tryon?.status === "complete");
  const audienceBriefChanged = Boolean(
    tournament?.status === "complete" &&
      (target.trim() !== tournament.audience.trim() ||
        impression.trim() !== tournament.impression.trim()),
  );

  function mergeLocalResults(updated: Outfit[]) {
    const byId = new Map(updated.map((outfit) => [outfit.id, outfit]));
    setOutfits((current) =>
      current.map((outfit) => {
        const next = byId.get(outfit.id);
        return next
          ? { ...outfit, tryon: next.tryon }
          : outfit;
      }),
    );
  }

  async function uploadGarment(event: FormEvent) {
    event.preventDefault();
    if (!garmentFile) {
      setError("Choose a garment photo first.");
      return;
    }
    setBusy("garment");
    setError(null);
    const form = new FormData();
    form.set("file", garmentFile);
    form.set("name", garmentName.trim());
    form.set("category", category);
    try {
      await api<Garment>("/api/items", { method: "POST", body: form });
      setGarmentFile(null);
      setGarmentName("");
      const input = document.querySelector<HTMLInputElement>("#garment-file");
      if (input) input.value = "";
      setNotice("Garment added locally. No API call was made.");
      await load();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Upload failed.");
    } finally {
      setBusy(null);
    }
  }

  async function uploadReference(file: File | null) {
    if (!file) return;
    setBusy("reference");
    setError(null);
    const form = new FormData();
    form.set("file", file);
    try {
      const nextReference = await api<ReferencePhoto>("/api/reference", {
        method: "POST",
        body: form,
      });
      setReference(nextReference);
      setNotice("Reference saved. A face-blurred copy was created locally.");
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Upload failed.");
    } finally {
      setBusy(null);
    }
  }

  async function updateItem(id: string, changes: Partial<Garment>) {
    setError(null);
    try {
      const updated = await api<Garment>(`/api/items/${id}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(changes),
      });
      setItems((current) =>
        current.map((item) => (item.id === id ? updated : item)),
      );
      return true;
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Update failed.");
      return false;
    }
  }

  function beginNameEdit(item: Garment) {
    setEditingItemId(item.id);
    setEditingName(item.name);
  }

  async function saveName(event: FormEvent, item: Garment) {
    event.preventDefault();
    const name = editingName.trim();
    if (!name) {
      setError("Clothing name cannot be empty.");
      return;
    }
    const saved = await updateItem(item.id, { name });
    if (saved) {
      setEditingItemId(null);
      setEditingName("");
      setNotice("Clothing name updated locally.");
    }
  }

  async function generateShortlist() {
    setBusy("generate");
    setError(null);
    setNotice(null);
    try {
      const generated = await api<{
        outfits: Outfit[];
        combinations_considered: number;
      }>("/api/outfits/generate", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ limit: 8 }),
      });
      setOutfits(generated.outfits);
      setEvaluation(null);
      setNotice(
        `${generated.outfits.length} looks shortlisted from ${generated.combinations_considered} local combinations. No API call was made.`,
      );
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Generation failed.");
    } finally {
      setBusy(null);
    }
  }

  async function runPanel() {
    setBusy("panel");
    setError(null);
    setNotice(null);
    try {
      const result = await api<Evaluation>("/api/evaluations", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          audience: target.trim(),
          impression: impression.trim(),
          outfit_ids: outfits.map((outfit) => outfit.id),
          panel_size: panelSize,
        }),
      });
      setEvaluation(result);
      setOutfits(result.outfits);
      setNotice(
        result.cache_hit
          ? "Loaded the matching panel result from local cache. Zero API calls."
          : `Panel complete with ${result.api_calls} OpenAI calls. The result is now cached.`,
      );
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Panel failed.");
    } finally {
      setBusy(null);
    }
  }

  async function startTournament() {
    const resumable = tournament && ["paused", "ready"].includes(tournament.status);
    const audience = target.trim();
    const desiredImpression = impression.trim();
    const retestingNewBrief = audienceBriefChanged;
    if (!resumable) {
      const confirmed = window.confirm(
        `${retestingNewBrief ? "Retest every enabled outfit with this new audience brief?" : "Evaluate every enabled outfit with all six Luna audience lenses?"}\n\nTarget audience: ${audience}\nDesired impression: ${desiredImpression}\n\nThis full-panel run has no spend cap. Previous completed runs remain saved locally.`,
      );
      if (!confirmed) return;
    }
    setBusy("tournament");
    setError(null);
    setNotice(null);
    try {
      const path = resumable
        ? `/api/tournaments/${tournament.id}/resume`
        : "/api/tournaments";
      const result = await api<Tournament>(path, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: resumable
          ? undefined
            : JSON.stringify({
                audience,
                impression: desiredImpression,
                mode: "full_panel",
              }),
      });
      setTournament(result);
      setNotice(
        resumable
          ? `${result.mode === "full_panel" ? "Full panel" : "Tournament"} resumed from the last completed batch.`
          : `${result.total_outfits} looks entered for six-lens evaluation.${retestingNewBrief ? " The previous audience result remains saved locally." : ""} The run is persistent and survives browser refreshes.`,
      );
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Tournament could not start.");
    } finally {
      setBusy(null);
    }
  }

  async function runTryons() {
    setBusy("tryon");
    setError(null);
    setNotice("Loading the local try-on model and generating the top three looks...");
    try {
      const result = await api<LocalModelResponse>("/api/tryon", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          outfit_ids: outfits.slice(0, 3).map((outfit) => outfit.id),
        }),
      });
      mergeLocalResults(result.outfits);
      setNotice(
        `${result.generated} try-on${result.generated === 1 ? "" : "s"} generated locally; ${result.cache_hits} loaded from cache.`,
      );
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Try-on failed.");
      setNotice(null);
    } finally {
      setBusy(null);
    }
  }

  async function startMobileIntake() {
    setMobileBusy(true);
    setError(null);
    setNotice(null);
    try {
      const next = await api<MobileAdminSession>("/api/mobile/session", {
        method: "POST",
      });
      setMobileSession(next);
      setNotice("Private iPhone intake opened on your local network. Scan the QR code and enter the PIN.");
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Could not start iPhone intake.");
    } finally {
      setMobileBusy(false);
    }
  }

  async function stopMobileIntake() {
    setMobileBusy(true);
    setError(null);
    try {
      await api<{ active: boolean }>("/api/mobile/session", { method: "DELETE" });
      setMobileSession({ active: false });
      setNotice("iPhone intake closed. Wardrobe photos already saved remain on this PC.");
      await load();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Could not close iPhone intake.");
    } finally {
      setMobileBusy(false);
    }
  }

  const tournamentRunning = Boolean(
    tournament && ["preparing", "running"].includes(tournament.status),
  );
  const tournamentResumable = Boolean(
    tournament && ["paused", "ready"].includes(tournament.status),
  );
  const tournamentButtonLabel = busy === "tournament"
    ? tournamentResumable ? "Resuming..." : "Preparing full panel..."
    : tournamentRunning
      ? tournament?.mode === "full_panel" ? "Full panel running" : "Tournament running"
      : tournamentResumable
        ? tournament?.mode === "full_panel" ? "Resume full panel" : "Resume tournament"
        : tournament?.status === "budget_stopped"
          ? "Budget cap reached"
          : tournament?.status === "complete"
            ? audienceBriefChanged ? "Retest with new brief" : "Run full panel again"
            : "Six-lens every outfit";

  return (
    <main>
      <header className="topbar">
        <a className="brand" href="#top" aria-label="AI Wardrobe Lab home">
          <span className="brand-mark">AWL</span>
          <span>AI Wardrobe Lab</span>
        </a>
        <div className="status-row" aria-label="System status">
          <span className={`status-dot ${health ? "online" : ""}`} />
          <span>{health ? "Local service online" : "Local service offline"}</span>
          <span className="status-divider" />
          <span>{health?.openai_model ?? "OpenAI not checked"}</span>
        </div>
      </header>

      <section className="hero" id="top">
        <div>
          <p className="eyebrow">Local wardrobe intelligence</p>
          <h1>Dress for the room.<br />Keep your closet private.</h1>
          <p className="hero-copy">
            Build every outfit on this computer, then use a quick shortlist or an
            exhaustive synthetic-audience tournament. Uploads, combination building,
            and try-on stay local.
          </p>
        </div>
        <div className="hero-metrics" aria-label="Wardrobe summary">
          <div><strong>{items.length}</strong><span>garments saved</span></div>
          <div><strong>{availableCount}</strong><span>available now</span></div>
          <div><strong>{tournamentRunning ? tournament?.completed_calls ?? 0 : evaluation?.api_calls ?? 0}</strong><span>{tournamentRunning ? "calls in tournament" : "calls last run"}</span></div>
        </div>
      </section>

      {(error || notice) && (
        <div className={`message ${error ? "message-error" : "message-ok"}`} role="status">
          <span>{error ?? notice}</span>
          <button onClick={() => { setError(null); setNotice(null); }} aria-label="Dismiss message">x</button>
        </div>
      )}

      <section className="workflow-strip" aria-label="Workflow">
        <span className={items.length ? "done" : "active"}><b>01</b> Closet</span>
        <i />
        <span className={outfits.length ? "done" : items.length ? "active" : ""}><b>02</b> Shortlist</span>
        <i />
        <span className={evaluation ? "done" : tournamentRunning || outfits.length ? "active" : ""}><b>03</b> Audience</span>
        <i />
        <span className={completedTryons.length ? "done" : outfits.length ? "active" : ""}><b>04</b> Try-on</span>
      </section>

      <div className="dashboard-grid">
        <section className="panel closet-panel">
          <div className="panel-heading">
            <div><p className="section-kicker">Your inventory</p><h2>Wardrobe checklist</h2></div>
            <span className="privacy-chip">Stored locally</span>
          </div>

          <form className="upload-form" onSubmit={uploadGarment}>
            <label className="file-drop" htmlFor="garment-file">
              <span className="upload-plus">+</span>
              <span>
                <strong>{garmentFile ? garmentFile.name : "Add one garment"}</strong>
                <small>JPG, PNG or WebP / processed locally</small>
              </span>
              <input id="garment-file" type="file" accept="image/jpeg,image/png,image/webp" onChange={(event) => setGarmentFile(event.target.files?.[0] ?? null)} />
            </label>
            <div className="upload-fields">
              <input value={garmentName} onChange={(event) => setGarmentName(event.target.value)} placeholder="Optional name" aria-label="Garment name" />
              <select value={category} onChange={(event) => setCategory(event.target.value)} aria-label="Garment category">
                {categories.map(([value, label]) => <option key={value} value={value}>{label}</option>)}
              </select>
              <button className="button button-dark" disabled={busy === "garment"}>{busy === "garment" ? "Saving..." : "Add garment"}</button>
            </div>
          </form>

          <div className="inventory-list">
            {items.length === 0 ? (
              <div className="empty-state">
                <span>01</span>
                <div><strong>Your closet starts here.</strong><p>Upload one clear garment photo at a time. You can correct every label later.</p></div>
              </div>
            ) : items.map((item) => (
              <article className={`garment-row ${item.available ? "" : "unavailable"}`} key={item.id}>
                <img src={mediaUrl(item.media_url)} alt={item.name} />
                <div className="garment-info">
                  {editingItemId === item.id ? (
                    <form className="item-name-editor" onSubmit={(event) => void saveName(event, item)}>
                      <input
                        autoFocus
                        maxLength={80}
                        value={editingName}
                        onChange={(event) => setEditingName(event.target.value)}
                        onKeyDown={(event) => {
                          if (event.key === "Escape") {
                            setEditingItemId(null);
                            setEditingName("");
                          }
                        }}
                        aria-label={`Edit name for ${item.name}`}
                      />
                      <button type="submit">Save</button>
                      <button type="button" onClick={() => { setEditingItemId(null); setEditingName(""); }}>Cancel</button>
                    </form>
                  ) : (
                    <div className="item-name-display">
                      <strong>{item.name}</strong>
                      <button type="button" onClick={() => beginNameEdit(item)} aria-label={`Edit name for ${item.name}`}>Edit</button>
                    </div>
                  )}
                  <span><i style={{ background: item.color_hex }} />{item.dominant_color} / {item.category.replace("_", " ")}</span>
                </div>
                <div className="last-worn">
                  <small>Last worn</small>
                  <button title="Mark as worn today" onClick={() => void updateItem(item.id, { last_worn: new Date().toISOString().slice(0, 10) })}>{formatDate(item.last_worn)}</button>
                </div>
                <label className="availability-toggle">
                  <input type="checkbox" checked={item.available} onChange={(event) => void updateItem(item.id, { available: event.target.checked })} />
                  <span /><em>{item.available ? "Available" : "Unavailable"}</em>
                </label>
              </article>
            ))}
          </div>
        </section>

        <aside className="side-stack">
          <section className="panel audience-panel">
            <div className="panel-heading compact">
              <div><p className="section-kicker">The room</p><h2>Audience brief</h2></div>
              <span className="number-badge">{panelSize}</span>
            </div>
            <label className="field-label">Target audience</label>
            <textarea value={target} onChange={(event) => setTarget(event.target.value)} rows={3} />
            <label className="field-label">Desired impression</label>
            <textarea value={impression} onChange={(event) => setImpression(event.target.value)} rows={3} />
            {audienceBriefChanged && (
              <p className="brief-change-note">
                <strong>New brief ready.</strong> Retesting creates a separate saved result with all six lenses.
              </p>
            )}
            <label className="field-label" htmlFor="panel-size">Audience size</label>
            <select id="panel-size" className="panel-size-select" value={panelSize} onChange={(event) => setPanelSize(Number(event.target.value))}>
              {Array.from({ length: health?.focus_group.max_panel_size ?? 6 }, (_, index) => index + 1).map((size) => (
                <option key={size} value={size}>{size} perception {size === 1 ? "lens" : "lenses"}</option>
              ))}
            </select>
            <div className="call-budget">
              <span>Maximum new OpenAI calls<small>Exact matching runs use the local cache.</small></span>
              <strong>{panelSize}</strong>
            </div>
          </section>

          <section className="panel privacy-panel">
            <div className="panel-heading compact">
              <div><p className="section-kicker">Private by design</p><h2>Body reference</h2></div>
              <span className="privacy-chip">Local</span>
            </div>
            {reference ? (
              <div className="reference-preview">
                <img src={mediaUrl(reference.privacy_media_url)} alt="Privacy-protected body reference" />
                <div><strong>Privacy copy ready</strong><p>{reference.privacy_method}. The original is used only for local try-on.</p></div>
              </div>
            ) : (
              <label className="reference-empty" htmlFor="reference-file">
                <span className="silhouette" />
                <strong>Add a full-body photo</strong>
                <small>Your face is blurred locally.</small>
              </label>
            )}
            <input id="reference-file" className="visually-hidden" type="file" accept="image/jpeg,image/png,image/webp" onChange={(event) => void uploadReference(event.target.files?.[0] ?? null)} />
            <label className="button button-light full" htmlFor="reference-file">{busy === "reference" ? "Processing..." : reference ? "Replace reference" : "Choose reference"}</label>
          </section>

          <section className="panel mobile-admin-panel">
            <div className="panel-heading compact">
              <div><p className="section-kicker">Fast phone capture</p><h2>iPhone intake</h2></div>
              <span className={`mobile-admin-status ${mobileSession.active ? "active" : ""}`}>{mobileSession.active ? "Open" : "Closed"}</span>
            </div>
            {mobileSession.active ? (
              <div className="mobile-admin-active">
                {mobileSession.qr_media_url && <img className="mobile-admin-qr" src={mediaUrl(mobileSession.qr_media_url)} alt="QR code for private iPhone wardrobe intake" />}
                <div className="mobile-admin-pin"><span>PIN</span><strong>{mobileSession.pin}</strong></div>
                <p>Scan with the iPhone Camera, then enter this PIN. Both devices must be on the same Wi-Fi.</p>
                <div className="mobile-admin-meta">
                  <span><b>{mobileSession.uploads ?? 0}</b> uploaded</span>
                  <span><b>{mobileSession.reference_uploaded ? "Yes" : "No"}</b> body reference</span>
                </div>
                <button className="button button-light full" onClick={() => void stopMobileIntake()} disabled={mobileBusy}>{mobileBusy ? "Closing..." : "Stop iPhone intake"}</button>
              </div>
            ) : (
              <div className="mobile-admin-empty">
                <p>Take one garment photo after another or select a batch from Photos. HEIC is supported and everything saves straight to this PC.</p>
                <button className="button button-dark full" onClick={() => void startMobileIntake()} disabled={mobileBusy || !health?.mobile.available}>{mobileBusy ? "Opening..." : "Start iPhone intake"}</button>
                {!health?.mobile.available && health && <small>Connect this PC to the same private Wi-Fi as the iPhone.</small>}
              </div>
            )}
          </section>
        </aside>
      </div>

      <section className="panel results-panel">
        <div className="results-header">
          <div>
            <p className="section-kicker">Local combinations, then audience ranking</p>
            <h2>Your strongest looks</h2>
            <p>{categoryCount < 2 ? "Add at least two compatible garment categories to begin." : `${availableCount} available pieces are ready for local combination.`}</p>
          </div>
          <div className="action-cluster">
            <button className="button button-light" onClick={() => void generateShortlist()} disabled={busy !== null || tournamentRunning || categoryCount < 2}>{busy === "generate" ? "Building..." : "Build shortlist"}</button>
            <button className="button button-accent" onClick={() => void runPanel()} disabled={busy !== null || tournamentRunning || outfits.length === 0 || !target.trim() || !impression.trim()}>{busy === "panel" ? "Panel is ranking..." : evaluation ? "Run panel again" : "Ask audience"}</button>
            <button className="button button-tournament" onClick={() => void startTournament()} disabled={busy !== null || tournamentRunning || tournament?.status === "budget_stopped" || categoryCount < 2 || !target.trim() || !impression.trim() || !health?.openai_configured}>{tournamentButtonLabel}</button>
            <button className="button button-dark" onClick={() => void runTryons()} disabled={busy !== null || outfits.length === 0 || !reference || !health?.tryon.ready}>{busy === "tryon" ? "Generating..." : "Try on top 3"}</button>
          </div>
        </div>

        {tournament && (
          <section className={`tournament-progress-card ${tournament.status}`} aria-live="polite">
            <div className="tournament-progress-heading">
              <div>
                <p className="section-kicker">
                  {tournament.mode === "full_panel"
                    ? "Every outfit / six audience lenses"
                    : "Exhaustive audience tournament"}
                </p>
                <strong>
                  {tournament.status === "complete"
                    ? tournament.mode === "full_panel"
                      ? `All ${tournament.ranked_outfits || tournament.total_outfits} looks fully ranked`
                      : "Final ranking complete"
                    : tournament.stage === "screening"
                      ? tournament.mode === "full_panel"
                        ? `${tournament.screened_outfits} of ${tournament.total_outfits} looks scored by six lenses`
                        : `${tournament.screened_outfits} of ${tournament.total_outfits} looks screened`
                      : tournament.stage === "semifinal"
                        ? `${tournament.semifinalists} semifinalists under six lenses`
                        : `${tournament.finalists} finalists under six lenses`}
                </strong>
              </div>
              <span>{tournament.status.replace("_", " ")}</span>
            </div>
            <div className="tournament-progress-track"><i style={{ width: `${Math.min(100, tournamentProgress(tournament))}%` }} /></div>
            <div className="tournament-stats">
              <span><b>{tournament.completed_calls}</b> / {tournament.estimated_calls} calls</span>
              <span><b>${tournament.estimated_cost_usd.toFixed(3)}</b> / {tournament.budget_usd > 0 ? `$${tournament.budget_usd.toFixed(2)} cap` : "no spend cap"}</span>
              <span>
                <b>{tournament.mode === "full_panel" && tournament.status === "complete" ? tournament.ranked_outfits : tournament.screened_outfits}</b>
                {tournament.mode === "full_panel" ? tournament.status === "complete" ? " ranked" : " fully scored" : " screened"}
              </span>
              <span><b>{tournament.finalists}</b> top results</span>
            </div>
            <p>{tournament.audience} / {tournament.impression}. Contact sheets contain garment visuals and neutral outfit labels only.</p>
          </section>
        )}

        {outfits.length === 0 ? (
          <div className="results-empty">
            <div className="empty-cards"><span /><span /><span /></div>
            <strong>No shortlist yet</strong>
            <p>The combination engine runs on this computer and sends nothing to OpenAI.</p>
          </div>
        ) : (
          <div className="outfit-grid">
            {outfits.slice(0, 8).map((outfit, index) => {
              const scores = [
                { label: "Fashion", value: outfit.fashionable },
                { label: "Confidence", value: outfit.confident },
                { label: "Approachable", value: outfit.approachable },
                { label: "Audience fit", value: outfit.audience_fit },
              ];
              return (
                <article className={`outfit-card ${index < 3 ? "top-choice" : ""}`} key={outfit.id}>
                  <div className="outfit-image-wrap">
                    <img src={mediaUrl(outfit.board_media_url)} alt={`Outfit ${index + 1}: ${outfit.item_names.join(", ")}`} />
                    <span className="rank-badge">#{outfit.rank ?? index + 1}</span>
                    {index < 3 && <span className="tryon-badge">Top 3</span>}
                  </div>
                  <div className="outfit-card-body">
                    <div className="outfit-title"><strong>{outfit.item_names.join(" + ")}</strong><span>{scoreLabel(outfit.overall)}<small>/10</small></span></div>
                    {evaluation ? (
                      <>
                        <div className="score-bars">
                          {scores.map(({ label, value }) => (
                            <div key={label}><span>{label}</span><i><b style={{ width: `${Number(value ?? 0) * 10}%` }} /></i><em>{scoreLabel(value)}</em></div>
                          ))}
                        </div>
                        <p className="consensus">&quot;{outfit.consensus}&quot;</p>
                      </>
                    ) : (
                      <div className="local-score"><span>Local coordination</span><strong>{outfit.local_score.toFixed(1)}</strong></div>
                    )}
                  </div>
                </article>
              );
            })}
          </div>
        )}

        {completedTryons.length > 0 && (
          <section className="tryon-section">
            <div className="tryon-heading">
              <div><p className="section-kicker">Generated on this GPU</p><h3>Top-look try-ons</h3></div>
              <span>Local originals shown / blurred copies are API-safe</span>
            </div>
            <div className="tryon-grid">
              {completedTryons.map((outfit, index) => (
                <article key={outfit.id}>
                  <div><img src={mediaUrl(outfit.tryon?.media_url)} alt={`Local try-on ${index + 1}`} /><span>#{outfit.rank ?? index + 1}</span></div>
                  <strong>{outfit.item_names.join(" + ")}</strong>
                  <small>Face-blurred privacy copy ready</small>
                </article>
              ))}
            </div>
          </section>
        )}

        <div className="model-rail">
          <div><span className={`model-light ${health?.tryon.verified ? "ready" : ""}`} /><strong>Local try-on</strong><p>{health?.tryon.label ?? "Checking runtime"}</p></div>
          <div><span className={`model-light ${health?.openai_configured ? "ready" : ""}`} /><strong>Audience panel</strong><p>{health?.openai_configured ? "OpenAI key detected" : "Add a project API key to enable"}</p></div>
        </div>
      </section>

      <footer>
        <span>AI Wardrobe Lab</span>
        <p>OpenAI receives garment-only contact sheets and the audience brief, never your original body photo.</p>
      </footer>
    </main>
  );
}
