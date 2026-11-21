import { useCallback, useEffect, useState } from "react";
import { api, statusTone } from "./api";
import StudioForm from "./components/StudioForm";
import Storyboard from "./components/Storyboard";
import LiveMonitor from "./components/LiveMonitor";
import ProviderDashboard from "./components/ProviderDashboard";
import Library from "./components/Library";

const ACTIVE_STATUSES = ["running", "waiting", "assembling"];

export default function App() {
  const [config, setConfig] = useState(null);
  const [health, setHealth] = useState(null);
  const [providers, setProviders] = useState(null);
  const [project, setProject] = useState(null);
  const [view, setView] = useState("studio");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  const notify = useCallback((message) => {
    setError(String(message || "Something went wrong"));
    window.setTimeout(() => setError(""), 9000);
  }, []);

  useEffect(() => {
    let cancelled = false;
    Promise.all([api.config(), api.health(), api.providers()])
      .then(([cfg, status, providerPayload]) => {
        if (cancelled) return;
        setConfig(cfg);
        setHealth(status);
        setProviders(providerPayload);
      })
      .catch((err) => {
        if (!cancelled) notify(err.message);
      });
    return () => {
      cancelled = true;
    };
  }, [notify]);

  const projectStatus = project ? project.status : null;
  const projectId = project ? project.id : null;

  useEffect(() => {
    if (!projectId || !ACTIVE_STATUSES.includes(projectStatus)) return undefined;
    const timer = window.setInterval(() => {
      api
        .getProject(projectId)
        .then((fresh) => setProject(fresh))
        .catch(() => {});
    }, 2500);
    return () => window.clearInterval(timer);
  }, [projectId, projectStatus]);

  useEffect(() => {
    const shouldPoll = view === "providers" || ACTIVE_STATUSES.includes(projectStatus);
    if (!shouldPoll) return undefined;
    const timer = window.setInterval(() => {
      api
        .providers()
        .then((payload) => setProviders(payload))
        .catch(() => {});
    }, 7000);
    return () => window.clearInterval(timer);
  }, [view, projectStatus]);

  const refresh = useCallback(async () => {
    if (!project) return null;
    const fresh = await api.getProject(project.id);
    setProject(fresh);
    return fresh;
  }, [project]);

  const withBusy = useCallback(
    async (fn) => {
      setBusy(true);
      try {
        await fn();
      } catch (err) {
        notify(err.message);
      } finally {
        setBusy(false);
      }
    },
    [notify]
  );

  const handleCreate = (payload) =>
    withBusy(async () => {
      const created = await api.createProject(payload);
      setProject(created);
      setView("storyboard");
    });

  const handleStart = () =>
    withBusy(async () => {
      const fresh = await api.start(project.id);
      setProject(fresh);
      setView("studio");
    });

  const handlePause = () =>
    withBusy(async () => {
      const fresh = await api.pause(project.id);
      setProject(fresh);
    });

  const handleAssemble = () =>
    withBusy(async () => {
      await api.assemble(project.id);
      await refresh();
    });

  const handleReplan = () =>
    withBusy(async () => {
      const fresh = await api.replan(project.id);
      setProject(fresh);
    });

  const handlePatchScene = (sceneId, patch) =>
    withBusy(async () => {
      const fresh = await api.patchScene(project.id, sceneId, patch);
      setProject(fresh);
    });

  const handleAssign = (sceneId, provider, model) =>
    withBusy(async () => {
      const fresh = await api.assignScene(project.id, sceneId, { provider, model });
      setProject(fresh);
    });

  const handleRegenerate = (sceneId) =>
    withBusy(async () => {
      const fresh = await api.regenerateScene(project.id, sceneId);
      setProject(fresh);
      setView("studio");
    });

  const handleOpen = (projectId) =>
    withBusy(async () => {
      const fresh = await api.getProject(projectId);
      setProject(fresh);
      setView(fresh.status === "completed" ? "studio" : "storyboard");
    });

  const handleNew = () => {
    setProject(null);
    setView("studio");
  };

  const handleResetProvider = (providerId) =>
    withBusy(async () => {
      await api.resetProvider(providerId);
      const payload = await api.providers();
      setProviders(payload);
    });

  const configured = config ? config.providers_configured || [] : [];
  const tone = statusTone(projectStatus);

  return (
    <div className="app">
      <header className="topbar">
        <div className="brand">
          <div className="brand-mark">OR</div>
          <div>
            <div className="brand-name">OmniReel</div>
            <div className="brand-sub">multi-model AI film studio</div>
          </div>
        </div>

        <div className="health">
          <span className={`dot ${configured.length ? "ok" : "bad"}`} />
          <span>
            {configured.length
              ? `${configured.length} provider${configured.length > 1 ? "s" : ""} ready`
              : "no provider keys"}
          </span>
          <span className={`dot ${health && health.ffmpeg ? "ok" : "warn"}`} />
          <span>{health && health.ffmpeg ? "ffmpeg ok" : "ffmpeg missing"}</span>
          {project ? <span className={`badge ${tone}`}>{project.status}</span> : null}
        </div>

        <nav className="tabs">
          <button
            type="button"
            className={`tab ${view === "studio" && !project ? "active" : ""}`}
            onClick={() => {
              setProject(null);
              setView("studio");
            }}
          >
            New film
          </button>
          <button
            type="button"
            className={`tab ${view === "storyboard" ? "active" : ""}`}
            disabled={!project}
            onClick={() => setView("storyboard")}
          >
            Storyboard
          </button>
          <button
            type="button"
            className={`tab ${view === "studio" && project ? "active" : ""}`}
            disabled={!project}
            onClick={() => setView("studio")}
          >
            Production
          </button>
          <button
            type="button"
            className={`tab ${view === "providers" ? "active" : ""}`}
            onClick={() => setView("providers")}
          >
            Providers
          </button>
          <button
            type="button"
            className={`tab ${view === "library" ? "active" : ""}`}
            onClick={() => setView("library")}
          >
            Library
          </button>
        </nav>
      </header>

      <main className="main">
        {view === "studio" && !project ? (
          <StudioForm
            config={config}
            providers={providers}
            busy={busy}
            onCreate={handleCreate}
          />
        ) : null}

        {view === "studio" && project ? (
          <LiveMonitor
            project={project}
            providers={providers}
            busy={busy}
            onStart={handleStart}
            onPause={handlePause}
            onAssemble={handleAssemble}
            onRegenerate={handleRegenerate}
            onNew={handleNew}
            onRefresh={refresh}
          />
        ) : null}

        {view === "storyboard" && project ? (
          <Storyboard
            project={project}
            providers={providers}
            busy={busy}
            onStart={handleStart}
            onReplan={handleReplan}
            onPatchScene={handlePatchScene}
            onAssign={handleAssign}
            onRegenerate={handleRegenerate}
            onNew={handleNew}
          />
        ) : null}

        {view === "providers" ? (
          <ProviderDashboard payload={providers} onReset={handleResetProvider} busy={busy} />
        ) : null}

        {view === "library" ? (
          <Library onOpen={handleOpen} busy={busy} />
        ) : null}
      </main>

      {error ? <div className="toast">{error}</div> : null}
    </div>
  );
}
