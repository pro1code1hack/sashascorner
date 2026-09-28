// Live QR scanning from the rear camera.
//
// Decoder: the browser's own BarcodeDetector when it reads QR (Chrome on Android,
// ChromeOS, macOS), else jsQR, imported only the first time it is needed so the
// rest of the site never downloads it. `decodeImageData` is the jsQR path on its
// own, exported so it can be checked against a generated image without a camera.

type JsQR = typeof import('jsqr').default;

let jsqrP: Promise<JsQR> | null = null;
function loadJsQR(): Promise<JsQR> {
  jsqrP ??= import('jsqr').then((m) => m.default);
  return jsqrP;
}

/** Decode one RGBA frame. Tries normal, then inverted (a dark-mode web card). */
export async function decodeImageData(data: Uint8ClampedArray, width: number, height: number): Promise<string | null> {
  const jsQR = await loadJsQR();
  const hit = jsQR(data, width, height, { inversionAttempts: 'attemptBoth' });
  return hit?.data || null;
}

interface Detector {
  detect(source: CanvasImageSource): Promise<{ rawValue: string }[]>;
}
declare global {
  interface Window {
    BarcodeDetector?: {
      new (opts: { formats: string[] }): Detector;
      getSupportedFormats(): Promise<string[]>;
    };
  }
}

async function nativeDetector(): Promise<Detector | null> {
  const BD = window.BarcodeDetector;
  if (!BD) return null;
  try {
    const formats = await BD.getSupportedFormats();
    return formats.includes('qr_code') ? new BD({ formats: ['qr_code'] }) : null;
  } catch {
    return null;
  }
}

export type CameraProblem = 'denied' | 'no-camera' | 'insecure' | 'busy' | 'other';

export function cameraProblemText(p: CameraProblem): string {
  switch (p) {
    case 'denied':
      return 'Camera access was refused. Allow the camera for this site in the browser settings, then tap Start camera. Or type the code instead.';
    case 'no-camera':
      return 'No camera was found on this device. Type the code instead.';
    case 'insecure':
      return 'The camera only works over a secure (https) connection. Type the code instead.';
    case 'busy':
      return 'Another app is using the camera. Close it, then tap Start camera.';
    default:
      return "The camera didn't start. Tap Start camera to try again, or type the code.";
  }
}

export interface Scanner {
  start(): Promise<void>;
  stop(): void;
  pause(): void;
  resume(): void;
  readonly running: boolean;
  readonly torchAvailable: boolean;
  setTorch(on: boolean): Promise<boolean>;
}

export function createScanner(
  video: HTMLVideoElement,
  onCode: (text: string) => void,
  onProblem: (p: CameraProblem) => void,
): Scanner {
  let stream: MediaStream | null = null;
  let detector: Detector | null | undefined;
  let paused = false;
  let busy = false;
  let loopId = 0;
  let lastAt = 0;
  let torch = false;
  const canvas = document.createElement('canvas');
  const ctx = canvas.getContext('2d', { willReadFrequently: true });

  const track = () => stream?.getVideoTracks()[0] ?? null;

  async function tick() {
    if (!stream || paused || busy || video.readyState < 2) return;
    const now = performance.now();
    const gap = detector ? 90 : 160; // jsQR is heavier; give the main thread room
    if (now - lastAt < gap) return;
    lastAt = now;
    busy = true;
    try {
      let text: string | null = null;
      if (detector) {
        const found = await detector.detect(video);
        text = found[0]?.rawValue ?? null;
      } else if (ctx) {
        // Only the centre square, scaled down: the QR is held in the frame there,
        // and 480px is plenty for a phone-screen QR at arm's length.
        const vw = video.videoWidth;
        const vh = video.videoHeight;
        const side = Math.min(vw, vh) * 0.85;
        const size = Math.min(480, Math.round(side));
        canvas.width = size;
        canvas.height = size;
        ctx.drawImage(video, (vw - side) / 2, (vh - side) / 2, side, side, 0, 0, size, size);
        const img = ctx.getImageData(0, 0, size, size);
        text = await decodeImageData(img.data, size, size);
      }
      if (text && !paused) onCode(text);
    } catch {
      /* a frame that fails to decode is just a frame */
    } finally {
      busy = false;
    }
  }

  function loop() {
    cancel();
    const v = video as HTMLVideoElement & { requestVideoFrameCallback?: (cb: () => void) => number };
    const step = () => {
      void tick();
      loopId = v.requestVideoFrameCallback ? v.requestVideoFrameCallback(step) : requestAnimationFrame(step);
    };
    step();
  }
  function cancel() {
    const v = video as HTMLVideoElement & { cancelVideoFrameCallback?: (id: number) => void };
    if (!loopId) return;
    if (v.cancelVideoFrameCallback) v.cancelVideoFrameCallback(loopId);
    cancelAnimationFrame(loopId);
    loopId = 0;
  }

  return {
    get running() {
      return !!stream;
    },
    get torchAvailable() {
      const t = track();
      try {
        const caps = t?.getCapabilities?.() as Record<string, unknown> | undefined;
        return !!caps?.torch;
      } catch {
        return false;
      }
    },
    async start() {
      if (stream) {
        paused = false;
        loop();
        return;
      }
      if (!window.isSecureContext || !navigator.mediaDevices?.getUserMedia) {
        onProblem('insecure');
        return;
      }
      if (detector === undefined) detector = await nativeDetector();
      if (!detector) void loadJsQR(); // warm it while the camera starts
      try {
        stream = await navigator.mediaDevices.getUserMedia({
          audio: false,
          video: { facingMode: { ideal: 'environment' }, width: { ideal: 1280 }, height: { ideal: 720 } },
        });
      } catch (e) {
        const name = (e as DOMException)?.name;
        onProblem(
          name === 'NotAllowedError' || name === 'SecurityError'
            ? 'denied'
            : name === 'NotFoundError' || name === 'OverconstrainedError'
              ? 'no-camera'
              : name === 'NotReadableError'
                ? 'busy'
                : 'other',
        );
        return;
      }
      video.srcObject = stream;
      video.setAttribute('playsinline', '');
      video.muted = true;
      try {
        await video.play();
      } catch {
        /* autoplay of a muted inline stream is allowed; ignore the odd race */
      }
      paused = false;
      torch = false;
      loop();
    },
    stop() {
      cancel();
      stream?.getTracks().forEach((t) => t.stop());
      stream = null;
      video.srcObject = null;
      torch = false;
    },
    pause() {
      paused = true;
      cancel();
    },
    resume() {
      if (!stream) return;
      paused = false;
      lastAt = 0;
      loop();
    },
    async setTorch(on: boolean) {
      const t = track();
      if (!t) return false;
      try {
        await t.applyConstraints({ advanced: [{ torch: on } as MediaTrackConstraintSet] });
        torch = on;
      } catch {
        torch = false;
      }
      return torch;
    },
  };
}
