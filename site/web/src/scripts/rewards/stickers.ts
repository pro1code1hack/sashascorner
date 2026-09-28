// The stamp stickers, shared by the web card (/c, the /rewards sample) and the staff
// scanner (/staff). A different sticker per slot in a fixed order -- slot 1 is always
// the cat -- so every card, and the Apple/Google strip images, look the same.
//
// The files in public/stickers/ are COPIES. The canonical SVGs are assets/pass/stickers/
// at the repo root (they also draw the wallet strips); `cafeops wallet assets` refreshes
// the copies and `cafeops wallet doctor` says when they drift. Edit there, not here.
//
// Stickers are decorative: always alt="", with the count said in text or aria-label.
export const STICKER_SLOTS = 8;
/** 0-based slot index -> sticker URL. */
export const stickerSrc = (slot: number) => `/stickers/slot-${(slot % STICKER_SLOTS) + 1}.svg`;
export const REWARD_STICKER = '/stickers/reward.svg';
export const stickerImg = (src: string, cls: string) =>
  `<img class="${cls}" src="${src}" alt="" width="64" height="64" decoding="async" draggable="false">`;
