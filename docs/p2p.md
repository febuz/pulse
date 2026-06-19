# P2P Architecture

How a Knitweb peer reaches other peers, replicates signed state, discovers the
rest of the Web, polices misbehavior, and self-heals after churn. This documents
the `knitweb.p2p` package and the two node stacks that consume it
(`knitweb.p2p.node.AsyncioP2PNode` and `knitweb.fabric.node.FabricNode`).

A single, load-bearing invariant runs through everything below: **a frame is
opaque, length-prefixed canonical CBOR, and no carrier ever re-encodes it.** A
signed record (a `Knit`, a `FeedHead`, an `EquivocationReport`, an author-signed
fabric record) keeps byte-identical bytes from the moment it is framed to the
moment a peer verifies it, so a fresh Knit's CID is the same whether it crossed a
TCP socket or an HTTP relay mailbox.

## Layering

```
                      AsyncioP2PNode            FabricNode
                  (feeds, Knits, PEX,        (Web record gossip
                   equivocation gossip)       + convergence)
                          \                       /
                           \                     /
                            +--- _dispatch ------+      carrier-agnostic handler:
                            |                           request map in, map out
        ----------------------------------------------------------------
         transport.Dialer  ──routes by PeerAddress.transport tag──►
         transport.Transport (Protocol): dial() / listen() / close()
              │                                   │
         TcpTransport ("tcp")             RelayTransport ("relay")
         asyncio sockets                  HTTP store-and-forward mailbox
              │                                   │
        ----------------------------------------------------------------
         wire: write_frame_bytes / read_frame_bytes
               (4-byte big-endian length prefix + canonical CBOR)
              │
         core.canonical (float-free canonical CBOR — the byte-identity floor)
```

Cross-cutting primitives sit beside the carriers and are reused by **both** node
stacks: `discovery` (PEX), `reputation` + `policing` (the consequence ledger),
`anti_entropy` (the convergence loop), and `metrics` (integer observability).

## The wire (`p2p/wire.py`)

The framing is deliberately boring so the security properties stay in the feed
and ledger primitives, not the transport. `write_frame_bytes` is the single
source of truth: it `core.canonical.encode`s the map and prepends a 4-byte
big-endian length. `read_frame_bytes` reverses it and re-decodes through
`canonical.decode`, rejecting any non-canonical encoding as a `WireError`.
`read_frame` / `write_frame` wrap the byte functions for an `asyncio` stream;
`RelayTransport` calls the byte functions directly. Because both carriers emit
**the same bytes from the same map**, signed-record byte-identity is carrier-
independent.

Guards: `MAX_FRAME_BYTES` (8 MiB) caps a frame on both encode and decode; an
empty, truncated, oversized, or non-canonical frame is a `WireError`.

Typed (de)serializers convert domain objects to/from canonical wire maps:
`feed_head_{to,from}_record`, `multiproof_{to,from}_record`,
`knit_{to,from}_record`, and `equivocation_report_{to,from}_record`. These do
structural validation only; the cryptographic checks live in the fabric/ledger
layers and are always re-run from the received bytes.

## Pluggable transport (`p2p/transport.py`)

Real Knitweb peers are mostly behind NAT/firewalls, so a node cannot assume
inbound raw TCP works. The wire layer is therefore split from the *carrier*:

- **`Transport`** (a `runtime_checkable` `Protocol`) is the carrier. It knows how
  to `dial(peer, request) -> response` (one-shot request/response) and how to
  `listen(handler)` (accept inbound requests and feed each decoded map to the
  handler). It never inspects the payload. `close()` releases resources and
  `local_address()` returns the `PeerAddress` peers should dial.

- **`PeerAddress`** is a frozen, hashable dataclass carrying a `transport` tag
  (`"tcp"` / `"relay"`), classic `host`/`port`, and an opaque `params` map. It
  has a canonical string form via `uri()` / `parse_peer_uri` (`tcp://host:port`
  or `relay://mailbox@base_url`).

- **`Dialer`** routes each outbound dial to the transport that owns the peer's
  `transport` tag (`Dialer.register` / `Dialer.dial`). One node holds a single
  `Dialer` registered with every transport it speaks, so directly-dialable TCP
  peers and NAT'd relay-mailbox peers coexist behind one `dial` call.

### TcpTransport (`tag = "tcp"`)

The original direct-socket behavior, extracted verbatim. `dial` opens a one-shot
`asyncio.open_connection`, writes one request frame, reads one response frame,
and closes. `listen` runs an `asyncio.start_server` accept loop. Two
deterministic, integer carrier-level knobs bound it against floods (PRs #41):

- **`max_inbound`** (default `DEFAULT_MAX_INBOUND = 64`) — an `asyncio.Semaphore`
  caps how many inbound connections are *served* at once; excess connections
  queue without fanning out unbounded handler coroutines.
- **`read_timeout_s`** (default `DEFAULT_READ_TIMEOUT_S = 30`) — a single-frame
  read deadline; a slow-loris peer that stalls mid-frame is dropped at the
  deadline, freeing its slot.

Exactly one frame is read per connection, so a peer cannot pipeline a flood down
a single accepted socket. Both knobs are integers with no randomness and live
entirely in the carrier — they never touch the framing bytes, so byte-identity
is untouched.

### RelayTransport (`tag = "relay"`, `p2p/relay.py`)

An HTTP client for a store-and-forward PHP relay (`api/relay/send` and
`api/relay/fetch`). A firewalled node *listens* by registering a mailbox and
polling `fetch`; a peer reaches it by `send`-ing to that mailbox instead of
opening a socket. The relay is a dumb pipe: it carries **the same opaque,
length-prefixed canonical-CBOR frame** the TCP transport carries, base64-wrapped
only to survive the HTTP/JSON hop. The relay never decodes the payload and this
client only base64-(de)codes the exact bytes `wire` would have written to a
socket — so no signed bytes change.

Request/response correlation over a one-directional mailbox: each request frame
is tagged with a fresh integer `_relay_rid` and a `_relay_reply_to` mailbox; the
responder sends its reply (carrying the same `rid`) back to `reply_to`. These
`_relay_*` keys live in the **transport envelope** only — `_strip_envelope`
removes them before any signed/business logic, so they never enter
canonical/hashed bytes. `HttpPoster` is an injectable stdlib-`urllib` seam (run
off-loop via `asyncio.to_thread`) so tests drive an in-memory relay without a
socket.

### Hole-punch seam

A future STUN-assisted hole-punch transport slots in behind the same `Transport`
protocol: it implements `listen` exactly as TCP does, differing only in how its
listening socket becomes reachable (rendezvous → simultaneous-open → hand the
connected socket to the same handler loop). Neither the protocol nor the node
layer needs to change. See the `HOLE-PUNCH SEAM` note on `Transport.listen`.

## Node stacks

Both nodes are constructed with an optional `transport` (defaults to a
`TcpTransport`) plus optional `extra_transports`, register them all on a shared
`Dialer`, and expose a single **carrier-agnostic** `_dispatch(msg) -> msg`
handler that the listening `Transport` feeds every decoded request to — whether
it arrived over a TCP stream or a relay mailbox poll. The TCP path additionally
wraps `_dispatch` in a per-socket `_handle_peer` that owns connection-level
concerns (the banned-peer gate keyed on the socket endpoint, and malformed/
oversized-frame penalties). The relay path has no socket, so it stamps the
sender's mailbox identity onto the request as the `ENVELOPE_PEER_KEY` transport
envelope key; `_dispatch` honours the *same* ban gate, then drops the key.

### AsyncioP2PNode (`p2p/node.py`)

The Phase-3 peer: static peers, signed feed replication, partial Merkle
sync, equivocation gossip, PEX, and a two-party Knit (payment) handshake.

- **Full feed sync** — `sync_feed(peer, feed_id)` requests every entry
  (`count = null`) and checks the full entry set against the signed `FeedHead`
  via `fabric.feed.verify_entries`.
- **Partial (range) feed sync** (PR #30) — `sync_feed_range(peer, feed_id,
  start, count)` transfers `count` entries plus an `O(count + log n)` range
  multiproof (`fabric.feed_multiproof.prove_range`) instead of the whole log,
  then authenticates the slice against the signed head with
  `verify_range_multiproof`. A verified `FeedSlice` is trusted exactly as much as
  the feed author without holding the full history. The server side is
  `_serve_feed`, which serves a whole feed only from `start == 0` and otherwise
  serves the requested window plus the shared-path proof.
- **Conflict quarantine + consequence** (PR #31) — `_merge_replica` compares an
  incoming `FeedReplica` against what the node holds. `_conflict_reason` detects
  same-`(length, fork)`-different-root equivocation (`check_conflict`) or a
  rewrite of an already-signed prefix (`check_prefix_conflict`). On conflict the
  feed is frozen (`frozen_feeds`) and `_consequence_on_conflict` either builds a
  portable `EquivocationReport` (`fabric.equivocation.prove_equivocation`) and
  bans the feed key, or applies a graded `FEED_CONFLICT` penalty for a prefix
  rewrite.
- **Equivocation gossip** — `gossip_equivocation_report` sends a proven report;
  `_handle_equivocation_report` re-verifies it from its own bytes
  (`verify_equivocation_report`) before banning + freezing, so a forged report is
  a no-op on the receiver.
- **Knit handshake** — `send_knit` runs propose → accept → finalize over three
  round-trips, validating each step with `ledger.knitweb.validate_knit` and
  applying the Knit locally only after the peer finalizes the identical Knit.
  `_handle_knit_proposal` / `_handle_knit_finalize` are the server side, with
  per-sender-nonce replay protection (`_seen_incoming_nonces`).

### FabricNode (`fabric/node.py`)

The smallest useful "live Web" peer (issue #9): it owns a fabric `Web`, signs
each locally woven record under its author key, and gossips it to every known
peer. `weave(record)` weaves locally and broadcasts; `sync_from(peer)` pulls a
peer's full record set for catch-up. Because `Web.weave` is content-addressed and
idempotent, gossip converges to an identical node set regardless of arrival order
or duplicates — once settled, two nodes share the same
`fabric.items.web_state_root` (they have **converged**). `_ingest_signed`
verifies a domain-separated author signature (`_RECORD_TAG`) before weaving;
`_handle_peer` turns a forged author signature into an `INVALID_SIGNATURE`
penalty on the relaying peer.

This node reuses the Phase-3 primitives directly rather than inventing a new
transport: the same `wire` framing, the same `PeerAddress`/`Dialer`/`Transport`,
the same `reputation`/`metrics`.

## Peer discovery — PEX (`p2p/discovery.py`)

For the Web to grow beyond hand-configured peers, a node tells a peer the
addresses it knows, the peer merges them and replies with its own, and over a few
rounds the component converges on the same peer set — classic peer-exchange, the
bootstrap Bitcoin/libp2p use before a full DHT.

The module is a **transport-free core**: `PeerDirectory` (dedup + merge +
deterministic, sort-ordered sample), the canonical `peer-exchange` message
(`PEER_EXCHANGE_KIND`), and a pure `handle_peer_exchange` (merge-and-reply). A
share is bounded by `DEFAULT_SHARE_K = 32` so an advertised set never grows with
the directory. Dedup is carrier-aware: `PeerDirectory`'s key folds in the
transport tag and sorted `params`, so a relay mailbox peer and a TCP peer never
collide.

`AsyncioP2PNode` wires it in (PR #36): the directory is seeded from the
`StaticPeerBook` (`directory_from_peerbook`), and `bootstrap_peers(seeds)` runs
one PEX round against each seed through the shared `Dialer` — identical frame
bytes over `tcp://` or `relay://` — folding replies in via `bootstrap_round` and
returning the count of newly-learned peers. An unreachable or non-PEX seed is
skipped without sinking the round.

## Reputation + policing — the consequence loop

Detection without a consequence is toothless, so the **detect → prove →
consequence** loop is closed across two modules (PR #31):

- **`p2p/reputation.py`** — `PeerReputation` holds a per-peer integer
  misbehavior score; `penalize` adds an `Offense`'s weight and returns whether
  the peer is now banned (`is_banned` at or above `DEFAULT_BAN_THRESHOLD = 100`).
  `Offense` weights are graded by how objective/severe the offense is:
  `MALFORMED_FRAME` (10) and `OVERSIZED_FRAME` (20) are cheap noise;
  `INVALID_SIGNATURE` and `STALE_OR_FORGED_PROOF` (50) are serious; a
  `FEED_CONFLICT` or `EQUIVOCATION` (100) is cryptographically provable and
  triggers a one-shot ban. `decay` / `decay_all` (called explicitly per Pulse
  epoch) rehabilitate. **No wall-clock and no randomness** — two honest nodes
  observing the same offense stream reach the same ban verdict.

- **`p2p/policing.py`** — pure glue from proof to consequence:
  `police_equivocation_report` (verifies a report, then bans the offending feed
  key), `police_feed_conflict` (bans on a genuine `check_conflict`), and
  `police_invalid_proof`. Each penalizes only when the evidence verifies, so a
  node never penalizes on hearsay.

Where consequence is applied: the per-socket `_handle_peer` gates banned peers
and penalizes bad frames; `_dispatch` gates banned relay senders via
`ENVELOPE_PEER_KEY`; `sync_feed` penalizes a `STALE_OR_FORGED_PROOF`;
`_merge_replica` funnels conflicts through `_consequence_on_conflict`.

## Self-healing convergence — anti-entropy (`p2p/anti_entropy.py`)

A live Web is never static: peers crash, partition, and rejoin. The primitives to
*recover* exist (`bootstrap_peers` re-grows the directory, `sync_feed` re-pulls a
feed, `FabricNode.sync_from` re-pulls Web state) but nothing *drives* them on a
loop. `AntiEntropy` is that missing convergence engine, the same background
reconciliation loop every production P2P stack carries (Cassandra/Dynamo
anti-entropy, Bitcoin's reconnect loop).

It is the **transport-free, socket-free core** so it can drive either node stack
without editing the node modules:

- A `SyncRound` is an injected coroutine that performs one reconciliation attempt
  and returns the integer *progress* it made (peers learned, entries pulled). A
  raised exception (an unreachable peer) is treated as a failed round.
- `Backoff` is **integer attempt-count based** — the delay before retry
  `attempt` is `base * 2**attempt` clamped to `ceiling`, with no wall-clock and
  no randomness, so two peers replaying the same success/failure sequence
  schedule identically. A `BackoffState` resets to `0` on progress and lengthens
  (capped) on failure.
- Sleeping is injected, so tests advance a virtual clock and drive thousands of
  rounds deterministically with no real time. `run_cycle` runs every round once;
  `run(cycles)` is a bounded loop.

It touches no signed record, no reputation gate, and no hash path — a fresh
Knit's CID is identical whether or not a node is healing.

Both nodes wire it in opt-in (PRs #43, #45): `start_anti_entropy(peers, feeds=…)`
builds one bootstrap round plus one round per feed and launches them on a
background task; nothing runs until called, so a plain `start()` keeps its
existing behavior. `stop_anti_entropy` / `stop` cancel the loop cleanly. A single
bad peer among several never sinks a round; the driver swallows a failed round and
lets backoff govern the retry cadence.

## Observability — metrics (`p2p/metrics.py`)

`Metrics` is a flat, **integer-only** registry of named counters
(`incr`, monotonic) and gauges (`gauge`), with a name never seen reading as `0`.
The point is `snapshot()`: a `dict[str, int]` with **sorted keys**, so it is
directly canonical-CBOR-encodable and byte-identical across two nodes that
observed the same event stream. No float, no wall-clock, no randomness. The
canonical series are enumerated in the shared `FABRIC_METRICS` vocabulary
(`records_woven`, `broadcasts_sent`/`broadcasts_failed`, `sync_pulls`,
`frames_in`/`frames_out`, `frames_malformed`/`frames_oversized`,
`banned_refusals`).

**Both** node stacks are metered against this one vocabulary, not just the
fabric node. After the `BaseNode` unification each node owns a `Metrics()`
(allocated in `BaseNode.__init__`), and the shared per-connection prologue
(`_handle_peer`) increments the wire-path series — `frames_in`/`frames_out`,
`frames_malformed`/`frames_oversized`, and `banned_refusals` — for **both**
`FabricNode` and `AsyncioP2PNode` (the latter's wire path was wired in #48, and
#23 lifted the registry onto the common base). The fabric node additionally
emits the domain series (`records_woven`, `broadcasts_sent`/`broadcasts_failed`,
`sync_pulls`) from its weave/broadcast/sync paths. Because the two stacks share
one `FABRIC_METRICS` name set, a dashboard enumerates one vocabulary for either
node, and two nodes observing the same event stream produce byte-identical
snapshots. Metering touches no hash path.

## Interop export (`fabric/jsonld.py`)

Orthogonal to the live carriers, `export_web` / `import_web` (PR #34) render a
fabric `Web` as a JSON-LD/DKG document — nodes (any canonical record, keyed by
its CID) and first-class typed edges, with a `ual_for_node` UAL per node — for
OriginTrail-style interop. Records keep their own CID identity regardless of who
relays or exports them.

## Byte-identity, determinism, and money

These hold across the whole layer:

- **Canonical byte-identity is sacred.** Every carrier moves opaque,
  length-prefixed canonical-CBOR frames and never re-encodes them. Transport
  envelope keys (`_relay_*`, `ENVELOPE_PEER_KEY`) are stripped before any
  signed/business logic. A fresh Knit's CID is unchanged whether it crosses TCP
  or a relay.
- **Integers only on hashed/state paths.** Reputation scores, offense weights,
  backoff delays, metrics, frame lengths, and feed `length`/`fork` are all
  integers; canonical CBOR rejects floats outright.
- **Determinism where it counts.** Reputation, backoff, PEX sampling, and metrics
  snapshots are pure functions of their input event stream — no wall-clock, no
  randomness on any path that two honest nodes must agree on. Carrier-only
  timeouts (`read_timeout_s`, relay poll intervals) are policy knobs that never
  touch a signed or hashed value.
