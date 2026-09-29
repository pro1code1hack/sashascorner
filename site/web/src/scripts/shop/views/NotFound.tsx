import { TopBar } from '../components/TopBar';
import { paths } from '../router';
import { Button } from '../ui';
import { useTitle } from './useTitle';

export function NotFound({ what = 'page' }: { what?: 'page' | 'category' | 'item' }) {
  useTitle('Not found');
  return (
    <>
      <TopBar back={{ href: paths.overview(), label: 'Menu' }} />
      <section class="wrap sh-view sh-empty">
        <h1 class="sh-h1" tabIndex={-1}>
          {what === 'page' ? "There's nothing here" : what === 'category' ? "That category isn't on the menu" : "That item isn't on the menu"}
        </h1>
        <p>It may have moved, or the link is out of date.</p>
        <p>
          <Button href={paths.overview()} tone="caramel">
            See the menu
          </Button>
        </p>
      </section>
    </>
  );
}
