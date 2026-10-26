/** Show one system notification, the way this browser actually allows.
 *
 * `new Notification()` is what desktop browsers accept, and Chrome on Android
 * REFUSES it: the constructor throws "Illegal constructor, use
 * ServiceWorkerRegistration.showNotification()". The watch alarm used the
 * constructor alone and caught the error, so on the phone browser most people
 * carry, a granted permission produced no notification at all, and nothing
 * said so. The service worker path works on both, so it is tried first; the
 * constructor stays as the fallback for a page with no worker (dev server,
 * private mode, a browser without service workers).
 *
 * Resolves to whether a notification was actually handed to the system, so
 * the caller can say so instead of assuming it.
 */
export interface NotifyOptions {
  body: string
  tag: string
  /** where a click should take the reader (a deep link to the event) */
  url?: string
}

export async function showNotification(title: string, options: NotifyOptions): Promise<boolean> {
  if (typeof Notification === 'undefined' || Notification.permission !== 'granted') return false
  const { url, ...rest } = options
  try {
    const registration = await navigator.serviceWorker?.getRegistration?.()
    if (registration) {
      await registration.showNotification(title, { ...rest, data: { url: url ?? '/' } })
      return true
    }
  } catch {
    /* fall through to the page constructor */
  }
  try {
    new Notification(title, rest)
    return true
  } catch {
    return false
  }
}
