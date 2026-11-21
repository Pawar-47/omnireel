import { useEffect, useState } from "react";
import { api, formatSeconds } from "../api";

export default function Library({ onOpen, busy }) {
  const [items, setItems] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  useEffect(() => {
    let cancelled = false;
    api
      .library()
      .then((payload) => {
        if (!cancelled) setItems(payload.items || []);
      })
      .catch((err) => {
        if (!cancelled) setError(err.message);
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  return (
    <div className="panel">
      <div className="panel-head">
        <h2>Finished films</h2>
        <span className="spacer" />
        <span className="badge">{items.length} in library</span>
      </div>

      {loading ? <div className="empty">Loading…</div> : null}
      {error ? <div className="notice">{error}</div> : null}
      {!loading && items.length === 0 ? (
        <div className="empty">
          Nothing finished yet. Generate a project and it will appear here with its MP4 and
          per-scene clips.
        </div>
      ) : null}

      <div className="grid two">
        {items.map((item) => (
          <div key={item.id} className="video-card">
            <video
              controls
              playsinline
              preload="metadata"
              src={`/api/projects/${item.id}/video`}
            />
            <div className="meta">
              <div className="scene-title">{item.title}</div>
              <div className="scene-meta">
                {formatSeconds(item.final_duration)} · {item.aspect_ratio} ·{" "}
                {item.progress ? item.progress.scenes_ready : 0} clips ·{" "}
                {item.updated_at}
              </div>
              <div className="row" style={{ marginTop: 10 }}>
                <button
                  type="button"
                  className="button ghost small"
                  disabled={busy}
                  onClick={() => onOpen(item.id)}
                >
                  Open project
                </button>
                <a className="button small" href={`/api/projects/${item.id}/video`} download>
                  Download
                </a>
              </div>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
