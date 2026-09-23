import { useState } from 'react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { LIVE, getKey } from './lib/api'
import { KeyState, Masthead, Unlock, type ScreenName } from './components/shell'
import { Composition } from './screens/Composition'
import { Stock } from './screens/Stock'

const client = new QueryClient({
  defaultOptions: {
    queries: {
      // Nothing in this app is urgent (spec §10) and nothing on these two
      // screens changes minute to minute, so it does not poll and it does not
      // refetch behind the reader's back while they are thinking.
      staleTime: 5 * 60 * 1000,
      refetchOnWindowFocus: false,
      retry: 1,
    },
  },
})

export function App() {
  const [screen, setScreen] = useState<ScreenName>('composition')
  const [unlocked, setUnlocked] = useState(!LIVE || getKey() !== null)

  if (!unlocked) return <Unlock onDone={() => setUnlocked(true)} />

  return (
    <QueryClientProvider client={client}>
      <Masthead screen={screen} onScreen={setScreen} />
      {screen === 'composition' ? <Composition /> : <Stock />}
      <KeyState />
    </QueryClientProvider>
  )
}
