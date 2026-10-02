export class ApiError extends Error {
  constructor(message, status, data) {
    super(message)
    this.status = status
    this.data = data
  }
}

function getCookie(name) {
  const match = document.cookie.split('; ').find((row) => row.startsWith(name + '='))
  return match ? decodeURIComponent(match.split('=')[1]) : ''
}

/** Drops empty values and serialises arrays as comma separated lists. */
export function cleanParams(params = {}) {
  const out = {}
  for (const [key, value] of Object.entries(params)) {
    if (value === undefined || value === null || value === '') continue
    if (Array.isArray(value)) {
      if (value.length) out[key] = value.join(',')
      continue
    }
    out[key] = String(value)
  }
  return out
}

export async function api(path, { method = 'GET', params, body } = {}) {
  const url = new URL('/api' + path, window.location.origin)
  const query = cleanParams(params)
  for (const [key, value] of Object.entries(query)) url.searchParams.set(key, value)

  const headers = { Accept: 'application/json' }
  if (method !== 'GET') {
    headers['Content-Type'] = 'application/json'
    headers['X-CSRFToken'] = getCookie('csrftoken')
  }
  const response = await fetch(url, {
    method,
    headers,
    credentials: 'same-origin',
    body: body === undefined ? undefined : JSON.stringify(body),
  })

  if (response.status === 204) return null
  let data = null
  try {
    data = await response.json()
  } catch {
    data = null
  }
  if (!response.ok) {
    throw new ApiError(data?.detail || `Request failed (${response.status})`, response.status, data)
  }
  return data
}
