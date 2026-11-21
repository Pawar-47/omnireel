import { useState } from "react";
import { formatSeconds } from "../api";

const DURATION_PRESETS = [60, 90, 120, 180];
const MODE_HINTS = {
  free_max: "Spend free allowances across every eligible provider first.",
  balanced: "Weigh quality, quota and how many providers are still available.",
  best_quality: "Prefer the strongest model that still has allowance left.",
  preferred: "Start with your preferred provider, fail over automatically.",
};

export default function StudioForm({ config, providers, busy, onCreate }) {
  const styles = (config && config.styles) || [];
  const modes = (config && config.modes) || ["free_max", "balanced", "best_quality", "preferred"];
  const aspects = (config && config.aspect_ratios) || ["16:9", "9:16", "1:1"];
  const resolutions = (config && config.resolutions) || ["720p", "1080p", "480p"];
  const ladder = (config && config.durations_ladder) || [];
  const allowPaid = Boolean(config && config.allow_paid);

  const [prompt, setPrompt] = useState("");
  const [target, setTarget] = useState((config && config.default_target_seconds) || 120);
  const [style, setStyle] = useState("cinematic");
  const [aspect, setAspect] = useState("16:9");
  const [resolution, setResolution] = useState("720p");
  const [mode, setMode] = useState("free_max");
  const [preferred, setPreferred] = useState("");
  const [continuity, setContinuity] = useState(true);
  const [crossfade, setCrossfade] = useState(false);
  const [keepAudio, setKeepAudio] = useState(true);
  const [narration, setNarration] = useState("");
  const [musicPath, setMusicPath] = useState("");
  const [advanced, setAdvanced] = useState(false);

  const configured = (config && config.providers_configured) || [];
  const providerOptions = (providers && providers.providers) || [];
  const segments = ladder.length ? Math.max(1, Math.round(Number(target) / ladder[0])) : null;

  const submit = (event) => {
    event.preventDefault();
    if (!prompt.trim()) return;
    onCreate({
      prompt: prompt.trim(),
      target_seconds: Number(target),
      style,
      aspect_ratio: aspect,
      resolution,
      mode,
      free_only: true,
      preferred_provider: mode === "preferred" ? preferred || null : null,
      continuity,
      crossfade,
      keep_scene_audio: keepAudio,
      narration_script: narration.trim() ? narration.trim() : null,
      music_path: musicPath.trim() ? musicPath.trim() : null,
    });
  };

  return (
    <form className="panel" onSubmit={submit}>
      <div className="panel-head">
        <h2>Describe the film</h2>
        <span className="spacer" />
        <span className="badge busy">{formatSeconds(target)} target</span>
      </div>

      {configured.length === 0 ? (
        <div className="notice" style={{ marginBottom: 16 }}>
          No provider keys found yet. Add <code>AGNES_API_KEY</code> to{" "}
          <code>.env</code> and restart the backend so generation has somewhere to run.
        </div>
      ) : null}

      <div className="field">
        <label htmlFor="prompt">Creative prompt</label>
        <textarea
          id="prompt"
          rows={4}
          placeholder="A cinematic documentary about exploring a mysterious tropical island"
          value={prompt}
          onChange={(event) => setPrompt(event.target.value)}
        />
      </div>

      <div className="field">
        <label>Target duration</label>
        <div className="row">
          <div className="chips">
            {DURATION_PRESETS.map((preset) => (
              <button
                type="button"
                key={preset}
                className={`chip ${Number(target) === preset ? "active" : ""}`}
                onClick={() => setTarget(preset)}
              >
                {formatSeconds(preset)}
              </button>
            ))}
          </div>
          <input
            type="number"
            min={20}
            max={600}
            step={5}
            style={{ maxWidth: 120 }}
            value={target}
            onChange={(event) => setTarget(event.target.value)}
            aria-label="Target duration in seconds"
          />
        </div>
        {segments ? (
          <p className="scene-meta" style={{ marginTop: 8 }}>
            Longest available clip is {ladder[0]}s - roughly {segments} segments will be
            generated and joined into one film.
          </p>
        ) : null}
      </div>

      <div className="field">
        <label>Visual style</label>
        <div className="chips">
          {styles.map((entry) => (
            <button
              type="button"
              key={entry.id}
              className={`chip ${style === entry.id ? "active" : ""}`}
              onClick={() => setStyle(entry.id)}
            >
              {entry.label}
            </button>
          ))}
        </div>
      </div>

      <div className="grid three">
        <div className="field">
          <label htmlFor="aspect">Aspect ratio</label>
          <select
            id="aspect"
            value={aspect}
            onChange={(event) => setAspect(event.target.value)}
          >
            {aspects.map((value) => (
              <option key={value} value={value}>
                {value}
              </option>
            ))}
          </select>
        </div>
        <div className="field">
          <label htmlFor="resolution">Output quality</label>
          <select
            id="resolution"
            value={resolution}
            onChange={(event) => setResolution(event.target.value)}
          >
            {resolutions.map((value) => (
              <option key={value} value={value}>
                {value}
              </option>
            ))}
          </select>
        </div>
        <div className="field">
          <label htmlFor="mode">Generation mode</label>
          <select id="mode" value={mode} onChange={(event) => setMode(event.target.value)}>
            {modes.map((value) => (
              <option key={value} value={value}>
                {value.replace("_", " ")}
              </option>
            ))}
          </select>
        </div>
      </div>

      <p className="scene-meta" style={{ marginBottom: 14 }}>
        {MODE_HINTS[mode] || ""} {allowPaid ? "" : "Paid models are excluded."}
      </p>

      {mode === "preferred" ? (
        <div className="field">
          <label htmlFor="preferred">Preferred provider</label>
          <select
            id="preferred"
            value={preferred}
            onChange={(event) => setPreferred(event.target.value)}
          >
            <option value="">None</option>
            {providerOptions
              .filter((entry) => entry.configured)
              .map((entry) => (
                <option key={entry.provider} value={entry.provider}>
                  {entry.label || entry.provider}
                </option>
              ))}
          </select>
        </div>
      ) : null}

      <div className="row" style={{ marginBottom: 14 }}>
        <label className="row small" style={{ margin: 0, textTransform: "none" }}>
          <input
            type="checkbox"
            checked={continuity}
            onChange={(event) => setContinuity(event.target.checked)}
            style={{ width: 16, height: 16 }}
          />
          <span>Use previous frame for continuity</span>
        </label>
        <label className="row small" style={{ margin: 0, textTransform: "none" }}>
          <input
            type="checkbox"
            checked={crossfade}
            onChange={(event) => setCrossfade(event.target.checked)}
            style={{ width: 16, height: 16 }}
          />
          <span>Crossfade between scenes (shortens the cut)</span>
        </label>
        <label className="row small" style={{ margin: 0, textTransform: "none" }}>
          <input
            type="checkbox"
            checked={keepAudio}
            onChange={(event) => setKeepAudio(event.target.checked)}
            style={{ width: 16, height: 16 }}
          />
          <span>Keep any audio the model generated</span>
        </label>
      </div>

      <button
        type="button"
        className="button ghost small"
        onClick={() => setAdvanced((value) => !value)}
      >
        {advanced ? "Hide audio options" : "Audio options"}
      </button>

      {advanced ? (
        <div className="grid two" style={{ marginTop: 14 }}>
          <div className="field">
            <label htmlFor="music">Background music path</label>
            <input
              id="music"
              placeholder="/home/you/Music/score.mp3"
              value={musicPath}
              onChange={(event) => setMusicPath(event.target.value)}
            />
            <p className="scene-meta" style={{ marginTop: 6 }}>
              Local file, mixed under the footage with fades.
            </p>
          </div>
          <div className="field">
            <label htmlFor="narration">Narration script</label>
            <textarea
              id="narration"
              rows={3}
              placeholder="Optional voice-over, rendered offline with espeak-ng or piper"
              value={narration}
              onChange={(event) => setNarration(event.target.value)}
            />
          </div>
        </div>
      ) : null}

      <div className="row right" style={{ marginTop: 18 }}>
        <button type="submit" className="button" disabled={busy || !prompt.trim()}>
          {busy ? "Building storyboard…" : "Build storyboard"}
        </button>
      </div>
    </form>
  );
}
