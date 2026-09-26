// Shapes for the photo admin, per site/BRIEF.md "Media & image slots".
// The client normalises whatever the server sends into these, so a small
// difference in field naming on the backend does not break the page.

export interface Focal {
  x: number; // 0..1, left -> right
  y: number; // 0..1, top -> bottom
}

export interface Media {
  id: number;
  src: string;
  srcset: string;
  width: number;
  height: number;
  alt: string;
  original_name: string;
  bytes: number;
  blur: string;
  /** Slot keys this photo is assigned to (de-duplicated). */
  usage: string[];
}

export interface SlotItem {
  media_id: number;
  src: string;
  srcset: string;
  width: number;
  height: number;
  alt: string;
  focal: Focal;
  blur: string;
}

export interface Slot {
  key: string;
  label: string;
  page: string;
  aspect: string;
  multiple: boolean;
  max: number;
  hint: string;
  items: SlotItem[];
}

/** What the owner is editing for one slot, before it is saved. */
export interface DraftItem {
  media_id: number;
  /** Per-slot description. Empty means "use the photo's own description". */
  alt: string;
  focal: Focal;
}

export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
    public body: unknown = null,
  ) {
    super(message);
  }
}
