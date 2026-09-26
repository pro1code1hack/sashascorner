// Pence in, text out. The only place money becomes a string.
export const gbp = (pence: number): string =>
  `£${Math.floor(pence / 100)}.${String(pence % 100).padStart(2, '0')}`;

export const WEEKDAYS = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday'];

/** "07:30" -> "7.30am" -- British café-window style. */
export const clock = (hhmm: string): string => {
  const [h, m] = hhmm.split(':').map(Number);
  const suffix = h >= 12 ? 'pm' : 'am';
  const h12 = h % 12 === 0 ? 12 : h % 12;
  return m === 0 ? `${h12}${suffix}` : `${h12}.${String(m).padStart(2, '0')}${suffix}`;
};
