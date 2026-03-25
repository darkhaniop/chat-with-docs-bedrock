# 06 — Frontend

React 19 + TypeScript 6.0, built with Vite, styled with Tailwind and shadcn/ui, shipped as
static assets to S3 behind CloudFront with Origin Access Control.

## Layout

```
web/
├── src/
│   ├── main.tsx
│   ├── app/
│   │   ├── router.tsx              # TanStack Router (file-less, code-defined routes)
│   │   └── providers.tsx           # auth, query client, events, theme
│   ├── auth/
│   │   ├── oidc.ts                 # Cognito Managed Login, PKCE, silent renew
│   │   └── useAuth.ts
│   ├── api/
│   │   ├── client.ts               # fetch wrapper: bearer token, error mapping, retry
│   │   └── hooks/                  # TanStack Query hooks, one file per resource
│   ├── events/
│   │   ├── channel.ts              # AppSync Events WebSocket client
│   │   └── useChannel.ts           # subscribe + seq-gap detection + reconcile
│   ├── features/
│   │   ├── projects/
│   │   ├── documents/              # upload dropzone, ingest progress, document list
│   │   ├── chat/                   # message list, composer, streaming reducer
│   │   └── viewer/                 # pdf.js viewer + highlight overlay
│   ├── components/ui/              # shadcn/ui primitives (generated, lightly edited)
│   └── lib/                        # geometry, formatting, ULID, small utilities
├── public/
│   └── pdf.worker.min.mjs          # pdf.js worker, self-hosted, version-pinned
├── index.html
├── vite.config.ts
└── package.json
```

State management: **TanStack Query** for everything server-owned, plain React state for
ephemeral UI, and one purpose-built reducer for the streaming message. No Redux, no Zustand —
the only genuinely complex client state is the in-flight assistant message, and it is a
reducer over a well-defined event stream.

## Screen structure

```
┌───────────────────────────────────────────────────────────────────────┐
│  header: project switcher · user menu                                 │
├───────────────┬───────────────────────────┬───────────────────────────┤
│  documents    │  chat                     │  viewer                   │
│               │                           │                           │
│  ▸ report.pdf │  ┌─ assistant ──────────┐ │  ┌─────────────────────┐  │
│    42p ✓      │  │ Uptime was 94% in Q3 │ │  │  pdf.js canvas      │  │
│  ▸ scan.pdf   │  │ [1], up from 88% [2] │ │  │  ▓▓▓ highlighted    │  │
│    ⟳ 12/40    │  └──────────────────────┘ │  │  ▓▓▓ span           │  │
│  ▸ chart.png  │  sources: report p7, p6   │  │                     │  │
│    ✓          │                           │  └─────────────────────┘  │
│               │  ┌─ composer ───────────┐ │  report.pdf · page 7      │
│  + upload     │  │ Ask a question…      │ │                           │
└───────────────┴───┴──────────────────────┴─┴───────────────────────────┘
```

Three resizable panes. The viewer pane collapses when nothing is selected, giving the chat
the full width — the common case is reading answers, not staring at PDFs.

## Authentication

Cognito **Managed Login** (hosted UI), authorization code flow with PKCE, public client, no
secret in the SPA.

- Library: `oidc-client-ts` with a React wrapper, configured against the user pool's OIDC
  discovery document.
- The **access/ID tokens live in memory only**. The refresh token is held by the library in
  `sessionStorage`. This is the pragmatic choice for an internal tool: `localStorage` survives
  tab close (worse), and a cookie-based BFF would mean adding a server we otherwise do not
  need.
- Silent renew via a hidden iframe against the hosted UI, firing at 80% of token lifetime.
- On 401 from the API, the client attempts one silent renew, retries once, then redirects to
  login. It never retries a 401 in a loop.
- Sign-out clears memory, revokes the refresh token, and redirects to the Cognito logout
  endpoint.

## Upload flow

1. Dropzone validates type and size client-side and shows an immediate error for anything
   the API would reject — the round trip is wasted otherwise.
2. `POST /projects/{p}/documents` → presigned `PUT`.
3. `fetch(url, {method: 'PUT', body: file, headers})` with an `XMLHttpRequest`-based progress
   fallback for the upload bar (fetch has no upload progress).
4. `POST .../:ingest`.
5. Ingest progress arrives on the project channel; the document row shows
   `stage` + `pagesDone/pagesTotal`.
6. On `document.ready` the row becomes selectable and the documents query is invalidated.

Multiple uploads run with a concurrency of 3. A failed upload is retryable from the row
without re-selecting the file.

## Chat and streaming

The in-flight assistant message is a reducer over channel events:

```ts
type StreamState = {
  messageId: string;
  status: 'starting' | 'retrieving' | 'thinking' | 'streaming' | 'done' | 'blocked' | 'failed';
  text: string;
  citations: Citation[];
  sources: Source[];
  lastSeq: number;
  gapDetected: boolean;
};
```

Rules:

- `message.delta` appends `data.text` and records `seq`. If `seq !== lastSeq + 1`, set
  `gapDetected` — do not attempt to reorder or backfill.
- `message.citation` appends to `citations`. Citations may arrive before the text they anchor
  to; they render in the sources strip immediately and inline once `spanStart <= text.length`.
- `message.completed` triggers a `GET /conversations/{c}/messages` for the authoritative
  record, which replaces the reconstruction. **The streamed text is never persisted as truth
  in the client cache** — it is a preview. This makes gap handling trivial: any gap is
  corrected within a second of completion.
- `message.blocked` / `message.failed` render inline with a retry affordance.

The composer is disabled while a turn is in flight (matching the server-side conversation
lock) and shows a cancel button that calls `:cancel`.

### Rendering citations

The assistant message text is rendered by walking `citations` sorted by `spanStart` and
splitting the text into runs. Each cited run gets a subtle underline and a superscript marker
`[n]`. Hovering shows the `citedText` in a popover; clicking:

1. Selects the citation's document in the viewer pane (loading it if needed).
2. Scrolls to `pageNumber`.
3. Draws the highlight rectangles and pulses them once.

Markdown in the answer is rendered with a restricted renderer (headings, lists, bold, italic,
code, tables — no raw HTML, no images, no links to arbitrary URLs). Citation splitting happens
on the *plain text* offsets, so the renderer must be applied per-run rather than to the whole
string; this is the fiddliest part of the UI and has dedicated vitest coverage.

## PDF viewer and highlighting

`pdf.js` (`pdfjs-dist`), with the worker self-hosted from `public/` and pinned to the exact
package version — a CDN worker version drift is a classic source of silent rendering breakage.

- The document is fetched with the presigned `source-url`, with `withCredentials: false`.
- Only the visible page ± 1 is rendered; pages virtualise on scroll.
- **Highlights** are absolutely-positioned `div`s in an overlay layer above the canvas, one
  per rect, with `mix-blend-mode: multiply` so text stays legible.

Coordinate conversion is the one place this can go wrong, and it has exactly one
implementation:

```ts
// lib/geometry.ts — canonical PDF points (origin top-left) → viewport CSS pixels
export function toViewportRect(
  rect: [number, number, number, number],
  viewport: PageViewport,
): DOMRectLike {
  const [x0, y0, x1, y1] = rect;
  // pdf.js viewports use a bottom-left origin; flip y using the page height
  const h = viewport.viewBox[3] - viewport.viewBox[1];
  const [a, b, c, d] = viewport.convertToViewportRectangle([x0, h - y1, x1, h - y0]);
  return { left: Math.min(a, c), top: Math.min(b, d),
           width: Math.abs(c - a), height: Math.abs(d - b) };
}
```

Nothing else in the codebase multiplies by `viewport.scale`. See
[02-data-model.md § Coordinate systems](02-data-model.md#coordinate-systems).

**Image documents** bypass pdf.js entirely: the viewer renders the `render-url` image with the
same overlay component, using the Page item's `width`/`height` as the coordinate space.

## Reconnection and reconciliation

The WebSocket will drop. The design assumes it.

- The channel client reconnects with exponential backoff (1s → 30s, jittered) and resubscribes.
- On reconnect, and on any `gapDetected`, the affected query is invalidated: the conversation
  messages query for chat, the documents query for ingest.
- If a message is `STREAMING` in the fetched record but no events are arriving, the UI polls
  that conversation every 3 s until it is terminal, capped at 5 minutes.
- Posting a message subscribes to the channel first when the socket is already open; when it
  is not, the post proceeds and reconciliation covers the gap.

The net effect: the WebSocket is a latency optimisation, and the app is correct without it.

## Accessibility and polish

- Keyboard: `⌘K` project/document switcher, `⌘↵` send, `Esc` closes the viewer, arrow keys
  move between citations in the focused message.
- Citation markers are real `<button>`s with `aria-describedby` pointing at the popover.
- Highlights are decorative and `aria-hidden`; the citation popover carries the text.
- Streaming text uses `aria-live="polite"` on the message container, announced on completion
  rather than per delta.
- Respect `prefers-reduced-motion` for the highlight pulse and skeleton shimmer.
- Dark mode via Tailwind's `class` strategy, following the system preference by default.

## Build and deploy

- `npm run build` → `web/dist`, hashed asset filenames.
- CDK `BucketDeployment` uploads `dist` to the site bucket and creates a CloudFront
  invalidation for `/index.html` and `/` only (hashed assets never need invalidating).
- Runtime configuration (API base URL, Cognito ids, AppSync Events endpoint) is injected at
  **build** time from CDK outputs into `web/.env.{env}`, not fetched at runtime — one fewer
  request on cold load, and the values are not secret.
- `Cache-Control`: `max-age=31536000, immutable` for hashed assets, `no-cache` for
  `index.html`.
