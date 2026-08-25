# arxivist

A command-line tool that scans a folder for PDFs, figures out which ones are
research papers, renames each to **`<year> - <title>.pdf`**, and files it into a
**topic folder** — growing a fine-grained taxonomy that matches your library.

Point it at `~/Downloads`, `~/Documents`, or a synced Google Drive folder; it
leaves non-papers untouched and never overwrites anything.

```
2024 - A Survey of Synthetic Data Generation for Time-to-Event Outcomes.pdf
   → <library>/Survival Synthetic Data Generation/
```

## How it works

Each PDF flows through a pipeline:

1. **Extract** (offline) — first-page text + embedded metadata; find any DOI or
   arXiv id.
2. **Detect** — score section cues, citation density, references, abstract, and
   negative cues (invoice, manual, slides…). Below the threshold it's left alone.
3. **Look up** (online, optional) — resolve the DOI via **Crossref** or the id
   via the **arXiv API** to get an authoritative title, year, and abstract. No
   API key required. Falls back to text heuristics when there's no id or no net.
4. **Classify** — an **adaptive** step tuned for a corpus where every paper is
   close in subject. It reads the topic folders you already have (plus any you
   seed in config) and asks Claude to **reuse an existing topic** or, only when
   nothing fits, propose a concise new one. Rename or merge folders yourself
   anytime; the next run adapts. Without an API key it falls back to keyword
   matching, then `_Unsorted`.
5. **Name & file** — build `<year> - <title>.pdf`, sanitize it, and move it into
   the topic folder, suffixing ` (2)` on collisions.

Low-confidence topics land in `_NeedsReview/` so you can place them by hand.

## Install

```bash
cd ~/arxivist
python3 -m pip install -e ".[all]"     # CLI + LLM classifier + web UI
# or pick extras: ".[llm]" (Anthropic/Bedrock SDK), ".[web]" (web UI), or bare "." (offline CLI)
```

Requires Python 3.9+.

## Web UI

Run arxivist on a server and drive it from the browser on your Mac — upload PDFs,
watch each one get analyzed live, and download the organized result as a zip:

```bash
arxivist serve --host 0.0.0.0 --port 8000            # then open http://<server>:8000
```

Files you upload are analyzed and filed into topic folders inside a per-session
workspace on the server; the **Download organized .zip** button hands you an
`organized/<Topic>/<year> - title.pdf` tree to drop into your real library. Your
originals on the Mac are never touched. Flags: `--config`, `--workdir` (where
session workspaces live), `--host`, `--port`.

> Behind a reverse proxy at a sub-path, the UI uses relative URLs — serve it under
> a location with a trailing slash (e.g. `/arxivist/`) and it just works.

## CLI usage

Dry run (default — shows a table, moves nothing):

```bash
arxivist organize ~/Downloads --dest ~/Papers
```

Actually move files:

```bash
arxivist organize ~/Downloads --dest ~/Papers --apply
```

Undo the last run:

```bash
arxivist undo --dest ~/Papers
```

Useful flags: `--no-online` (skip Crossref/arXiv), `--no-llm` (keyword classifier
only), `--no-recursive`, `--config path/to/config.yaml`. If `--dest` is omitted,
the source folder is used as the library root.

## Configuration

Copy `config.example.yaml` to `~/.config/arxivist/config.yaml`. You can set the
model, toggle online/LLM, tune thresholds, and seed a starter taxonomy. See the
comments in that file.

### LLM credentials — Anthropic API or Amazon Bedrock

The classifier uses the Anthropic Python SDK and works with either backend; pick
one with `provider:` in config (or `ARXIVIST_PROVIDER`):

- **`anthropic`** (default) — reads `ANTHROPIC_API_KEY` or an `ant auth login`
  profile. Model via `model:` (default `claude-opus-5`).
- **`bedrock`** — uses the standard AWS credential chain (env vars,
  `~/.aws/credentials`, or an IAM role). Set `bedrock_model:` to a model id or
  cross-region inference profile enabled in your account, and `aws_region:` if
  needed. Example:

  ```yaml
  provider: bedrock
  bedrock_model: us.anthropic.claude-opus-4-5-20251101-v1:0
  aws_region: us-west-2
  ```

No credentials? Run with `--no-llm` (or `use_llm: false`) — arxivist still renames
and files using online lookup + your keyword topics.

## "Google cloud" folders

Google Drive / Cloud Storage synced to your Mac appears as a normal local path
(e.g. `~/Library/CloudStorage/GoogleDrive-you@example.com/My Drive/Papers`).
Point `--dest`/`SOURCE` at that path — no cloud API integration needed.

## Safety

- **Dry run by default**; nothing moves without `--apply`.
- **Never overwrites** — collisions get a numeric suffix.
- Every applied run writes a JSONL **manifest** under `<library>/.arxivist/` that
  `arxivist undo` replays in reverse.
