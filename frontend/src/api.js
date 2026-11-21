const BASE = "";

async function request(path, options = {}) {
  const response = await fetch(`${BASE}${path}`, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  if (!response.ok) {
    let detail = `${response.status} ${response.statusText}`;
    try {
      const body = await response.json();
      if (body && body.detail) {
        detail = typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail);
      }
    } catch (error) {
      /* keep the status text */
    }
    throw new Error(detail);
  }
  if (response.status === 204) return null;
  return response.json();
}

export const api = {
  health: () => request("/api/health"),
  config: () => request("/api/config"),
  providers: () => request("/api/providers"),
  resetProvider: (provider) =>
    request(`/api/providers/${provider}/reset`, { method: "POST" }),

  listProjects: () => request("/api/projects"),
  createProject: (body) =>
    request("/api/projects", { method: "POST", body: JSON.stringify(body) }),
  getProject: (id) => request(`/api/projects/${id}`),
  deleteProject: (id) => request(`/api/projects/${id}`, { method: "DELETE" }),
  replan: (id) => request(`/api/projects/${id}/plan`, { method: "POST" }),

  start: (id) => request(`/api/projects/${id}/start`, { method: "POST" }),
  pause: (id) => request(`/api/projects/${id}/pause`, { method: "POST" }),
  assemble: (id) => request(`/api/projects/${id}/assemble`, { method: "POST" }),

  patchScene: (id, sceneId, body) =>
    request(`/api/projects/${id}/scenes/${sceneId}`, {
      method: "PATCH",
      body: JSON.stringify(body),
    }),
  assignScene: (id, sceneId, body) =>
    request(`/api/projects/${id}/scenes/${sceneId}/assign`, {
      method: "POST",
      body: JSON.stringify(body),
    }),
  regenerateScene: (id, sceneId) =>
    request(`/api/projects/${id}/scenes/${sceneId}/regenerate`, { method: "POST" }),
  patchSettings: (id, body) =>
    request(`/api/projects/${id}/settings`, { method: "POST", body: JSON.stringify(body) }),

  library: () => request("/api/library"),
};

export function formatSeconds(value) {
  const total = Number(value) || 0;
  const minutes = Math.floor(total / 60);
  const seconds = Math.round(total % 60);
  if (minutes <= 0) return `${seconds}s`;
  return `${minutes}m ${String(seconds).padStart(2, "0")}s`;
}

export function statusTone(status) {
  switch (status) {
    case "completed":
      return "ok";
    case "running":
    case "assembling":
      return "busy";
    case "waiting":
    case "paused":
      return "warn";
    case "failed":
      return "bad";
    default:
      return "muted";
  }
}

export function providerLabel(providers, providerId) {
  if (!providerId) return null;
  const found = (providers || []).find((entry) => entry.provider === providerId);
  return found ? found.label || providerId : providerId;
}
