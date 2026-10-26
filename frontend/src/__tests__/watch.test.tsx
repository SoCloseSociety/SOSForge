/** The watch funnel: place -> what to watch -> notifications (reason FIRST)
 * -> confirmation, with its honest states said in place. */
import { fireEvent, render, screen } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { WatchPanel } from '../components/WatchPanel'
import { useStore } from '../store'
import { resetStore } from './helpers'

function stubNotification(permission: 'default' | 'granted' | 'denied') {
  const requestPermission = vi.fn(async () => 'granted' as const)
  class FakeNotification {
    static permission = permission
    static requestPermission = requestPermission
  }
  vi.stubGlobal('Notification', FakeNotification)
  return requestPermission
}

const TOKYO = { lat: 35.68, lon: 139.69, name: 'Tokyo' }

beforeEach(() => {
  resetStore()
  localStorage.clear()
})
afterEach(() => vi.unstubAllGlobals())

describe('step 1: the place', () => {
  it('asks for the place with ONE primary action, and says the location stays here', () => {
    stubNotification('default')
    const { container } = render(<WatchPanel alarms={[]} />)
    expect(screen.getByText('Step 1 of 3')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /Use my location/ })).toHaveClass('btn-primary')
    expect(container.querySelectorAll('.btn-primary')).toHaveLength(1)
    expect(screen.getByText(/never leaves your device/)).toBeInTheDocument()
  })
})

describe('step 2: what to watch', () => {
  it('starts with every kind on, and tsunamis cannot be switched off', () => {
    stubNotification('default')
    useStore.setState({ watch: TOKYO })
    render(<WatchPanel alarms={[]} />)
    expect(screen.getByText('Step 2 of 3')).toBeInTheDocument()
    const tsunami = screen.getByRole('button', { name: /Tsunamis/ })
    expect(tsunami).toHaveAttribute('aria-pressed', 'true')
    expect(tsunami).toHaveAttribute('aria-disabled', 'true')
    fireEvent.click(tsunami)
    expect(tsunami).toHaveAttribute('aria-pressed', 'true')
    // space weather has no position: it is not offered as something to watch
    expect(screen.queryByRole('button', { name: /Space weather/ })).toBeNull()
  })

  it('saves the choice, tsunamis included, and the alarm reads it', () => {
    stubNotification('denied')
    useStore.setState({ watch: TOKYO })
    render(<WatchPanel alarms={[]} />)
    fireEvent.click(screen.getByRole('button', { name: /Heat/ }))
    fireEvent.click(screen.getByRole('button', { name: 'Continue' }))
    const kinds = useStore.getState().watch?.kinds ?? []
    expect(kinds).toContain('tsunami')
    expect(kinds).toContain('earthquake')
    expect(kinds).not.toContain('heat')
    expect(JSON.parse(localStorage.getItem('sosforge.watch') ?? '{}').kinds).toEqual(kinds)
  })
})

describe('step 3: notifications, the reason before the prompt', () => {
  it('shows why BEFORE the browser asks, and asks only on the click', () => {
    const requestPermission = stubNotification('default')
    useStore.setState({ watch: TOKYO })
    render(<WatchPanel alarms={[]} />)
    fireEvent.click(screen.getByRole('button', { name: 'Continue' }))
    expect(screen.getByText('Step 3 of 3')).toBeInTheDocument()
    expect(screen.getByText(/reach you in another tab/)).toBeInTheDocument()
    expect(requestPermission).not.toHaveBeenCalled()
    fireEvent.click(screen.getByRole('button', { name: 'Enable notifications' }))
    expect(requestPermission).toHaveBeenCalledTimes(1)
  })

  it('"Not now" keeps the watch and lands on the confirmation, which still offers it', () => {
    stubNotification('default')
    useStore.setState({ watch: TOKYO })
    render(<WatchPanel alarms={[]} />)
    fireEvent.click(screen.getByRole('button', { name: 'Continue' }))
    fireEvent.click(screen.getByRole('button', { name: 'Not now' }))
    expect(screen.getByText('Watching')).toBeInTheDocument()
    expect(useStore.getState().watch?.name).toBe('Tokyo')
    expect(screen.getByRole('button', { name: 'Enable notifications' })).toBeInTheDocument()
  })

  it('is skipped when the browser already decided', () => {
    stubNotification('granted')
    useStore.setState({ watch: TOKYO })
    render(<WatchPanel alarms={[]} />)
    fireEvent.click(screen.getByRole('button', { name: 'Continue' }))
    expect(screen.queryByText('Step 3 of 3')).toBeNull()
    expect(screen.getByText('Notifications on')).toBeInTheDocument()
  })
})

describe('confirmation: the honest states', () => {
  const watched = { ...TOKYO, kinds: ['earthquake' as const, 'tsunami' as const] }

  it('says a closed tab cannot alert, and points to the Android app', () => {
    stubNotification('granted')
    useStore.setState({ watch: watched })
    render(<WatchPanel alarms={[]} />)
    expect(screen.getByText(/A closed tab cannot alert you/)).toBeInTheDocument()
    expect(screen.getByText(/Android app/)).toBeInTheDocument()
    expect(screen.getByText('Nothing concerns this area')).toBeInTheDocument()
  })

  it('says notifications are blocked, with where to unblock them', () => {
    stubNotification('denied')
    useStore.setState({ watch: watched })
    render(<WatchPanel alarms={[]} />)
    expect(screen.getByText(/Notifications blocked/)).toBeInTheDocument()
  })

  it('says when the browser has no notifications at all', () => {
    vi.stubGlobal('Notification', undefined)
    useStore.setState({ watch: watched })
    render(<WatchPanel alarms={[]} />)
    expect(screen.getByText(/cannot show notifications/)).toBeInTheDocument()
  })

  it('says the sound is off, since the alarm would then be silent', () => {
    stubNotification('granted')
    useStore.setState({ watch: watched, soundOn: false })
    render(<WatchPanel alarms={[]} />)
    expect(screen.getByText(/Sound is off/)).toBeInTheDocument()
  })

  it('"Change" goes back to the choice, on the saved kinds', () => {
    stubNotification('granted')
    useStore.setState({ watch: watched })
    render(<WatchPanel alarms={[]} />)
    fireEvent.click(screen.getByRole('button', { name: 'Change' }))
    expect(screen.getByRole('button', { name: /Earthquakes/ })).toHaveAttribute('aria-pressed', 'true')
    expect(screen.getByRole('button', { name: /Volcanoes/ })).toHaveAttribute('aria-pressed', 'false')
  })

  it('the rules behind "Show more" are the ones the alarm applies', () => {
    stubNotification('granted')
    useStore.setState({ watch: watched })
    render(<WatchPanel alarms={[]} />)
    fireEvent.click(screen.getByRole('button', { name: 'Show more' }))
    expect(screen.getByText('Earthquakes: Moderate and up, as far as they are felt')).toBeInTheDocument()
    expect(screen.getByText('Tsunamis: Moderate and up, within 600 km')).toBeInTheDocument()
  })
})

describe('the alarm memory', () => {
  it('a new place starts clean, a change of kinds at the same place does not', () => {
    useStore.getState().setWatch(TOKYO)
    useStore.getState().markAlarmed(['emsc:1'])
    useStore.getState().setWatch({ ...TOKYO, kinds: ['earthquake', 'tsunami'] })
    expect(useStore.getState().alarmed.has('emsc:1')).toBe(true)
    useStore.getState().setWatch({ lat: 38.27, lon: 140.87, name: 'Sendai' })
    expect(useStore.getState().alarmed.size).toBe(0)
  })
})
