/**
 * `#/website/menu` is retired (owner, 2026-09-29: "shop menu and menu itself
 * should be consolidated — it is the same thing", DECISIONS.md 29). The public
 * website's menu page reads the same shop catalogue as Order online, so its
 * descriptions, signature marks, visibility and order are edited on Menu items:
 * the "Online ordering" section of each item's page and the Categories drawer
 * on the list. The path redirects there.
 */
import { useEffect } from 'react'
import { Loading } from '../../components/ui'
import { navigate } from '../../lib/router'

export function SiteMenuRedirect() {
  useEffect(() => {
    navigate('/menu', { replace: true, query: { view: 'list' } })
  }, [])
  return <Loading what="Opening Menu items" />
}
