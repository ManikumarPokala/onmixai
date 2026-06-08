// Top-level error boundary for render-time crashes (CLAUDE.md §10: every async/render path
// has an explicit error state). Catches the React subtree, shows a recoverable message, and
// lets the user reset without a full reload.

import { Component, type ErrorInfo, type ReactNode } from 'react'

interface Props {
  children: ReactNode
}

interface State {
  error: Error | null
}

export class ErrorBoundary extends Component<Props, State> {
  state: State = { error: null }

  static getDerivedStateFromError(error: Error): State {
    return { error }
  }

  componentDidCatch(error: Error, info: ErrorInfo): void {
    // A real exporter (Sentry) goes here; console is the dev sink.
    console.error('Unhandled UI error', error, info.componentStack)
  }

  private reset = () => this.setState({ error: null })

  render(): ReactNode {
    if (this.state.error) {
      return (
        <div className="screen-center" role="alert">
          <div className="panel">
            <h1>Something went wrong</h1>
            <p>The page hit an unexpected error. You can try again.</p>
            <button type="button" className="btn" onClick={this.reset}>
              Try again
            </button>
          </div>
        </div>
      )
    }
    return this.props.children
  }
}
