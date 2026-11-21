import { formatSeconds, statusTone } from "../api";

function toneForScene(scene) {
  if (scene.status === "ready") return "ok";
  if (scene.status === "failed" || scene.status === "blocked") return "bad";
  if (["polling", "downloading", "dispatched"].includes(scene.status)) return "busy";
  return "muted";
}

export default function LiveMonitor({
  project,
  providers,
  busy,
  onStart,
  onPause,
  onAssemble,
  onRegenerate,
  onNew,
  onRefresh,
}) {
  const progress = project.progress || {};
  const scenes = project.scenes || [];
  const active = scenes.find((scene) => scene.status !== "ready" && scene.status !== "blocked");
  const running = ["running", "waiting", "assembling"].includes(project.status);
  const paused = project.status === "paused";
  const completed = project.status === "completed";
  const tone = statusTone(project.status);
  const percent = Math.round((progress.duration_completion || 0) * 100);
  const providerMix = progress.providers_used || {};
  const failing = scenes.filter((scene) => scene.last_error);

  return (
    <div className="panel">
      <div className="panel-head">
        <div>
          <h2>{project.title}</h2>
          <p className="scene-meta">
            {formatSeconds(project.target_seconds)} target · {scenes.length} segments ·{" "}
            {project.aspect_ratio} · {project.resolution}
          </p>
        </div>
        <span className="spacer" />
        <span className={`badge ${tone}`}>{project.status}</span>
        <button type="button" className="button ghost small" onClick={onNew}>
          New film
        </button>
      </div>

      <div className="progress" style={{ marginBottom: 10 }}>
        <span style={{ width: `${percent}%` }} />
      </div>
      <p className="scene-meta" style={{ marginBottom: 16 }}>
        {formatSeconds(progress.generated_seconds)} generated of{" "}
        {formatSeconds(progress.planned_seconds)} planned ·{" "}
        {progress.scenes_ready || 0}/{progress.scenes_total || 0} scenes ready ·{" "}
        {formatSeconds(progress.remaining_seconds)} remaining
      </p>

      {project.status_message ? (
        <div className={`notice ${running ? "info" : ""}`} style={{ marginBottom: 16 }}>
          {project.status_message}
          {project.next_retry_at && project.status === "waiting"
            ? ` — next automatic check after ${project.next_retry_at}`
            : ""}
        </div>
      ) : null}

      <div className="row" style={{ marginBottom: 18 }}>
        {!running && !completed ? (
          <button type="button" className="button" disabled={busy} onClick={onStart}>
            {paused ? "Resume generation" : "Start generation"}
          </button>
        ) : null}
        {running ? (
          <button type="button" className="button ghost" disabled={busy} onClick={onPause}>
            Pause
          </button>
        ) : null}
        {!completed && (progress.scenes_pending === 0) ? (
          <button type="button" className="button" disabled={busy} onClick={onAssemble}>
            Assemble final film
          </button>
        ) : null}
        <button type="button" className="button ghost small" disabled={busy} onClick={onRefresh}>
          Refresh
        </button>
      </div>

      {completed && project.video_url ? (
        <div className="video-card" style={{ marginBottom: 18 }}>
          <video controls playsinline src={project.video_url} />
          <div className="meta row">
            <div>
              <div className="scene-title">
                Final film · {formatSeconds(project.final_duration)}
              </div>
              <div className="scene-meta">
                {progress.scenes_ready} clips joined with FFmpeg
              </div>
            </div>
            <span className="spacer" style={{ marginLeft: "auto" }} />
            <a className="button small" href={project.video_url} download>
              Download MP4
            </a>
          </div>
        </div>
      ) : null}

      {active ? (
        <div className="stat" style={{ marginBottom: 18 }}>
          <div className="stat-label">Now generating</div>
          <div className="stat-value" style={{ fontSize: 17 }}>
            Scene {active.index + 1} · {formatSeconds(active.duration_seconds)}
          </div>
          <p className="scene-meta">
            {active.provider ? `${active.provider} · ${active.model}` : "waiting for a free model"}{" "}
            — {active.description}
          </p>
        </div>
      ) : null}

      <div className="grid two" style={{ marginBottom: 18 }}>
        <div>
          <label>Provider mix</label>
          {Object.keys(providerMix).length === 0 ? (
            <p className="scene-meta">Nothing generated yet.</p>
          ) : (
            <div className="chips">
              {Object.entries(providerMix).map(([provider, count]) => (
                <span key={provider} className="badge busy">
                  {provider} · {count} scene{count > 1 ? "s" : ""}
                </span>
              ))}
            </div>
          )}
        </div>
        <div>
          <label>Failover signals</label>
          {failing.length === 0 ? (
            <p className="scene-meta">No failures recorded.</p>
          ) : (
            <div className="chips">
              {failing.slice(0, 4).map((scene) => (
                <span key={scene.id} className="badge bad">
                  scene {scene.index + 1}: {String(scene.last_error).slice(0, 60)}
                </span>
              ))}
            </div>
          )}
        </div>
      </div>

      <label>Scene timeline</label>
      <div>
        {scenes.map((scene) => (
          <div key={scene.id} className="scene">
            <div className="scene-head">
              <div className="scene-index">{scene.index + 1}</div>
              <div className="scene-title" style={{ fontSize: 13 }}>
                {scene.description}
              </div>
              <span className={`badge ${toneForScene(scene)}`}>{scene.status}</span>
              <span className="badge">{formatSeconds(scene.actual_duration || scene.duration_seconds)}</span>
              {scene.provider ? (
                <span className="badge">{scene.provider}</span>
              ) : null}
              <span className="spacer" style={{ marginLeft: "auto" }} />
              {scene.clip_url ? (
                <a className="button ghost small" href={scene.clip_url} download>
                  Clip
                </a>
              ) : null}
              <button
                type="button"
                className="button ghost small"
                disabled={busy}
                onClick={() => onRegenerate(scene.id)}
              >
                {scene.status === "ready" ? "Regenerate" : "Retry"}
              </button>
            </div>
            {scene.last_error ? <div className="scene-error">{scene.last_error}</div> : null}
          </div>
        ))}
      </div>

      {providers && (providers.providers || []).length > 0 ? (
        <p className="scene-meta" style={{ marginTop: 16 }}>
          Quotas and provider health live on the Providers tab.
        </p>
      ) : null}
    </div>
  );
}
