/**
 * Website › Events › one event: the form (add or edit, publish or unpublish),
 * delete, and the replies (RSVPs) with cancel / restore.
 *
 * Writes go through `siteWrite`, which never throws; a refusal is shown as the
 * site wrote it, next to the field it is about when we can tell which.
 */
import { useRef, useState } from "react";
import type { FormEvent, ReactNode } from "react";
import { useQuery } from "@tanstack/react-query";
import {
  Button,
  Checkbox,
  ConfirmTwiceButton,
  Empty,
  ErrorBox,
  Field,
  Input,
  Loading,
  MoneyInput,
  PageBody,
  PageHeader,
  Pill,
  Select,
  Textarea,
  WarnBox,
  cx,
} from "../../components/ui";
import { ago, plural, stamp } from "../../lib/format";
import { href, navigate, useLocation } from "../../lib/router";
import type { Media } from "../../lib/types/website";
import {
  WEBSITE_KEY,
  livePageUrl,
  mediaSrcset,
  mediaUrl,
  siteGet,
  siteWrite,
  useInvalidateWebsite,
  useWebsiteConnection,
} from "../../lib/website-api";
import { WebsiteGate } from "./shared";
import {
  EventStatus,
  longDate,
  parsePence,
  penceToInput,
  seatsText,
  timeRange,
  todayLondon,
  useEvents,
  useRsvps,
} from "./events-shared";
import type {
  AdminEvent,
  AdminEventIn,
  AdminRsvp,
  DeletedOut,
} from "./events-shared";

/* ---------------------------------------------------------------- pages --- */

function BackLink() {
  return (
    <a
      href={href("/website/events")}
      className="inline-flex min-h-11 items-center text-base text-ink-2 underline underline-offset-2 hover:text-ink sm:min-h-0"
    >
      ← All events
    </a>
  );
}

export function NewEventPage() {
  return (
    <>
      <PageHeader title="New event" subtitle={<BackLink />} />
      <PageBody className="bg-canvas">
        <WebsiteGate>
          <div className="mx-auto max-w-[720px]">
            <Panel title="The event" id="ev-form-h">
              <EventForm event={null} />
            </Panel>
          </div>
        </WebsiteGate>
      </PageBody>
    </>
  );
}

export function EventPage({ eventId }: { eventId: number }) {
  const q = useEvents();
  const conn = useWebsiteConnection();
  const e = q.data?.find((x) => x.id === eventId);
  const live =
    e && e.published && !e.past
      ? livePageUrl(conn.data, `/events#${e.slug}`)
      : null;
  return (
    <>
      <PageHeader
        title={e?.title ?? "Event"}
        subtitle={<BackLink />}
        actions={
          live ? (
            <a
              href={live}
              target="_blank"
              rel="noreferrer"
              className="inline-flex min-h-11 items-center text-base text-brand-ink underline underline-offset-2 sm:min-h-0"
            >
              See it on the website ↗
            </a>
          ) : undefined
        }
      />
      <PageBody className="bg-canvas">
        <WebsiteGate>
          <EventPageBody eventId={eventId} />
        </WebsiteGate>
      </PageBody>
    </>
  );
}

function EventPageBody({ eventId }: { eventId: number }) {
  const q = useEvents();
  const created = useLocation().query.get("created");
  const e = q.data?.find((x) => x.id === eventId);
  return (
    <>
      {q.isError && <ErrorBox error={q.error} what="the event" />}
      {q.isPending && <Loading what="Reading the event" />}
      {q.data && !e && (
        <Empty action={<BackLink />}>
          There is no event with this number. It may have been deleted.
        </Empty>
      )}
      {e && (
        <div className="mx-auto flex max-w-[1180px] flex-col gap-4">
          <p className="flex flex-wrap items-center gap-x-3 gap-y-1 text-base">
            <span className="fig font-bold">
              {longDate(e.date)}, {timeRange(e)}
            </span>
            <EventStatus e={e} />
            <span className="fig text-ink-2">{seatsText(e)}</span>
          </p>
          {created && (
            <p role="status" className="text-sm text-ink-2">
              {created === "live"
                ? "Added. It is on the website."
                : "Added as a draft: not on the website yet."}
            </p>
          )}
          <div className="grid gap-4 compact:grid-cols-[minmax(0,1fr)_minmax(320px,420px)]">
            <div className="flex min-w-0 flex-col gap-4">
              <Panel title="The event" id="ev-form-h">
                {/* Re-seed the form when the event is deleted-and-recreated under the same page, never on refetch. */}
                <EventForm key={e.id} event={e} />
              </Panel>
              <DeleteEvent e={e} />
            </div>
            <Replies e={e} />
          </div>
        </div>
      )}
    </>
  );
}

function Panel({
  title,
  id,
  right,
  children,
  className,
}: {
  title: ReactNode;
  id: string;
  right?: ReactNode;
  children: ReactNode;
  className?: string;
}) {
  return (
    <section
      aria-labelledby={id}
      className={cx(
        "min-w-0 rounded-card-lg border border-line bg-surface px-4 py-4 sm:px-5",
        className,
      )}
    >
      <div className="mb-3 flex flex-wrap items-baseline justify-between gap-x-3 gap-y-1">
        <h2 id={id} className="text-lg font-extrabold tracking-[-.01em]">
          {title}
        </h2>
        {right}
      </div>
      {children}
    </section>
  );
}

type Outcome = { tone: "ok" | "bad"; text: string } | null;

function OutcomeLine({
  outcome,
  className,
}: {
  outcome: Outcome;
  className?: string;
}) {
  if (!outcome) return null;
  return (
    <p
      role={outcome.tone === "bad" ? "alert" : "status"}
      className={cx(
        "text-sm",
        outcome.tone === "bad" ? "text-bad-ink" : "text-ink-2",
        className,
      )}
    >
      {outcome.text}
    </p>
  );
}

/* ----------------------------------------------------------------- form --- */

type FieldKey =
  "title" | "date" | "start" | "end" | "price" | "capacity" | "image" | "slug";

/** Which field a server refusal is about, when its words say so. */
function fieldOf(message: string): FieldKey | null {
  const m = message.toLowerCase();
  if (m.includes("slug") || m.includes("web address")) return "slug";
  if (m.includes("after the start")) return "end";
  if (m.includes("photo")) return "image";
  return null;
}

function EventForm({ event }: { event: AdminEvent | null }) {
  const invalidate = useInvalidateWebsite();
  const photos = useQuery({
    queryKey: [...WEBSITE_KEY, "media"],
    queryFn: () => siteGet<Media[]>("/media"),
  });
  const [title, setTitle] = useState(event?.title ?? "");
  const [date, setDate] = useState(event?.date ?? "");
  const [start, setStart] = useState(event?.start ?? "18:00");
  const [end, setEnd] = useState(event?.end ?? "");
  const [desc, setDesc] = useState(event?.description ?? "");
  const [price, setPrice] = useState(penceToInput(event?.price_pence ?? null));
  const [cap, setCap] = useState(
    event?.capacity != null ? String(event.capacity) : "",
  );
  const [image, setImage] = useState(
    event?.image_media_id != null ? String(event.image_media_id) : "",
  );
  const [slug, setSlug] = useState(event?.slug ?? "");
  const [published, setPublished] = useState(event?.published ?? false);
  const [errors, setErrors] = useState<Partial<Record<FieldKey, string>>>({});
  const [outcome, setOutcome] = useState<Outcome>(null);
  const [busy, setBusy] = useState(false);
  const inFlight = useRef(false);

  const photoList = photos.data ?? [];
  const chosen = photoList.find((m) => String(m.id) === image) ?? null;
  // The event's own photo still shows while the library loads (or if it cannot be read).
  const preview = chosen
    ? { src: chosen.src, srcset: chosen.srcset, alt: chosen.alt }
    : event?.image && String(event.image.media_id) === image
      ? event.image
      : null;

  async function submit(ev: FormEvent) {
    ev.preventDefault();
    if (inFlight.current) return;
    setOutcome(null);
    const p = parsePence(price);
    const local: Partial<Record<FieldKey, string>> = {};
    if (!title.trim()) local.title = "Give it a name.";
    if (!date) local.date = "Pick a date.";
    if (!start) local.start = "Pick a start time.";
    if (end && start && end.slice(0, 5) <= start.slice(0, 5))
      local.end = "Must be after the start.";
    if (p === "bad") local.price = "A price like 12 or 12.50.";
    if (cap && (!/^\d+$/.test(cap) || Number(cap) < 1 || Number(cap) > 500))
      local.capacity = "A whole number from 1 to 500, or empty.";
    if (slug.trim() && !/^[a-z0-9]+(-[a-z0-9]+)*$/.test(slug.trim()))
      local.slug =
        "Small letters, numbers and single dashes only, like quiz-night-oct.";
    setErrors(local);
    if (Object.keys(local).length > 0 || p === "bad") return;

    const body: AdminEventIn = {
      title: title.trim(),
      date,
      start: start.slice(0, 5),
      end: end ? end.slice(0, 5) : null,
      description: desc.trim(),
      price_pence: p,
      capacity: cap ? Number(cap) : null,
      image_media_id: image ? Number(image) : null,
      published,
    };
    if (slug.trim() || event) body.slug = slug.trim() || event?.slug;

    inFlight.current = true;
    setBusy(true);
    const r = event
      ? await siteWrite<AdminEvent>(`/events/${event.id}`, body, "PATCH")
      : await siteWrite<AdminEvent>("/events", body, "POST");
    inFlight.current = false;
    setBusy(false);
    if (r.kind === "ok") {
      void invalidate();
      if (!event) {
        navigate(`/website/events/${r.data.id}`, {
          replace: true,
          query: { created: r.data.published ? "live" : "draft" },
        });
        return;
      }
      setSlug(r.data.slug);
      // Drop the one-off "Added…" note from the address once the event has been edited.
      navigate(`/website/events/${r.data.id}`, { replace: true });
      setOutcome({
        tone: "ok",
        text: r.data.published
          ? "Saved. It is on the website."
          : "Saved as a draft: not on the website.",
      });
      return;
    }
    const f = r.kind === "refused" ? fieldOf(r.message) : null;
    if (f) setErrors({ [f]: r.message });
    else setOutcome({ tone: "bad", text: r.message });
  }

  return (
    <form className="flex flex-col gap-3.5" noValidate onSubmit={submit}>
      <Field label="Title" error={errors.title}>
        <Input
          value={title}
          onChange={(e) => setTitle(e.target.value)}
          maxLength={120}
          required
        />
      </Field>
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-3">
        <Field
          label="Date"
          error={errors.date}
          className="col-span-2 sm:col-span-1"
        >
          <Input
            type="date"
            value={date}
            onChange={(e) => setDate(e.target.value)}
            min={event ? undefined : todayLondon()}
            required
          />
        </Field>
        <Field label="Starts" error={errors.start}>
          <Input
            type="time"
            value={start}
            onChange={(e) => setStart(e.target.value)}
            step={300}
            required
          />
        </Field>
        <Field label="Ends" hint="Optional" error={errors.end}>
          <Input
            type="time"
            value={end}
            onChange={(e) => setEnd(e.target.value)}
            step={300}
          />
        </Field>
      </div>
      <Field
        label="What happens"
        hint="A few plain sentences. A blank line starts a new paragraph."
      >
        <Textarea
          value={desc}
          onChange={(e) => setDesc(e.target.value)}
          rows={6}
          maxLength={4000}
          className="min-h-36"
        />
      </Field>
      <div className="grid gap-3 sm:grid-cols-2">
        <Field
          label="Price"
          hint="Leave empty if there is no charge to show."
          error={errors.price}
        >
          <MoneyInput
            value={price}
            onChange={(e) => setPrice(e.target.value)}
            placeholder="12.50"
          />
        </Field>
        <Field
          label="Places"
          hint="People, not replies. Empty means no limit."
          error={errors.capacity}
        >
          <Input
            value={cap}
            onChange={(e) => setCap(e.target.value)}
            inputMode="numeric"
            className="fig"
          />
        </Field>
      </div>
      <Field
        label="Photo"
        error={errors.image}
        hint={
          photos.isError ? (
            "Couldn’t read the Photos library; the current choice is kept."
          ) : photos.isPending ? (
            "Reading the Photos library…"
          ) : photoList.length ? (
            "From the Photos library."
          ) : (
            <>
              Upload photos in{" "}
              <a
                href={href("/website/photos")}
                className="underline underline-offset-2"
              >
                Photos
              </a>{" "}
              first.
            </>
          )
        }
      >
        <Select value={image} onChange={(e) => setImage(e.target.value)}>
          <option value="">No photo</option>
          {/* Keep the current choice selectable while the library loads. */}
          {event?.image &&
            !photoList.some((m) => m.id === event.image?.media_id) && (
              <option value={String(event.image.media_id)}>
                {event.image.alt || `Photo ${event.image.media_id}`}
              </option>
            )}
          {photoList.map((m) => (
            <option key={m.id} value={String(m.id)}>
              {m.alt
                ? `${m.alt.slice(0, 60)}${m.alt.length > 60 ? "…" : ""}`
                : m.original_name}
            </option>
          ))}
        </Select>
      </Field>
      {preview && (
        <img
          src={mediaUrl(preview.src)}
          srcSet={mediaSrcset(preview.srcset)}
          sizes="240px"
          alt={preview.alt}
          className="aspect-[4/3] w-full max-w-60 rounded-card border border-line object-cover"
        />
      )}
      <Field
        label="Web address"
        error={errors.slug}
        hint={
          event
            ? `…/events#${event.slug}. Changing it breaks links already shared.`
            : "Optional: made from the title and date when empty."
        }
      >
        <Input
          value={slug}
          onChange={(e) => setSlug(e.target.value)}
          maxLength={80}
          placeholder="made from the title and date"
          autoCapitalize="none"
          spellCheck={false}
        />
      </Field>
      <Checkbox
        checked={published}
        onChange={setPublished}
        label="Show it on the website"
        className="min-h-11 sm:min-h-0"
      />
      <div className="flex flex-wrap items-center gap-3 pt-1">
        <Button
          type="submit"
          variant="primary"
          pending={busy}
          pendingLabel="Saving…"
          className="max-sm:h-11"
        >
          {event ? "Save changes" : "Add event"}
        </Button>
        <OutcomeLine outcome={outcome} />
      </div>
    </form>
  );
}

/* --------------------------------------------------------------- delete --- */

function DeleteEvent({ e }: { e: AdminEvent }) {
  const invalidate = useInvalidateWebsite();
  const [busy, setBusy] = useState(false);
  const [hasReplies, setHasReplies] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function del(force: boolean) {
    if (busy) return;
    setBusy(true);
    setError(null);
    const r = await siteWrite<DeletedOut>(
      `/events/${e.id}${force ? "?force=1" : ""}`,
      undefined,
      "DELETE",
    );
    setBusy(false);
    if (r.kind === "ok") {
      await invalidate();
      navigate("/website/events", {
        replace: true,
        query: { tab: e.past ? "past" : undefined },
      });
      return;
    }
    // 409: it has replies. Say so, and offer the forced delete as a separate, deliberate step.
    if (!force && r.kind === "refused" && /repl/i.test(r.message))
      setHasReplies(r.message);
    else setError(r.message);
  }

  return (
    <Panel title="Delete" id="ev-del-h">
      {hasReplies === null ? (
        <>
          <p className="mb-3 text-base text-ink-2">
            Deleting takes it off the website for good. To hide it but keep it
            and its replies, untick “Show it on the website” instead.
          </p>
          <ConfirmTwiceButton
            armedLabel="Tap again to delete"
            onConfirm={() => void del(false)}
            pending={busy}
            pendingLabel="Deleting…"
            size="md"
            className="max-sm:h-11"
          >
            Delete event
          </ConfirmTwiceButton>
        </>
      ) : (
        <WarnBox>
          <div className="font-bold">{hasReplies}</div>
          <div className="mt-2 flex flex-wrap gap-2">
            <ConfirmTwiceButton
              armedLabel="Tap again: delete event and replies"
              onConfirm={() => void del(true)}
              pending={busy}
              pendingLabel="Deleting…"
              size="md"
              className="max-sm:h-11"
            >
              Delete it with its replies
            </ConfirmTwiceButton>
            <Button
              variant="secondary"
              onClick={() => setHasReplies(null)}
              disabled={busy}
              className="max-sm:h-11"
            >
              Keep it
            </Button>
          </div>
        </WarnBox>
      )}
      {error && (
        <p role="alert" className="mt-2 text-sm text-bad-ink">
          {error}
        </p>
      )}
    </Panel>
  );
}

/* -------------------------------------------------------------- replies --- */

function Replies({ e }: { e: AdminEvent }) {
  const q = useRsvps(e.id);
  const rows = q.data ?? [];
  const live = rows.filter((r) => !r.cancelled_at);
  const people = live.reduce((n, r) => n + r.party, 0);
  return (
    <Panel title="Replies" id="ev-rsvp-h" className="self-start">
      {q.isError && <ErrorBox error={q.error} what="the replies" />}
      {q.isPending && <Loading what="Reading the replies" />}
      {q.data && (
        <>
          <p className="fig mb-3 text-base">
            {live.length} {plural(live.length, "reply", "replies")}, {people}{" "}
            {plural(people, "person", "people")}
            {e.capacity !== null && ` of ${e.capacity} places`}.
            {rows.length > live.length && (
              <span className="text-ink-2">
                {" "}
                {rows.length - live.length} cancelled.
              </span>
            )}
          </p>
          {rows.length === 0 ? (
            <p className="text-base text-ink-2">No replies yet.</p>
          ) : (
            <ul className="border-t border-line">
              {rows.map((r) => (
                <ReplyRow key={r.id} e={e} r={r} />
              ))}
            </ul>
          )}
        </>
      )}
    </Panel>
  );
}

function ReplyRow({ e, r }: { e: AdminEvent; r: AdminRsvp }) {
  const invalidate = useInvalidateWebsite();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const cancelled = r.cancelled_at !== null;

  async function toggle() {
    if (busy) return;
    setBusy(true);
    setError(null);
    const res = await siteWrite<AdminRsvp>(
      `/events/${e.id}/rsvps/${r.id}`,
      { cancelled: !cancelled },
      "PATCH",
    );
    if (res.kind === "ok") await invalidate();
    else setError(res.message);
    setBusy(false);
  }

  const contacts: ReactNode[] = [];
  if (r.phone)
    contacts.push(
      <a
        key="tel"
        href={`tel:${r.phone.replace(/[^\d+]/g, "")}`}
        className="underline underline-offset-2"
      >
        {r.phone}
      </a>,
    );
  if (r.email)
    contacts.push(
      <a
        key="mail"
        href={`mailto:${r.email}?subject=${encodeURIComponent(e.title)}`}
        className="underline underline-offset-2 [overflow-wrap:anywhere]"
      >
        {r.email}
      </a>,
    );

  return (
    <li className="grid grid-cols-[minmax(0,1fr)_auto] gap-x-3 gap-y-1 border-b border-line py-3">
      <span className="flex min-w-0 flex-wrap items-center gap-x-2 gap-y-1">
        <span
          className={cx(
            "font-bold [overflow-wrap:anywhere]",
            cancelled && "line-through",
          )}
        >
          {r.name}
        </span>
        <span className="fig text-ink-2">
          · {r.party} {plural(r.party, "person", "people")}
        </span>
        {cancelled && <Pill tone="muted">Cancelled</Pill>}
      </span>
      <Button
        variant="secondary"
        size="sm"
        onClick={() => void toggle()}
        pending={busy}
        pendingLabel="Saving…"
        className="max-sm:h-11"
      >
        {cancelled ? "Restore" : "Cancel reply"}
      </Button>
      <span className="col-span-2 text-sm text-ink-2">
        {contacts.flatMap((c, i) => (i ? [" · ", c] : [c]))}
        {contacts.length ? " · " : ""}
        <span title={stamp(r.created_at)}>replied {ago(r.created_at)}</span>
        {cancelled && r.cancelled_at && <> · cancelled {ago(r.cancelled_at)}</>}
      </span>
      {r.note && (
        <span className="col-span-2 text-sm [overflow-wrap:anywhere]">
          “{r.note}”
        </span>
      )}
      {error && (
        <p role="alert" className="col-span-2 text-sm text-bad-ink">
          {error}
        </p>
      )}
    </li>
  );
}
