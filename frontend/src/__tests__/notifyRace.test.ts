/** Review of PR #5: on a place change, the close of the old place's
 * notification (two awaits: the registration, then getNotifications) raced
 * with the new place showing the SAME tag, and could close the new one. A
 * show now waits for any close still running on its tag. */
import { afterEach, describe, expect, it, vi } from 'vitest'
import { closeNotification, showNotification } from '../notify'

afterEach(() => vi.unstubAllGlobals())

describe('close, then show, on one tag', () => {
  it('the show lands after the close, never under it', async () => {
    const order: string[] = []
    let release: () => void = () => undefined
    const listed = new Promise<void>((resolve) => {
      release = resolve
    })
    const registration = {
      getNotifications: vi.fn(async () => {
        await listed // the browser is slow to list
        return [{ close: () => order.push('close') }]
      }),
      showNotification: vi.fn(async () => {
        order.push('show')
      }),
    }
    vi.stubGlobal('Notification', { permission: 'granted' })
    Object.defineProperty(navigator, 'serviceWorker', {
      configurable: true,
      value: { getRegistration: async () => registration },
    })
    const closing = closeNotification('usgs:q1')
    const showing = showNotification('Earthquakes -- Yokohama', { body: 'M6', tag: 'usgs:q1' })
    await Promise.resolve()
    release()
    await Promise.all([closing, showing])
    expect(order).toEqual(['close', 'show'])
  })
})
