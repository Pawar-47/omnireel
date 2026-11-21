import { useEffect, useState } from "react";
import { formatSeconds, statusTone } from "../api";

function modelOptions(providers) {
  const list = (providers && providers.providers) || [];
  const options = [];
  list.forEach((entry) => {
    (entry.models || []).forEach((model) => {
      options.push({
        value: `${entry.provider}:${model.model}`,
        label: `${entry.label || entry.provider} · ${model.label || model.model}`,
      });
    });
  });
  return options;
}

function SceneCard({ scene, assignment, options, editable, busy, onSave, onAssign, onRegenerate }) {
  const [editing, setEditing] = useState(false);
  const [prompt, setPrompt] = useState(scene.prompt);

  useEffect(() => {
    setPrompt(scene.prompt);
  }, [scene.prompt]);

  const tone = statusTone(
    scene.status === "ready" ? "completed" : scene.status === "failed" ? "failed" : scene.status
  );
  const selected = scene.provider && scene.model ? `${scene.provider}:${scene.model}` : "";
  const planned = assignment && assignment.provider ? assignment : null;

  return (
    <div className="scene">
      <div className="scene-head">
        <div className="scene-index">{scene.index + 1}</div>
        <div className="scene-title">{scene.description || "Scene"}</div>
        <span className={`badge ${tone}`}>{scene.status}</span>
        <span className="badge">{formatSeconds(scene.duration_seconds)}</span>
        {scene.provider ? (
          <span className="badge busy">
            {scene.provider}
            {scene.model ? ` · ${scene.model}` : ""}
          </span>
        ) : planned ? (
          <span className="badge">
            planned: {planned.provider}
            {planned.model ? ` · ${planned.model}` : ""}
          </span>
        ) : (
          <span className="badge warn">no eligible model</span>
        )}
        <span className="spacer" style={{ marginLeft: "auto" }} />
        {editable ? (
          <button
            type="button"
            className="button ghost small"
            disabled={busy}
            onClick={() => setEditing((value) => !value)}
          >
            {editing ? "Close" : "Edit"}
          </button>
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

      {editing ? (
        <div style={{ marginTop: 12 }}>
          <label htmlFor={`prompt-${scene.id}`}>Generation prompt</label>
          <textarea
            id={`prompt-${scene.id}`}
            rows={5}
            value={prompt}
            onChange={(event) => setPrompt(event.target.value)}
          />
          <div className="row" style={{ marginTop: 10 }}>
            <div style={{ flex: 1, minWidth: 220 }}>
              <label htmlFor={`model-${scene.id}`}>Pin this scene to a model</label>
              <select
                id={`model-${scene.id}`}
                value={selected}
                onChange={(event) => {
                  const [provider, ...rest] = event.target.value.split(":");
                  if (provider && rest.length) onAssign(scene.id, provider, rest.join(":"));
                }}
              >
                <option value="">Automatic (recommended)</option>
                {options.map((option) => (
                  <option key={option.value} value={option.value}>
                    {option.label}
                  </option>
                ))}
              </select>
            </div>
          </div>
          <div className="row right" style={{ marginTop: 10 }}>
            <button
              type="button"
              className="button small"
              disabled={busy}
              onClick={async () => {
                await onSave(scene.id, { prompt });
                setEditing(false);
              }}
            >
              Save prompt
            </button>
          </div>
        </div>
      ) : (
        <p className="scene-prompt">{scene.prompt}</p>
      )}

      <div className="row" style={{ marginTop: 8 }}>
        <span className="scene-meta">Camera: {scene.camera}</span>
        <span className="scene-meta">Lighting: {scene.lighting}</span>
        {scene.attempts > 0 ? (
          <span className="scene-meta">attempts: {scene.attempts}</span>
        ) : null}
      </div>

      {scene.last_error ? <div className="scene-error">{scene.last_error}</div> : null}

      {scene.attempts_log && scene.attempts_log.length > 0 ? (
        <details style={{ marginTop: 8 }}>
          <summary className="scene-meta">Retry history</summary>
          <table className="table" style={{ marginTop: 6 }}>
            <tbody>
              {scene.attempts_log.map((entry, index) => (
                <tr key={`${scene.id}-attempt-${index}`}>
                  <td>{entry.provider}</td>
                  <td>{entry.model}</td>
                  <td>{entry.status}</td>
                  <td className="muted">{entry.error || "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </details>
      ) : null}
    </div>
  );
}

export default function Storyboard({
  project,
  providers,
  busy,
  onStart,
  onReplan,
  onPatchScene,
  onAssign,
  onRegenerate,
  onNew,
}) {
  const scenes = project.scenes || [];
  const options = modelOptions(providers);
  const planned = scenes.reduce((total, scene) => total + Number(scene.duration_seconds || 0), 0);
  const target = Number(project.target_seconds || 0);
  const drift = Math.abs(planned - target);
  const anyGenerated = scenes.some((scene) => scene.status !== "planned");

  return (
    <div className="panel">
      <div className="panel-head">
        <div>
          <h2>{project.title}</h2>
          <p className="scene-meta">{project.prompt}</p>
        </div>
        <span className="spacer" />
        <button type="button" className="button ghost small" onClick={onNew}>
          New film
        </button>
      </div>

      <div className="grid three" style={{ marginBottom: 16 }}>
        <div className="stat">
          <div className="stat-value">{scenes.length}</div>
          <div className="stat-label">segments</div>
        </div>
        <div className="stat">
          <div className="stat-value">{formatSeconds(planned)}</div>
          <div className="stat-label">planned runtime</div>
        </div>
        <div className="stat">
          <div className="stat-value">{drift < 0.75 ? "exact" : `${drift.toFixed(1)}s`}</div>
          <div className="stat-label">timeline drift</div>
        </div>
      </div>

      {drift >= 0.75 ? (
        <div className="notice" style={{ marginBottom: 16 }}>
          The available models cannot hit {formatSeconds(target)} exactly, so the plan is{" "}
          {formatSeconds(planned)}. The assembler trims or reports the difference rather than
          hiding it.
        </div>
      ) : null}

      <div className="row" style={{ marginBottom: 16 }}>
        <button
          type="button"
          className="button"
          disabled={busy || scenes.length === 0}
          onClick={onStart}
        >
          {project.status === "running" || project.status === "waiting"
            ? "Open production"
            : "Start generation"}
        </button>
        <button
          type="button"
          className="button ghost"
          disabled={busy || anyGenerated}
          onClick={onReplan}
        >
          Re-plan storyboard
        </button>
        <span className="scene-meta">
          {anyGenerated
            ? "Editing is locked for generated scenes; re-plan once the project is fresh."
            : "Every prompt here is editable before you spend a single API call."}
        </span>
      </div>

      {scenes.map((scene) => (
        <SceneCard
          key={scene.id}
          scene={scene}
          assignment={project.assignments ? project.assignments[scene.id] : null}
          options={options}
          editable={scene.status !== "ready"}
          busy={busy}
          onSave={onPatchScene}
          onAssign={onAssign}
          onRegenerate={onRegenerate}
        />
      ))}
    </div>
  );
}
