/**
 * `#/shop/menu` is retired (owner, 2026-09-29: "shop menu and menu itself should
 * be consolidated — it is the same thing"). What lived here moved into Menu
 * items: the Online column, filter and bulk actions on the list, the "Online
 * ordering" section on each item's page (screens/shop/OnlineSection.tsx) and the
 * Categories drawer (screens/shop/CategoriesDrawer.tsx). The path redirects.
 */
import { useEffect } from 'react'
import { Loading } from '../../components/ui'
import { navigate } from '../../lib/router'

export function ShopMenuRedirect() {
  useEffect(() => {
    navigate('/menu', { replace: true, query: { view: 'list' } })
  }, [])
  return <Loading what="Opening Menu items" />
}
