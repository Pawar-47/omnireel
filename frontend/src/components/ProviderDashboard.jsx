const KEY_ENV = {
  agnes: "AGNES_API_KEY",
  replicate: "REPLICATE_API_TOKEN",
  huggingface: "HF_TOKEN",
  runware: "RUNWARE_API_KEY",
  yepapi: "YEPAPI_API_KEY",
  mock: "OMNIREEL_ENABLE_MOCK=1",
};

function quotaLabel(quota) {
  if (quota.remaining !== null && quota.remaining !== undefined) {
    return `${quota.remaining} left`;
  }
  if (quota.limit_window !== null && quota.limit_window !== undefined) {
    return `${quota.used_window}/${quota.limit_window} this window`;
  }
  return "not published";
}

export default function ProviderDashboard({ payload, onReset, busy }) {
  if (!payload) {
    return (
      <div className="panel">
        <div className="empty">Loading provider state…</div>
      </div>
    );
  }
  const providers = payload.providers || [];
  const usage = payload.usage || [];

  return (
    <>
      <div className="panel">
        <div className="panel-head">
          <h2>Providers &amp; quotas</h2>
          <span className="spacer" />
          <span className={`badge ${payload.free_only ? "ok" : "warn"}`}>
            {payload.free_only ? "free-only mode" : "paid allowed"}
          </span>
        </div>
        <p className="scene-meta" style={{ marginBottom: 16 }}>
          Estimated numbers are labelled; OmniReel never invents a quota a provider does not
          publish.
        </p>

        {providers.map((entry) => {
          const envName = KEY_ENV[entry.provider] || "API key";
          return (
            <div key={entry.provider} className="scene" style={{ marginBottom: 14 }}>
              <div className="scene-head">
                <div className="scene-title">{entry.label || entry.provider}</div>
                <span className={`badge ${entry.configured ? "ok" : "warn"}`}>
                  {entry.configured ? "configured" : "no key"}
                </span>
                <span className={`badge ${entry.healthy ? "ok" : "bad"}`}>
                  {entry.healthy ? "healthy" : "cooling down"}
                </span>
                {entry.consecutive_failures > 0 ? (
                  <span className="badge bad">{entry.consecutive_failures} failures</span>
                ) : null}
                <span className="spacer" style={{ marginLeft: "auto" }} />
                <button
                  type="button"
                  className="button ghost small"
                  disabled={busy}
                  onClick={() => onReset(entry.provider)}
                >
                  Reset health
                </button>
              </div>

              {!entry.configured ? (
                <p className="scene-meta" style={{ marginTop: 8 }}>
                  Set <code>{envName}</code> in <code>.env</code> to enable this provider.
                </p>
              ) : null}
              {entry.last_error ? (
                <div className="scene-error">{entry.last_error}</div>
              ) : null}

              {entry.models && entry.models.length > 0 ? (
                <table className="table" style={{ marginTop: 10 }}>
                  <thead>
                    <tr>
                      <th>Model</th>
                      <th>Clip lengths</th>
                      <th>Resolutions</th>
                      <th>Free tier</th>
                      <th>Verified</th>
                    </tr>
                  </thead>
                  <tbody>
                    {entry.models.map((model) => (
                      <tr key={model.key}>
                        <td>
                          <div>{model.label || model.model}</div>
                          <div className="scene-meta">{model.model}</div>
                        </td>
                        <td>{(model.durations || []).join(", ")}s</td>
                        <td>{(model.resolutions || []).join(", ")}</td>
                        <td>{model.free_kind}</td>
                        <td>
                          <span className={`badge ${model.verified ? "ok" : "warn"}`}>
                            {model.verified ? model.verified_at : "unverified"}
                          </span>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              ) : null}

              {entry.quotas && entry.quotas.length > 0 ? (
                <table className="table" style={{ marginTop: 10 }}>
                  <thead>
                    <tr>
                      <th>Model · key</th>
                      <th>Status</th>
                      <th>Remaining</th>
                      <th>Next eligible</th>
                      <th>Source</th>
                    </tr>
                  </thead>
                  <tbody>
                    {entry.quotas.map((quota) => (
                      <tr key={`${quota.provider}-${quota.model}-${quota.key_id}`}>
                        <td>
                          {quota.model}
                          <span className="scene-meta"> · {quota.key_id}</span>
                        </td>
                        <td>{quota.kind}</td>
                        <td>{quotaLabel(quota)}</td>
                        <td className="muted">{quota.next_eligible_at || "now"}</td>
                        <td>
                          <span className={`badge ${quota.estimated ? "warn" : "ok"}`}>
                            {quota.source}
                          </span>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              ) : null}

              {entry.quotas && entry.quotas.some((quota) => quota.last_error) ? (
                <p className="scene-meta" style={{ marginTop: 8 }}>
                  Last error:{" "}
                  {entry.quotas.find((quota) => quota.last_error).last_error}
                </p>
              ) : null}
            </div>
          );
        })}
      </div>

      <div className="panel">
        <div className="panel-head">
          <h2>Recent usage</h2>
        </div>
        {usage.length === 0 ? (
          <div className="empty">No generation attempts recorded yet.</div>
        ) : (
          <table className="table">
            <thead>
              <tr>
                <th>When</th>
                <th>Provider</th>
                <th>Model</th>
                <th>Outcome</th>
                <th>Detail</th>
              </tr>
            </thead>
            <tbody>
              {usage.map((event) => (
                <tr key={event.id}>
                  <td className="muted">{event.at}</td>
                  <td>{event.provider}</td>
                  <td>{event.model}</td>
                  <td>
                    <span className={`badge ${event.kind === "success" ? "ok" : "bad"}`}>
                      {event.kind}
                    </span>
                  </td>
                  <td className="muted">{event.note || "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </>
  );
}
