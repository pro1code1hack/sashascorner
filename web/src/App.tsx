import { Suspense, useEffect, useState } from 'react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { LIVE, SIGNED_OUT_EVENT, getCredential } from './lib/api'
import { navigate, useLocation } from './lib/router'
import { DEFAULT_ROUTE, ROUTES, resolveRoute } from './routes'
import { Shell } from './components/shell/Shell'
import { Login } from './components/shell/Login'
import { ScreenLoading } from './components/shell/ScreenFallback'

const client = new QueryClient({
  defaultOptions: {
    queries: {
      // Nothing in this app is urgent (spec §10) and nothing here changes
      // minute to minute, so it does not poll and it does not refetch behind
      // the reader's back while they are thinking.
      staleTime: 5 * 60 * 1000,
      refetchOnWindowFocus: false,
      retry: 1,
    },
  },
})

function Routed() {
  const loc = useLocation()
  const route = resolveRoute(loc)

  useEffect(() => {
    if (route === null) navigate(ROUTES[DEFAULT_ROUTE].path, { replace: true })
  }, [route])

  return (
    <Shell route={route}>
      {route && (
        <Suspense key={route.id} fallback={<ScreenLoading />}>
          <route.Screen />
        </Suspense>
      )}
    </Shell>
  )
}

export function App() {
  const [authed, setAuthed] = useState(() => !LIVE || getCredential() !== null)
  const [notice, setNotice] = useState<string | null>(null)

  // A live request answered 401: the password changed or the session ended.
  useEffect(() => {
    const onOut = () => {
      client.clear()
      setNotice('The password was changed or this session ended. Ask whoever runs the café.')
      setAuthed(false)
    }
    window.addEventListener(SIGNED_OUT_EVENT, onOut)
    return () => window.removeEventListener(SIGNED_OUT_EVENT, onOut)
  }, [])

  if (!authed) {
    return (
      <Login
        notice={notice}
        onSignedIn={() => {
          client.clear()
          setNotice(null)
          setAuthed(true)
        }}
      />
    )
  }

  return (
    <QueryClientProvider client={client}>
      <Routed />
    </QueryClientProvider>
  )
}
