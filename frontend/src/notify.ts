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

/** The notifications shown through the page constructor, by tag: the only
 * handle there is to close one (the service worker ones are found again with
 * `getNotifications`). */
const pageNotes = new Map<string, Notification>()

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
    pageNotes.set(rest.tag, new Notification(title, rest))
    return true
  } catch {
    return false
  }
}

/** Take a notification off the tray: the event it announced was cancelled,
 * lifted, expired, or moved out of the watched place's reach. A cancelled
 * "take cover" early warning left in the tray is an alarm still ringing for
 * nothing (lane N's browser drill). Both paths, since either may have shown
 * it; closing a tag that is not there costs nothing. */
export async function closeNotification(tag: string): Promise<void> {
  pageNotes.get(tag)?.close()
  pageNotes.delete(tag)
  try {
    const registration = await navigator.serviceWorker?.getRegistration?.()
    const shown = await registration?.getNotifications?.({ tag })
    for (const note of shown ?? []) note.close()
  } catch {
    /* no worker, or a browser that cannot list them: nothing to close there */
  }
}
