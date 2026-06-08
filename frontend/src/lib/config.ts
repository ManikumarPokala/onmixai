// Runtime config. The API base is overridable via VITE_API_URL (e.g. a deployed origin);
// in dev it defaults to the same-origin prefix and Vite proxies it to the backend.
export const API_BASE = import.meta.env.VITE_API_URL ?? '/api/v1'
