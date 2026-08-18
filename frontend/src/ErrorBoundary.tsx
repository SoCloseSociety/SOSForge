import { Component, type ErrorInfo, type ReactNode } from 'react'

/** Last line of defence against a blank page.
 *
 * A blank page is this product's worst failure: it is silent, the build stays
 * green through it, and someone looking for an earthquake sees nothing at all.
 * Lessons 1, 2 and 3 are all instances of it. React unmounts the entire tree on
 * an uncaught render error, so without a boundary a single bad event -- one
 * unexpected null in one row -- takes the whole tracker down.
 *
 * What it shows instead says where the data still is: the API is a separate
 * process and keeps serving even when this UI cannot render.
 */
interface State {
  error: Error | null
}

export class ErrorBoundary extends Component<{ children: ReactNode }, State> {
  state: State = { error: null }

  static getDerivedStateFromError(error: Error): State {
    return { error }
  }

  componentDidCatch(error: Error, info: ErrorInfo): void {
    console.error('SOSForge render error:', error, info.componentStack)
  }

  render(): ReactNode {
    if (!this.state.error) return this.props.children

    return (
      <div className="crash">
        <h1>SOSForge could not render this page</h1>
        <p>
          The live feed itself is still running. You can reload, or read the raw
          data directly:
        </p>
        <ul>
          <li>
            <a href="/api/events">/api/events</a> -- the recent events as JSON
          </li>
          <li>
            <a href="/api/sources">/api/sources</a> -- the state of every source
          </li>
        </ul>
        <button type="button" onClick={() => location.reload()}>
          Reload
        </button>
        <pre>{this.state.error.message}</pre>
      </div>
    )
  }
}
