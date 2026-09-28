# issue-triage

Point it at a GitHub repository and get back one Excel workbook that answers: which open issues are
actual bugs, how severe they are, which ones to fix first, which labels look wrong, and which issues
are probably duplicates.

```bash
issue-triage https://github.com/owner/repo
```

It reads the labels the maintainers already applied, adds thumbs-up counts, activity and
reproduction signals, and can optionally have Claude read issues that nobody has categorized yet.

## Quick start

You need Python 3.11+ and a GitHub token.

```bash
# install (pipx keeps it isolated; plain `pip install` works too)
pipx install "git+https://github.com/victogomez-cs/issue-triage"

# let it read GitHub: either log in with the GitHub CLI...
gh auth login
# ...or set a personal access token
export GITHUB_TOKEN=github_pat_...

# run it
issue-triage https://github.com/owner/repo
```

Run `issue-triage` with no arguments and it asks for the repository instead.

The report is written to `triage-output/<owner>__<repo>/<owner>__<repo>_triage.xlsx`, with a `.csv` of
the same rows next to it.

### With AI classification (optional)

```bash
pipx install "issue-triage[ai] @ git+https://github.com/victogomez-cs/issue-triage"
export ANTHROPIC_API_KEY=sk-ant-...
issue-triage https://github.com/owner/repo
```

When `ANTHROPIC_API_KEY` is set, the same command also classifies each issue's kind and severity with
Claude. It shows a token estimate and asks before spending anything. Try a small run first:

```bash
issue-triage owner/repo --limit 50
```

Get a key at [platform.claude.com](https://platform.claude.com) (Settings → API keys). API usage is
billed separately from Claude.ai subscriptions; see [pricing](https://www.anthropic.com/pricing).

### From source

```bash
git clone https://github.com/victogomez-cs/issue-triage
cd issue-triage
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -e ".[ai]"
issue-triage --help
```

## What's in the workbook

| Sheet | Contents |
|---|---|
| Summary | Counts by kind, triage status, priority, severity and review flag, plus bugs by team/area × urgency. Live formulas. |
| Bugs Ranked | Every bug, highest score first. Start at the top. |
| Review Queue | Issues whose labels probably need attention: untriaged, crashes at the lowest priority, AI disagreeing with a label, possible duplicates. |
| All Issues | Every open issue with every column, as a filterable table. |
| Duplicates | Possible duplicate pairs with a similarity score. |
| Labels | Every label in the repo and how it was interpreted. Use it to spot labels the tool doesn't understand yet. |
| Scoring | Every weight and rule used, so any ranking can be explained. |

Each issue number links to GitHub, and a **Score breakdown** column shows exactly why an issue ranks
where it does.

## How it decides

**Human labels win.** If a maintainer labeled an issue as a bug or a feature (or set GitHub's issue
type), that's its kind. The AI only fills in issues without one, and adds a review flag when it
confidently disagrees with a label. Nothing is ever written back to GitHub.

**Severity** is how bad a bug is: the worse of what the labels say (crash, regression, `severity: high`,
…) and the AI's rating. Without AI, an unlabeled issue whose title mentions a crash, hang or data loss
is rated high.

**Urgency** is what to fix first. It's a score combining priority labels, severity, reproduction steps,
production/customer labels, thumbs-up reactions, a recent "found in" release and recent activity,
minus points for issues waiting on the reporter. All weights can be changed.

**Duplicates** are found by comparing titles and descriptions locally (no API). Issues that differ only
in a number or platform ("Release 3.4.1" / "Release 3.4.2", "…on Windows" / "…on Linux") are treated
as intentional siblings, not duplicates. Add `--embeddings` for a more semantic comparison
(`pip install "issue-triage[embeddings]"`).

## Label profiles

Every project labels issues differently, so label handling lives in a **profile**:

- `generic` (the default) understands common conventions: `bug` / `enhancement` / `question`,
  `kind/bug`, `type: bug`, `C-bug`, `priority: high`, `P1`, `P-high`, `priority/important-soon`,
  `area/x`, `sig/x`, `T-x`, `needs-triage`, `needs more info`, `waiting for response`, and more.
- `flutter` follows [flutter/flutter's triage process](https://github.com/flutter/flutter/tree/master/docs/triage)
  (P0–P3, `team-*` / `triaged-*`, `c:` categories). It's picked automatically for that repo.

To adapt it to your repository, copy [`examples/triage.toml`](examples/triage.toml), edit it, and pass it in:

```bash
issue-triage owner/repo --config triage.toml
```

Your rules are checked before the profile's own, so you only write what's different. To see how
labels are currently read, look at the Labels sheet or run:

```bash
issue-triage labels owner/repo
```

To add a built-in profile for another project, add a `.toml` file to `src/issue_triage/profiles/`
(and optionally an entry in `AUTO_PROFILES` in `profile.py`).

## Commands

| Command | What it does |
|---|---|
| `issue-triage <repo>` or `issue-triage run <repo>` | The whole process: export → AI (if a key is set) → duplicates → report |
| `issue-triage export <repo>` | Download open issues (resumes if interrupted) |
| `issue-triage classify <repo>` | AI classification only (`--dry-run` to estimate tokens) |
| `issue-triage duplicates <repo>` | Duplicate detection only |
| `issue-triage report <repo>` | Rebuild the workbook from saved data (e.g. after editing your config) |
| `issue-triage import-ai <repo> <file>` | Use AI results produced elsewhere (`.json` array or `.jsonl`) |
| `issue-triage labels <repo>` | Show how each label is interpreted |
| `issue-triage profiles` | List built-in profiles |

Useful options for `run`: `--refresh` (download issues again), `--no-ai` / `--ai`, `--limit N`,
`--scope all|bugs|uncategorized`, `--ai-mode batch|sync`, `--model`, `--config`, `--profile`,
`--out DIR`, `--yes` (don't ask before spending API credits). See `issue-triage run -h`.

Everything is cached per repository, so re-running is cheap: the export is reused unless you pass
`--refresh`, and AI results are reused for every issue that hasn't been edited since.

## AI details

- Default model: `claude-haiku-4-5-20251001` (fast and inexpensive). Use `--model` for another.
- Up to 300 issues are sent as immediate requests; more go through the
  [Message Batches API](https://platform.claude.com/docs/en/build-with-claude/batch-processing), which costs
  half as much and usually finishes within an hour (the API allows up to 24 hours). Use `--no-wait` to submit
  and come back later; running the same command again collects the results.
- Each request contains one issue's title, labels and a cleaned-up description (template comments
  removed, long logs shortened). Comments aren't sent.
- The prompt tells the model to treat issue text as untrusted data, and the model can only answer with
  a fixed set of fields. Still, treat AI severities as a sorting aid and read an issue before acting.
- Add a `project_context = "..."` line to your config to give the model one sentence about the project.

## Tokens and permissions

- **Public repos:** any token works; a fine-grained token with *Public repositories (read-only)* is enough.
- **Private repos:** a fine-grained token with read access to *Issues* and *Metadata* on that repo.
- **GitHub Enterprise:** pass the full URL (`https://ghe.example.com/org/repo`) and set `GH_ENTERPRISE_TOKEN`
  (or `GITHUB_TOKEN`), or log in with `gh auth login --hostname ghe.example.com`.

The export uses GitHub's GraphQL API, about one request per 50 issues, and backs off automatically if
it hits a rate limit.

## Privacy

All data stays on your machine in `triage-output/`, which `.gitignore` excludes. When AI is enabled,
issue titles, labels and descriptions are sent to Anthropic's API.

## Development

```bash
pip install -e ".[dev]"
pytest
```

The tests fake both GitHub and the Anthropic API, so they run offline. The code is small and split by stage:

| File | Stage |
|---|---|
| `github.py` | Export via GraphQL |
| `ai.py` | Classification, batching and the results cache |
| `dedupe.py` | Duplicate detection |
| `profile.py`, `profiles/*.toml` | Label interpretation |
| `scoring.py` | Kind, severity, urgency and review flags |
| `report.py` | The Excel workbook and CSV |
| `cli.py` | Commands |

## Limitations

- Only open issues are analyzed; pull requests are ignored.
- Severity and kind from the AI are estimates from the issue text, not a verdict.
- Duplicate detection finds similar wording; it can miss duplicates described very differently
  (try `--embeddings`) and will list some related-but-distinct issues.

## License

MIT. See [LICENSE](LICENSE).
