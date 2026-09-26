/**
 * The Menu area (owner, 2026-09-26): one nav entry, three places.
 *
 *   #/menu                 the list, with filters on top
 *   #/menu/<menu item id>  one item as a full page (prices, recipe, time, sales)
 *   #/menu/recipes         recipe (template) editing, formerly its own nav entry
 *
 * Old links keep working: `#/menu?item=<id>` (Ingredients' "used in") goes to
 * the item page and `#/recipes?t=<id>` goes to `#/menu/recipes?t=<id>`.
 */
import { useEffect } from 'react'
import { Loading } from '../../components/ui'
import { navigate, useLocation } from '../../lib/router'
import { RecipesScreen } from '../recipes/RecipesScreen'
import { ItemPage } from './ItemPage'
import { MenuItemsScreen } from './MenuItemsScreen'

export function MenuArea() {
  const loc = useLocation()
  const sub = loc.segments[1]
  const legacyItem = loc.query.get('item')
  const redirect = sub === undefined && legacyItem !== null && /^\d+$/.test(legacyItem)
  useEffect(() => {
    if (redirect) navigate(`/menu/${legacyItem}`, { replace: true })
  }, [redirect, legacyItem])
  if (redirect) return <Loading what="Opening the item" />
  if (sub === 'recipes') return <RecipesScreen />
  if (sub !== undefined && /^\d+$/.test(sub)) return <ItemPage menuItemId={Number(sub)} />
  return <MenuItemsScreen />
}

export function RecipesRedirect() {
  const loc = useLocation()
  useEffect(() => {
    navigate('/menu/recipes', { query: Object.fromEntries(loc.query.entries()), replace: true })
  }, [loc.query])
  return <Loading what="Opening recipes" />
}
