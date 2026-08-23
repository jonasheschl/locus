export async function api(path, options = {}) {
  const isForm = typeof FormData !== 'undefined' && options.body instanceof FormData;
  const response = await fetch(path, {
    ...options,
    headers: {
      ...(options.body && !isForm ? { 'Content-Type': 'application/json' } : {}),
      ...(options.headers || {})
    }
  });
  if (!response.ok) {
    let message = `Request failed (${response.status})`;
    try {
      const body = await response.json();
      message = body.detail || message;
    } catch (_) {
      // Keep the status-based message.
    }
    throw new Error(message);
  }
  return response.json();
}

export const getNotes = () => api('/api/notes');
export const getFiles = () => api('/api/files');
export const getStats = () => api('/api/stats');
export const getAuthStatus = () => api('/api/auth/codex/status');
export const getNote = (path) => api(`/api/notes/${encodeURIComponent(path)}`);
export const getSpreadsheet = (path) => api(`/api/spreadsheets/${encodeURIComponent(path)}`);
export const getIngestItem = (path) => api(`/api/ingest/items/${encodeURIComponent(path)}`);
export const getThreads = () => api('/api/chat/threads');
export const getThread = (id) => api(`/api/chat/threads/${encodeURIComponent(id)}`);
export const searchNotes = (query) => api(`/api/search?q=${encodeURIComponent(query)}`);
