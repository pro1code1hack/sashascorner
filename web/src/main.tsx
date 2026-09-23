import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import '@fontsource/dm-mono/latin-300.css'
import '@fontsource/dm-mono/latin-400.css'
import '@fontsource/dm-mono/latin-500.css'
import './styles.css'
import { App } from './App'

const el = document.getElementById('root')
if (el === null) throw new Error('#root is missing from index.html')
createRoot(el).render(
  <StrictMode>
    <App />
  </StrictMode>,
)
