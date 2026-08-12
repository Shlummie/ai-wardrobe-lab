# AI Wardrobe Lab

A local-first wardrobe catalog, outfit generator, synthetic audience panel, and optional virtual try-on workstation. The application keeps the garment library and database on the host computer, creates deterministic outfit candidates locally, and sends only an explicitly assembled shortlist board and text brief when the optional OpenAI evaluation is used.

## Capabilities

- Import and catalog garment images with structured metadata.
- Generate outfit combinations with local coordination rules.
- Review, shortlist, and compare outfits in a browser interface.
- Run a bracket-style synthetic audience evaluation with resumable state, usage accounting, and a configurable budget ceiling.
- Start a temporary LAN/mobile intake endpoint for uploading new garment photos.
- Generate local try-on previews for shortlisted looks through an optional CatVTON adapter.
- Store wardrobe records, evaluations, and cached outputs in a configurable local data directory.

## Architecture

```text
browser UI -> local API -> SQLite + wardrobe files
                    |-> outfit engine (local)
                    |-> OpenAI panel (optional, metered)
                    +-> CatVTON worker (optional, local GPU)
```

The TypeScript application supplies the interface and rendered routes. The Python backend owns persistence, image processing, outfit generation, evaluation state, and try-on orchestration. Drizzle files cover the separate example/worker database path; the primary local wardrobe service uses SQLite.

## Public repository boundary

This repository contains source and setup scripts only. It intentionally excludes:

- `.env` and API keys;
- wardrobe and reference photos;
- SQLite databases;
- CatVTON, DensePose, checkpoints, and model caches;
- generated boards, try-on outputs, logs, PID files, builds, and virtual environments.

No private wardrobe counts, workstation benchmarks, or personal images are claimed by the public documentation.

## Setup on Windows

Requirements: Node.js 22+, Python 3.11+, PowerShell, and enough local disk space for any optional model integration.

```bat
Setup_AI_Wardrobe_Lab.cmd
Start_AI_Wardrobe_Lab.cmd
```

The setup script creates local environments and installs project dependencies. The start script binds to loopback by default. Stop it with:

```bat
Stop_AI_Wardrobe_Lab.cmd
```

Manual web commands:

```bash
npm install
npm run dev
```

Copy `.env.example` to `.env` only when optional provider features are needed. `OPENAI_API_KEY` must never be committed. The default `gpt-5.6-luna` identifier and current capabilities are documented on the [official OpenAI model page](https://developers.openai.com/api/docs/models/gpt-5.6-luna); availability and pricing can change, so check the official page before setting a budget.

## Optional CatVTON integration

`Setup_CatVTON.cmd` invokes the isolated setup path and stores the upstream source and checkpoints under ignored model directories. CatVTON is **not bundled** here. Its upstream project states that its code, checkpoints, and demo are licensed under CC BY-NC-SA 4.0 for non-commercial use. Review the [CatVTON repository](https://github.com/Zheng-Chong/CatVTON) before downloading or distributing it. The MIT license for AI Wardrobe Lab does not override CatVTON's license or the terms of its model dependencies.

## Mobile intake and privacy

The normal service is loopback-only. `Enable_iPhone_Access.cmd` temporarily opens the configured local port and firewall rule so a phone on the LAN can upload images; `Disable_iPhone_Access.cmd` removes that access. Treat LAN mode as an explicit exposure window, use a trusted network, and disable it immediately afterward.

OpenAI evaluation sends the generated comparison board and written brief for the selected candidates. Raw wardrobe folders are not uploaded by the local code path, but a comparison board still contains garment imagery. Do not enable the provider integration for content you are not authorized to send.

## Tests

```bash
npm run lint
npm run build
node --test tests/rendered-html.test.mjs
python -m unittest -v tests.test_backend
```

The combined `npm test` command expects the local Python environment created by setup.

## License

Original application source is MIT licensed. Optional provider services, npm/Python dependencies, model code, checkpoints, and generated outputs retain their own terms. See `SECURITY.md` and the CatVTON notice above.
