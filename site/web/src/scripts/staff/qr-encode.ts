// QR encoder for "Find member": the recovery link shown as a big QR the customer
// scans with their own phone. Uses the site's own dependency-free encoder
// (scripts/rewards/qr.ts, Agent C), loaded lazily: only this screen needs it.
// That encoder goes up to version 10 (213 bytes); a card link with its token is
// about 140, so a RangeError here means the link format changed.

export async function qrSvg(text: string, label: string): Promise<string> {
  const { qrSvg: encode } = await import('../rewards/qr');
  try {
    return encode(text, { label });
  } catch (e) {
    if (e instanceof RangeError) throw new Error('The card link is too long to show as a QR code. Tell the manager.');
    throw e;
  }
}
