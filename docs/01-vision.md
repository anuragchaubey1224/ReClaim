# 01 · Vision & Problem

## The problem (told honestly)

Every developer's machine slowly rots.

You clone a repo, run `npm install`, and 400MB of `node_modules` appears. You spin up
a Python project, create a `.venv`, and it pulls 1.2GB of wheels. You build a Docker
image and 3GB of layers land in `/var/lib/docker`. You run tests and `__pycache__`,
`.pytest_cache`, `coverage` files scatter everywhere. You build a Rust project and
`target/` balloons to 5GB. Next.js leaves `.next/`, Gradle leaves `~/.gradle`, npm
leaves a global cache, pip leaves `~/.cache/pip`.

None of this is visible day-to-day. Then one morning macOS says **"Your disk is
almost full"** and you have no idea where 200GB went. You open a disk analyzer, stare
at a treemap, and freeze — because you genuinely don't know what is safe to delete.
That hesitation is the real problem. **The bytes are easy to find; the confidence to
remove them is not.**

### Two facts that define the product

1. **Almost all of this clutter is _regenerable_.** `node_modules` comes back with
   `npm install`. `.venv` comes back with `pip install -r requirements.txt`. Build
   outputs come back with a build. Docker images come back with a pull. This is the
   single most important observation in the whole product.

2. **A small slice of it is _irreplaceable_ and mixed right in.** Your uncommitted git
   changes. Your `.env` with secrets. A local SQLite database. A `data/` folder you
   scraped for 6 hours. Existing tools are dangerous precisely because they don't
   understand this distinction — they treat the filesystem as bytes, not as *meaning*.

**Reclaim exists to remove #1 with total confidence while never touching #2.**

## Who it's for

- **Primary:** Working developers (web, ML, mobile, systems) who juggle many repos on
  a laptop with a non-infinite SSD. The classic "256GB MacBook, 40 side projects" user.
- **Secondary:** Data / ML engineers whose caches (HuggingFace models, datasets, conda
  envs) are enormous and opaque.
- **Tertiary (later):** Teams / CI runners where disk hygiene is an ops cost.

## What we are NOT

- ❌ Not a general Mac cleaner (we don't touch browser caches, mail, photos — that's
  CleanMyMac's turf and it's a trust minefield).
- ❌ Not a "delete everything" nuke. The default posture is conservative and reversible.
- ❌ Not an LLM chatbot with `rm` bolted on. The AI **plans and explains**; a
  deterministic engine **decides and executes**.

## The vision (where this goes)

A developer never thinks about disk space again — because a quiet, trustworthy agent
understands their projects, knows what's safe to reclaim, watches disk growth in the
background, and — when space is needed — proposes a plan in plain language:

> "I can reclaim **34 GB**. 28 GB is build artifacts from 6 dormant projects you
> haven't opened in 3+ months. 6 GB is Docker layers with no running container.
> Everything here regenerates. **2 projects have uncommitted changes — I'm leaving
> those alone.** Want the plan? You can undo any of it for 7 days."

That paragraph is the whole product. Everything in these docs exists to make it true
and trustworthy.

## Why now

- **Repos got heavier.** Modern JS/ML toolchains produce gigabytes of derived files.
- **SSDs stayed small at the base tier.** 256/512GB laptops are still the default.
- **LLMs finally make _explainable safety_ possible.** The hard part was never
  deletion — it was giving a human enough context to trust a deletion. That's exactly
  what a good LLM layer, grounded on deterministic facts, can now do.

## What success looks like (for the resume goal)

This project is a success if, in an interview, you can spend 20 minutes going deep on:
- how you scan millions of files concurrently and fast,
- how you *guarantee* you never delete irreplaceable data,
- how you make deletions reversible (transactions, quarantine, rollback),
- and how the AI agent is grounded so it can't hallucinate a dangerous action.

If those four conversations are strong, the project has done its job — see
[`04-technical-depth.md`](./04-technical-depth.md).
