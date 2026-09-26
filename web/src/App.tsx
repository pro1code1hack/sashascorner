import { useState } from 'react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { LIVE, getKey } from './lib/api'
import { Shell, Unlock, type ScreenName } from './components/shell'
import { Composition } from './screens/Composition'
import { Stock } from './screens/Stock'
import { Today } from './screens/Today'
import { Orders } from './screens/Orders'
import { Margin } from './screens/Margin'
import { Channels } from './screens/Channels'
import { Money } from './screens/Money'
import { Proposals } from './screens/Proposals'

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
  const [screen, setScreen] = useState<ScreenName>('today')
  const [unlocked, setUnlocked] = useState(!LIVE || getKey() !== null)

  if (!unlocked) return <Unlock onDone={() => setUnlocked(true)} />

  return (
    <QueryClientProvider client={client}>
      <Shell screen={screen} onScreen={setScreen}>
        {screen === 'composition' && <Composition />}
        {screen === 'stock' && <Stock />}
        {screen === 'today' && <Today />}
        {screen === 'orders' && <Orders />}
        {screen === 'margin' && <Margin />}
        {screen === 'channels' && <Channels />}
        {screen === 'proposals' && <Proposals />}
        {screen === 'money' && <Money />}
      </Shell>
    </QueryClientProvider>
  )
}
