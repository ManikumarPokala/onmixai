// App root: provider composition wrapping the route table. The Router itself lives in main
// (and tests supply a MemoryRouter), so this component is router-agnostic.

import { AppProviders } from './app/AppProviders'
import { AppRoutes } from './app/AppRoutes'

export function App() {
  return (
    <AppProviders>
      <AppRoutes />
    </AppProviders>
  )
}
