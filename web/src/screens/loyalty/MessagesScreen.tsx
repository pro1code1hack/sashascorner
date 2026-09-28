/**
 * Loyalty card › Messages (`#/loyalty/messages`): lock-screen messages through the
 * wallet card. docs/loyalty/BACKOFFICE-V2.md §3.
 *
 * Left: what has gone out and what is scheduled, with how many came back within a
 * week. Right: the composer. On a narrow screen the composer comes first — writing a
 * message is why somebody opens this tab.
 */
import { Empty, ErrorBox, Loading, PageBody, cx } from '../../components/ui'
import { useLoyaltyCampaigns } from '../../lib/loyalty-api'
import type { LoyaltyCampaignsResponse } from '../../lib/types/loyalty'
import { LoyaltyHeader } from './LoyaltyHeader'
import { CampaignCard } from './messages/CampaignCard'
import { Composer } from './messages/Composer'

export function MessagesScreen() {
  const q = useLoyaltyCampaigns()
  return (
    <>
      <LoyaltyHeader current="messages" />
      {q.isError && !q.data ? (
        <PageBody>
          <ErrorBox error={q.error} what="messages" />
        </PageBody>
      ) : !q.data ? (
        <PageBody>
          <Loading what="Loading messages" />
        </PageBody>
      ) : (
        <PageBody flush>
          <div className="grid min-h-full grid-cols-1 md:grid-cols-[minmax(0,1fr)_minmax(18rem,22rem)]">
            <div className="order-2 min-w-0 bg-canvas px-4 py-5 sm:px-5 md:order-1">
              <List data={q.data} />
            </div>
            <aside className="order-1 min-w-0 border-b border-line bg-surface px-4 py-5 sm:px-5 md:order-2 md:border-b-0 md:border-l">
              <Composer data={q.data} />
            </aside>
          </div>
        </PageBody>
      )}
    </>
  )
}

function List({ data }: { data: LoyaltyCampaignsResponse }) {
  const limit = data.promo_limit_per_month
  const used = data.promos_this_month
  return (
    <div className="flex flex-col gap-3">
      <div>
        <h2 className="text-xl font-extrabold tracking-[-.01em]">Messages to members</h2>
        <p className="text-base text-ink-2">
          They land on the lock screen through the wallet card. Only people who ticked the box get promotions.
        </p>
      </div>
      <div className="inline-flex w-fit items-center gap-2 rounded-full border border-line bg-surface px-3 py-1.5 text-sm">
        <span className="flex gap-1" aria-hidden="true">
          {Array.from({ length: limit }, (_, i) => (
            <span key={i} className={cx('size-2.5 rounded-full', i < used ? 'bg-brand' : 'bg-line-strong')} />
          ))}
        </span>
        <span>
          <span className="fig font-bold">
            {used} of {limit}
          </span>{' '}
          <span className="text-ink-2">promos this month, max {limit} per member</span>
        </span>
      </div>
      {data.campaigns.length === 0 ? (
        <Empty>Nothing sent yet. The first message you write shows up here.</Empty>
      ) : (
        data.campaigns.map((c) => <CampaignCard key={c.id} c={c} />)
      )}
    </div>
  )
}
