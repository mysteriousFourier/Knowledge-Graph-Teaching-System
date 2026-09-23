// Nginx serves this module when a browser asks for a hashed bundle that was
// removed by a newer deployment. Reload once so index.html can point the
// browser at the current bundle instead of leaving an old SPA running.
const reloadKey = "kgts:stale-chunk-reload"
const now = Date.now()
const lastReload = Number(sessionStorage.getItem(reloadKey) || 0)

if (now - lastReload > 10_000) {
  sessionStorage.setItem(reloadKey, String(now))
  window.location.reload()
} else {
  throw new Error("The requested frontend bundle is no longer available. Reload the page to continue.")
}
